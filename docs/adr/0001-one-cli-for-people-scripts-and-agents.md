# One deterministic CLI that people, scripts and agents all drive

Installing and managing a sandbox is almost entirely deterministic: branch on exit codes, ask a few questions with computed defaults, run idempotent steps in order, retry, verify. So it is a real CLI, `comfyui-sandbox`, that works without an LLM. An agent that helps a user drives the same CLI a person or a script does.

## Consequences

- Common operations are CLI commands with the gotchas built in. The `comfy`, `cm-cli` and `exec` passthroughs cover the rest, so a caller never needs raw `docker exec` or `wsl.exe` calls.
- Scripts and agents drive the CLI with `--json --non-interactive` and ask the user any questions themselves ([0008](0008-every-question-is-a-flag.md)). The `--json` document and the exit codes are therefore a public contract (`docs/scripting.md`).
