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


def test_load_config_profiles_overlay_builtin_defaults(tmp_path: Path):
    _write_config(
        tmp_path,
        {
            "profiles": {
                "default": {
                    "character": {
                        "name": "John Test",
                    },
                },
                "gm": {
                    "ctx": {
                        "prefix": ";",
                    },
                },
            },
            "defaultProfile": "gm",
        },
    )

    cfg, warnings = load_config(tmp_path)

    assert warnings == []
    default_profile = cfg.profiles["default"]
    assert default_profile.character["name"] == "John Test"
    assert default_profile.character["stats"]["strength"] == 16
    assert default_profile.character["csettings"]["color"] == 10027008
    assert default_profile.ctx["author"]["display_name"] == "Aelar Wyn"

    gm_profile = cfg.profiles["gm"]
    assert gm_profile.ctx["prefix"] == ";"
    assert gm_profile.ctx["author"]["display_name"] == "Aelar Wyn"


def test_load_config_profile_fixed_time(tmp_path: Path):
    _write_config(tmp_path, {"profiles": {"default": {"time": 1735689600.5}}})

    cfg, warnings = load_config(tmp_path)

    assert warnings == []
    assert cfg.profiles["default"].time == 1735689600.5


@pytest.mark.parametrize("value", [True, "1735689600", float("nan"), float("inf")])
def test_load_config_invalid_profile_fixed_time_warns(tmp_path: Path, value: object):
    _write_config(tmp_path, {"profiles": {"default": {"time": value}}})

    cfg, warnings = load_config(tmp_path)

    assert cfg.profiles["default"].time is None
    assert any("profiles.default.time" in warning for warning in warnings)


def test_load_config_testing_instruction_logging(tmp_path: Path):
    _write_config(tmp_path, {"testing": {"logInstructionCounts": True, "logLoopCounts": True}})

    cfg, warnings = load_config(tmp_path)

    assert warnings == []
    assert cfg.testing.log_instruction_counts is True
    assert cfg.testing.log_loop_counts is True
