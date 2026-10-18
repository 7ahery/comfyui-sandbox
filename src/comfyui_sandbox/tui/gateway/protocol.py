"""The `Gateway` protocol, the TUI's whole view of the world, and the operation handle it returns.

What a screen can learn is a query here, named by what it means ("the sandboxes and their
states", "this machine's GPU"), never by how it's found out; what a screen can change is a command
line it asks the gateway to run. A gateway for the real CLI answers the queries with the core's
read-only functions and runs the command lines through `main()`; the demo gateway fakes both.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from .records import CheckItem, GpuReading, Progress, Result, Sandbox


class OperationHandle(Protocol):
    """A command line the gateway is running, or has run. The screen that started it shows
    `progress` as it goes and waits for `result`; nothing ties the operation to that screen, so a
    later one (or several, for background operations) can pick it up the same way.

    Home runs one at a time, but nothing here assumes it."""

    argv: tuple[str, ...]  # the command line, without the program's name: what the screen echoes
    progress: Progress

    @property
    def result(self) -> Result | None:
        """How it ended, or None while it's still going."""
        ...

    async def wait(self) -> Result:
        """Waits until it has ended, and says how."""
        ...

    def cancel(self) -> None:
        """Asks it to stop. It ends soon after, running its cleanup first, usually as interrupted
        (130); a command whose end is stopping (run) may end OK. Asking again does nothing."""
        ...

    def answer(self, value: str | bool) -> None:
        """Answers the question in progress.question: one of its choices' values, the text typed,
        or True / False, by its kind. To back out of it instead, cancel()."""
        ...


class Gateway(Protocol):
    """Everything the TUI knows of the world, and the only way it acts on it.

    Queries are cheap enough to poll: home reads the sandboxes after every action and every few
    seconds while idle, and the GPU about once a second. Neither may wake a distro."""

    async def sandboxes(self) -> list[Sandbox]:
        """Every sandbox, with its state, its notes and the update marks found so far. Each call
        returns new records (see Sandbox for how the TUI keeps its own)."""
        ...

    async def distro_running(self, name: str) -> bool:
        """Whether the sandbox `name`'s distro is up (its machine, not its ComfyUI). Asked when a
        menu opens, not polled: a command that reads a stopped sandbox's log would otherwise
        boot the distro and Docker, so the menu offers it only when this is True."""
        ...

    async def check_this_pc(self) -> list[CheckItem]:
        """What the core's check finds about this machine, as a list of items."""
        ...

    def gpu(self) -> GpuReading | None:
        """The latest reading of the machine's GPU, or None when there are none to be had (no
        NVIDIA GPU, or nothing to read it with): then the meters aren't shown. Read again for the
        next one; it never waits."""
        ...

    def run(self, argv: Sequence[str]) -> OperationHandle:
        """Starts the command line argv (without the program's name, e.g. ["stop", "--distro",
        "comfyui"]) and returns its handle at once. It runs in the background; the screen echoes
        exactly argv, so what's shown is what ran."""
        ...

    def close(self) -> None:
        """The screen that used it has closed: stop what the gateway runs on its own to answer
        the queries (the GPU readings' process). Its queries aren't asked again after this."""
        ...
