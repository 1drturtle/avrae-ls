from pathlib import Path

import pytest
from lsprotocol import types
from pygls.workspace import Workspace

from avrae_ls.config import AvraeLSConfig
from avrae_ls.lsp.server import (
    AvraeLanguageServer,
    _prefetch_modules,
    _runtime_diagnostic_with_source,
    on_completion,
    on_definition,
    on_hover,
    run_alias,
)
from avrae_ls.analysis.module_symbols import MAX_MODULE_PREFETCH_DEPTH, prefetch_module_dependency_ids
from avrae_ls.runtime.alias_preview import render_alias_command
from avrae_ls.runtime.context import ContextData, GVarResolver
from avrae_ls.runtime.runtime import MockExecutor


def _server_with_doc(source: str, uri: str = "file:///integration.alias") -> AvraeLanguageServer:
    server = AvraeLanguageServer()
    server.load_workspace(Path("."))
    server.protocol._workspace = Workspace(None, sync_kind=types.TextDocumentSyncKind.Incremental)
    server.protocol.workspace.put_text_document(
        types.TextDocumentItem(uri=uri, language_id="avrae", version=1, text=source)
    )
    return server


def test_completion_round_trip_integration():
    code = "character()."
    uri = "file:///completion.alias"
    server = _server_with_doc(code, uri)
    params = types.CompletionParams(
        text_document=types.TextDocumentIdentifier(uri=uri),
        position=types.Position(line=0, character=len(code)),
        context=None,
    )
    items = on_completion(server, params)
    labels = {item.label for item in items}
    assert "name" in labels
    assert "attacks" in labels


def test_using_module_completion_and_remote_definition():
    code = 'using(lib="remote")\nlib.heal'
    uri = "file:///module-completion.alias"
    server = _server_with_doc(code, uri)
    server.state.module_resolver.seed({"remote": "def heal(target):\n    return target\nbonus = 2"})
    completion_params = types.CompletionParams(
        text_document=types.TextDocumentIdentifier(uri=uri),
        position=types.Position(line=1, character=len("lib.he")),
        context=None,
    )
    labels = {item.label for item in on_completion(server, completion_params)}
    assert "heal" in labels
    definition_params = types.DefinitionParams(
        text_document=types.TextDocumentIdentifier(uri=uri),
        position=types.Position(line=1, character=len("lib.heal")),
    )
    target = on_definition(server, definition_params)
    assert target is not None
    assert target.uri == "avrae-gvar://gvar/remote.gvar"


@pytest.mark.asyncio
async def test_prefetches_nested_remote_modules_for_completion_and_definition(monkeypatch):
    code = 'using(outer="outer")\nouter.inner.answer'
    uri = "file:///nested-module.alias"
    server = _server_with_doc(code, uri)
    server.state.config.enable_gvar_fetch = True
    server.state.config.service.token = "token"
    values = {
        "outer": 'using(inner="inner")\nvalue = 1',
        "inner": "answer = 42",
    }
    fetched: set[str] = set()

    class DummyResponse:
        status_code = 200

        def __init__(self, key: str):
            self._key = key

        def json(self):
            return {"value": values[self._key]}

    class DummyClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            key = url.rsplit("/", 1)[-1]
            fetched.add(key)
            return DummyResponse(key)

    monkeypatch.setattr("avrae_ls.runtime.context.httpx.AsyncClient", DummyClient)

    await _prefetch_modules(server, code)

    completion_params = types.CompletionParams(
        text_document=types.TextDocumentIdentifier(uri=uri),
        position=types.Position(line=1, character=len("outer.inner.an")),
        context=None,
    )
    labels = {item.label for item in on_completion(server, completion_params)}
    assert "answer" in labels
    definition_params = types.DefinitionParams(
        text_document=types.TextDocumentIdentifier(uri=uri),
        position=types.Position(line=1, character=len("outer.inner.answer")),
    )
    target = on_definition(server, definition_params)
    assert target is not None
    assert target.uri == "avrae-gvar://gvar/inner.gvar"
    assert fetched == {"outer", "inner"}


@pytest.mark.asyncio
async def test_module_prefetch_stops_at_cycles_and_depth_limit():
    class RecordingResolver(GVarResolver):
        def __init__(self, values: dict[str, str]):
            super().__init__(AvraeLSConfig.default(Path(".")))
            self.seed(values)
            self.calls: set[str] = set()

        async def ensure_many(self, keys):
            requested = {str(key) for key in keys}
            self.calls.update(requested)
            return {key: self.get_local(key) is not None for key in requested}

    cycle = RecordingResolver({"outer": 'using(inner="inner")', "inner": 'using(outer="outer")'})
    await prefetch_module_dependency_ids(["outer"], cycle)
    assert cycle.calls == {"outer", "inner"}

    chain = {f"module-{index}": f'using(child="module-{index + 1}")' for index in range(MAX_MODULE_PREFETCH_DEPTH)}
    chain[f"module-{MAX_MODULE_PREFETCH_DEPTH}"] = "answer = 42"
    depth_limited = RecordingResolver(chain)
    await prefetch_module_dependency_ids(["module-0"], depth_limited)
    assert f"module-{MAX_MODULE_PREFETCH_DEPTH - 1}" in depth_limited.calls
    assert f"module-{MAX_MODULE_PREFETCH_DEPTH}" not in depth_limited.calls


def test_using_local_gvar_definition_opens_source_file(tmp_path: Path):
    module = tmp_path / "library.gvar"
    module.write_text("def heal(target):\n    return target\n")
    code = 'using(lib="local")\nlib.heal'
    uri = "file:///local-module.alias"
    server = _server_with_doc(code, uri)
    server.state.module_resolver.seed({"local": {"filePath": str(module)}})
    params = types.DefinitionParams(
        text_document=types.TextDocumentIdentifier(uri=uri),
        position=types.Position(line=1, character=len("lib.heal")),
    )
    target = on_definition(server, params)
    assert target is not None
    assert target.uri == module.as_uri()


def test_hover_round_trip_integration():
    code = "val = character().spellbook\nval"
    uri = "file:///hover.alias"
    server = _server_with_doc(code, uri)
    params = types.HoverParams(
        text_document=types.TextDocumentIdentifier(uri=uri),
        position=types.Position(line=1, character=len("val")),
    )
    hover = on_hover(server, params)
    assert hover is not None
    assert "AliasSpellbook" in hover.contents.value


@pytest.mark.asyncio
async def test_run_alias_returns_structured_module_error_details(tmp_path: Path):
    (tmp_path / ".avraels.json").write_text('{"varFiles":["vars.json"],"profiles":{"default":{"vars":{"gvars":{}}}}}')
    (tmp_path / "vars.json").write_text(
        '{"gvars":{"outer":"using(inner=\\"inner\\")\\nvalue = 1","inner":"a = 1\\nload_json(\\"\\")"}}'
    )
    server = AvraeLanguageServer()
    server.load_workspace(tmp_path)

    result = await run_alias(server, {"text": '!alias boom echo <drac2>using(outer="outer")</drac2>'})

    assert result["error"].startswith("Error in module outer -> inner at line 2:")
    assert result["errorDetails"]["module"] == "inner"
    assert result["errorDetails"]["module_line"] == 2
    assert result["errorDetails"]["import_chain"] == ["outer", "inner"]


@pytest.mark.asyncio
async def test_runtime_diagnostic_for_nested_module_error_points_at_outer_using(tmp_path: Path):
    source = '!alias boom echo\n<drac2>\nusing(outer="outer")\n</drac2>'
    resolver_cfg = AvraeLSConfig.default(tmp_path)
    resolver = GVarResolver(resolver_cfg)
    resolver.reset(
        {
            "outer": 'using(inner="inner")\nvalue = 1',
            "inner": 'a = 1\nload_json("")',
        }
    )
    result = await MockExecutor().run('using(outer="outer")', ContextData(), resolver)

    assert result.error is not None
    diag = _runtime_diagnostic_with_source(result.error, "error", source)
    assert diag.range.start.line == 2
    assert diag.range.start.character == 0
    assert "outer -> inner" in diag.message
    assert "line 2" in diag.message


@pytest.mark.asyncio
async def test_runtime_diagnostic_for_gvar_function_error_points_at_alias_call(tmp_path: Path):
    source = '!echo <drac2>\n\nusing(abc="xxxxxxxxxxxxxx") \nabc.b()\n</drac2>'
    resolver_cfg = AvraeLSConfig.default(tmp_path)
    resolver = GVarResolver(resolver_cfg)
    resolver.reset(
        {
            "xxxxxxxxxxxxxx": '\t\n\n\na = "hello"\n\ndef b():\n  return 1 / 0',
        }
    )
    result = await render_alias_command(source, MockExecutor(), ContextData(), resolver)

    assert result.error is not None
    diag = _runtime_diagnostic_with_source(result.error, "error", source)
    assert diag.range.start.line == 3
    assert diag.range.start.character == 0
    assert diag.range.end.line == 3
    assert diag.range.end.character == len("abc.b()")
    assert "division by zero" in diag.message
