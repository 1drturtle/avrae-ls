import pytest
from pathlib import Path

from avrae_ls.config import AvraeLSConfig
from avrae_ls.context import ContextBuilder, GVarResolver
from avrae_ls.server import AvraeLanguageServer, refresh_gvars


@pytest.mark.asyncio
async def test_refresh_resets_and_seeds_without_fetch():
    cfg = AvraeLSConfig.default(Path("."))
    resolver = GVarResolver(cfg)
    resolver.reset({"old": "value"})

    snapshot = await resolver.refresh({"seed": "x"}, keys=["missing"])

    assert snapshot.get("seed") == "x"
    assert "old" not in snapshot
    # enable_gvar_fetch defaults to false, so missing key should not be added
    assert "missing" not in snapshot


@pytest.mark.asyncio
async def test_refresh_gvars_command_uses_profile_seed():
    server = AvraeLanguageServer()
    server.load_workspace(Path("."))
    result = await refresh_gvars(server, {"profile": "default", "keys": ["abc123"]})

    assert "gvars" in result
    assert result["count"] == len(result["gvars"])
    assert "abc123" not in result["gvars"]


def test_context_builder_preserves_cached_gvars(tmp_path):
    cfg = AvraeLSConfig.default(tmp_path)
    builder = ContextBuilder(cfg)
    resolver = builder.gvar_resolver

    resolver.seed({"cached": "value"})
    builder.build()
    builder.build("default")

    assert resolver.get_local("cached") == "value"
