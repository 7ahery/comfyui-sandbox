"""The command line that runs this CLI again: `start`'s run window and the launcher shortcuts both use it."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from shutil import which

PROG = "comfyui-sandbox"
PYTHON = Path(sys.executable)


def cli_prefix() -> list[str]:
    """What runs this CLI, always starting with a full path: the script on PATH, else the script next to the
    running interpreter (under `uv run` the venv's Scripts folder is not on PATH), else the interpreter
    itself with `-m` (`python -m comfyui_sandbox`, `pip --user`: no script is reachable)."""
    on_path = which(PROG)
    if on_path:
        return [on_path]
    beside_python = PYTHON.with_name(f"{PROG}.exe")
    if beside_python.is_file():
        return [str(beside_python)]
    return [str(PYTHON), "-m", "comfyui_sandbox"]


def run_argv(distro: str) -> list[str]:
    """The command that opens `run` for `distro`: the executable, then its arguments."""
    return [*cli_prefix(), "run", "--distro", distro]


def shortcut_command(distro: str) -> tuple[str, str]:
    """`run_argv(distro)` as a shortcut stores it: the target, and the arguments as one string."""
    exe, *arguments = run_argv(distro)
    return exe, subprocess.list2cmdline(arguments)
