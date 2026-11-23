from __future__ import annotations

import keyword
import logging
import asyncio
from dataclasses import dataclass, field
from pathlib import Path
import ast
from typing import Any, Dict
from importlib import metadata

import draconic
from lsprotocol import types
from pygls import uris
from pygls.lsp.server import LanguageServer

from avrae_ls.config import AvraeLSConfig, load_config
from avrae_ls.runtime.context import ContextBuilder, GVarResolver
from avrae_ls.analysis.diagnostics import DiagnosticProvider
from avrae_ls.runtime.runtime import MockExecutor
from avrae_ls.runtime.alias_preview import render_alias_command, simulate_command
from avrae_ls.runtime.errors import format_runtime_error, runtime_error_details
from avrae_ls.analysis.parser import DRACONIC_RE, INLINE_DRACONIC_RE, is_alias_module_path
from avrae_ls.analysis.source_context import build_source_context, code_for_position
from avrae_ls.lsp.signature_help import load_signatures, signature_help_for_code
from avrae_ls.lsp.completions import gather_suggestions, completion_items_for_position, hover_for_position
from avrae_ls.lsp.code_actions import code_actions_for_document
from avrae_ls.analysis.bindings import build_binding_index
from avrae_ls.analysis.workspace_symbols import workspace_sources, module_references, same_location, navigation_resolver
from avrae_ls.analysis.symbols import (
    build_symbol_table,
    document_symbols,
    range_for_word,
)
from avrae_ls.analysis.module_symbols import (
    literal_using_imports,
    module_exports_for_code,
    prefetch_module_dependency_ids,
)

# Prefer package metadata so the server version matches the installed wheel.
try:
    __version__ = metadata.version("avrae-ls")
except metadata.PackageNotFoundError:
    __version__ = "0.0.0+unknown"
log = logging.getLogger(__name__)

RUN_ALIAS_COMMAND = "avrae.runAlias"
REFRESH_CONFIG_COMMAND = "avrae.reloadConfig"
REFRESH_GVARS_COMMAND = "avrae.refreshGvars"
GET_GVAR_CONTENT_COMMAND = "avrae.getGvarContent"
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
    module_resolver: GVarResolver
    warnings: list[str] = field(default_factory=list)
    module_gvars: dict[str, Any] | None = None
    module_profile: str | None = None
    module_gvars_stale: bool = False

    def sync_module_gvars(self, gvars: dict[str, Any], profile: str | None) -> GVarResolver:
        previous = self.module_gvars or {}
        for key in previous.keys() - gvars.keys():
            self.module_resolver.discard(key)
        self.module_resolver.seed(
            {key: value for key, value in gvars.items() if key not in previous or previous[key] != value}
        )
        self.module_gvars = gvars
        self.module_profile = profile
        self.module_gvars_stale = False
        return self.module_resolver

    def current_module_resolver(self) -> GVarResolver:
        if self.module_gvars is None or self.module_gvars_stale:
            baseline = self.context_builder.build_baseline(self.module_profile)
            return self.sync_module_gvars(baseline.vars.gvars, self.module_profile)
        return self.module_resolver


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
            module_resolver=GVarResolver(config),
            warnings=list(warnings),
        )
        self._workspace_root = root
        log.info("Loaded workspace at %s", root)


ls = AvraeLanguageServer()


@ls.feature(types.INITIALIZE)
def on_initialize(server: AvraeLanguageServer, params: types.InitializeParams):
    root_uri = params.root_uri or (params.workspace_folders[0].uri if params.workspace_folders else None)
    if root_uri:
        fs_path = uris.to_fs_path(root_uri)
        root_path = Path(fs_path) if fs_path else Path.cwd()
    else:
        root_path = Path.cwd()
    server.load_workspace(root_path)


@ls.feature(types.INITIALIZED)
async def on_initialized(server: AvraeLanguageServer, params: types.InitializedParams):
    for warning in server.state.warnings:
        server.window_log_message(types.LogMessageParams(type=types.MessageType.Warning, message=warning))


@ls.feature(types.TEXT_DOCUMENT_DID_OPEN)
async def did_open(server: AvraeLanguageServer, params: types.DidOpenTextDocumentParams):
    asyncio.create_task(_prefetch_modules(server, params.text_document.text))
    await _publish_diagnostics(server, params.text_document.uri)


@ls.feature(types.TEXT_DOCUMENT_DID_CHANGE)
async def did_change(server: AvraeLanguageServer, params: types.DidChangeTextDocumentParams):
    doc = server.workspace.get_text_document(params.text_document.uri)
    asyncio.create_task(_prefetch_modules(server, doc.source))
    await _publish_diagnostics(server, params.text_document.uri)


@ls.feature(types.TEXT_DOCUMENT_DID_SAVE)
async def did_save(server: AvraeLanguageServer, params: types.DidSaveTextDocumentParams):
    await _publish_diagnostics(server, params.text_document.uri)


@ls.feature(types.WORKSPACE_DID_CHANGE_WATCHED_FILES)
def did_change_watched_files(server: AvraeLanguageServer, params: types.DidChangeWatchedFilesParams) -> None:
    state = server.state
    for change in params.changes:
        path = uris.to_fs_path(change.uri)
        if path is not None and state.context_builder.invalidate_gvar_source(Path(path)):
            state.module_gvars_stale = True


@ls.feature(types.WORKSPACE_DID_CHANGE_CONFIGURATION)
async def did_change_config(server: AvraeLanguageServer, params: types.DidChangeConfigurationParams):
    server.load_workspace(server.workspace_root)
    for warning in server.state.warnings:
        server.window_log_message(types.LogMessageParams(type=types.MessageType.Warning, message=warning))


@ls.feature(types.TEXT_DOCUMENT_DOCUMENT_SYMBOL)
def on_document_symbol(server: AvraeLanguageServer, params: types.DocumentSymbolParams):
    doc = server.workspace.get_text_document(params.text_document.uri)
    symbols = document_symbols(doc.source, treat_as_module=_is_alias_module_document(doc))
    return symbols


@ls.feature(types.TEXT_DOCUMENT_DEFINITION)
def on_definition(server: AvraeLanguageServer, params: types.DefinitionParams):
    doc = server.workspace.get_text_document(params.text_document.uri)
    resolver = navigation_resolver(
        server.state.current_module_resolver(), server.state.config, server.workspace.text_documents
    )
    is_module = _is_alias_module_document(doc)
    for rng, target in module_references(doc.source, is_module, resolver):
        if (
            rng.start.line == params.position.line
            and rng.start.character <= params.position.character <= rng.end.character
        ):
            return target
    index = build_binding_index(doc.source, treat_as_module=is_module)
    binding = index.at(params.position)
    if binding is None or not binding.declarations:
        return None
    return types.Location(uri=params.text_document.uri, range=binding.declarations[0])


def _symbol_locations(server: AvraeLanguageServer, params, *, include_declaration=True, new_name=None):
    doc = server.workspace.get_text_document(params.text_document.uri)
    resolver = navigation_resolver(
        server.state.current_module_resolver(), server.state.config, server.workspace.text_documents
    )
    is_module = _is_alias_module_document(doc)
    index = build_binding_index(doc.source, treat_as_module=is_module)
    binding = index.at(params.position)
    target = None
    if binding is not None:
        if new_name and not index.can_rename(binding, new_name):
            return []
        if not binding.declarations:
            return []
        target = types.Location(uri=doc.uri, range=binding.declarations[0])
        locations = [
            types.Location(uri=doc.uri, range=r)
            for r in [*(binding.declarations if include_declaration else []), *binding.reads]
        ]
        if binding.scope is not index.root or not is_module:
            return locations
    else:
        locations = []
        for rng, location in module_references(doc.source, is_module, resolver):
            p = params.position
            if rng.start.line == p.line and rng.start.character <= p.character <= rng.end.character:
                target = location
                break
    if target is None:
        return []
    # Remote gvars are navigable but cannot be safely renamed in Avrae.
    if new_name and not target.uri.startswith("file:"):
        return []
    sources = workspace_sources(server.workspace_root, server.workspace.text_documents)
    if target.uri.startswith("file:") and target.uri not in sources:
        path = uris.to_fs_path(target.uri)
        if path:
            try:
                sources[target.uri] = (Path(path).read_text(), True)
            except (OSError, UnicodeError):
                return []
    for uri, (source, module) in sources.items():
        if uri == target.uri:
            target_index = build_binding_index(source, treat_as_module=module)
            target_binding = target_index.at(target.range.start)
            if target_binding is None or (new_name and not target_index.can_rename(target_binding, new_name)):
                return []
            locations.extend(
                types.Location(uri=uri, range=r)
                for r in [*(target_binding.declarations if include_declaration else []), *target_binding.reads]
            )
        for rng, definition in module_references(source, module, resolver):
            if same_location(definition, target):
                locations.append(types.Location(uri=uri, range=rng))
    unique = {}
    for location in locations:
        r = location.range
        unique[(location.uri, r.start.line, r.start.character, r.end.line, r.end.character)] = location
    return list(unique.values())


@ls.feature(types.TEXT_DOCUMENT_REFERENCES)
def on_references(server: AvraeLanguageServer, params: types.ReferenceParams):
    return _symbol_locations(server, params, include_declaration=params.context.include_declaration)


@ls.feature(types.TEXT_DOCUMENT_PREPARE_RENAME)
def on_prepare_rename(server: AvraeLanguageServer, params: types.PrepareRenameParams):
    doc = server.workspace.get_text_document(params.text_document.uri)
    if not _symbol_locations(server, params):
        return None
    return range_for_word(doc.source, params.position)


@ls.feature(types.TEXT_DOCUMENT_RENAME)
def on_rename(server: AvraeLanguageServer, params: types.RenameParams):
    if (
        not params.new_name.isidentifier()
        or keyword.iskeyword(params.new_name)
        or params.new_name in server.state.executor.available_names(server.state.context_builder.build())
    ):
        return None
    locations = _symbol_locations(server, params, new_name=params.new_name)
    if not locations:
        return None
    changes: dict[str, list[types.TextEdit]] = {}
    for location in locations:
        changes.setdefault(location.uri, []).append(types.TextEdit(range=location.range, new_text=params.new_name))
    return types.WorkspaceEdit(changes=changes)


@ls.feature(types.WORKSPACE_SYMBOL)
def on_workspace_symbol(server: AvraeLanguageServer, params: types.WorkspaceSymbolParams):
    symbols: list[types.SymbolInformation] = []
    query = (params.query or "").lower()
    for uri, (source, is_module) in workspace_sources(server.workspace_root, server.workspace.text_documents).items():
        table = build_symbol_table(source, treat_as_module=is_module)
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
    selected = code_for_position(source_ctx, params.position.line, params.position.character)
    if selected is None:
        return None
    code, rel_line, rel_char = selected
    return signature_help_for_code(code, rel_line, rel_char, server._signatures)


@ls.feature(
    types.TEXT_DOCUMENT_COMPLETION,
    types.CompletionOptions(trigger_characters=["."]),
)
def on_completion(server: AvraeLanguageServer, params: types.CompletionParams):
    doc = server.workspace.get_text_document(params.text_document.uri)
    ctx_data = server.state.context_builder.build()
    is_module = _is_alias_module_document(doc)
    suggestions = gather_suggestions(
        ctx_data, server.state.context_builder.gvar_resolver, server._signatures, treat_as_module=is_module
    )
    source_ctx = build_source_context(doc.source, is_module)
    selected = code_for_position(source_ctx, params.position.line, params.position.character)
    if selected is None:
        return []
    code, rel_line, rel_char = selected
    modules = module_exports_for_code(code, server.state.current_module_resolver())
    return completion_items_for_position(code, rel_line, rel_char, suggestions, modules)


@ls.feature(types.TEXT_DOCUMENT_HOVER)
def on_hover(server: AvraeLanguageServer, params: types.HoverParams):
    doc = server.workspace.get_text_document(params.text_document.uri)
    ctx_data = server.state.context_builder.build()
    is_module = _is_alias_module_document(doc)
    source_ctx = build_source_context(doc.source, is_module)
    selected = code_for_position(source_ctx, params.position.line, params.position.character)
    if selected is None:
        return None
    code, rel_line, rel_char = selected
    return hover_for_position(
        code,
        rel_line,
        rel_char,
        server._signatures,
        ctx_data,
        server.state.context_builder.gvar_resolver,
        treat_as_module=is_module,
    )


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
    raw_alias_args: str | None = None
    doc = None
    if isinstance(payload, dict):
        uri = payload.get("uri")
        text = payload.get("text")
        profile = payload.get("profile")
        if isinstance(payload.get("args"), list):
            alias_args = [str(a) for a in payload["args"]]
        if isinstance(payload.get("rawArgs"), str):
            raw_alias_args = payload["rawArgs"]

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
        raw_args=raw_alias_args,
    )
    preview = simulate_command(rendered.command)

    response: dict[str, Any] = {
        "stdout": rendered.stdout,
        "result": preview.preview if preview.preview is not None else rendered.last_value,
        "command": rendered.command,
        "commandName": preview.command_name,
    }
    if rendered.error:
        details = runtime_error_details(rendered.error)
        response["error"] = details.message
        response["errorDetails"] = details.to_dict()
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
                _runtime_diagnostic_with_source(rendered.error, server.state.config.diagnostics.runtime_level, src)
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

    state = server.state
    ctx_data = state.context_builder.build(profile)
    # The editor-facing resolver owns the virtual GVAR documents and module
    # symbol cache. Rebuild it too, so Refresh GVARs cannot leave definitions
    # and virtual documents pointing at stale remote content.
    state.module_resolver.reset(ctx_data.vars.gvars)
    state.module_gvars = ctx_data.vars.gvars
    state.module_profile = profile
    state.module_gvars_stale = False
    snapshot = await state.context_builder.gvar_resolver.refresh(ctx_data.vars.gvars, keys)
    if keys:
        await state.current_module_resolver().ensure_many(keys)
    return {"count": len(snapshot), "gvars": snapshot}


@ls.command(GET_GVAR_CONTENT_COMMAND)
async def get_gvar_content(server: AvraeLanguageServer, *args: Any):
    payload = args[0] if args else {}
    gvar_id = str(payload.get("id", "")) if isinstance(payload, dict) else ""
    if not gvar_id:
        return {"content": None}
    resolver = server.state.current_module_resolver()
    await resolver.ensure(gvar_id)
    content = resolver.get_local(gvar_id)
    return {"content": content if isinstance(content, str) else None}


@ls.command(REFRESH_CONFIG_COMMAND)
def reload_config(server: AvraeLanguageServer, *args: Any):
    server.load_workspace(server.workspace_root)
    return {"status": "ok"}


async def _prefetch_modules(server: AvraeLanguageServer, source: str) -> None:
    """Warm the editor-only module resolver without delaying completion requests."""
    state = server.state
    baseline = state.context_builder.build_baseline()
    resolver = state.sync_module_gvars(baseline.vars.gvars, None)
    source_ctx = build_source_context(source, False)
    keys: set[str] = set()
    blocks = source_ctx.blocks or []
    if blocks:
        for block in blocks:
            keys.update(literal_using_imports(block.code).values())
    else:
        keys.update(literal_using_imports(source_ctx.prepared).values())
    if keys:
        await prefetch_module_dependency_ids(keys, resolver)


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


def _runtime_diagnostic_with_source(error: BaseException, level: str, source: str | None) -> types.Diagnostic:
    severity = LEVEL_TO_SEVERITY.get(level.lower(), types.DiagnosticSeverity.Error)
    details = runtime_error_details(error)
    if source:
        rng = _runtime_node_range_in_source(error, source, details.import_chain[0] if details.import_chain else None)
        if rng:
            return types.Diagnostic(
                message=details.message,
                range=rng,
                severity=severity,
                source="avrae-ls-runtime",
            )
    if source and details.import_chain:
        rng = _find_using_range_in_source(source, details.import_chain[0])
        if rng:
            return types.Diagnostic(
                message=details.message,
                range=rng,
                severity=severity,
                source="avrae-ls-runtime",
            )
    if isinstance(error, draconic.DraconicSyntaxError):
        rng = types.Range(
            start=types.Position(line=max((error.lineno or 1) - 1, 0), character=max((error.offset or 1) - 1, 0)),
            end=types.Position(
                line=max(((error.end_lineno or error.lineno or 1) - 1), 0),
                character=max(((error.end_offset or error.offset or 1) - 1), 0),
            ),
        )
        message = format_runtime_error(error)
    elif hasattr(error, "node"):
        node = getattr(error, "node")
        rng = types.Range(
            start=types.Position(
                line=max(getattr(node, "lineno", 1) - 1, 0), character=max(getattr(node, "col_offset", 0), 0)
            ),
            end=types.Position(
                line=max(getattr(node, "end_lineno", getattr(node, "lineno", 1)) - 1, 0),
                character=max(getattr(node, "end_col_offset", getattr(node, "col_offset", 0) + 1), 0),
            ),
        )
        message = format_runtime_error(error)
    else:
        rng = types.Range(
            start=types.Position(line=0, character=0),
            end=types.Position(line=0, character=1),
        )
        message = format_runtime_error(error)
    return types.Diagnostic(message=message, range=rng, severity=severity, source="avrae-ls-runtime")


def _runtime_node_range_in_source(
    error: BaseException, source: str, imported_module: str | None = None
) -> types.Range | None:
    node = getattr(error, "node", None)
    if node is None:
        return None
    if _node_is_using_import_for_module(node, imported_module):
        return None
    rng = _range_from_ast_node(node)
    if rng is None:
        return None

    blocks = _raw_draconic_block_offsets(source)
    if not blocks:
        return rng
    if len(blocks) == 1:
        line_offset, char_offset, _ = blocks[0]
        return _shift_range(rng, line_offset, char_offset)
    for line_offset, char_offset, line_count in blocks:
        if rng.start.line < line_count:
            return _shift_range(rng, line_offset, char_offset)
    return None


def _node_is_using_import_for_module(node: Any, module: str | None) -> bool:
    if not module:
        return False
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "using":
        return False
    for kw in node.keywords:
        if kw.arg and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            if kw.value.value == module:
                return True
    return False


def _raw_draconic_block_offsets(source: str) -> list[tuple[int, int, int]]:
    matches: list[tuple[int, int, int, int]] = []
    for match in DRACONIC_RE.finditer(source):
        matches.append(_raw_block_offset(source, match))
    for match in INLINE_DRACONIC_RE.finditer(source):
        matches.append(_raw_block_offset(source, match))
    matches.sort(key=lambda item: item[0])
    return [(line_offset, char_offset, line_count) for _, line_offset, char_offset, line_count in matches]


def _raw_block_offset(source: str, match: Any) -> tuple[int, int, int, int]:
    raw = match.group(1)
    prefix = source[: match.start(1)]
    line_offset = prefix.count("\n")
    last_nl = prefix.rfind("\n")
    char_offset = match.start(1) - (last_nl + 1 if last_nl != -1 else 0)
    line_count = raw.count("\n") + 1 if raw else 1
    return match.start(), line_offset, char_offset, line_count


def _range_from_ast_node(node: Any) -> types.Range | None:
    lineno = getattr(node, "lineno", None)
    col_offset = getattr(node, "col_offset", None)
    if not isinstance(lineno, int) or not isinstance(col_offset, int):
        return None
    end_lineno = getattr(node, "end_lineno", lineno)
    end_col_offset = getattr(node, "end_col_offset", col_offset + 1)
    if not isinstance(end_lineno, int):
        end_lineno = lineno
    if not isinstance(end_col_offset, int):
        end_col_offset = col_offset + 1
    return types.Range(
        start=types.Position(line=max(lineno - 1, 0), character=max(col_offset, 0)),
        end=types.Position(line=max(end_lineno - 1, 0), character=max(end_col_offset, col_offset + 1)),
    )


def _find_using_range_in_source(source: str, module: str | None) -> types.Range | None:
    rng = _find_using_range(source, module)
    if rng is not None:
        return rng

    source_ctx = build_source_context(source, False)
    for block in source_ctx.blocks:
        rng = _find_using_range(block.code, module)
        if rng is not None:
            return _shift_range(rng, block.line_offset, block.char_offset)
    return None


def _shift_range(rng: types.Range, line_offset: int, char_offset: int) -> types.Range:
    def _shift_position(position: types.Position) -> types.Position:
        line = position.line + line_offset
        character = position.character + char_offset if position.line == 0 else position.character
        return types.Position(line=line, character=character)

    return types.Range(start=_shift_position(rng.start), end=_shift_position(rng.end))


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
                        end_line = (node.end_lineno or node.lineno) - 1
                        end_char = node.end_col_offset if node.end_col_offset is not None else node.col_offset + 1
                        end = types.Position(line=end_line, character=end_char)
                        return types.Range(start=start, end=end)
    return None


def _is_alias_module_document(doc: Any) -> bool:
    language_id = getattr(doc, "language_id", None)
    if language_id == "avrae-module":
        return True
    uri = getattr(doc, "uri", None)
    if not isinstance(uri, str):
        return False
    if uri.startswith("avrae-gvar://"):
        return True
    try:
        fs_path = uris.to_fs_path(uri)
        if not fs_path:
            return uri.endswith(".alias-module")
        path = Path(fs_path)
    except Exception:
        return uri.endswith(".alias-module")
    return is_alias_module_path(path)


def create_server() -> AvraeLanguageServer:
    return ls
