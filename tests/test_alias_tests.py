import pytest

from avrae_ls.alias_tests import AliasTestError, parse_alias_tests, run_alias_tests
from avrae_ls.config import AvraeLSConfig
from avrae_ls.context import ContextBuilder
from avrae_ls.runtime import MockExecutor


@pytest.mark.asyncio
async def test_runs_simple_alias_test(tmp_path):
    alias_path = tmp_path / "hello.alias"
    alias_path.write_text("!alias hello echo hi {{1 + 2}}")
    test_path = tmp_path / "test-hello.alias-test"
    test_path.write_text("!hello\n---\nhi 3\n")

    config = AvraeLSConfig.default(tmp_path)
    builder = ContextBuilder(config)
    executor = MockExecutor(config.service)

    case = parse_alias_tests(test_path)[0]
    results = await run_alias_tests([case], builder, executor)
    result = results[0]

    assert result.passed
    assert result.actual == "hi 3"


@pytest.mark.asyncio
async def test_runs_embed_alias_test(tmp_path):
    alias_path = tmp_path / "embedtest.alias"
    alias_path.write_text('!alias embedtest embed -title "Hello" -desc "World"')
    test_path = tmp_path / "test-embedtest.alias-test"
    test_path.write_text("!embedtest\n---\ntitle: Hello\ndescription: World\n")

    config = AvraeLSConfig.default(tmp_path)
    builder = ContextBuilder(config)
    executor = MockExecutor(config.service)

    case = parse_alias_tests(test_path)[0]
    results = await run_alias_tests([case], builder, executor)
    result = results[0]

    assert result.passed
    assert result.embed is not None
    assert result.actual.get("title") == "Hello"
    assert result.actual.get("description") == "World"


@pytest.mark.asyncio
async def test_parses_multiple_tests_in_one_file(tmp_path):
    alias_path = tmp_path / "multi.alias"
    alias_path.write_text("!alias multi echo multi")
    test_path = tmp_path / "test-multi.alias-test"
    test_path.write_text(
        "!multi\n"
        "---\n"
        "multi\n"
        "\n"
        "!multi -b arg\n"
        "---\n"
        "multi\n"
    )

    config = AvraeLSConfig.default(tmp_path)
    builder = ContextBuilder(config)
    executor = MockExecutor(config.service)

    cases = parse_alias_tests(test_path)
    assert len(cases) == 2
    results = await run_alias_tests(cases, builder, executor)
    assert all(res.passed for res in results)


def test_parse_alias_test_requires_alias_file(tmp_path):
    test_path = tmp_path / "test-missing.alias-test"
    test_path.write_text("!missing\n---\nexpected\n")

    with pytest.raises(AliasTestError):
        parse_alias_tests(test_path)


@pytest.mark.asyncio
async def test_alias_tests_support_regex_expected(tmp_path):
    alias_path = tmp_path / "greet.alias"
    alias_path.write_text("!alias greet echo hello {{1 + 2}}")
    test_path = tmp_path / "test-greet.alias-test"
    test_path.write_text("!greet\n---\n/hello \\d/\n")

    config = AvraeLSConfig.default(tmp_path)
    builder = ContextBuilder(config)
    executor = MockExecutor(config.service)

    case = parse_alias_tests(test_path)[0]
    result = (await run_alias_tests([case], builder, executor))[0]
    assert result.passed


@pytest.mark.asyncio
async def test_alias_tests_multiple_cases_per_file(tmp_path):
    alias_path = tmp_path / "multi.alias"
    alias_path.write_text("!alias multi echo hi {{1 + 2}}")
    test_path = tmp_path / "test-multi.alias-test"
    test_path.write_text(
        "!multi\n"
        "---\n"
        "hi 3\n"
        "!multi -b there\n"
        "---\n"
        "/hi \\d/\n"
    )

    config = AvraeLSConfig.default(tmp_path)
    builder = ContextBuilder(config)
    executor = MockExecutor(config.service)

    cases = parse_alias_tests(test_path)
    assert len(cases) == 2
    results = await run_alias_tests(cases, builder, executor)
    assert len(results) == 2
    assert all(res.passed for res in results)


@pytest.mark.asyncio
async def test_alias_tests_support_regex_in_embed(tmp_path):
    alias_path = tmp_path / "embed.alias"
    alias_path.write_text('!alias embed embed -title "Report" -desc "Score 42" -f "A|Value: 42|inline"')
    test_path = tmp_path / "test-embed.alias-test"
    test_path.write_text(
        "!embed\n"
        "---\n"
        "title: /Report/\n"
        "description: /Score \\d+/\n"
        "fields:\n"
        "  - name: A\n"
        "    value: '/Value: \\d+/'\n"
        "    inline: true\n"
    )

    config = AvraeLSConfig.default(tmp_path)
    builder = ContextBuilder(config)
    executor = MockExecutor(config.service)

    case = parse_alias_tests(test_path)[0]
    result = (await run_alias_tests([case], builder, executor))[0]
    assert result.passed


@pytest.mark.asyncio
async def test_alias_tests_allow_partial_fields(tmp_path):
    alias_path = tmp_path / "fields.alias"
    alias_path.write_text('!alias fields embed -title "Report" -f "A|One" -f "B|Two|inline"')
    test_path = tmp_path / "test-fields.alias-test"
    test_path.write_text(
        "!fields\n"
        "---\n"
        "title: Report\n"
        "fields:\n"
        "  - name: A\n"
        "    value: One\n"
    )

    config = AvraeLSConfig.default(tmp_path)
    builder = ContextBuilder(config)
    executor = MockExecutor(config.service)

    case = parse_alias_tests(test_path)[0]
    result = (await run_alias_tests([case], builder, executor))[0]
    assert result.passed


@pytest.mark.asyncio
async def test_alias_tests_allow_empty_field_value(tmp_path):
    alias_path = tmp_path / "fields2.alias"
    alias_path.write_text('!alias fields2 embed -title "Report" -f "A|One"')
    test_path = tmp_path / "test-fields2.alias-test"
    test_path.write_text(
        "!fields2\n"
        "---\n"
        "fields:\n"
        "  - name: A\n"
        "    value: ''\n"
    )

    config = AvraeLSConfig.default(tmp_path)
    builder = ContextBuilder(config)
    executor = MockExecutor(config.service)

    case = parse_alias_tests(test_path)[0]
    result = (await run_alias_tests([case], builder, executor))[0]
    assert result.passed


@pytest.mark.asyncio
async def test_alias_tests_mixed_literal_and_regex(tmp_path):
    alias_path = tmp_path / "mixed.alias"
    alias_path.write_text("!alias mixed echo **test** hello")
    test_path = tmp_path / "test-mixed.alias-test"
    test_path.write_text("!mixed\n---\n\"**test** /.*/\"\n")

    config = AvraeLSConfig.default(tmp_path)
    builder = ContextBuilder(config)
    executor = MockExecutor(config.service)

    case = parse_alias_tests(test_path)[0]
    result = (await run_alias_tests([case], builder, executor))[0]
    assert result.passed


@pytest.mark.asyncio
async def test_alias_tests_allow_metadata_character_override(tmp_path):
    alias_path = tmp_path / "who.alias"
    alias_path.write_text("!alias who echo <drac2>return character().name</drac2>")
    test_path = tmp_path / "test-who.alias-test"
    test_path.write_text(
        "!who\n"
        "---\n"
        "Tester\n"
        "---\n"
        "name: who-test\n"
        "character:\n"
        "  name: Tester\n"
    )

    config = AvraeLSConfig.default(tmp_path)
    builder = ContextBuilder(config)
    executor = MockExecutor(config.service)

    case = parse_alias_tests(test_path)[0]
    assert case.name == "who-test"
    result = (await run_alias_tests([case], builder, executor))[0]
    assert result.passed
    assert result.actual == "Tester"


@pytest.mark.asyncio
async def test_alias_tests_allow_metadata_vars_override(tmp_path):
    alias_path = tmp_path / "hp.alias"
    alias_path.write_text("!alias hp echo <drac2>return get('hp')</drac2>")
    test_path = tmp_path / "test-hp.alias-test"
    test_path.write_text(
        "!hp\n"
        "---\n"
        "\"99\"\n"
        "---\n"
        "vars:\n"
        "  cvars:\n"
        "    hp: 99\n"
    )

    config = AvraeLSConfig.default(tmp_path)
    builder = ContextBuilder(config)
    executor = MockExecutor(config.service)

    case = parse_alias_tests(test_path)[0]
    result = (await run_alias_tests([case], builder, executor))[0]
    assert result.passed
