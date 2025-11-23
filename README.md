# Avrae Draconic Alias Language Server

Language Server Protocol (LSP) implementation targeting Avrae-style draconic aliases. It provides syntax/semantic diagnostics, a mocked execution command, and a thin configuration layer driven by a workspace `.avraels.json` file. Credit to Avrae team for all code yoinked!

## Install (released package)

- CLI/server via `uv tool` (preferred): `uv tool install avrae-ls` then `uv tool run avrae-ls --help` to see stdio/TCP options (same as `python -m avrae_ls`). The VS Code extension uses this invocation by default. The draconic interpreter is vendored, so no Git deps are needed.

## VS Code extension (released)

- Install from VSIX: download `avrae-ls-client.vsix` from the GitHub releases page, then in VS Code run “Extensions: Install from VSIX” and select the file.
- Open your alias workspace; commands like `Avrae: Show Alias Preview` and `Avrae: Run Alias` will be available.

## Developing locally

- Prereqs: [uv](https://github.com/astral-sh/uv) and Node.js.
- Install deps: `uv sync --all-extras` then `make vscode-deps`.
- Build everything locally: `make package` (wheel + VSIX in `dist/`).
- Run tests/lint: `make check`.
- Run via uv tool from source: `uv tool install --from . avrae-ls`.

## Releasing (maintainers)

1. Bump `pyproject.toml` version.
2. `make release` (clean, build, `twine check`, upload to PyPI).
3. Build and attach the VSIX to the GitHub release (`make vsix`).
4. Tag and push.
