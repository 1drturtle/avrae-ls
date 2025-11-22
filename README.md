# Avrae Draconic Alias Language Server

Language Server Protocol (LSP) implementation targeting Avrae-style draconic aliases. It provides syntax/semantic diagnostics, a mocked execution command, and a thin configuration layer driven by a workspace `.avraels.json` file. Credit to Avrae team for all code yoinked!

## Setup

- Prereq: UV
- Run `uv sync`, `make package`
- In VSCode, run Extensions: Install from Vsix and install the file listed in the newly-created `dist/` folder
- In your alias folder of choice, copy the avrae*.whl, then run `uv add avrae...whl` in that workspace
- Restart the VSCode Window
- Open a .alias folder and view the `avrae: show alias preview` command