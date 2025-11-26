# Avrae Draconic Alias Language Server

Language Server Protocol (LSP) implementation targeting Avrae-style draconic aliases. It provides syntax/semantic diagnostics, a mocked execution command, and a thin configuration layer driven by a workspace `.avraels.json` file. Credit to Avrae team for all code yoinked!

## Install (released package)

- CLI/server via `uv tool` (preferred): `uv tool install avrae-ls` then `avrae-ls --help` to see stdio/TCP options (same as `python -m avrae_ls`). The VS Code extension uses this invocation by default. The draconic interpreter is vendored, so no Git deps are needed.

## VS Code extension (released)

- Install from VSIX: download `avrae-ls-client.vsix` from the GitHub releases page, then in VS Code run “Extensions: Install from VSIX” and select the file.
- Open your alias workspace; commands like `Avrae: Show Alias Preview` and `Avrae: Run Alias` will be available.

## Developing locally

- Prereqs: [uv](https://github.com/astral-sh/uv) and Node.js.
- Install deps: `uv sync --all-extras` then `make vscode-deps`.
- Build everything locally: `make package` (wheel + VSIX in `dist/`).
- Run tests/lint: `make check`.
- Run via uv tool from source: `uv tool install --from . avrae-ls`.
- Run diagnostics for a single file (stdout + stderr logs): `avrae-ls --analyze path/to/alias.txt --log-level DEBUG`.

## How to test

- Quick check (ruff + pytest): `make check` (uses `uv run ruff` and `uv run pytest` under the hood).
- Lint only: `make lint` or `uv run ruff check src tests`.
- Tests only (with coverage): `make test` or `uv run pytest tests --cov=src`.
- CLI smoke test without installing: `uv run python -m avrae_ls --analyze path/to/alias.txt`.

## Runtime differences (mock vs. live Avrae)

- Mock execution never writes back to Avrae: cvar/uvar/gvar mutations only live for the current run and reset before the next.
- Network is limited to gvar fetches (when `enableGvarFetch` is true) and `verify_signature`; other Avrae/Discord calls are replaced with mocked context data from `.avraels.json`.
- `get_gvar`/`using` values are pulled from local var files first; remote fetches go to `https://api.avrae.io/customizations/gvars/<id>` (or your `avraeService.baseUrl`) using `avraeService.token` and are cached for the session.
- `signature()` returns a mock string (`mock-signature:<int>`). `verify_signature()` POSTs to `/bot/signature/verify`, reuses the last successful response per signature, and includes `avraeService.token` if present.

## Troubleshooting gvar fetch / verify_signature

- `get_gvar` returns `None` or `using(...)` raises `ModuleNotFoundError`: ensure the workspace `.avraels.json` sets `enableGvarFetch: true`, includes a valid `avraeService.token`, or seed the gvar in a var file referenced by `varFiles`.
- HTTP 401/403/404 from fetch/verify calls: check the token (401/403) and the gvar/signature id (404). Override `avraeService.baseUrl` if you mirror the API.
- Slow or flaky calls: disable remote fetches by flipping `enableGvarFetch` off to rely purely on local vars.

## Other editors (stdio)

- Any client can launch the server with stdio: `avrae-ls --stdio` (flag accepted for client compatibility) or `python -m avrae_ls`. The server will also auto-discover `.avraels.json` in parent folders.
- Neovim (nvim-lspconfig example):
  ```lua
  require("lspconfig").avraels.setup({
    cmd = { "avrae-ls", "--stdio" },
    filetypes = { "avrae" },
    root_dir = require("lspconfig.util").root_pattern(".avraels.json", ".git"),
  })
  ```
- Emacs (lsp-mode snippet):
  ```elisp
  (lsp-register-client
   (make-lsp-client
    :new-connection (lsp-stdio-connection '("avrae-ls" "--stdio"))
    :major-modes '(fundamental-mode)  ;; bind to your Avrae alias mode
    :server-id 'avrae-ls))
  ```
- VS Code commands to mirror: `Avrae: Run Alias (Mock)`, `Avrae: Show Alias Preview`, `Avrae: Refresh GVARs`, and `Avrae: Reload Workspace Config` run against the same server binary.

## Releasing (maintainers)

1. Bump `pyproject.toml` / `package.json`
2. Create Github release
