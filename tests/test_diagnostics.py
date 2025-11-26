import pytest

from avrae_ls.config import AvraeLSConfig, DiagnosticSettings, VarSources
from avrae_ls.context import ContextData, GVarResolver
from avrae_ls.diagnostics import DiagnosticProvider
from avrae_ls.alias_preview import render_alias_command
from avrae_ls.runtime import MockExecutor


def _provider():
    executor = MockExecutor()
    settings = DiagnosticSettings()
    return DiagnosticProvider(executor, settings)


def _resolver(tmp_path):
    cfg = AvraeLSConfig.default(tmp_path)
    return GVarResolver(cfg)


@pytest.mark.asyncio
async def test_reports_syntax_error(tmp_path):
    provider = _provider()
    resolver = _resolver(tmp_path)
    ctx_data = ContextData(vars=VarSources())

    diags = await provider.analyze("if True print('hi')", ctx_data, resolver)
    assert diags
    assert any(d.severity.value == 1 for d in diags)  # DiagnosticSeverity.Error


@pytest.mark.asyncio
async def test_fetches_gvar_when_enabled(tmp_path):
    cfg = AvraeLSConfig.default(tmp_path)
    cfg.enable_gvar_fetch = True

    class _RecordingResolver(GVarResolver):
        def __init__(self, cfg):
            super().__init__(cfg)
            self.calls: list[str] = []

        async def ensure(self, key: str) -> bool:
            self.calls.append(str(key))
            self._cache[str(key)] = f"fetched-{key}"
            return True

    provider = _provider()
    resolver = _RecordingResolver(cfg)
    ctx_data = ContextData(vars=VarSources())

    diags = await provider.analyze("get_gvar('abc123')", ctx_data, resolver)
    assert not any("gvar" in d.message for d in diags)
    assert resolver.calls == ["abc123"]
    assert resolver.get_local("abc123") == "fetched-abc123"


@pytest.mark.parametrize(
    "alias_text, expected_command, expected_messages, resolver_seed, ctx_kwargs",
    [
        pytest.param("echo <drac2></drac2>", "echo ", [], None, None, id="empty_inline_block"),
        pytest.param('!alias echo <drac2> return "s"\n</drac2>', "s", [], None, None, id="inline_return_alias"),
        pytest.param("!alias inline echo prefix <drac2>bad_var</drac2> suffix", None, ["undefined"], None, None, id="inline_bad_var"),
        pytest.param("using(mod='mod')\nmod.answer", None, [], {"mod": "answer = 'ok'"}, None, id="using_imported_name"),
        pytest.param('!alias next embed \n-title "Are you done?"', "embed \n-title \"Are you done?\"", [], None, None, id="plain_embed_flags"),
        pytest.param("for x in range(3):\n    y = x\nprint(x)", None, [], None, None, id="for_loop_binds_target"),
        pytest.param("!alias aaa echo \n<drac2>\nfor i in range(3):\n  return i\n\n</drac2>", "echo \n0", [], None, None, id="drac_loop_return"),
        pytest.param("!alias hello echo\n<drac2>\nx = 3\nreturn x\n</drac2>", "echo\n3", [], None, None, id="drac_simple_return"),
        pytest.param("len(1, 2)", None, ["invalid arguments"], None, None, id="bad_args"),
        pytest.param("import os\nx=1", None, ["Imports are not supported"], None, None, id="imports_not_supported"),
        pytest.param("x + 1", None, ["undefined"], None, None, id="unknown_name"),
        pytest.param("get_gvar('abc')", None, ["gvar"], None, None, id="unknown_gvar"),
        pytest.param("class X:\n    def _hidden(self):\n        return 1\n\nX()._hidden()", None, ["private methods"], None, None, id="private_method_call"),
        pytest.param("x = roll('1d1')\ny = vroll('1d1')\nx + y.total", None, [], None, None, id="roll_and_vroll_available"),
        pytest.param("character().hp", None, ["character context"], None, None, id="character_context_missing"),
        pytest.param("combat().round_num", None, ["combat context"], None, None, id="combat_context_missing"),
        pytest.param("character.hp", None, ["Call character()"], None, None, id="character_not_called"),
        pytest.param("combat().combatants.hp", None, ["index or iterate"], None, {"combat": {"combatants": []}}, id="iterable_attribute_chain"),
        pytest.param("ctx.author()", None, ["property"], None, None, id="calling_property"),
        pytest.param("!alias inline echo prefix {{bad_var}} suffix", None, ["undefined"], None, None, id="inline_expression_bad_var"),
        pytest.param("!alias roll echo rolled {1}", "echo rolled 1", [], None, None, id="inline_roll_replaced"),
        pytest.param("[x for x in range(3)]", None, [], None, None, id="list_comprehension_scopes_target"),
        pytest.param("get_cvar", None, ["undefined"], None, None, id="bare_get_cvar_not_allowed"),
    ],
)
@pytest.mark.asyncio
async def test_diagnostic_matrix(tmp_path, alias_text: str, expected_command, expected_messages, resolver_seed, ctx_kwargs):
    provider = _provider()
    resolver = _resolver(tmp_path)
    if resolver_seed:
        resolver.reset(resolver_seed)
    ctx_kwargs = ctx_kwargs or {}
    ctx_data = ContextData(vars=VarSources(), **ctx_kwargs)

    diags = await provider.analyze(alias_text, ctx_data, resolver)
    if not expected_messages:
        assert diags == []
    else:
        messages = " ".join(d.message for d in diags)
        for expected in expected_messages:
            assert expected in messages
    if expected_command is not None:
        rendered = await render_alias_command(alias_text, provider._executor, ctx_data, resolver)
        assert rendered.command == expected_command


@pytest.mark.asyncio
async def test_drac_block_diagnostics_offset_respects_prefix_lines(tmp_path):
    provider = _provider()
    resolver = _resolver(tmp_path)
    ctx_data = ContextData(vars=VarSources())

    alias_text = "# comment before\n!alias oops echo\n<drac2>\nfoo = 1\nbar\n</drac2>\n# after"
    diags = await provider.analyze(alias_text, ctx_data, resolver)
    assert diags
    diag = diags[0]
    # 'bar' is on line 4 (0-based index)
    assert diag.range.start.line == 4


@pytest.mark.asyncio
async def test_drac_block_diagnostics_offset_respects_inline_char(tmp_path):
    provider = _provider()
    resolver = _resolver(tmp_path)
    ctx_data = ContextData(vars=VarSources())

    alias_text = "!alias inline echo prefix <drac2>bad_var</drac2> suffix"
    diags = await provider.analyze(alias_text, ctx_data, resolver)
    assert diags
    diag = diags[0]
    # bad_var starts after the prefix and opening tag
    assert diag.range.start.line == 0
    assert diag.range.start.character > alias_text.index("<drac2>")


@pytest.mark.asyncio
async def test_inline_expression_diagnostics_offset_respects_inline_char(tmp_path):
    provider = _provider()
    resolver = _resolver(tmp_path)
    ctx_data = ContextData(vars=VarSources())

    alias_text = "!alias inline echo prefix {{bad_var}} suffix"
    diags = await provider.analyze(alias_text, ctx_data, resolver)
    assert diags
    diag = diags[0]
    assert diag.range.start.line == 0
    assert diag.range.start.character > alias_text.index("{{")
