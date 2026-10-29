# The user installs the CLI from their own terminal, with uv

uv is a hard requirement: the CLI is installed with `uv tool install <repo>/archive/refs/tags/<tag>.zip`, and uv provides Python. The source is GitHub's archive of the tag, not a `git+` URL, because uv needs Git on PATH to fetch a `git+` source and a fresh Windows has none. The user runs it (`winget install astral-sh.uv`, `uv tool install ...`, `uv tool upgrade comfyui-sandbox`) in their own terminal, not an agent on their behalf.

`install.ps1` at the repository root runs those same commands for the README's one-line install (`irm .../install.ps1 | iex`). The user pastes it into their own PowerShell, so it stays within this decision.

The reason: run from inside the packaged (MSIX) Claude desktop app, uv's tools can land in the app's private AppData, which a double-clicked shortcut can't see. For the same reason the CLI detects a packaged-app context (`winapi.is_packaged_app`) and refuses to create shortcuts there, telling the user to reinstall from their own terminal.
