from avrae_ls.runtime.argument_parsing import apply_argument_parsing


def test_argument_parsing_replacements():
    text = "echo %1% %*% <drac2>return &1& + len(&ARGS&)</drac2>"
    rendered = apply_argument_parsing(text, ["first arg", "second"])
    assert '"first arg"' in rendered  # %1% gets quoted for spaces
    assert "first arg second" in rendered  # %*% preserved order
    assert "[\'first arg\', \'second\']" in rendered or "[\"first arg\", \"second\"]" in rendered
    assert "&1&" not in rendered
    assert "&ARGS&" not in rendered
