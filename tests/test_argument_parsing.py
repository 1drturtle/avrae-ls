from avrae_ls.runtime.argument_parsing import apply_argument_parsing


RAW_ARGS = 'test "test arg" test'
PARSED_ARGS = ["test", "test arg", "test"]


def test_argument_parsing_percent_index():
    rendered = apply_argument_parsing("echo %1%", PARSED_ARGS, raw_args=RAW_ARGS)
    assert rendered == "echo test"


def test_argument_parsing_percent_all_uses_raw_args():
    rendered = apply_argument_parsing("echo %*%", PARSED_ARGS, raw_args=RAW_ARGS)
    assert rendered == 'echo "test \\"test arg\\" test"'


def test_argument_parsing_amp_index_escapes_quotes():
    rendered = apply_argument_parsing("echo &1&", ['say "hi"'], raw_args='say "hi"')
    assert rendered == 'echo say \\"hi\\"'


def test_argument_parsing_amp_all_uses_raw_args():
    rendered = apply_argument_parsing("echo &*&", PARSED_ARGS, raw_args=RAW_ARGS)
    assert rendered == 'echo test \\"test arg\\" test'


def test_argument_parsing_args_list_literal():
    rendered = apply_argument_parsing("echo &ARGS&", PARSED_ARGS, raw_args=RAW_ARGS)
    assert rendered == "echo ['test', 'test arg', 'test']"


def test_argument_parsing_missing_placeholder_is_preserved_at_runtime():
    rendered = apply_argument_parsing("echo %2% &2%", ["one"], raw_args="one")
    assert rendered == "echo %2% &2%"


def test_argument_parsing_analysis_mode_keeps_code_parseable():
    rendered = apply_argument_parsing("echo %2% <drac2>return int(&2&)</drac2>", runtime=False)
    assert "arg2" in rendered
