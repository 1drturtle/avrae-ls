from pathlib import Path

import pytest

from avrae_ls.config import AvraeLSConfig, VarSources
from avrae_ls.runtime.context import ContextData, GVarResolver
from avrae_ls.runtime.errors import runtime_error_details
from avrae_ls.runtime.runtime import MockExecutor


def _ctx() -> ContextData:
    return ContextData(vars=VarSources())


def _resolver(tmp_path: Path, gvars: dict[str, str] | None = None) -> GVarResolver:
    cfg = AvraeLSConfig.default(tmp_path)
    resolver = GVarResolver(cfg)
    resolver.reset(gvars or {})
    return resolver


@pytest.mark.asyncio
async def test_missing_module_error_details(tmp_path: Path):
    result = await MockExecutor().run('using(mod="missing")', _ctx(), _resolver(tmp_path))

    assert result.error is not None
    details = runtime_error_details(result.error)
    assert details.kind == "missing_module"
    assert details.module == "missing"
    assert details.import_chain == ["missing"]
    assert "No gvar named 'missing'" in details.message


@pytest.mark.asyncio
async def test_direct_module_error_details_include_module_line(tmp_path: Path):
    resolver = _resolver(tmp_path, {"mod": 'a = 1\nload_json("")'})

    result = await MockExecutor().run('using(mod="mod")', _ctx(), resolver)

    assert result.error is not None
    details = runtime_error_details(result.error)
    assert details.kind == "module"
    assert details.module == "mod"
    assert details.import_chain == ["mod"]
    assert details.module_line == 2
    assert details.cause and "Expecting value" in details.cause
    assert details.message.startswith("Error in module mod at line 2:")


@pytest.mark.asyncio
async def test_nested_using_error_details_include_full_chain_and_deepest_cause(tmp_path: Path):
    resolver = _resolver(
        tmp_path,
        {
            "outer": 'using(inner="inner")\nvalue = 1',
            "inner": 'a = 1\nload_json("")',
        },
    )

    result = await MockExecutor().run('using(outer="outer")', _ctx(), resolver)

    assert result.error is not None
    details = runtime_error_details(result.error)
    assert details.kind == "module"
    assert details.module == "inner"
    assert details.import_chain == ["outer", "inner"]
    assert details.module_line == 2
    assert details.cause and "Expecting value" in details.cause
    assert details.message.startswith("Error in module outer -> inner at line 2:")


@pytest.mark.asyncio
async def test_nested_module_function_error_details_use_module_sources(tmp_path: Path):
    resolver = _resolver(
        tmp_path,
        {
            "outer": 'using(inner="inner")\ndef call():\n    return inner.boom()',
            "inner": 'def boom():\n    return load_json("")',
        },
    )

    result = await MockExecutor().run('using(outer="outer")\nouter.call()', _ctx(), resolver)

    assert result.error is not None
    details = runtime_error_details(result.error)
    assert details.kind == "module"
    assert details.module == "inner"
    assert details.import_chain == ["outer", "inner"]
    assert details.module_line == 2
    assert details.module_col == 12
    assert details.cause and "Expecting value" in details.cause


@pytest.mark.asyncio
async def test_circular_import_error_details(tmp_path: Path):
    resolver = _resolver(
        tmp_path,
        {
            "mod_a": 'using(b="mod_b")',
            "mod_b": 'using(a="mod_a")',
        },
    )

    result = await MockExecutor().run('using(a="mod_a")', _ctx(), resolver)

    assert result.error is not None
    details = runtime_error_details(result.error)
    assert details.kind == "circular_import"
    assert details.import_chain == ["mod_a", "mod_b", "mod_a"]
    assert details.message == "Circular import detected: mod_a -> mod_b -> mod_a"
