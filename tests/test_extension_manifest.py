import json
from pathlib import Path


def test_vscode_extension_exposes_refresh_gvars_command():
    manifest_path = Path("vscode-extension/package.json")
    manifest = json.loads(manifest_path.read_text())

    commands = {cmd["command"] for cmd in manifest.get("contributes", {}).get("commands", [])}
    activations = set(manifest.get("activationEvents", []))

    assert "avrae-ls.refreshGvars" in commands
    assert "onCommand:avrae-ls.refreshGvars" in activations


def test_vscode_extension_exposes_server_path_override():
    manifest_path = Path("vscode-extension/package.json")
    manifest = json.loads(manifest_path.read_text())

    properties = manifest["contributes"]["configuration"]["properties"]
    setting = properties["avraeLS.server.path"]

    assert setting["type"] == "string"
    assert setting["default"] == ""
