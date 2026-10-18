# Two front ends over one core, kept apart by import rules

comfyui-sandbox has two front ends over one core: the scriptable CLI and the terminal UI (home and the Textual run window), which lives in the `comfyui_sandbox.tui` subpackage. The CLI is the only way into the core for changes: every action the TUI takes is a command line run through the CLI's `main()`, so a TUI action and the same typed command behave the same, are logged the same, and are tested once. The TUI sees the world only through a `Gateway` (`comfyui_sandbox.tui.gateway`), the way a web front end sees its server only through an API client: queries for what it shows, and command lines it asks to run, each returning an operation handle. The real gateway answers from the core's read-only functions and runs commands through `main()`; the demo gateway answers from made-up sandboxes and fake progress, like Storybook fixtures, so the screens can be run, redesigned and snapshot-tested with no WSL, Docker or GPU. A redesign rewrites the screens against the same `Gateway` and touches nothing else. We chose this over a TUI that calls core functions directly, which would be quicker to write but would let screens and commands drift apart and tie every redesign to the core's internals.

## Windows only, and platform code only in the core

The project is Windows-only for now; ADR 0003 records that for the CLI, and this carries it to the TUI. Every platform-specific piece lives in the core behind `Deps` (ADR 0013): the subprocess runner, the Win32 API, the `wsl` transport and the platform itself, never in the `tui` subpackage. The `Gateway`'s queries are named by what they mean ("the sandboxes and their states", "is a run window open", "does its own ComfyUI answer", "this machine's GPU"), never by how a Windows core finds out, so a future Linux core can answer them without a TUI change.

## Enforcement

Intent alone erodes, so the suite fails when these lines are crossed (`tests/test_import_rules.py`, with import-linter contracts in `pyproject.toml`, the equivalent of ESLint's `no-restricted-imports`):

- The core (every module outside `tui`) doesn't import the `tui` subpackage or `textual`. The one exception is the front-end launcher's lazy import (`comfyui_sandbox.front_end` importing `comfyui_sandbox.tui.entry` inside a function: the CLI's way of opening home), declared as an ignored import.
- Inside `tui`, only the gateway layer imports the rest of `comfyui_sandbox`. Screens reach the core only through it.
- Nothing in `tui`, the gateway layer included, directly imports the platform layer: `winapi`, `wsl` and `runner`, nor the standard library modules that would do their job (`subprocess`, `webbrowser`, `ctypes`, `winreg`, `msvcrt`). Reaching them through `main()`, `Deps` and the core is allowed; that is the design.
- Importing the CLI in a fresh interpreter doesn't load Textual, so a plain command never pays for it.

Each rule is also run against a copy of the package with a forbidden import planted in it, and must fail there.

## Consequences

- Textual is a required runtime dependency but is imported only when a TUI opens.
- Anything a screen needs that the core can't yet answer is added as a meaning-named `Gateway` query backed by a core function, not reached for directly.
- The demo gateway's `FAKE:` notes are the map of what the real gateway must provide.
