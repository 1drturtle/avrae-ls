from avrae_ls.parser import find_draconic_blocks


def test_alias_module_treats_entire_file_as_draconic_block():
    source = "x = 1\n<drac2>y</drac2>"
    blocks = find_draconic_blocks(source, treat_as_module=True)

    assert len(blocks) == 1
    block = blocks[0]
    assert block.code == source
    assert block.line_offset == 0
    assert block.char_offset == 0
    assert block.line_count == 2
    assert not block.inline
