# Configuration

The language server reads a workspace-level `.avraels.json` file to shape diagnostics, hover/completion context, and gvar fetching. The file is optional; defaults are provided if absent.

## Full `.avraels.json` example (all options)

```json
{
  "enableGvarFetch": true,
  "avraeService": {
    "baseUrl": "https://api.avrae.io",
    "token": "YOUR_AVRAE_TOKEN"
  },
  "diagnostics": {
    "semanticLevel": "warning",
    "runtimeLevel": "error"
  },
  "varFiles": [
    ".avrae-vars.json",
    "profiles/gm-vars.json"
  ],
  "profiles": {
    "default": {
      "description": "Everyday adventuring profile for Aelar.",
      "ctx": {
        "guild": { "id": 123456789012345678, "name": "Fabled Realms" },
        "channel": {
          "id": 234567890123456789,
          "name": "tavern-rp",
          "topic": "Adventuring party chat",
          "category": { "id": 9876543210, "name": "Adventures" },
          "parent": null
        },
        "author": {
          "id": 345678901234567890,
          "name": "AelarW",
          "discriminator": "0420",
          "display_name": "Aelar Wyn",
          "roles": [{ "id": 4567, "name": "DM" }, { "id": 4568, "name": "Player" }]
        },
        "prefix": "!",
        "alias": "hunter",
        "message_id": 456789012345678901
      },
      "combat": {
        "name": "Goblin Ambush",
        "round_num": 2,
        "turn_num": 15,
        "me": "cmb_aelar"
      },
      "character": {
        "name": "Aelar Wyn",
        "race": "Half-Elf",
        "stats": { "strength": 16, "dexterity": 14, "constitution": 15, "intelligence": 10, "wisdom": 12, "charisma": 13, "prof_bonus": 3 },
        "levels": { "Fighter": 3, "Rogue": 2 },
        "attacks": [{ "name": "Longsword", "verb": "swings", "activation_type": 1, "raw": { "name": "Longsword", "bonus": "+7", "damage": "1d8+4 slashing" } }],
        "spellbook": { "dc": 14, "sab": 6, "caster_level": 5, "slots": { "1": 4, "2": 2 }, "max_slots": { "1": 4, "2": 2 }, "spells": [{ "name": "Cure Wounds", "prepared": true }] },
        "skills": { "perception": { "value": 5, "prof": 1, "bonus": 0, "adv": null } },
        "saves": { "dex": 4 },
        "resistances": { "resist": [], "vuln": [], "immune": [], "neutral": [] },
        "consumables": { "Hit Dice": { "name": "Hit Dice", "value": 5, "max": 5, "reset_on": "long" } },
        "cvars": { "favorite_enemy": "goblinoids", "fighting_style": "defense" },
        "coinpurse": { "gp": 47, "sp": 12, "cp": 34 },
        "max_hp": 44,
        "hp": 38,
        "temp_hp": 3,
        "ac": 17,
        "passive_perception": 15,
        "speed": 30
      },
      "vars": {
        "cvars": { "me.role": "scout" },
        "uvars": { "party": ["Aelar", "Kerris", "Mythe"], "favored_enemy": "goblins" },
        "svars": { "campaign": "Emerald Enclave" }
      }
    },
    "gm": {
      "description": "DM view with admin vars and different alias prefix.",
      "ctx": { "prefix": ";" },
      "vars": {
        "cvars": { "me.role": "dm" }
      }
    }
  },
  "defaultProfile": "default"
}
```

### Included fields
- `enableGvarFetch`: Opt-in to fetch gvars from Avrae using `avraeService.token` when diagnostics/commands encounter `get_gvar`. When disabled, only locally provided gvars are used.
- `avraeService.baseUrl` / `token`: Host and bearer token for gvar fetches.
- `diagnostics.semanticLevel` / `runtimeLevel`: How LSP diagnostics are reported (`error`, `warning`, `information`, `hint`).
- `varFiles`: Additional JSON files merged into `vars` for the active profile; workspace-relative unless absolute. Each file uses the same shape as `vars` below.
- `profiles`: Named context profiles. Each profile can override `ctx`, `combat`, `character`, `vars`, and optionally `description`.
- `defaultProfile`: Which profile is selected when the server starts.

### Var file example
Place additional vars in any file referenced by `varFiles`. Local gvars here override remote fetches:

```json
{
  "cvars": { "fav_weapon": "longsword" },
  "uvars": { "patron": "The Raven Queen" },
  "svars": {},
  "gvars": { "xyz789": { "name": "xyz789", "value": "{'hp': 12}" } }
}
```

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
