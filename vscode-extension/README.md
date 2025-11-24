# Avrae/Draconic Language Server Extension

VS Code client for `avrae-ls` that provides diagnostics, completion, and mock alias execution in the preview panel. Configure `.avraels.json` in your workspace (see the repo README) to supply tokens or local var files if you need gvar/import support.

## Mock vs. live Avrae caveats

- Mock runs are local-only: `set_cvar`/`set_uvar`/`get_gvar` changes do not persist to Avrae; each preview/run resets state.
- Network traffic is limited to optional gvar fetches and `verify_signature`; everything else (ctx/combat/character, Discord APIs) is mocked from your `.avraels.json`.
- Gvars resolve from local var files first; remote fetches use `avraeService.baseUrl`/`avraeService.token` when `enableGvarFetch` is true and are cached for the session.
- `signature()` returns `mock-signature:<int>`; `verify_signature()` POSTs to `/bot/signature/verify`, reuses the last successful response per signature, and respects `verifySignatureTimeout`/`verifySignatureRetries`.
