"""`comfyui-sandbox update nodes`: update every custom node (`comfy node update all` in the container)."""

from __future__ import annotations

import typer

from comfyui_sandbox.commands.update_support import (
    CHECK_TIMEOUT,
    NOT_CHECKED,
    Context,
    Pending,
    Prepared,
    in_container,
    prepared,
    probe,
    run_ending_stopped,
    unknown,
    updated_in_container,
)
from comfyui_sandbox.output import Outcome
from comfyui_sandbox.steps import container_running

NAME = "nodes"


# For each git-installed custom node: "<folder> <local HEAD> <remote HEAD or ?>". `git ls-remote` only reads.
CHECK_SCRIPT = (
    'for d in /workspace/ComfyUI/custom_nodes/*/; do [ -e "$d.git" ] || continue; '
    'l=$(git -C "$d" rev-parse HEAD 2>/dev/null) || continue; '
    'r=$(git -C "$d" ls-remote origin HEAD 2>/dev/null | cut -f1); '
    'echo "$(basename "$d") $l ${r:-?}"; done'
)


def pending(session, install) -> Pending:
    """Each git custom node's HEAD against its remote's HEAD (read-only; nodes without a .git are skipped)."""
    if not container_running(session.wsl(), install, timeout=CHECK_TIMEOUT):
        return NOT_CHECKED
    done = probe(session, install, ["sh", "-c", CHECK_SCRIPT])
    if done.returncode != 0:
        return unknown("the custom nodes could not be read")
    rows = [line.split() for line in done.out().splitlines() if len(line.split()) == 3]
    behind = [name for name, local, remote in rows if remote != "?" and remote != local]
    unreachable = [name for name, _, remote in rows if remote == "?"]
    if behind:
        return Pending(True, f"{len(behind)} behind their remote: {', '.join(behind)}")
    if unreachable:
        return unknown(f"could not reach the remote of {', '.join(unreachable)}")
    return Pending(False, f"{len(rows)} custom node(s) match their remote" if rows else "no custom nodes to update")


WHAT = "Update the custom nodes"
UPDATE_SECONDS = 120  # expected: every node pulled, and its requirements installed


def prepare(session, install) -> Prepared:
    return prepared(
        NAME,
        in_container(WHAT, UPDATE_SECONDS, ["node", "update", "all"]),
        Context(session, install),
        updated_in_container(WHAT),
    )


def register(group: typer.Typer) -> None:
    @group.command(NAME)
    def update_nodes(ctx: typer.Context) -> Outcome:
        """Update all custom nodes (this also re-syncs the Manager). ComfyUI ends stopped."""
        return run_ending_stopped(ctx.obj, prepare)
