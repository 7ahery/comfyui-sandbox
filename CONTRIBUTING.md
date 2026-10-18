# Contributing

Thanks for your interest in comfyui-sandbox. Bug reports, fixes and ideas are all welcome.

For a security problem, follow [SECURITY.md](SECURITY.md) instead of opening an issue.

## Before you start

- **Bugs**: open an issue with the command you ran, what you expected and what happened. Output
  from `comfyui-sandbox check` and `comfyui-sandbox logs --cli` helps a lot.
- **Changes in behavior or new features**: open an issue first so we can agree on the shape
  before you write code. The decisions behind the current design are in [docs/adr/](docs/adr/),
  and the words the project uses are defined in [GLOSSARY.md](GLOSSARY.md).

## Development setup

You need:

- Windows with WSL2 (the CLI only runs on Windows for now),
- [uv](https://docs.astral.sh/uv/), which installs the Python version in `.python-version`,
- [Git for Windows](https://gitforwindows.org/), whose `bash.exe` the test suite uses.

Then:

```bash
uv sync
uv run pytest
```

`uv sync` installs the dev tools too, including shellcheck, ruff, ty, import-linter and Textual.
The suite shells out to real tools and fails, rather than skips, when one of them is missing.
`tests/toolchain.py` shows how they are found:

- **bash**: Git for Windows' `bash.exe`, found from the `git` install, then `bash` on PATH. The
  WSL launcher in `WindowsApps` is rejected. `COMFYUI_SANDBOX_TEST_BASH=<path>` overrides it.
- **shellcheck**: from `uv sync` (`shellcheck-py`), or `winget install koalaman.shellcheck`,
  `scoop install shellcheck` or `apt install shellcheck`. `COMFYUI_SANDBOX_TEST_SHELLCHECK=<path>`
  overrides it.

The tests run in parallel (`-n auto`, about a minute instead of eight). To debug one serially, or
to see a flow's timing without load, pass `-n 0`.

### Tests that can fail under load

A few TUI tests are sensitive to timing when the machine is busy (a parallel run loads it too).
Rerun a failure alone with `-n 0` before treating it as a regression. The ones seen so far:

- `tests/tui/test_snapshots.py`'s `home_splash`: the splash reveals its text gradually, and the
  snapshot is taken after a fixed delay, so a slow frame lands on a different step. A fix would
  wait for the last line to appear, then re-record.
- `home_install_fail` and `tests/tui/test_quitting.py` (a `MountError` when output arrives before
  its box has mounted): seen once each, not reproduced.

Suspects for the rest: Textual timers and workers still firing while the app exits,
`call_from_thread` after the app has closed, and snapshots taken before layout settles. Reproduce
a failure in a loop under load before changing code for it.

### The terminal UI

- [docs/tui.md](docs/tui.md) lists the rules home and the run window keep. Read it before changing
  either.
- `uv run python -m comfyui_sandbox.tui.demo` opens home on fake data, and `... demo run` the run
  window.
- `tests/tui/test_snapshots.py` compares each screen with `tests/tui/snapshots/`. After a change meant
  to alter a screen, re-record with `SNAPSHOT_UPDATE=1 uv run pytest tests/tui/test_snapshots.py` and
  include the reviewed snapshot diff in your pull request.

### Lint, format and types

The code is linted and formatted by ruff, configured in `pyproject.toml`. `uv sync` installs it, and
`tests/test_ruff.py` runs both checks, so the suite fails, rather than skips, when ruff is missing or
when a change leaves a finding. `uv run ruff format` and `uv run ruff check --fix` fix most of them.

The types are checked by ty, which is also configured in `pyproject.toml` and installed by `uv sync`.
`tests/test_ty.py` runs it over `src` and `tests`, and `uv run ty check` runs it on its own. ty is
pre-1.0, so an upgrade can bring new diagnostics: `pyproject.toml` pins the exact version, and an upgrade is a
deliberate step that fixes what it finds. Fix a finding rather than silence it; one that can't be
fixed gets a targeted `# ty: ignore[rule]` and its reason.

### Import rules

The CLI core and the TUI are kept apart by import-linter contracts in `pyproject.toml`
([ADR 0017](docs/adr/0017-two-front-ends-over-one-core.md)). `uv run lint-imports` checks them on
their own. If a change needs to break a contract, explain why in the pull request.

## Pull requests

- Keep each pull request to one change, with tests that cover it.
- Run the whole suite before you open it.
- Write commit subjects as a short imperative sentence that says what changes for the user,
  for example "Refuse reinstall on an unfinished install".
- Text the user sees (messages, prompts, help) is plain and short, uses commas or periods rather
  than semicolons, and says what to do next when something fails.
- Add a line under `## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md) for anything a user would
  notice.
- A change to what scripts and agents see (exit codes, the `--json` document, a command's `data`,
  `installs.json`) updates [docs/scripting.md](docs/scripting.md) in the same pull request. The suite
  checks the exit codes (`tests/test_exit_codes.py`) and the `data` keys (`tests/scripting_doc.py`,
  whose docstring says how it reads a row) against it. The code and tests a section governs cite it
  by title (`docs/scripting.md, "Exit codes"`), so a search for the title finds them. Renaming a
  section means updating those citations. A change to how a command is used updates the
  [README](README.md), and a change to a rule in [docs/tui.md](docs/tui.md) updates it.

By contributing, you agree that your contribution is licensed under the project's license.
