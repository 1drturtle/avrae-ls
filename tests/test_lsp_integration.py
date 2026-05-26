from pathlib import Path

import pytest
from lsprotocol import types
from pygls.workspace import Workspace

from avrae_ls.config import AvraeLSConfig
from avrae_ls.lsp.server import AvraeLanguageServer, _runtime_diagnostic_with_source, on_completion, on_hover, run_alias
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
