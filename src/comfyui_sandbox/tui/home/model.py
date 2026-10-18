"""What home keeps while it runs (an operation's screen state), and the facts about the CLI's
commands it repeats (update's targets, where backups go). The sandboxes themselves are the
gateway's records (gateway.Sandbox)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..gateway import Gateway, OperationHandle
from ..press_twice import PressTwice

if TYPE_CHECKING:
    from .chrome import Sampler
    from .live_view import LiveView
    from .log_views import OpLogEntry


@dataclass(eq=False)
class Operation:
    """What exists only while an operation runs, and until the next screen: its live view, its
    output (for L), the keys that cancel it, the cancel key pressed once, and the sampler the
    hero's logo previews it with. The app holds one as `op`, made when the operation starts
    (SandboxApp.begin_operation) and dropped when the next screen is drawn."""

    live_view: LiveView | None = None
    op_log: OpLogEntry | None = None
    cancel_keys: set[str] = field(default_factory=set)  # keys that cancel it ("Esc"); Ctrl+C always quits
    armed: PressTwice = field(default_factory=PressTwice)
    sampler: Sampler | None = None

    def cancellable(self, keys: set[str]) -> None:
        """Sets the keys that cancel it; an empty set when it's over."""
        self.cancel_keys = keys
        self.armed.clear()

    def finish(self) -> None:
        """Its steps are over: drops what only running needed (the live view and the cancel keys).
        The rest stays until the next screen is drawn (Console.draw), because it is read after the
        end: op_log, by L (on the pause that follows), and the sampler, which holds the logo's
        outcome on screen and then lets it re-form."""
        # Not op_log or the sampler: clearing them here would take L away from the pause prompt and
        # snap the logo back before the outcome had been seen.
        self.live_view = None
        self.cancellable(set())


class Tracked:
    """The gateway home was given, noting each operation it starts, so that quitting can stop
    whatever is still running (SandboxApp.stop_running). Every screen starts its command lines
    through `app.gateway.run`, so none of them has to remember to say what it started, and an
    operation no screen is looking at any more (one left behind by a flow that ended) is still
    found. Everything else is the gateway's own."""

    def __init__(self, gateway: Gateway) -> None:
        self._gateway = gateway
        self._handles: list[OperationHandle] = []

    def run(self, argv: Sequence[str]) -> OperationHandle:
        self._handles = self.running()  # the ended ones are forgotten, so the list stays short
        handle = self._gateway.run(argv)
        self._handles.append(handle)
        return handle

    def running(self) -> list[OperationHandle]:
        """The operations started through it that haven't ended yet."""
        return [handle for handle in self._handles if handle.result is None]

    def __getattr__(self, name: str):
        return getattr(self._gateway, name)


class Interrupt(Exception):
    """Ctrl+C, or a running operation cancelled. Plays the part KeyboardInterrupt plays in a
    plain CLI (a real KeyboardInterrupt would tear down asyncio's event loop)."""


class Cancel(Exception):
    """Esc on a question with no Back: the flow that asked it is abandoned."""


LINUX_USER = "comfy"  # the install's default Linux user (intake.DEFAULT_USER)
BACKUPS_DIR = f"/home/{LINUX_USER}/comfyui-backups"  # where `backup` writes, inside the distro
COMFYUI_DIR = f"/home/{LINUX_USER}/comfyui-docker/data/ComfyUI"  # ComfyUI's own folders, inside the distro


# `update`'s targets, in the order `update all` runs them: (label, what it updates). The gateway
# says which steps each one runs.
UPDATE_TARGETS = {
    "comfyui": ("ComfyUI", "ComfyUI itself, to the newest release (nightly if it's on nightly, a pinned one stays)"),
    "nodes": ("Custom nodes", "every custom node you installed, to its newest version"),
    "comfy-cli": ("comfy-cli", "the tool the sandbox uses to install ComfyUI and custom nodes"),
    "system": ("Docker", "Docker (and the NVIDIA container toolkit, with an NVIDIA GPU) inside the distro"),
    "setup": ("Sandbox setup", "ComfyUI Sandbox's own setup files, to this version. Your config files are kept"),
}

# Which ComfyUI? (Update… › ComfyUI): what each choice does, beside it. A release number is
# `update comfyui --version`'s, which pins the sandbox to it.
COMFYUI_VERSION_HINTS = {
    "latest": "the newest stable release, later updates keep it on the newest",
    "nightly": "the newest commit of ComfyUI's master branch: newest features, sometimes broken",
    "release": "one release, e.g. to go back to one that worked, the sandbox is pinned to it",
}
RELEASE = re.compile(r"v?(\d+(?:\.\d+){1,3})")  # as `update comfyui --version` reads one: 0.3.40, or v0.3.40


def release_number(text: str) -> bool | str:
    """Which ComfyUI?'s release number: True, or why it won't do (the core says the same)."""
    return bool(RELEASE.fullmatch(text.strip().lower())) or "A release number such as 0.3.40."


def comfyui_does(version: str) -> str:
    """What updating ComfyUI to `version` (latest, nightly or a release number) does, in a few words."""
    return {
        "latest": "ComfyUI itself, to the newest release",
        "nightly": "ComfyUI itself, to the newest nightly commit",
    }.get(version, f"ComfyUI itself, to release {version.strip().lower().removeprefix('v')}, pinned there")
