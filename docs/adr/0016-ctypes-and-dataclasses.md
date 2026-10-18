# ctypes and dataclasses, not pywin32 and Pydantic

The Win32 layer (`winapi.py`, `shell_api.py`) uses ctypes rather than pywin32, and records and answers are plain dataclasses rather than Pydantic models. Both are in the standard library: no compiled dependencies for `uv tool install`, no extra CLI startup time, and both import on Linux, so the faked test suite runs anywhere ([0013](0013-everything-outside-the-process-is-injected.md)).

## Consequences

- Win32 signatures and COM are written by hand: `shell_api.py` calls `IShellLinkW` and `IPersistFile` through their vtable slots.
- The JSON mapping in `state.py` is written by hand and doesn't check enum values (`state`, `models.mode`, `gpu.mode`): a hand-edited `installs.json` with an unknown value loads as is.
