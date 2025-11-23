import pytest

from avrae_ls.config import AvraeLSConfig, DiagnosticSettings, VarSources
from avrae_ls.context import ContextData, GVarResolver
from avrae_ls.diagnostics import DiagnosticProvider
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
async def test_reports_unknown_name(tmp_path):
    provider = _provider()
    resolver = _resolver(tmp_path)
    ctx_data = ContextData(vars=VarSources())

    diags = await provider.analyze("x + 1", ctx_data, resolver)
    assert any("undefined" in d.message for d in diags)


@pytest.mark.asyncio
async def test_reports_unknown_gvar(tmp_path):
    provider = _provider()
    resolver = _resolver(tmp_path)
    ctx_data = ContextData(vars=VarSources())

    diags = await provider.analyze("get_gvar('abc')", ctx_data, resolver)
    assert any("gvar" in d.message for d in diags)


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


@pytest.mark.asyncio
async def test_for_loop_binds_target(tmp_path):
    provider = _provider()
    resolver = _resolver(tmp_path)
    ctx_data = ContextData(vars=VarSources())

    code = "for x in range(3):\n    y = x\nprint(x)"
    diags = await provider.analyze(code, ctx_data, resolver)
    assert not any("undefined" in d.message for d in diags)


@pytest.mark.asyncio
async def test_drac_block_for_loop_return(tmp_path):
    provider = _provider()
    resolver = _resolver(tmp_path)
    ctx_data = ContextData(vars=VarSources())

    alias_text = "!alias aaa echo \n<drac2>\nfor i in range(3):\n  return i\n\n</drac2>"
    diags = await provider.analyze(alias_text, ctx_data, resolver)
    assert not any("undefined" in d.message for d in diags)


@pytest.mark.asyncio
async def test_handles_alias_drac_block(tmp_path):
    provider = _provider()
    resolver = _resolver(tmp_path)
    ctx_data = ContextData(vars=VarSources())

    alias_text = "!alias hello echo\n<drac2>\nx = 3\nreturn x\n</drac2>"
    diags = await provider.analyze(alias_text, ctx_data, resolver)
    assert diags == []


@pytest.mark.asyncio
async def test_reports_bad_args(tmp_path):
    provider = _provider()
    resolver = _resolver(tmp_path)
    ctx_data = ContextData(vars=VarSources())

    diags = await provider.analyze("len(1, 2)", ctx_data, resolver)
    assert any("invalid arguments" in d.message for d in diags)


@pytest.mark.asyncio
async def test_reports_import_usage(tmp_path):
    provider = _provider()
    resolver = _resolver(tmp_path)
    ctx_data = ContextData(vars=VarSources())

    diags = await provider.analyze("import os\nx=1", ctx_data, resolver)
    assert any("Imports are not supported" in d.message for d in diags)


@pytest.mark.asyncio
async def test_blocks_private_method_calls(tmp_path):
    provider = _provider()
    resolver = _resolver(tmp_path)
    ctx_data = ContextData(vars=VarSources())

    diags = await provider.analyze("class X:\n    def _hidden(self):\n        return 1\n\nX()._hidden()", ctx_data, resolver)
    assert any("private methods" in d.message for d in diags)


@pytest.mark.asyncio
async def test_roll_and_vroll_available(tmp_path):
    provider = _provider()
    resolver = _resolver(tmp_path)
    ctx_data = ContextData(vars=VarSources())

    diags = await provider.analyze("x = roll('1d1')\ny = vroll('1d1')\nx + y.total", ctx_data, resolver)
    assert not any("undefined" in d.message for d in diags)
