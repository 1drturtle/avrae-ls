from pathlib import Path

import pytest

from avrae_ls.testing.alias_tests import AliasTestError, parse_alias_tests, run_alias_tests
from avrae_ls.__main__ import _run_alias_tests, main
from avrae_ls.config import AvraeLSConfig, ContextProfile, VarSources
from avrae_ls.runtime.context import ContextBuilder
from avrae_ls.runtime.runtime import MockExecutor


def _profile_like(config: AvraeLSConfig, name: str, *, character_name: str, hp: str | None = None) -> ContextProfile:
    vars = config.profiles["default"].vars
    if hp is not None:
        vars = vars.merge(VarSources(cvars={"hp": hp}))
    return ContextProfile(
        name=name,
        ctx=dict(config.profiles["default"].ctx),
        combat=dict(config.profiles["default"].combat),
        character={**config.profiles["default"].character, "name": character_name},
        vars=vars,
        description="",
    )


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
async def test_alias_tests_allow_empty_expected_for_none_result(tmp_path):
    alias_path = tmp_path / "noop.alias"
    alias_path.write_text("!alias noop")
    test_path = tmp_path / "test-noop.alias-test"
    test_path.write_text("!noop\n---\n\n")

    config = AvraeLSConfig.default(tmp_path)
    builder = ContextBuilder(config)
    executor = MockExecutor(config.service)

    case = parse_alias_tests(test_path)[0]
    result = (await run_alias_tests([case], builder, executor))[0]

    assert result.passed
    assert result.actual is None


@pytest.mark.asyncio
async def test_alias_tests_allow_empty_expected_for_embed(tmp_path):
    alias_path = tmp_path / "embed-empty.alias"
    alias_path.write_text('!alias embed-empty embed -title "Hello" -desc "World"')
    test_path = tmp_path / "test-embed-empty.alias-test"
    test_path.write_text("!embed-empty\n---\n\n")

    config = AvraeLSConfig.default(tmp_path)
    builder = ContextBuilder(config)
    executor = MockExecutor(config.service)

    case = parse_alias_tests(test_path)[0]
    result = (await run_alias_tests([case], builder, executor))[0]

    assert result.passed
    assert result.embed is not None


@pytest.mark.asyncio
async def test_alias_tests_include_error_line(tmp_path):
    alias_path = tmp_path / "boom.alias"
    alias_path.write_text('!alias boom echo\n<drac2>\nload_json("")\n</drac2>\n')
    test_path = tmp_path / "test-boom.alias-test"
    test_path.write_text("!boom\n---\n\n")

    config = AvraeLSConfig.default(tmp_path)
    builder = ContextBuilder(config)
    executor = MockExecutor(config.service)

    case = parse_alias_tests(test_path)[0]
    result = (await run_alias_tests([case], builder, executor))[0]

    assert not result.passed
    assert result.error is not None
    assert result.error_line == 3


@pytest.mark.asyncio
async def test_alias_tests_include_error_line_with_inline_drac2_tag(tmp_path):
    alias_path = tmp_path / "embed-error.alias"
    alias_path.write_text(
        '!alias embed-error embed <drac2>\n# testing 123\na = 3\n# testing 456\nload_json("")\n</drac2>\n'
    )
    test_path = tmp_path / "test-embed-error.alias-test"
    test_path.write_text("!embed-error\n---\n\n")

    config = AvraeLSConfig.default(tmp_path)
    builder = ContextBuilder(config)
    executor = MockExecutor(config.service)

    case = parse_alias_tests(test_path)[0]
    result = (await run_alias_tests([case], builder, executor))[0]

    assert not result.passed
    assert result.error is not None
    assert result.error_line == 5


def test_run_tests_output_uses_rendered_command(tmp_path, capsys):
    (tmp_path / ".avraels.json").write_text('{"enableGvarFetch": false}')
    alias_path = tmp_path / "speak.alias"
    alias_path.write_text("!alias speak say hello {{1 + 2}}")
    test_path = tmp_path / "test-speak.alias-test"
    test_path.write_text("!speak\n---\nsay hello 4\n")

    exit_code = _run_alias_tests([tmp_path])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "Expected: say hello 4" in captured.out
    assert "Actual: say hello 3" in captured.out


def test_run_tests_output_skips_diff_on_execution_error(tmp_path, capsys):
    (tmp_path / ".avraels.json").write_text('{"enableGvarFetch": false}')
    alias_path = tmp_path / "boom.alias"
    alias_path.write_text('!alias boom echo\n<drac2>\nload_json("")\n</drac2>\n')
    test_path = tmp_path / "test-boom.alias-test"
    test_path.write_text("!boom\n---\nshould not compare\n")

    exit_code = _run_alias_tests([tmp_path])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "Execution Error" in captured.out
    assert "- Execution Error" not in captured.out
    assert "Expected:" not in captured.out
    assert "Actual:" not in captured.out
    assert "Diff:" not in captured.out


def test_main_run_tests_defaults_to_current_directory(monkeypatch):
    recorded: list[list[Path]] = []

    def fake_run_tests(targets, **kwargs):
        recorded.append(list(targets))
        return 0

    monkeypatch.setattr("avrae_ls.__main__._run_alias_tests", fake_run_tests)

    with pytest.raises(SystemExit) as exc:
        main(["--run-tests"])

    assert exc.value.code == 0
    assert recorded == [[Path(".")]]


def test_main_run_tests_accepts_repeated_flags(monkeypatch):
    recorded: list[list[Path]] = []

    def fake_run_tests(targets, **kwargs):
        recorded.append(list(targets))
        return 0

    monkeypatch.setattr("avrae_ls.__main__._run_alias_tests", fake_run_tests)

    with pytest.raises(SystemExit) as exc:
        main(["--run-tests", "one", "--run-tests", "two"])

    assert exc.value.code == 0
    assert recorded == [[Path("one"), Path("two")]]


def test_main_run_tests_supports_mixed_default_and_explicit_flags(monkeypatch):
    recorded: list[list[Path]] = []

    def fake_run_tests(targets, **kwargs):
        recorded.append(list(targets))
        return 0

    monkeypatch.setattr("avrae_ls.__main__._run_alias_tests", fake_run_tests)

    with pytest.raises(SystemExit) as exc:
        main(["--run-tests", "--run-tests", "other_dir"])

    assert exc.value.code == 0
    assert recorded == [[Path("."), Path("other_dir")]]


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
    test_path.write_text("!multi\n---\nmulti\n\n!multi -b arg\n---\nmulti\n")

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


def test_parse_alias_tests_support_avrae_quote_pairs(tmp_path):
    alias_path = tmp_path / "say.alias"
    alias_path.write_text("!alias say echo %1%")
    test_path = tmp_path / "test-say.alias-test"
    test_path.write_text('!say \u201chello world\u201d\n---\n"hello world"\n')

    case = parse_alias_tests(test_path)[0]
    assert case.args == ["hello world"]


def test_parse_alias_tests_reads_profile_metadata(tmp_path):
    alias_path = tmp_path / "who.alias"
    alias_path.write_text("!alias who echo hi")
    test_path = tmp_path / "test-who.alias-test"
    test_path.write_text("!who\n---\nhi\n---\nname: who-test\nprofile: gm\n")

    case = parse_alias_tests(test_path)[0]

    assert case.name == "who-test"
    assert case.profile == "gm"


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
    test_path.write_text("!multi\n---\nhi 3\n!multi -b there\n---\n/hi \\d/\n")

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
    test_path.write_text("!fields\n---\ntitle: Report\nfields:\n  - name: A\n    value: One\n")

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
    test_path.write_text("!fields2\n---\nfields:\n  - name: A\n    value: ''\n")

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
    test_path.write_text('!mixed\n---\n"**test** /.*/"\n')

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
    test_path.write_text("!who\n---\nTester\n---\nname: who-test\ncharacter:\n  name: Tester\n")

    config = AvraeLSConfig.default(tmp_path)
    builder = ContextBuilder(config)
    executor = MockExecutor(config.service)

    case = parse_alias_tests(test_path)[0]
    assert case.name == "who-test"
    result = (await run_alias_tests([case], builder, executor))[0]
    assert result.passed
    assert result.actual == "Tester"


@pytest.mark.asyncio
async def test_alias_tests_character_override_makes_character_truthy(tmp_path):
    alias_path = tmp_path / "haschar.alias"
    alias_path.write_text(
        "!alias haschar echo <drac2>\n"
        "ch = character()\n"
        "if not ch:\n"
        '  return "bad output"\n'
        'return "good output"\n'
        "</drac2>"
    )
    test_path = tmp_path / "test-haschar.alias-test"
    test_path.write_text("!haschar\n---\ngood output\n---\ncharacter:\n  name: Tester\n")

    config = AvraeLSConfig.default(tmp_path)
    builder = ContextBuilder(config)
    executor = MockExecutor(config.service)

    case = parse_alias_tests(test_path)[0]
    result = (await run_alias_tests([case], builder, executor))[0]

    assert result.passed
    assert result.actual == "good output"


@pytest.mark.asyncio
async def test_alias_tests_allow_metadata_vars_override(tmp_path):
    alias_path = tmp_path / "hp.alias"
    alias_path.write_text("!alias hp echo <drac2>return get('hp')</drac2>")
    test_path = tmp_path / "test-hp.alias-test"
    test_path.write_text('!hp\n---\n"99"\n---\nvars:\n  cvars:\n    hp: 99\n')

    config = AvraeLSConfig.default(tmp_path)
    builder = ContextBuilder(config)
    executor = MockExecutor(config.service)

    case = parse_alias_tests(test_path)[0]
    result = (await run_alias_tests([case], builder, executor))[0]
    assert result.passed


@pytest.mark.asyncio
async def test_alias_tests_support_per_case_profiles(tmp_path):
    alias_path = tmp_path / "who.alias"
    alias_path.write_text("!alias who echo <drac2>return character().name</drac2>")
    test_path = tmp_path / "test-who.alias-test"
    test_path.write_text("!who\n---\nDefault Hero\n---\nprofile: default\n\n!who\n---\nGM Hero\n---\nprofile: gm\n")

    config = AvraeLSConfig.default(tmp_path)
    config.profiles["default"].character["name"] = "Default Hero"
    config.profiles["gm"] = _profile_like(config, "gm", character_name="GM Hero")
    builder = ContextBuilder(config)
    executor = MockExecutor(config.service)

    cases = parse_alias_tests(test_path)
    results = await run_alias_tests(cases, builder, executor)

    assert len(results) == 2
    assert all(result.passed for result in results)
    assert [result.actual for result in results] == ["Default Hero", "GM Hero"]


@pytest.mark.asyncio
async def test_alias_tests_profile_overrides_still_apply_on_selected_profile(tmp_path):
    alias_path = tmp_path / "who.alias"
    alias_path.write_text("!alias who echo <drac2>return f\"{character().name}|{get('hp')}\"</drac2>")
    test_path = tmp_path / "test-who.alias-test"
    test_path.write_text(
        "!who\n---\nTester|99\n---\nprofile: gm\nvars:\n  cvars:\n    hp: 99\ncharacter:\n  name: Tester\n"
    )

    config = AvraeLSConfig.default(tmp_path)
    config.profiles["gm"] = _profile_like(config, "gm", character_name="GM Hero", hp="7")
    builder = ContextBuilder(config)
    executor = MockExecutor(config.service)

    case = parse_alias_tests(test_path)[0]
    result = (await run_alias_tests([case], builder, executor))[0]

    assert case.profile == "gm"
    assert result.passed


@pytest.mark.asyncio
async def test_alias_tests_reuse_nested_gvar_fetches_across_cases(monkeypatch, tmp_path):
    alias_path = tmp_path / "outer.alias"
    alias_path.write_text('!alias outer echo <drac2>using(mod="remote")\nreturn mod.answer</drac2>')
    test_path = tmp_path / "outer.alias-test"
    test_path.write_text('!outer\n---\n"42"\n\n!outer\n---\n"42"\n')

    config = AvraeLSConfig.default(tmp_path)
    config.enable_gvar_fetch = True
    config.service.token = "token"
    builder = ContextBuilder(config)
    executor = MockExecutor(config.service)

    calls: list[str] = []

    class DummyResponse:
        status_code = 200

        def __init__(self, key: str):
            self.key = key

        def json(self):
            return {"value": "answer = 42\n"}

    class DummyClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            calls.append(url.rsplit("/", 1)[-1])
            return DummyResponse(calls[-1])

    monkeypatch.setattr("avrae_ls.runtime.context.httpx.AsyncClient", DummyClient)

    cases = parse_alias_tests(test_path)
    results = await run_alias_tests(cases, builder, executor)

    assert all(result.passed for result in results)
    assert calls == ["remote"]


@pytest.mark.asyncio
async def test_alias_tests_reuse_nested_gvar_fetches_across_profiles(monkeypatch, tmp_path):
    alias_path = tmp_path / "outer.alias"
    alias_path.write_text('!alias outer echo <drac2>using(mod="remote")\nreturn mod.answer</drac2>')
    test_path = tmp_path / "outer.alias-test"
    test_path.write_text('!outer\n---\n"42"\n---\nprofile: default\n\n!outer\n---\n"42"\n---\nprofile: gm\n')

    config = AvraeLSConfig.default(tmp_path)
    config.enable_gvar_fetch = True
    config.service.token = "token"
    config.profiles["gm"] = _profile_like(
        config, "gm", character_name=str(config.profiles["default"].character["name"])
    )
    builder = ContextBuilder(config)
    executor = MockExecutor(config.service)

    calls: list[str] = []

    class DummyResponse:
        status_code = 200

        def __init__(self, key: str):
            self.key = key

        def json(self):
            return {"value": "answer = 42\n"}

    class DummyClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            calls.append(url.rsplit("/", 1)[-1])
            return DummyResponse(calls[-1])

    monkeypatch.setattr("avrae_ls.runtime.context.httpx.AsyncClient", DummyClient)

    cases = parse_alias_tests(test_path)
    results = await run_alias_tests(cases, builder, executor)

    assert all(result.passed for result in results)
    assert calls == ["remote"]
