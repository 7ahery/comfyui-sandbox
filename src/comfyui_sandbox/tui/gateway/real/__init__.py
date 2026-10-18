"""The real gateway: the `Gateway` over the real machine, through the core.

It's an adapter, and holds no logic about the machine itself. It reads through the core's small
read-only functions (`comfyui_sandbox.queries`, named by what they mean) and, once actions are
real, runs command lines through the CLI's `main()`. It never reaches the platform layer
(`winapi`, `wsl`, `runner`) itself: it's handed the `Deps` the CLI built (tui/entry.py) and passes
them on, so tests drive it with the CLI's fakes, and the import rules (ADR 0017) hold.

Each of the protocol's parts lives in a module of its own, so they can grow apart:

    listing     sandboxes(): the listing, the state rule and the notes
    updates     the update marks on that listing: a background check per sandbox, once a session
    meters      gpu(): the GPU readings, from a process that runs until close()
    check       check_this_pc(): the core's check, as data
    operation   run(): a command line through the CLI's main(), on a worker thread
    questions   the TUI's Prompter: what a command asks, asked on the screen
    output      the TUI's Output, which writes what a command says into its operation's progress
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from comfyui_sandbox import queries
from comfyui_sandbox.deps import Deps

from ..protocol import OperationHandle
from ..records import CheckItem, GpuReading, Sandbox
from . import check, listing
from .listing import list_sandboxes
from .meters import Meters
from .operation import RealOperation
from .updates import UpdateChecks

__all__ = ["Deps", "RealGateway"]  # Deps: for tui/entry.py, which hands them over

UPDATE = "update"  # the command after which its sandbox is checked for updates again (run)


class RealGateway:
    """A Gateway on the real machine, as `deps` see it."""

    def __init__(self, deps: Deps) -> None:
        self.deps = deps
        self.meters = Meters(deps)
        self.updates = UpdateChecks(deps)

    async def sandboxes(self) -> list[Sandbox]:
        rows = await list_sandboxes(self.deps)
        self.updates.mark(rows)
        return rows

    async def distro_running(self, name: str) -> bool:
        return await asyncio.to_thread(queries.distro_running, self.deps, name)

    async def check_this_pc(self) -> list[CheckItem]:
        return await check.check_this_pc(self.deps)

    def gpu(self) -> GpuReading | None:
        return self.meters.reading()

    def close(self) -> None:
        self.meters.close()

    def sandbox(self, name: str) -> Sandbox:
        """The one sandbox the run window is for, read now (it blocks briefly: the window reads it
        once, before it opens). Not part of the Gateway protocol: the demo's run window is handed
        its sandbox by tui/demo.py."""
        return listing.named(self.deps, name)

    def run(self, argv: Sequence[str]) -> OperationHandle:
        on_end = None
        if argv and argv[0] == UPDATE:
            # Whatever it updated, the sandbox's update check (made once a session) may name it
            # still: check it again. (Bare `update` only lists, but a fresh look then costs
            # nothing worth saving.)

            def on_end(result):
                return self.updates.forget(_distro(argv))

        return RealOperation(self.deps, argv, on_end=on_end).start()


def _distro(argv: Sequence[str]) -> str | None:
    """The sandbox a command line names with --distro, or None (the CLI picks the only one)."""
    for k, word in enumerate(argv):
        if word == "--distro" and k + 1 < len(argv):
            return argv[k + 1]
        if word.startswith("--distro="):
            return word.removeprefix("--distro=")
    return None
