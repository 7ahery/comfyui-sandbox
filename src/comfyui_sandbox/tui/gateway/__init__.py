"""The TUI's gateway layer: the `Gateway` protocol, its records, the real gateway and the demo one.

Screens, widgets and flows import this package and nothing behind it. Whatever the TUI shows or
does goes through a `Gateway` they are handed, the way a web front end goes through its API client:
that's what lets a redesign rewrite the screens without touching the CLI, and the CLI change
without touching the screens. It is also the only layer of the TUI that may import the rest of
`comfyui_sandbox` (the real gateway does; the demo gateway only to open a console and the
browser, through the core's WinApi). No part of the TUI, this layer included, imports `winapi`,
`wsl` or `runner` directly, nor the standard library's own ways to start a process, open a
browser or call Win32: the gateway reaches them only through `main()` and the core. ADR 0017
records why, and `tests/test_import_rules.py` fails the suite when either rule breaks.

    protocol   Gateway, and the OperationHandle a command line returns
    records    Sandbox, CheckItem, GpuReading; Progress, Step, Question (and its kind) and Result
    real       RealGateway: the real machine, through the core's read-only queries and main()
    demo       DemoGateway: the prototypes' fakes, behind the protocol
"""

from .protocol import Gateway, OperationHandle
from .records import CheckItem, GpuReading, Progress, Question, QuestionKind, Result, Sandbox, Step

__all__ = [
    "CheckItem",
    "Gateway",
    "GpuReading",
    "OperationHandle",
    "Progress",
    "Question",
    "QuestionKind",
    "Result",
    "Sandbox",
    "Step",
]
