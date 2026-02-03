from pathlib import Path

from lsprotocol import types
from pygls.workspace import Workspace

from avrae_ls.analysis.symbols import (
    build_symbol_table,
    document_symbols,
    find_definition_range,
    find_references,
    range_for_word,
)
from avrae_ls.lsp.server import AvraeLanguageServer, on_rename


def test_build_symbol_table_and_document_symbols():
    source = "x = 1\n\ndef foo():\n    return x\n"
    table = build_symbol_table(source)

    assert any(entry.name == "x" for entry in table.entries)
    assert any(entry.name == "foo" for entry in table.entries)

    doc_symbols = document_symbols(source)
    assert {s.name for s in doc_symbols} == {"x", "foo"}


def test_build_symbol_table_includes_annotated_assignment():
    source = 'result: "SimpleRollResult" = None\n'
    table = build_symbol_table(source)
    assert any(entry.name == "result" for entry in table.entries)


def test_find_definition_range():
    source = "x = 1\ny = x + 2\n"
    table = build_symbol_table(source)
    rng = find_definition_range(table, "x")
    assert rng is not None
    assert rng.start.line == 0
    assert rng.start.character == 0


def test_find_references_includes_definition_and_usages():
    source = "x = 1\ny = x + x\n"
    table = build_symbol_table(source)
    ranges = find_references(table, source, "x", include_declaration=True)
    starts = {(r.start.line, r.start.character) for r in ranges}
    assert (0, 0) in starts  # declaration
    assert (1, 4) in starts
    assert (1, 8) in starts
    assert len(ranges) == 3  # definition deduped


def test_find_references_respects_inline_draconic_offsets():
    source = "text <drac2>x = 1; y = x</drac2>\n"
    table = build_symbol_table(source)
    ranges = find_references(table, source, "x", include_declaration=True)
    starts = {(r.start.line, r.start.character) for r in ranges}
    assert (0, 12) in starts  # first x
    assert (0, 23) in starts  # second x
    assert len(ranges) == 2


def test_find_references_omits_declaration_when_requested():
    source = "x = 1\ny = x + x\n"
    table = build_symbol_table(source)
    ranges = find_references(table, source, "x", include_declaration=False)
    starts = {(r.start.line, r.start.character) for r in ranges}
    assert (0, 0) not in starts
    assert len(ranges) == 2


def test_range_for_word_returns_selection():
    source = "foo = 1\nbar = foo\n"
    pos = types.Position(line=1, character=6)
    rng = range_for_word(source, pos)
    assert rng is not None
    assert rng.start.line == 1
    assert rng.start.character == 6
    assert rng.end.character == 9


def test_on_rename_returns_workspace_edit_for_symbol():
    server = AvraeLanguageServer()
    server.load_workspace(Path("."))
    server.protocol._workspace = Workspace(None, sync_kind=types.TextDocumentSyncKind.Incremental)
    uri = "file:///test.alias"
    source = "x = 1\ny = x\n"
    server.protocol.workspace.put_text_document(
        types.TextDocumentItem(uri=uri, language_id="python", version=1, text=source)
    )

    params = types.RenameParams(
        text_document=types.TextDocumentIdentifier(uri=uri),
        position=types.Position(line=1, character=4),
        new_name="renamed",
    )
    edit = on_rename(server, params)
    assert edit is not None
    edits = edit.changes[uri]
    updated = _apply_edits(source, edits)
    assert "renamed = 1" in updated
    assert "y = renamed" in updated


def _apply_edits(source: str, edits: list[types.TextEdit]) -> str:
    lines = source.splitlines()
    # Apply edits in reverse document order to avoid shifting offsets
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
