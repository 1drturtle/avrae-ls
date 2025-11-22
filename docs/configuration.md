# Configuration

The language server reads a workspace-level `.avraels.json` file to shape diagnostics, hover/completion context, and gvar fetching. The file is optional; defaults are provided if absent.

## Example `.avraels.json`

```json
{
  "enableGvarFetch": true,
  "avraeService": {
    "baseUrl": "https://api.avrae.io",
    "token": "YOUR_AVRAE_TOKEN"
  },
  "varFiles": [".avrae-vars.json"]
}
```

## Settings
- `enableGvarFetch`: Opt-in to fetch gvars from Avrae using `avraeService.token` when diagnostics/commands encounter `get_gvar`. When disabled, only locally provided gvars are used.
- `avraeService.baseUrl` / `token`: Host and bearer token for gvar fetches.
- `varFiles`: JSON files merged into the current profile’s vars (same shape as `vars` above); paths are workspace-relative unless absolute.
- `profiles`: Named context profiles. Each can override `ctx`, `combat`, `character`, and `vars`. `defaultProfile` selects which profile is used by default.

## Commands
- `avrae.runAlias`: Execute the active alias with the current profile.
- `avrae.reloadConfig`: Reload `.avraels.json` and rebuild the mock context.
- `avrae.refreshGvars`: Clear the gvar cache and optionally prefetch gvars. Payload example:
  ```json
  { "profile": "default", "keys": ["abc123", "xyz789"] }
  ```
  When `enableGvarFetch` is true and a token is set, the keys are fetched from Avrae; otherwise only locally configured gvars are cached.

## Defaults
When no profiles are defined, the built-in `default` profile is fully populated with realistic sample `ctx`, `combat`, `character`, and vars to make hover/completion/runtime features work out of the box.
