"""The states the TUI tracks, as enums: a typo is an error the type checker catches, not a
silent mismatch. They are StrEnums, so each still prints and formats as its word.

StrEnum, not a plain Enum, so a member can go where its word did without a conversion: in an
f-string (OpLog.status prints an Outcome as it is). One enum per kind of thing, because the same
word (running, failed) means different things in each, and the type checker can then flag an
SandboxState compared with a StepStatus. (At runtime, being strings, they would compare equal.)"""

from __future__ import annotations

from enum import StrEnum


class SandboxState(StrEnum):
    """A sandbox's state. A gateway reports running, stopped, incomplete or starting (see
    Sandbox); while the TUI's own command acts on a sandbox, the TUI shows installing, starting,
    stopping or restarting in its place (SandboxApp.mark)."""

    RUNNING = "running"
    STOPPED = "stopped"
    INCOMPLETE = "incomplete"
    INSTALLING = "installing"
    STARTING = "starting"
    STOPPING = "stopping"
    RESTARTING = "restarting"


class LaunchPhase(StrEnum):
    """Where a run window's launch is."""

    STARTING = "starting"
    RUNNING = "running"
    FAILED = "failed"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ELSEWHERE = "elsewhere"


class StageState(StrEnum):
    """A launch stage."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class StepStatus(StrEnum):
    """A step of a long operation, in the live view."""

    PENDING = "pending"
    SKIPPED = "skipped"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Outcome(StrEnum):
    """How an operation is going or ended."""

    RUNNING = "running"
    OK = "ok"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SamplerKind(StrEnum):
    """What the hero's logo previews (see Sampler)."""

    INSTALL = "install"
    UNINSTALL = "uninstall"
    RECREATE = "recreate"
    UPDATE = "update"
    BACKUP = "backup"
    RESTORE = "restore"
    CHECK = "check"
