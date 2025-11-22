import pytest

from avrae_ls.api import SimpleRollResult
from avrae_ls.config import VarSources
from avrae_ls.context import ContextData
from avrae_ls.runtime import MockExecutor


def _ctx():
    return ContextData(vars=VarSources())


@pytest.mark.asyncio
async def test_roll_returns_total():
    executor = MockExecutor()
    result = await executor.run("roll('1d1')", _ctx())
    assert result.error is None
    assert result.value == 1


@pytest.mark.asyncio
async def test_vroll_returns_roll_result():
    executor = MockExecutor()
    result = await executor.run("vroll('1d1')", _ctx())
    assert result.error is None
    assert isinstance(result.value, SimpleRollResult)
    assert result.value.total == 1
    assert result.value.dice
    assert result.value.consolidated()


@pytest.mark.asyncio
async def test_vroll_handles_bad_input():
    executor = MockExecutor()
    result = await executor.run("vroll('not dice')", _ctx())
    assert result.error is None
    assert result.value is None
