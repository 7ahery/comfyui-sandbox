"""`comfyui-sandbox update comfy-cli`: update comfy-cli itself inside the container."""

from __future__ import annotations

import re

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
    version_key,
)
from comfyui_sandbox.output import Outcome
from comfyui_sandbox.steps import container_running

NAME = "comfy-cli"


def pending(session, install) -> Pending:
    """The comfy-cli installed in the container against the newest on the package index (read-only)."""
    if not container_running(session.wsl(), install, timeout=CHECK_TIMEOUT):
        return NOT_CHECKED
    installed = probe(session, install, ["pip", "show", "comfy-cli"])
    match = re.search(r"^Version:\s*(\S+)", installed.out(), re.MULTILINE) if installed.returncode == 0 else None
    if not match:
        return unknown("could not read the installed comfy-cli version")
    newest = probe(session, install, ["pip", "index", "versions", "comfy-cli"])
    found = re.match(r"\s*comfy-cli \(([^)]+)\)", newest.out()) if newest.returncode == 0 else None
    if not found:
        return unknown("could not reach the package index")
    current, available = match.group(1), found.group(1)
    if version_key(available) > version_key(current):
        return Pending(True, f"comfy-cli {current} -> {available}", current, available)
    return Pending(False, f"comfy-cli {current} is the newest", current, available)


WHAT = "Update comfy-cli"
UPDATE_SECONDS = 30  # expected: one pip upgrade


def prepare(session, install) -> Prepared:
    return prepared(
        NAME,
        in_container(WHAT, UPDATE_SECONDS, ["update", "cli"]),
        Context(session, install),
        updated_in_container(WHAT),
    )


def register(group: typer.Typer) -> None:
    @group.command(NAME)
    def update_comfy_cli(ctx: typer.Context) -> Outcome:
        """Update comfy-cli itself in the container. ComfyUI ends stopped."""
        return run_ending_stopped(ctx.obj, prepare)
