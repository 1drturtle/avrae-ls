import pytest

from avrae_ls.__main__ import _run_alias_tests
from avrae_ls.config import AvraeLSConfig
from avrae_ls.runtime.context import ContextBuilder
from avrae_ls.runtime.runtime import MockExecutor
from avrae_ls.testing.gvar_tests import GVarTestError, parse_gvar_tests, run_gvar_tests


@pytest.mark.asyncio
async def test_runs_simple_gvar_test(tmp_path):
    gvar_path = tmp_path / "simple.gvar"
    gvar_path.write_text("constant = 'Constant Gvar value'\n")
    test_path = tmp_path / "simple.gvar-test"
    test_path.write_text("return simple.constant\n---\nConstant Gvar value\n")

    config = AvraeLSConfig.default(tmp_path)
    builder = ContextBuilder(config)
    executor = MockExecutor(config.service)

    case = parse_gvar_tests(test_path)[0]
    result = (await run_gvar_tests([case], builder, executor))[0]

    assert result.passed
    assert result.actual == "Constant Gvar value"


def test_parses_multiple_gvar_tests_in_one_file(tmp_path):
    gvar_path = tmp_path / "sample.gvar"
    gvar_path.write_text("answer = 42\n")
    test_path = tmp_path / "sample.gvar-test"
    test_path.write_text("return sample.answer\n---\n42\n---\n\nreturn sample.answer + 1\n---\n43\n")

    cases = parse_gvar_tests(test_path)

    assert len(cases) == 2
    assert cases[0].binding_name == "sample"


def test_parse_gvar_test_requires_sibling_gvar_file(tmp_path):
    test_path = tmp_path / "missing.gvar-test"
    test_path.write_text("return missing.value\n---\n1\n")

    with pytest.raises(GVarTestError):
        parse_gvar_tests(test_path)


@pytest.mark.asyncio
async def test_gvar_tests_normalize_hyphenated_binding_name(tmp_path):
    gvar_path = tmp_path / "foo-bar.gvar"
    gvar_path.write_text("answer = 42\n")
    test_path = tmp_path / "foo-bar.gvar-test"
    test_path.write_text("return foo_bar.answer\n---\n42\n")

    config = AvraeLSConfig.default(tmp_path)
    builder = ContextBuilder(config)
    executor = MockExecutor(config.service)

    case = parse_gvar_tests(test_path)[0]
    result = (await run_gvar_tests([case], builder, executor))[0]

    assert result.passed
    assert case.binding_name == "foo_bar"


@pytest.mark.asyncio
async def test_gvar_tests_normalize_digit_prefixed_binding_name(tmp_path):
    gvar_path = tmp_path / "123mod.gvar"
    gvar_path.write_text("answer = 7\n")
    test_path = tmp_path / "123mod.gvar-test"
    test_path.write_text("return gvar_123mod.answer\n---\n7\n")

    config = AvraeLSConfig.default(tmp_path)
    builder = ContextBuilder(config)
    executor = MockExecutor(config.service)

    case = parse_gvar_tests(test_path)[0]
    result = (await run_gvar_tests([case], builder, executor))[0]

    assert result.passed
    assert case.binding_name == "gvar_123mod"


@pytest.mark.asyncio
async def test_gvar_tests_allow_empty_expected(tmp_path):
    gvar_path = tmp_path / "noop.gvar"
    gvar_path.write_text("value = 'ok'\n")
    test_path = tmp_path / "noop.gvar-test"
    test_path.write_text("return noop.value\n---\n\n")

    config = AvraeLSConfig.default(tmp_path)
    builder = ContextBuilder(config)
    executor = MockExecutor(config.service)

    case = parse_gvar_tests(test_path)[0]
    result = (await run_gvar_tests([case], builder, executor))[0]

    assert result.passed


@pytest.mark.asyncio
async def test_gvar_tests_support_regex_and_partial_structures(tmp_path):
    gvar_path = tmp_path / "stats.gvar"
    gvar_path.write_text(
        "title = 'Report'\n"
        "fields = [\n"
        "  {'name': 'HP', 'value': '42'},\n"
        "  {'name': 'MP', 'value': '7'},\n"
        "]\n"
    )
    test_path = tmp_path / "stats.gvar-test"
    test_path.write_text(
        "return {'title': stats.title, 'fields': stats.fields}\n"
        "---\n"
        "title: /Rep.*/\n"
        "fields:\n"
        "  - name: HP\n"
        "    value: '/\\d+/'\n"
    )

    config = AvraeLSConfig.default(tmp_path)
    builder = ContextBuilder(config)
    executor = MockExecutor(config.service)

    case = parse_gvar_tests(test_path)[0]
    result = (await run_gvar_tests([case], builder, executor))[0]

    assert result.passed


@pytest.mark.asyncio
async def test_gvar_tests_allow_metadata_overrides(tmp_path):
    gvar_path = tmp_path / "ctxcheck.gvar"
    gvar_path.write_text("answer = 5\n")
    test_path = tmp_path / "ctxcheck.gvar-test"
    test_path.write_text(
        "return [ctxcheck.answer, get('hp'), character().name]\n"
        "---\n"
        "- 5\n"
        "- 99\n"
        "- Tester\n"
        "---\n"
        "vars:\n"
        "  cvars:\n"
        "    hp: 99\n"
        "character:\n"
        "  name: Tester\n"
    )

    config = AvraeLSConfig.default(tmp_path)
    builder = ContextBuilder(config)
    executor = MockExecutor(config.service)

    case = parse_gvar_tests(test_path)[0]
    result = (await run_gvar_tests([case], builder, executor))[0]

    assert result.passed


@pytest.mark.asyncio
async def test_gvar_tests_support_nested_using_dependencies(tmp_path):
    gvar_path = tmp_path / "main.gvar"
    gvar_path.write_text('using(inner="inner")\nvalue = inner.answer\n')
    test_path = tmp_path / "main.gvar-test"
    test_path.write_text(
        "return main.value\n"
        "---\n"
        "42\n"
        "---\n"
        "vars:\n"
        "  gvars:\n"
        "    inner: \"answer = 42\"\n"
    )

    config = AvraeLSConfig.default(tmp_path)
    builder = ContextBuilder(config)
    executor = MockExecutor(config.service)

    case = parse_gvar_tests(test_path)[0]
    result = (await run_gvar_tests([case], builder, executor))[0]

    assert result.passed


@pytest.mark.asyncio
async def test_gvar_tests_map_runtime_error_lines_to_test_body(tmp_path):
    gvar_path = tmp_path / "boom.gvar"
    gvar_path.write_text("answer = 42\n")
    test_path = tmp_path / "boom.gvar-test"
    test_path.write_text("a = 1\nload_json(\"\")\n---\n\n")

    config = AvraeLSConfig.default(tmp_path)
    builder = ContextBuilder(config)
    executor = MockExecutor(config.service)

    case = parse_gvar_tests(test_path)[0]
    result = (await run_gvar_tests([case], builder, executor))[0]

    assert not result.passed
    assert result.error is not None
    assert result.error_line == 2


@pytest.mark.asyncio
async def test_gvar_tests_preserve_module_execution_errors(tmp_path):
    gvar_path = tmp_path / "broken.gvar"
    gvar_path.write_text('load_json("")\n')
    test_path = tmp_path / "broken.gvar-test"
    test_path.write_text("return broken.answer\n---\n1\n")

    config = AvraeLSConfig.default(tmp_path)
    builder = ContextBuilder(config)
    executor = MockExecutor(config.service)

    case = parse_gvar_tests(test_path)[0]
    result = (await run_gvar_tests([case], builder, executor))[0]

    assert not result.passed
    assert result.error is not None
    assert "Error in module broken" in result.error
    assert result.error_line is None


def test_run_tests_discovers_alias_and_gvar_tests(tmp_path, capsys):
    (tmp_path / ".avraels.json").write_text('{"enableGvarFetch": false}')

    alias_path = tmp_path / "hello.alias"
    alias_path.write_text("!alias hello echo hi")
    alias_test_path = tmp_path / "hello.alias-test"
    alias_test_path.write_text("!hello\n---\nhi\n")

    gvar_path = tmp_path / "math.gvar"
    gvar_path.write_text("answer = 42\n")
    gvar_test_path = tmp_path / "math.gvar-test"
    gvar_test_path.write_text("return math.answer\n---\n42\n")

    exit_code = _run_alias_tests(tmp_path)
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "(alias: hello)" in captured.out
    assert "(gvar: math)" in captured.out
