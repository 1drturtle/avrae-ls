# Alias tests

Alias tests let you run a mock alias and compare the result to what you expect. They are small text files that live next to your alias files and can contain many tests per file.

## Quick start

1. Create an alias file (for example `greet.alias`).
2. Create a test file next to it (for example `greet.alias-test`).
3. Run tests:

```bash
avrae-ls --run-tests
```

## File layout and discovery

Alias tests are discovered by filename. The runner scans for:

- `*.alias-test`
- `*.aliastest`

You can run tests in a folder or point to a single test file:

```bash
avrae-ls --run-tests .
avrae-ls --run-tests path/to/greet.alias-test
```

Example layout:

```
my-aliases/
  .avraels.json
  greet.alias
  greet.alias-test
  combat/
    roll.alias
    roll.alias-test
```

### How the alias file is chosen

The test file sits next to the alias file it targets. The test command decides which alias to load:

- `!greet` looks for `greet`, `greet.alias`, or `greet.txt` in the same folder.
- If the test file name starts with `test-`, that prefix is ignored when finding the alias file.

If you keep aliases and tests side by side, you rarely need to think about this.

## Test file format

Each test has three sections:

1. The command to run (starts with `!`).
2. A separator line: `---`
3. The expected result.
4. Optional metadata after a second `---`.

You can repeat this pattern as many times as you like in one file.

### Basic example

```
!greet Ada
---
Hello, Ada!
```

- The command is parsed like a shell command line, so quotes work the way you expect:

```
!greet "Ada Lovelace"
---
Hello, Ada Lovelace!
```

### Multiple tests per file

```
!greet Ada
---
Hello, Ada!

!greet "Ada Lovelace"
---
Hello, Ada Lovelace!

!greet
---
Hello, adventurer!
```

### Numbers and other values

The expected section is YAML/JSON. That means numbers are numbers, lists are lists, and dictionaries are dictionaries.

```
!roll 1d6
---
3
```

### Embed comparison (partial match)

If the alias returns an embed, compare it with a YAML/JSON dictionary. Only the keys you include are checked.

```
!status
---
title: "Status"
description: "All systems go"
fields:
  - name: "HP"
    value: "12/12"
```

- `fields` is compared by order and prefix. If you list two fields, only the first two are checked.
- Extra fields in the actual embed do not fail the test.

### Regex matching

You can use regex in expected strings:

- Full regex: `/.../` or `re:...`
- Mixed literal + regex: only the `/.../` part is regex.

```
!roll 1d20
---
/\d+/
```

```
!greet Ada
---
Hello, /A.*/!
```

### Metadata (vars and character overrides)

Add a second `---` to include metadata. Supported keys:

- `name`: label for reporting.
- `vars`: per-test overrides for `cvars`, `uvars`, `svars`, and `gvars`.
- `character`: values merged into the mock character.

```
!damage 5
---
/\d+ damage/
---
name: "simple-damage"
vars:
  cvars:
    hp: 12
character:
  name: "Test Paladin"
  stats:
    strength: 18
```

### Run-only tests (no expected output)

If you just want to make sure the alias runs without error, leave the expected section blank.

```
!setup
---
---
name: "no-output-check"
```

## Common tips

- Expected output cannot include a line that starts with `!` because that marks the next test. If you need to check a `!` line, use a single-line string like `"line1\n!line2"` or a regex like `re:^!`.
- If you want a number treated as text, wrap it in quotes (YAML reads bare numbers as numbers).
- The mock context comes from `.avraels.json` and any var files you configure there (including gvar `{ filePath: ... }` entries). Metadata `vars` and `character` values are merged on top of those defaults for the test only.
- Each test runs independently, so one test does not affect another.
- The expected section is compared against the alias result or embed preview, not against stdout. Stdout is shown in the test report to help debug.

## GitHub Actions

This repo ships a composite action that installs `avrae-ls` and runs alias tests.

1. Create a minimal config file for CI (for example `.github/avrae/ci.avraels.json`):

```json
{
  "enableGvarFetch": true,
  "avraeService": {
    "token": "${AVRAE_TOKEN}"
  }
}
```

2. Add a workflow that uses the action:

```yaml
name: Alias Tests

on:
  pull_request:
  push:

jobs:
  alias-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: 1drturtle/avrae-ls@main
        with:
          test-path: .
          config-path: .github/avrae/ci.avraels.json
          avrae-token: ${{ secrets.AVRAE_TOKEN }}
```

- `config-path` defaults to `.avraels.json`. When you point it at another file, the action copies it to `.avraels.json` in the workspace before running tests.
- `avrae-token` is optional unless your tests rely on remote gvars or `verify_signature`. Prefer `${AVRAE_TOKEN}` in config files instead of hardcoding tokens.

## FAQ

**How do I run just one test file?**
Use the file path:

```bash
avrae-ls --run-tests path/to/greet.alias-test
```

**Can I put many tests in one file?**
Yes. Repeat the `command`, `---`, and `expected` blocks as many times as you want.

**What if I do not care about the output, only that it runs?**
Leave the expected section empty (see "Run-only tests"). That will pass as long as the alias runs without errors.

**How does it find the alias file?**
It looks in the same folder as the test file for the alias name in your command (for example `!greet` matches `greet`, `greet.alias`, or `greet.txt`). If your test file is named `test-greet.alias-test`, it will also try `greet`.

**Can I use JSON instead of YAML?**
Yes. JSON is valid YAML, so JSON objects and lists work in the expected and metadata sections.

**Why is a gvar missing or `using(...)` failing?**
Make sure `.avraels.json` enables gvar fetch and sets a token, or provide the gvar in your var files (inline or with `{ filePath: ... }`) or test metadata under `vars.gvars`.

**How do I ignore gvar fetch failures in CLI runs?**
Use `avrae-ls --run-tests --silent-gvar-fetch` to treat remote gvar fetch failures as `None` without warnings.

**Do tests change my real Avrae data?**
No. The mock runtime does not write back to Avrae. Any variable changes are isolated to a single test run.
