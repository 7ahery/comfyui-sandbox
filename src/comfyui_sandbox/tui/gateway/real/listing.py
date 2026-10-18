"""Home's sandboxes, read from the real machine: the listing, the state rule and the notes.

Each sandbox's state is worked out here from the core's meaning-named questions
(`comfyui_sandbox.queries`), in the order the spec's state rule gives. The core answers the
questions (on Windows: `installs.json`, the run-window marker and the reach check); this module only
knows what the answers mean for a row on home, so it would read the same on another platform's core.
"""

from __future__ import annotations

import asyncio

from comfyui_sandbox import queries
from comfyui_sandbox.deps import Deps
from comfyui_sandbox.state import Install

from ...states import SandboxState
from ..records import Sandbox


async def list_sandboxes(deps: Deps) -> list[Sandbox]:
    """Every sandbox, with its state and notes, as home lists it, in installs.json's order.

    The core's questions block (a file read, an HTTP request), so they run on worker threads: the
    screen keeps drawing meanwhile. The sandboxes are asked about all at once, so one whose port
    is slow to answer doesn't hold up the others: a poll takes as long as the slowest, not their sum.
    """
    installs = await asyncio.to_thread(queries.sandboxes, deps)
    return list(await asyncio.gather(*(asyncio.to_thread(look, deps, install) for install in installs)))


def named(deps: Deps, name: str) -> Sandbox:
    """The sandbox called name (as installs.json spells it, whatever case name is in), as home
    would list it: what the run window opens with, its name, port and models folder. The CLI has
    resolved name already, so a sandbox that isn't there is a bug, not a user's mistake."""
    install = next((i for i in queries.sandboxes(deps) if i.is_named(name)), None)
    if install is None:
        raise LookupError(f"No sandbox called {name!r} in installs.json.")
    return look(deps, install)


def look(deps: Deps, install: Install) -> Sandbox:
    """install's row: its state by the state rule, and the notes that go with it.

    The rule, in order: Incomplete (the install didn't finish), Stopping (its run window is
    stopping ComfyUI), Starting (a run window is open but
    its own ComfyUI doesn't answer yet), Running (its own ComfyUI answers, window or not), and
    Stopped otherwise. An unfinished install isn't asked anything more: nothing it has can run.
    """
    row = Sandbox(
        install.distro,
        SandboxState.INCOMPLETE,
        install.port,
        install.models.path,
        channel=install.answers.get("channel"),
    )  # the record's, as the last switch left it
    if not queries.install_finished(install):
        return row
    # Recorded whatever the state: for a Running one it's the "No run window" note (closing a
    # window won't stop it), and it's what tells Starting from Stopped.
    window = row.run_window = queries.run_window_open(deps, install)
    if queries.run_window_stopping(deps, install):
        row.state = SandboxState.STOPPING  # its window is mid-cleanup, whatever the port says
        return row
    answer = queries.own_comfyui(deps, install)
    if answer.answers:
        row.state = SandboxState.RUNNING
    elif window:
        row.state = SandboxState.STARTING
    else:
        row.state = SandboxState.STOPPED
        # Someone else on its port: Start would find it taken, so the row says who.
        row.port_held_by = answer.held_by
    return row
