import json
from pathlib import Path

import pytest

from avrae_ls.config import AvraeServiceConfig, CONFIG_FILENAME, load_config


def _write_config(tmp_path: Path, data: dict) -> Path:
    config_path = tmp_path / CONFIG_FILENAME
    config_path.write_text(json.dumps(data))
    return config_path


def test_load_config_env_substitution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("AVRAE_TOKEN", "shh-its-a-secret")
    monkeypatch.setenv("AVRAE_BASE", "https://env.example.invalid")
    _write_config(
        tmp_path,
        {
            "avraeService": {
                "baseUrl": "${AVRAE_BASE}",
                "token": "$AVRAE_TOKEN",
            },
        },
    )

    cfg, warnings = load_config(tmp_path)

    assert warnings == []
    assert cfg.service.base_url == "https://env.example.invalid"
    assert cfg.service.token == "shh-its-a-secret"


def test_load_config_workspace_root_substitution(tmp_path: Path):
    _write_config(
        tmp_path,
        {
            "varFiles": ["${workspaceRoot}/vars.json"],
        },
    )

    cfg, warnings = load_config(tmp_path)

    assert warnings == []
    assert cfg.var_files == (tmp_path / "vars.json",)


def test_load_config_missing_env_warns(tmp_path: Path):
    _write_config(
        tmp_path,
        {
            "avraeService": {
                "baseUrl": "${MISSING_AVRAE_BASE}",
                "token": "$MISSING_TOKEN",
            },
        },
    )

    cfg, warnings = load_config(tmp_path)

    assert any("MISSING_AVRAE_BASE" in warning for warning in warnings)
    assert any("MISSING_TOKEN" in warning for warning in warnings)
    assert cfg.service.base_url == AvraeServiceConfig.base_url
    assert cfg.service.token is None
