"""Exit codes and the errors commands raise (docs/scripting.md, "Exit codes").

Commands never call sys.exit; they raise CliError. cli.main maps exceptions to
exit codes in one place.
"""

from __future__ import annotations

from enum import IntEnum
from pathlib import Path
from typing import Any


class ExitCode(IntEnum):
    OK = 0
    FAILED = 1
    USAGE = 2
    NO_NVIDIA = 3
    UNSUPPORTED_HOST = 4
    VIRTUALIZATION_OFF = 5
    NO_DRIVER = 6
    WSL_NOT_READY = 7
    DESTRUCTIVE_REFUSED = 8
    ANSWER_MISSING = 9
    NOT_READY = 10
    INTERRUPTED = 130


class CliError(Exception):
    code: int = ExitCode.FAILED  # an int: a passed-through child exit code needn't be one of ExitCode

    def __init__(
        self, message: str, code: int | None = None, *, data: dict[str, Any] | None = None, log: Path | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.data = data or {}
        self.log = log


class UsageError(CliError):
    code = ExitCode.USAGE


class DestructiveRefused(CliError):
    code = ExitCode.DESTRUCTIVE_REFUSED


class NotReady(CliError):
    code = ExitCode.NOT_READY


class AnswerMissing(CliError):
    """--non-interactive and an answer is missing. `flag` is the flag that answers it."""

    code = ExitCode.ANSWER_MISSING

    def __init__(self, message: str, flag: str, *, data: dict[str, Any] | None = None, log: Path | None = None) -> None:
        super().__init__(message, data=data, log=log)
        self.flag = flag


def unregistered_message(distro: str) -> str:
    return (
        f"Distro {distro} is no longer registered in WSL (removed outside comfyui-sandbox). "
        f"Run `comfyui-sandbox uninstall --distro {distro}` to clean up its shortcuts "
        "and its installs.json entry."
    )
