from __future__ import annotations

import json
import re
from pathlib import Path

import tomllib


def bump_patch(version: str) -> str:
    parts = version.split(".")
    while len(parts) < 3:
        parts.append("0")
    major, minor, patch = parts[:3]
    return ".".join([major, minor, str(int(patch) + 1)])


def update_pyproject(new_version: str) -> str:
    path = Path("pyproject.toml")
    text = path.read_text()
    data = tomllib.loads(text)
    old_version = data["project"]["version"]
    pattern = re.compile(r'version\s*=\s*"[0-9]+\.[0-9]+\.[0-9]+"')
    path.write_text(pattern.sub(f'version = "{new_version}"', text, count=1))
    return old_version


def update_package_json(path: Path, new_version: str) -> str:
    data = json.loads(path.read_text())
    old_version = data.get("version", new_version)
    data["version"] = new_version
    path.write_text(json.dumps(data, indent=2) + "\n")
    return old_version


def update_package_lock(path: Path, new_version: str) -> str | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    old_version = data.get("version")
    data["version"] = new_version
    packages = data.get("packages")
    if isinstance(packages, dict) and "" in packages and isinstance(packages[""], dict):
        packages[""]["version"] = new_version
    path.write_text(json.dumps(data, indent=2) + "\n")
    return old_version


def main() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"]
    new_version = bump_patch(pyproject)
    py_old = update_pyproject(new_version)
    pkg_old = update_package_json(Path("vscode-extension/package.json"), new_version)
    lock_old = update_package_lock(Path("vscode-extension/package-lock.json"), new_version)
    print(f"Bumped pyproject version {py_old} -> {new_version}")
    print(f"Bumped vscode-extension/package.json version {pkg_old} -> {new_version}")
    if lock_old:
        print(f"Bumped vscode-extension/package-lock.json version {lock_old} -> {new_version}")


if __name__ == "__main__":
    main()
