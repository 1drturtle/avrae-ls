from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
import ast
from typing import Any, Dict
from importlib import metadata

import draconic
from lsprotocol import types
from pygls import uris
from pygls.lsp.server import LanguageServer

from .config import AvraeLSConfig, load_config
from .context import ContextBuilder
from .diagnostics import DiagnosticProvider
from .runtime import MockExecutor
from .alias_preview import render_alias_command, simulate_command
from .parser import is_alias_module_path
from .source_context import build_source_context, block_for_line
from .signature_help import load_signatures, signature_help_for_code
from .completions import gather_suggestions, completion_items_for_position, hover_for_position
from .code_actions import code_actions_for_document
from .symbols import build_symbol_table, document_symbols, find_definition_range, find_references, range_for_word

# Prefer package metadata so the server version matches the installed wheel.
try:
    __version__ = metadata.version("avrae-ls")
except metadata.PackageNotFoundError:
    __version__ = "0.0.0+unknown"
log = logging.getLogger(__name__)

RUN_ALIAS_COMMAND = "avrae.runAlias"
REFRESH_CONFIG_COMMAND = "avrae.reloadConfig"
REFRESH_GVARS_COMMAND = "avrae.refreshGvars"
LEVEL_TO_SEVERITY = {
    "error": types.DiagnosticSeverity.Error,
    "warning": types.DiagnosticSeverity.Warning,
    "info": types.DiagnosticSeverity.Information,
}


@dataclass
class ServerState:
    config: AvraeLSConfig
    context_builder: ContextBuilder
    diagnostics: DiagnosticProvider
    executor: MockExecutor
    warnings: list[str] = field(default_factory=list)


class AvraeLanguageServer(LanguageServer):
    def __init__(self):
        super().__init__(
            name="avrae-ls",
            version=__version__,
            text_document_sync_kind=types.TextDocumentSyncKind.Incremental,
        )
        self._state: ServerState | None = None
        self._workspace_root: Path | None = None
        self._signatures: Dict[str, Any] = load_signatures()

    @property
    def state(self) -> ServerState:
        if self._state is None:
            raise RuntimeError("Server has not been initialized")
        return self._state

    @property
    def workspace_root(self) -> Path:
        if self._workspace_root is None:
            return Path.cwd()
        return self._workspace_root

    def load_workspace(self, root: Path) -> None:
        config, warnings = load_config(root)
        executor = MockExecutor(config.service)
        context_builder = ContextBuilder(config)
        diagnostics = DiagnosticProvider(executor, config.diagnostics)
        self._state = ServerState(
            config=config,
            context_builder=context_builder,
            diagnostics=diagnostics,
            executor=executor,
            warnings=list(warnings),
        )
        self._workspace_root = root
        log.info("Loaded workspace at %s", root)


ls = AvraeLanguageServer()


@ls.feature(types.INITIALIZE)
def on_initialize(server: AvraeLanguageServer, params: types.InitializeParams):
    root_uri = params.root_uri or (params.workspace_folders[0].uri if params.workspace_folders else None)
    root_path = Path(uris.to_fs_path(root_uri)) if root_uri else Path.cwd()
    server.load_workspace(root_path)


@ls.feature(types.INITIALIZED)
async def on_initialized(server: AvraeLanguageServer, params: types.InitializedParams):
    for warning in server.state.warnings:
        server.window_log_message(
            types.LogMessageParams(type=types.MessageType.Warning, message=warning)
        )


@ls.feature(types.TEXT_DOCUMENT_DID_OPEN)
async def did_open(server: AvraeLanguageServer, params: types.DidOpenTextDocumentParams):
    await _publish_diagnostics(server, params.text_document.uri)


@ls.feature(types.TEXT_DOCUMENT_DID_CHANGE)
async def did_change(server: AvraeLanguageServer, params: types.DidChangeTextDocumentParams):
    await _publish_diagnostics(server, params.text_document.uri)


@ls.feature(types.TEXT_DOCUMENT_DID_SAVE)
async def did_save(server: AvraeLanguageServer, params: types.DidSaveTextDocumentParams):
    await _publish_diagnostics(server, params.text_document.uri)


@ls.feature(types.WORKSPACE_DID_CHANGE_CONFIGURATION)
async def did_change_config(server: AvraeLanguageServer, params: types.DidChangeConfigurationParams):
    server.load_workspace(server.workspace_root)
    for warning in server.state.warnings:
        server.window_log_message(
            types.LogMessageParams(type=types.MessageType.Warning, message=warning)
        )


@ls.feature(types.TEXT_DOCUMENT_DOCUMENT_SYMBOL)
def on_document_symbol(server: AvraeLanguageServer, params: types.DocumentSymbolParams):
    doc = server.workspace.get_text_document(params.text_document.uri)
    symbols = document_symbols(doc.source, treat_as_module=_is_alias_module_document(doc))
    return symbols


@ls.feature(types.TEXT_DOCUMENT_DEFINITION)
def on_definition(server: AvraeLanguageServer, params: types.DefinitionParams):
    doc = server.workspace.get_text_document(params.text_document.uri)
    table = build_symbol_table(doc.source, treat_as_module=_is_alias_module_document(doc))
    word = doc.word_at_position(params.position)
    rng = find_definition_range(table, word)
    if rng is None:
        return None
    return types.Location(uri=params.text_document.uri, range=rng)


@ls.feature(types.TEXT_DOCUMENT_REFERENCES)
def on_references(server: AvraeLanguageServer, params: types.ReferenceParams):
    doc = server.workspace.get_text_document(params.text_document.uri)
    is_module = _is_alias_module_document(doc)
    table = build_symbol_table(doc.source, treat_as_module=is_module)
    word = doc.word_at_position(params.position)
    if not word or not table.lookup(word):
        return []

    ranges = find_references(
        table,
        doc.source,
        word,
        include_declaration=params.context.include_declaration,
        treat_as_module=is_module,
    )
    return [types.Location(uri=params.text_document.uri, range=rng) for rng in ranges]


@ls.feature(types.TEXT_DOCUMENT_PREPARE_RENAME)
def on_prepare_rename(server: AvraeLanguageServer, params: types.PrepareRenameParams):
    doc = server.workspace.get_text_document(params.text_document.uri)
    table = build_symbol_table(doc.source, treat_as_module=_is_alias_module_document(doc))
    word = doc.word_at_position(params.position)
    if not word or not table.lookup(word):
        return None
    return range_for_word(doc.source, params.position)


@ls.feature(types.TEXT_DOCUMENT_RENAME)
def on_rename(server: AvraeLanguageServer, params: types.RenameParams):
    doc = server.workspace.get_text_document(params.text_document.uri)
    is_module = _is_alias_module_document(doc)
    table = build_symbol_table(doc.source, treat_as_module=is_module)
    word = doc.word_at_position(params.position)
    if not word or not table.lookup(word) or not params.new_name:
        return None

    ranges = find_references(table, doc.source, word, include_declaration=True, treat_as_module=is_module)
    if not ranges:
        return None
    edits = [types.TextEdit(range=rng, new_text=params.new_name) for rng in ranges]
    return types.WorkspaceEdit(changes={params.text_document.uri: edits})


@ls.feature(types.WORKSPACE_SYMBOL)
def on_workspace_symbol(server: AvraeLanguageServer, params: types.WorkspaceSymbolParams):
    symbols: list[types.SymbolInformation] = []
    query = (params.query or "").lower()
    for uri, doc in server.workspace.text_documents.items():
        table = build_symbol_table(doc.source, treat_as_module=_is_alias_module_document(doc))
        for entry in table.entries:
            if query and query not in entry.name.lower():
                continue
            symbols.append(
                types.SymbolInformation(
                    name=entry.name,
                    kind=entry.kind,
                    location=types.Location(uri=uri, range=entry.range),
                )
            )
    return symbols


@ls.feature(types.TEXT_DOCUMENT_SIGNATURE_HELP)
def on_signature_help(server: AvraeLanguageServer, params: types.SignatureHelpParams):
    doc = server.workspace.get_text_document(params.text_document.uri)
    is_module = _is_alias_module_document(doc)
    source_ctx = build_source_context(doc.source, is_module)
    pos = params.position
    if not source_ctx.blocks:
        return signature_help_for_code(source_ctx.prepared, pos.line, pos.character, server._signatures)

    block = block_for_line(source_ctx.blocks, pos.line)
    if block:
        rel_line = pos.line - block.line_offset
        help_ = signature_help_for_code(block.code, rel_line, pos.character, server._signatures)
        if help_:
            return help_
    return None


@ls.feature(
    types.TEXT_DOCUMENT_COMPLETION,
    types.CompletionOptions(trigger_characters=["."]),
)
def on_completion(server: AvraeLanguageServer, params: types.CompletionParams):
    doc = server.workspace.get_text_document(params.text_document.uri)
    ctx_data = server.state.context_builder.build()
    suggestions = gather_suggestions(ctx_data, server.state.context_builder.gvar_resolver, server._signatures)
    is_module = _is_alias_module_document(doc)
    source_ctx = build_source_context(doc.source, is_module)
    pos = params.position
    if not source_ctx.blocks:
        return completion_items_for_position(source_ctx.prepared, pos.line, pos.character, suggestions)

    block = block_for_line(source_ctx.blocks, pos.line)
    if block:
        rel_line = pos.line - block.line_offset
        return completion_items_for_position(block.code, rel_line, pos.character, suggestions)
    return []


@ls.feature(types.TEXT_DOCUMENT_HOVER)
def on_hover(server: AvraeLanguageServer, params: types.HoverParams):
    doc = server.workspace.get_text_document(params.text_document.uri)
    ctx_data = server.state.context_builder.build()
    pos = params.position
    is_module = _is_alias_module_document(doc)
    source_ctx = build_source_context(doc.source, is_module)
    if not source_ctx.blocks:
        return hover_for_position(
            source_ctx.prepared,
            pos.line,
            pos.character,
            server._signatures,
            ctx_data,
            server.state.context_builder.gvar_resolver,
        )

    block = block_for_line(source_ctx.blocks, pos.line)
    if block:
        rel_line = pos.line - block.line_offset
        return hover_for_position(
            block.code,
            rel_line,
            pos.character,
            server._signatures,
            ctx_data,
            server.state.context_builder.gvar_resolver,
        )
    return None


@ls.feature(types.TEXT_DOCUMENT_CODE_ACTION)
def on_code_action(server: AvraeLanguageServer, params: types.CodeActionParams):
    doc = server.workspace.get_text_document(params.text_document.uri)
    return code_actions_for_document(
        doc.source,
        params,
        server.workspace_root,
        treat_as_module=_is_alias_module_document(doc),
    )


@ls.command(RUN_ALIAS_COMMAND)
async def run_alias(server: AvraeLanguageServer, *args: Any):
    payload = args[0] if args else {}
    uri = None
    text = None
    profile = None
    alias_args: list[str] | None = None
    doc = None
    if isinstance(payload, dict):
        uri = payload.get("uri")
        text = payload.get("text")
        profile = payload.get("profile")
        if isinstance(payload.get("args"), list):
            alias_args = [str(a) for a in payload["args"]]

    if text is None and uri:
        doc = server.workspace.get_text_document(uri)
        text = doc.source

    if text is None:
        return {"error": "No alias content supplied"}

    ctx_data = server.state.context_builder.build(profile)
    rendered = await render_alias_command(
        text,
        server.state.executor,
        ctx_data,
        server.state.context_builder.gvar_resolver,
        args=alias_args,
    )
    preview = simulate_command(rendered.command)

    response: dict[str, Any] = {
        "stdout": rendered.stdout,
        "result": preview.preview if preview.preview is not None else rendered.last_value,
        "command": rendered.command,
        "commandName": preview.command_name,
    }
    if rendered.error:
        response["error"] = _format_runtime_error(rendered.error)
    if preview.validation_error:
        response["validationError"] = preview.validation_error
    if preview.embed:
        response["embed"] = preview.embed.to_dict()
    response["state"] = {
        "character": ctx_data.character,
        "combat": ctx_data.combat,
        "vars": {
            "cvars": dict(ctx_data.vars.cvars),
            "uvars": dict(ctx_data.vars.uvars),
            "svars": dict(ctx_data.vars.svars),
            "gvars": dict(ctx_data.vars.gvars),
        },
    }
    if uri:
        extra = []
        if rendered.error:
            src = doc.source if doc else text
            extra.append(
                _runtime_diagnostic_with_source(
                    rendered.error, server.state.config.diagnostics.runtime_level, src
                )
            )
        await _publish_diagnostics(server, uri, profile=profile, extra=extra)
    return response


@ls.command(REFRESH_GVARS_COMMAND)
async def refresh_gvars(server: AvraeLanguageServer, *args: Any):
    payload = args[0] if args else {}
    profile = None
    keys: list[str] | None = None
    if isinstance(payload, dict):
        profile = payload.get("profile")
        raw_keys = payload.get("keys")
        if isinstance(raw_keys, list):
            keys = [str(k) for k in raw_keys]

    ctx_data = server.state.context_builder.build(profile)
    resolver = server.state.context_builder.gvar_resolver
    snapshot = await resolver.refresh(ctx_data.vars.gvars, keys)
    return {"count": len(snapshot), "gvars": snapshot}


@ls.command(REFRESH_CONFIG_COMMAND)
def reload_config(server: AvraeLanguageServer, *args: Any):
    server.load_workspace(server.workspace_root)
    return {"status": "ok"}


async def _publish_diagnostics(
    server: AvraeLanguageServer,
    uri: str,
    profile: str | None = None,
    extra: list[types.Diagnostic] | None = None,
) -> None:
    doc = server.workspace.get_text_document(uri)
    ctx_data = server.state.context_builder.build(profile)
    diags = await server.state.diagnostics.analyze(
        doc.source,
        ctx_data,
        server.state.context_builder.gvar_resolver,
        treat_as_module=_is_alias_module_document(doc),
    )
    if extra:
        diags.extend(extra)
    server.text_document_publish_diagnostics(
        types.PublishDiagnosticsParams(uri=uri, diagnostics=diags, version=doc.version)
    )


def _format_runtime_error(error: BaseException) -> str:
    if isinstance(error, draconic.DraconicException):
        return error.msg
    return str(error)


def _runtime_diagnostic(error: BaseException, level: str) -> types.Diagnostic:
    return _runtime_diagnostic_with_source(error, level, None)


def _runtime_diagnostic_with_source(error: BaseException, level: str, source: str | None) -> types.Diagnostic:
    severity = LEVEL_TO_SEVERITY.get(level.lower(), types.DiagnosticSeverity.Error)
    if source and hasattr(error, "module"):
        rng = _find_using_range(source, getattr(error, "module", None))
        if rng:
            return types.Diagnostic(message=str(error), range=rng, severity=severity, source="avrae-ls-runtime")
    if isinstance(error, draconic.DraconicSyntaxError):
        rng = types.Range(
            start=types.Position(line=max((error.lineno or 1) - 1, 0), character=max((error.offset or 1) - 1, 0)),
            end=types.Position(
                line=max(((error.end_lineno or error.lineno or 1) - 1), 0),
                character=max(((error.end_offset or error.offset or 1) - 1), 0),
            ),
        )
        message = error.msg
    elif hasattr(error, "node"):
        node = getattr(error, "node")
        rng = types.Range(
            start=types.Position(line=max(getattr(node, "lineno", 1) - 1, 0), character=max(getattr(node, "col_offset", 0), 0)),
            end=types.Position(
                line=max(getattr(node, "end_lineno", getattr(node, "lineno", 1)) - 1, 0),
                character=max(getattr(node, "end_col_offset", getattr(node, "col_offset", 0) + 1), 0),
            ),
        )
        message = getattr(error, "msg", str(error))
    else:
        rng = types.Range(
            start=types.Position(line=0, character=0),
            end=types.Position(line=0, character=1),
        )
        message = str(error)
    return types.Diagnostic(message=message, range=rng, severity=severity, source="avrae-ls-runtime")


def _find_using_range(source: str, module: str | None) -> types.Range | None:
    if not module:
        return None
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "using":
            for kw in node.keywords:
                if kw.arg and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    if kw.value.value == module:
                        start = types.Position(line=node.lineno - 1, character=node.col_offset)
                        end = types.Position(line=node.end_lineno - 1, character=node.end_col_offset)
                        return types.Range(start=start, end=end)
    return None


def _is_alias_module_document(doc: Any) -> bool:
    language_id = getattr(doc, "language_id", None)
    if language_id == "avrae-module":
        return True
    uri = getattr(doc, "uri", None)
    if not isinstance(uri, str):
        return False
    try:
        path = Path(uris.to_fs_path(uri))
    except Exception:
        return uri.endswith(".alias-module")
    return is_alias_module_path(path)


def create_server() -> AvraeLanguageServer:
    return ls
