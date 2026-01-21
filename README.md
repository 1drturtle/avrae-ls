# Avrae Draconic Alias Language Server

Language Server Protocol (LSP) implementation targeting Avrae-style draconic aliases. It provides syntax/semantic diagnostics, a mocked execution command, and a thin configuration layer driven by a workspace `.avraels.json` file. Credit to Avrae team for all code yoinked!

## Install (released package)

- CLI/server via `uv tool` (preferred): `uv tool install avrae-ls` then `avrae-ls --help` to see stdio/TCP options (same as `python -m avrae_ls`). The VS Code extension uses this invocation by default. The draconic interpreter is vendored, so no Git deps are needed.

## VS Code extension (released)

- Install from VSIX: download `avrae-ls-client.vsix` from the GitHub releases page, then in VS Code run “Extensions: Install from VSIX” and select the file.
- Open your alias workspace; commands like `Avrae: Show Alias Preview` and `Avrae: Run Alias` will be available.
- Files ending with `.alias-module` are treated as full-file draconic modules under the `avrae-module` language id (no `<drac2>` tags; mock run/preview commands stay tied to `.alias` files).

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

## Alias tests

- Create files ending with `.alias-test` (or `.aliastest`) next to your alias file. Each test starts with an invocation, followed by `---` and the expected result; you can stack multiple tests in one file by repeating this pattern (optional metadata after a second `---` per test).
  ```
  !my-alias -b example args
  ---
  expected text or number
  ```
- For embed aliases, put a YAML/JSON dictionary after the separator to compare against the embed preview (partial dictionaries are allowed).
  ```
  !embedtest
  ---
  title: Hello
  description: World
  ```
- Embed fields lists can be partial: only the listed fields (in order) are matched; extra fields in the alias do not fail the test.
- Use regex expectations by wrapping strings in `/.../` (or `re:...`). You can also mix literals with regex segments (e.g., `Hello /world.*/`) so only the delimited part is treated as regex.
- Optional second `---` section can carry metadata:
  ``` 
  name: critical-hit
  vars:
    cvars:
      hp: 12
  character:
    name: Tester
  ```
  `name` is a label for reporting, `vars` are merged into cvars/uvars/svars/gvars, and `character` keys are deep-merged into the mock character.
- Run them with `avrae-ls --run-tests [path]` (defaults to the current directory); non-zero exit codes indicate failures.

## Config variable substitution

- `.avraels.json` values support environment variable substitution with `$NAME` or `${NAME}`. `workspaceRoot` and `workspaceFolder` are injected automatically. Missing variables are replaced with an empty string and logged as warnings.

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
