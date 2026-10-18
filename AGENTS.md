# Notes for coding agents

[CONTRIBUTING.md](CONTRIBUTING.md) is the source for setup, the tools the suite needs, the checks it runs and what a
pull request must update. Read it first. This file only adds what helps an agent.

## Working here

- Run Python through uv: `uv run pytest`, `uv run python ...`, never a bare `python`.
- `.claude/settings.json` lets Claude Code run `uv sync` and the suite's checks (pytest, ruff, ty, lint-imports)
  without asking. Personal permissions go in `.claude/settings.local.json`, which git ignores.
- While iterating, run the tests for the area you changed; run the whole suite once before you finish. Keep output
  short (`-q`, or write it to a file and read the tail).
- A TUI test that fails only in a full run is probably load, not your change: see "Tests that can fail under load" in
  CONTRIBUTING.md and rerun it alone with `-n 0`.
- Use the words defined in [GLOSSARY.md](GLOSSARY.md). Before changing an area, read the ADRs in `docs/adr/` that
  touch it, and say so when a change contradicts one rather than overriding it quietly.
- What scripts and agents see (exit codes, the `--json` document, each command's `data`, `installs.json`) is a
  public contract in [docs/scripting.md](docs/scripting.md). Change it there in the same change, and the suite
  checks the two agree.
