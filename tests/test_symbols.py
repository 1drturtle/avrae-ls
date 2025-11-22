from avrae_ls.symbols import build_symbol_table, document_symbols, find_definition_range


def test_build_symbol_table_and_document_symbols():
    source = "x = 1\n\ndef foo():\n    return x\n"
    table = build_symbol_table(source)

    assert any(entry.name == "x" for entry in table.entries)
    assert any(entry.name == "foo" for entry in table.entries)

    doc_symbols = document_symbols(source)
    assert {s.name for s in doc_symbols} == {"x", "foo"}


def test_find_definition_range():
    source = "x = 1\ny = x + 2\n"
    table = build_symbol_table(source)
    rng = find_definition_range(table, "x")
    assert rng is not None
    assert rng.start.line == 0
    assert rng.start.character == 0
