import json
from pathlib import Path

import pytest

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


@pytest.mark.asyncio
async def test_silent_gvar_fetch_seeds_none_when_disabled():
    cfg = AvraeLSConfig.default(Path("."))
    cfg.silent_gvar_fetch = True
    resolver = GVarResolver(cfg)

    fetched = await resolver.ensure("missing")

    assert fetched is True
    assert resolver.get_local("missing") is None


def test_context_builder_resets_cached_gvars_per_build(tmp_path):
    cfg = AvraeLSConfig.default(tmp_path)
    builder = ContextBuilder(cfg)
    resolver = builder.gvar_resolver

    resolver.seed({"cached": "value"})
    builder.build()
    builder.build("default")

    assert resolver.get_local("cached") is None


@pytest.mark.asyncio
async def test_missing_gvar_fetches_when_enabled(monkeypatch):
    cfg = AvraeLSConfig.default(Path("."))
    cfg.enable_gvar_fetch = True
    cfg.service.token = "token"
    resolver = GVarResolver(cfg)

    captured = {}

    class DummyResponse:
        status_code = 200
        text = "example gvar text"

        def json(self):
            return {"value": self.text}

    class DummyClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            captured["url"] = url
            captured["headers"] = headers or {}
            return DummyResponse()

        async def aclose(self):
            return None

    monkeypatch.setattr("avrae_ls.context.httpx.AsyncClient", DummyClient)

    fetched = await resolver.ensure("abc123")

    assert fetched is True
    assert resolver.get_local("abc123") == DummyResponse.text
    assert captured["url"].endswith("/customizations/gvars/abc123")
    assert captured["headers"].get("Authorization") == "token"


@pytest.mark.asyncio
async def test_ensure_many_fetches_concurrently(monkeypatch):
    cfg = AvraeLSConfig.default(Path("."))
    cfg.enable_gvar_fetch = True
    cfg.service.token = "token"
    resolver = GVarResolver(cfg)

    calls: list[str] = []

    class DummyResponse:
        status_code = 200

        def __init__(self, key: str):
            self.key = key

        def json(self):
            return {"value": f"val:{self.key}"}

    class DummyClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            key = url.rsplit("/", 1)[-1]
            calls.append(key)
            return DummyResponse(key)

    monkeypatch.setattr("avrae_ls.context.httpx.AsyncClient", DummyClient)

    results = await resolver.ensure_many(["a", "b"])

    assert results == {"a": True, "b": True}
    assert resolver.get_local("a") == "val:a"
    assert resolver.get_local("b") == "val:b"
    assert set(calls) == {"a", "b"}


@pytest.mark.asyncio
async def test_refresh_fetches_multiple_when_enabled(monkeypatch):
    cfg = AvraeLSConfig.default(Path("."))
    cfg.enable_gvar_fetch = True
    cfg.service.token = "token"
    resolver = GVarResolver(cfg)

    captured = {"urls": [], "headers": []}

    class DummyResponse:
        status_code = 200

        def __init__(self, key: str):
            self.value = f"value:{key}"

        def json(self):
            return {"value": self.value}

    class DummyClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            key = url.rsplit("/", 1)[-1]
            captured["urls"].append(url)
            captured["headers"].append(headers or {})
            return DummyResponse(key)

        async def aclose(self):
            return None

    monkeypatch.setattr("avrae_ls.context.httpx.AsyncClient", DummyClient)

    snapshot = await resolver.refresh({"seed": "present"}, keys=["g1", "g2"])

    assert snapshot["seed"] == "present"
    assert snapshot["g1"] == "value:g1"
    assert snapshot["g2"] == "value:g2"
    assert len(captured["urls"]) == 2
    assert captured["headers"] == [{"Authorization": "token"}, {"Authorization": "token"}]


def test_context_builder_loads_gvar_from_file_path(tmp_path: Path):
    var_dir = tmp_path / "vars"
    var_dir.mkdir()

    gvar_file = var_dir / "mod.gvar"
    gvar_file.write_text("answer = 42\n")
    gvar_file_alt = var_dir / "mod-alt.gvar"
    gvar_file_alt.write_text("answer = 84\n")

    var_file = var_dir / "gvars.json"
    var_file.write_text(
        json.dumps({"gvars": {"mod123": {"filePath": "mod.gvar"}, "mod456": {"path": "mod-alt.gvar"}}})
    )

    cfg = AvraeLSConfig.default(tmp_path)
    cfg.var_files = (var_file,)
    builder = ContextBuilder(cfg)

    ctx = builder.build()

    assert ctx.vars.gvars["mod123"] == "answer = 42\n"
    assert ctx.vars.gvars["mod456"] == "answer = 84\n"
    assert builder.gvar_resolver.get_local("mod123") == "answer = 42\n"


def test_context_builder_skips_missing_gvar_file_path(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    var_file = tmp_path / "gvars.json"
    var_file.write_text(
        json.dumps(
            {
                "gvars": {
                    "mod123": {"filePath": "missing.gvar"},
                    "inline": "return 1",
                }
            }
        )
    )

    cfg = AvraeLSConfig.default(tmp_path)
    cfg.var_files = (var_file,)

    with caplog.at_level("WARNING"):
        ctx = ContextBuilder(cfg).build()

    assert "mod123" not in ctx.vars.gvars
    assert ctx.vars.gvars["inline"] == "return 1"
    assert "Gvar content file not found for 'mod123'" in caplog.text
