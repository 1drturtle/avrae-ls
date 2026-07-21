# Avrae/Draconic Language Server Extension

VS Code client for `avrae-ls` that provides diagnostics, completion, and mock alias execution in the preview panel. Released VSIX files include the `avrae-ls` Python package and all of its runtime dependencies, so installing the extension does not require a separate `uv`, `pip`, or `avrae-ls` installation.

The bundled server requires Python 3.11 or newer: the extension starts `python3 -m avrae_ls` on macOS/Linux and `python -m avrae_ls` on Windows. If your Python executable is unavailable, or you want to run a development build, set `avraeLS.server.path` to the absolute path of an alternate `avrae-ls` executable. A configured path replaces the bundled launch entirely.

Configure `.avraels.json` in your workspace (see the repo README) to supply tokens or local var files if you need gvar/import support.

## Mock vs. live Avrae caveats

- Mock runs are local-only: `set_cvar`/`set_uvar`/`get_gvar` changes do not persist to Avrae; each preview/run resets state.
- Network traffic is limited to optional gvar fetches and `verify_signature`; everything else (ctx/combat/character, Discord APIs) is mocked from your `.avraels.json`.
- Gvars resolve from local var files first; remote fetches use `avraeService.baseUrl`/`avraeService.token` when `enableGvarFetch` is true and are cached for the session.
- `signature()` returns `mock-signature:<int>`; `verify_signature()` POSTs to `/bot/signature/verify` and reuses the last successful response per signature.
