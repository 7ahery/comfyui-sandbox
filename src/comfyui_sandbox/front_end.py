"""The screens the CLI can hand a command line to, and the launcher that opens them (ADR 0017).

The CLI and the TUI are two front ends over one core. The CLI decides which one a command line
gets (`cli.main()` for home, the `run` command for the run window); when it's the TUI, it asks
`Deps.launch` to open a screen and hands back its exit code. The launcher lives behind `Deps` so
tests can pass a fake one and never open a real screen.

`open_tui` is the real launcher, and the one place the core reaches into the `tui` subpackage. It
imports it inside the function: a plain command must start without loading Textual (the laziness
test in tests/test_import_rules.py), and the import-linter contract that keeps the core out of the
TUI declares this import as its one ignored exception (pyproject.toml, `[tool.importlinter]`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # deps.py imports this module for its default launcher
    from comfyui_sandbox.deps import Deps


@dataclass(frozen=True)
class Home:
    """Home: the TUI's main screen, listing the sandboxes and offering the everyday actions."""


@dataclass(frozen=True)
class RunWindow:
    """The run window for one sandbox: `run` at a terminal (what `start` and the desktop shortcuts
    open). The window runs `run --distro <distro>` itself, in-process, so the run lifecycle stays
    the core command's; this only says which sandbox, already resolved by the command line."""

    distro: str


Screen = Home | RunWindow
"""Every screen a launcher can be asked to open."""

Launcher = Callable[[Screen, "Deps"], int]
"""Opens a screen in this terminal, blocks until it closes, and returns the exit code.

It's handed the `Deps` the command line was given, and the screen reaches the machine only
through them (the real gateway passes them on to the core), so the TUI sees the same
installs.json, ports and markers a command would, and tests can play them with fakes.

A crash inside the screen is raised, after the terminal has been restored, so that `main()`'s
crash path reports it like any other: exit 1, the traceback in a transcript, its path printed.
"""


@dataclass(frozen=True)
class Closed:
    """What a command returns when it handed its command line to a screen: the screen has closed
    with `exit_code`. That is the whole result. The screen has shown whatever it had to say, so
    `main()` prints no message and no `Error:` line of its own (`run`'s window after a failure)."""

    exit_code: int


def open_tui(screen: Screen, deps: Deps) -> int:
    """The real launcher: opens `screen` with Textual in this terminal, on the real machine as
    `deps` see it (see `Launcher`)."""
    # Imported here, not at the top: the TUI pulls in Textual, and only this call needs it.
    from comfyui_sandbox.tui.entry import open_home, open_run_window

    if isinstance(screen, RunWindow):
        return open_run_window(screen.distro, deps)
    return open_home(deps)
