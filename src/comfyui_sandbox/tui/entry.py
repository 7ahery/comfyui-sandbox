"""Where the CLI comes in: the screens `comfyui-sandbox` opens at a terminal.

`comfyui_sandbox.front_end.open_tui` imports this module inside a function, as the core's one
import of the TUI (ADR 0017; the ignored import in pyproject.toml's first import-linter contract).
Keeping that door a single, small module means the TUI can change behind it without the core's
side or the contract changing.

Each function opens its screen in this terminal, blocks until it closes, and returns the exit
code. A crash is raised once the terminal has been restored, for `cli.main()` to report.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Named through the gateway layer, the TUI's one door to the core (ADR 0017's import rules).
    from .gateway.real import Deps


def open_home(deps: Deps) -> int:
    """Home, as a bare `comfyui-sandbox` or `comfyui-sandbox home` opens it: on the real gateway,
    reading the machine through the `Deps` the command line was given.

    The demo gateway stays behind `python -m comfyui_sandbox.tui.demo` (tui/demo.py), never here.
    """
    # Imported here: the screen pulls in Textual, and this module should cost nothing to import.
    from .gateway.real import RealGateway
    from .home.app import run_home

    return run_home(RealGateway(deps))


def open_run_window(distro: str, deps: Deps) -> int:
    """The run window for distro, as `comfyui-sandbox run` at a terminal opens it (and so `start`
    and the desktop shortcuts): on the real gateway, where it runs `run` itself, in-process.
    Returns `run`'s exit code."""
    from .gateway.real import RealGateway
    from .run_window.app import run_window

    gateway = RealGateway(deps)
    return run_window(gateway, gateway.sandbox(distro))
