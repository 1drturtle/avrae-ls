import pytest
from pathlib import Path

from lsprotocol import types
from pygls.workspace import Workspace

from avrae_ls.code_actions import code_actions_for_document
from avrae_ls.codes import MISSING_GVAR_CODE, UNDEFINED_NAME_CODE, UNSUPPORTED_IMPORT_CODE
from avrae_ls.server import AvraeLanguageServer, on_code_action


def _params(rng: types.Range, diagnostics: list[types.Diagnostic]) -> types.CodeActionParams:
    return types.CodeActionParams(
        text_document=types.TextDocumentIdentifier(uri="file://test"),
        range=rng,
        context=types.CodeActionContext(diagnostics=diagnostics),
    )


def test_wraps_document_in_drac2_block_when_missing():
    params = _params(
        types.Range(start=types.Position(line=0, character=0), end=types.Position(line=0, character=0)),
        diagnostics=[],
    )
    actions = code_actions_for_document("x = 1", params, Path("."))
    edits = [edit for action in actions for edit in (action.edit.changes or {}).get("file://test", [])]
    assert any("<drac2>" in edit.new_text and "x = 1" in edit.new_text for edit in edits)


def test_stub_variable_quick_fix():
    diag = types.Diagnostic(
        message="'foo' may be undefined in this scope",
        range=types.Range(start=types.Position(line=1, character=0), end=types.Position(line=1, character=3)),
        code=UNDEFINED_NAME_CODE,
        data={"name": "foo"},
    )
    params = _params(diag.range, [diag])
    actions = code_actions_for_document("x = 1\nfoo\n", params, Path("."))
    edits = [edit for action in actions for edit in (action.edit.changes or {}).get("file://test", [])]
    assert any("foo = None" in edit.new_text for edit in edits)


def test_gvar_using_stub_quick_fix():
    diag = types.Diagnostic(
        message="Unknown gvar 'abc-123'",
        range=types.Range(start=types.Position(line=0, character=0), end=types.Position(line=0, character=1)),
        code=MISSING_GVAR_CODE,
        data={"gvar": "abc-123"},
    )
    params = _params(diag.range, [diag])
    actions = code_actions_for_document("get_gvar('abc-123')\n", params, Path("."))
    edits = [edit for action in actions for edit in (action.edit.changes or {}).get("file://test", [])]
    assert any("using(abc_123=\"abc-123\")" in edit.new_text for edit in edits)


def test_import_rewrite_quick_fix():
    diag = types.Diagnostic(
        message="Imports are not supported in draconic aliases",
        range=types.Range(start=types.Position(line=0, character=0), end=types.Position(line=0, character=6)),
        code=UNSUPPORTED_IMPORT_CODE,
        data={"module": "foo"},
    )
    params = _params(diag.range, [diag])
    actions = code_actions_for_document("import foo\n", params, Path("."))
    edits = [edit for action in actions for edit in (action.edit.changes or {}).get("file://test", [])]
    assert any("using(foo" in edit.new_text for edit in edits)


def test_workspace_snippet_extension(tmp_path: Path):
    snippet_file = tmp_path / ".avraels.snippets.json"
    snippet_file.write_text('[{"key": "custom", "title": "Custom Snippet", "body": "hello"}]')
    params = _params(
        types.Range(start=types.Position(line=0, character=0), end=types.Position(line=0, character=0)),
        diagnostics=[],
    )
    actions = code_actions_for_document("", params, tmp_path)
    assert any(action.title == "Custom Snippet" for action in actions)


def _apply_edits(source: str, edits: list[types.TextEdit]) -> str:
    lines = source.splitlines()
    for edit in sorted(edits, key=lambda e: (e.range.start.line, e.range.start.character), reverse=True):
        start = edit.range.start
        end = edit.range.end
        if start.line == end.line:
            line = lines[start.line]
            lines[start.line] = line[: start.character] + edit.new_text + line[end.character :]
        else:
            before = lines[start.line][: start.character]
            after = lines[end.line][end.character :]
            lines[start.line : end.line + 1] = [before + edit.new_text + after]
    return "\n".join(lines) + ("\n" if source.endswith("\n") and not source.endswith("\n\n") else "")


def _setup_server_with_doc(source: str, uri: str) -> AvraeLanguageServer:
    server = AvraeLanguageServer()
    server.load_workspace(Path("."))
    server.protocol._workspace = Workspace(None, sync_kind=types.TextDocumentSyncKind.Incremental)
    server.protocol.workspace.put_text_document(
        types.TextDocumentItem(uri=uri, language_id="avrae", version=1, text=source)
    )
    return server


@pytest.mark.asyncio
async def test_code_action_round_trip_plain_text_stub_inserted_at_top():
    source = "x = 1\nfoo\n"
    uri = "file:///plain.alias"
    server = _setup_server_with_doc(source, uri)
    ctx_data = server.state.context_builder.build()
    diags = await server.state.diagnostics.analyze(
        source, ctx_data, server.state.context_builder.gvar_resolver
    )
    diag = next(d for d in diags if d.code == UNDEFINED_NAME_CODE)
    params = types.CodeActionParams(
        text_document=types.TextDocumentIdentifier(uri=uri),
        range=diag.range,
        context=types.CodeActionContext(diagnostics=diags),
    )
    actions = on_code_action(server, params)
    edits = [edit for action in actions for edit in (action.edit.changes or {}).get(uri, [])]
    updated = _apply_edits(source, edits)
    assert updated.splitlines()[0].startswith("foo = None")


@pytest.mark.asyncio
async def test_code_action_round_trip_draconic_block_respects_offsets():
    source = "\n".join(
        [
            "prefix text",
            "<drac2>",
            "foo",
            "</drac2>",
            "",
        ]
    )
    uri = "file:///drac.alias"
    server = _setup_server_with_doc(source, uri)
    ctx_data = server.state.context_builder.build()
    diags = await server.state.diagnostics.analyze(
        source, ctx_data, server.state.context_builder.gvar_resolver
    )
    diag = next(d for d in diags if d.code == UNDEFINED_NAME_CODE)
    params = types.CodeActionParams(
        text_document=types.TextDocumentIdentifier(uri=uri),
        range=diag.range,
        context=types.CodeActionContext(diagnostics=diags),
    )
    actions = on_code_action(server, params)
    edits = [edit for action in actions for edit in (action.edit.changes or {}).get(uri, [])]
    updated = _apply_edits(source, edits)
    lines = updated.splitlines()
    assert lines[2].startswith("foo = None")
