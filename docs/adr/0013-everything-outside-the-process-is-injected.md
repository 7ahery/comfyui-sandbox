# Everything outside the process is injected

Subprocesses, Win32 calls, terminal prompts, HTTP, sleeping, environment variables and the home folder reach commands only through one injected `Deps` object (`comfyui_sandbox.deps`). Tests replace them with fakes (`tests/fakes.py`) that record calls and return scripted results, so the unit suite runs on Windows, Linux or macOS without WSL, Docker or a GPU. Paths handed to Windows or shown to people are `PureWindowsPath` for the same reason. A few `@windows_only` tests exercise the real Win32 layer.

## Consequences

- Commands never call `subprocess`, `os.environ` or `time.sleep` directly, and never `print`: stdout is the result channel only (`docs/scripting.md`).
