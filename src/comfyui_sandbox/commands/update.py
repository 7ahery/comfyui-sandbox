"""The `update` command group: `comfyui-sandbox update [<target>]`.

Each target is one module in this package with `NAME`, `register(group)` (adds its command),
`prepare(session, install) -> Prepared` (its update as a step table, made ready: what `update all` runs; see
update_support) and `pending(session, install) -> a `Pending` (what bare `update` lists; read-only, never starts
ComfyUI). List the module in `TARGETS` below, in the order `update all` runs them: ComfyUI first, the system and
then the sandbox's setup last. Each target command (and `update all` once, around all of them) announces its plan
(`Output.plan`), stops ComfyUI if it is running, does its work and ends with it stopped
(`steps.ending_stopped`); `Prepared.run` itself only does the work. Nothing here ever updates on its own.
Why it ends stopped: docs/adr/0015. Each target's `data`: docs/scripting.md, "Per-command data".
"""

from __future__ import annotations

from collections.abc import Callable

import typer

from comfyui_sandbox import steps
from comfyui_sandbox.commands import (
    update_comfy_cli,
    update_comfyui,
    update_nodes,
    update_sandbox,
    update_system,
)
from comfyui_sandbox.commands.update_support import Pending, Prepared
from comfyui_sandbox.errors import CliError
from comfyui_sandbox.output import Outcome, PlannedStep
from comfyui_sandbox.runner import CommandTimeout
from comfyui_sandbox.wsl import Wsl

TARGETS = (update_comfyui, update_nodes, update_comfy_cli, update_system, update_sandbox)


def _naming(name: str, completed: list[str], exc: CliError) -> CliError:
    """`exc`, its message and data naming the target `name` that failed and the ones `completed`."""
    exc.message = f"update {name} failed: {exc.message}"
    exc.args = (exc.message,)
    exc.data = {**exc.data, "completed": list(completed), "failed": name}
    return exc


def prepare_all(session, install) -> list[Prepared]:
    """Every target's update made ready, in order. What a target's plan needs is read here, before
    anything changes, so one that can't be made ready fails the command with nothing changed."""
    ready = []
    for target in TARGETS:
        try:
            ready.append(target.prepare(session, install))
        except CliError as exc:
            raise _naming(target.NAME, [], exc) from None
    return ready


def plan_all(ready: list[Prepared]) -> list[PlannedStep]:
    """`update all`'s plan: each target's plan under a step that names the target (HEADER_SECONDS: it
    takes no time of its own), as the plain output has always grouped them."""
    return [
        step for update in ready for step in [PlannedStep(f"update {update.name}", HEADER_SECONDS), *update.planned()]
    ]


HEADER_SECONDS = 0


def run_all(session, install, ready: list[Prepared]) -> Outcome:
    """Every target in order; the first failure stops the run and names the target."""
    results: dict[str, dict] = {}
    for update in ready:
        session.out.step(f"update {update.name}")
        try:
            results[update.name] = update.run().data
        except CliError as exc:
            raise _naming(update.name, list(results), exc) from None
    return Outcome(
        f"Updated every target of {install.distro}: {', '.join(results)}.",
        {"distro": install.distro, "targets": results},
    )


def listing(session, install) -> Outcome:
    """What each target would do and what is pending. Reads, never changes."""
    rows, lines = [], []
    for target in TARGETS:
        try:
            check = target.pending(session, install)
        except CommandTimeout:
            check = Pending(None, f"unknown: the {target.NAME} check timed out (Docker did not answer)")
        rows.append(
            {
                "target": target.NAME,
                "pending": check.pending,
                "detail": check.detail,
                "current": check.current,
                "available": check.available,
                "pinned": check.pinned,
            }
        )
        mark = {True: "pending", False: "current", None: "unknown"}[check.pending]
        lines.append(f"  {target.NAME:<10} {mark:<8} {check.detail}")
    text = "\n".join(
        [
            "Update targets (nothing updates automatically):",
            *lines,
            "Run 'comfyui-sandbox update <target>', or 'comfyui-sandbox update all' for every target.",
        ]
    )
    return Outcome(text, {"distro": install.distro, "targets": rows})


class _Reader:
    """What the targets' pending checks use of a command's session (only `wsl()`), for a check
    made outside any command: no transcript, nothing printed."""

    def __init__(self, deps) -> None:
        self.deps = deps

    def wsl(self) -> Wsl:
        return Wsl(self.deps.runner, self.deps.environ)


def available(deps, install) -> tuple[str, ...]:
    """The names of the targets with an update waiting for `install` (see `pending_checks`)."""
    return tuple(pending_checks(deps, install))


def pending_checks(deps, install, on_found: Callable[[str, Pending], None] | None = None) -> dict[str, Pending]:
    """The targets with an update waiting for `install` (bare `update`'s "pending" ones), each with
    its check (whose `current` and `available` are the versions), in `update all`'s order: for a
    front end to mark the sandbox with, as home does.

    Read-only, like bare `update`, and silent. A target whose check fails or can't tell (unknown)
    isn't named: a mark says an update is there, never that one might be. The checks run in the
    sandbox's container and distro, so a stopped sandbox gets few answers, and asking wakes its
    distro: ask about a running one. Each check may take up to CHECK_TIMEOUT seconds, so
    `on_found(target, check)`, if given, is called as soon as each pending target is found, for a
    front end that shouldn't wait for the slower targets after it.
    """
    reader = _Reader(deps)
    found = {}
    for target in TARGETS:
        try:
            check = target.pending(reader, install)
        except CliError:  # WSL missing, a timeout...: this target can't tell
            continue
        if check.pending:
            found[target.NAME] = check
            if on_found:
                on_found(target.NAME, check)
    return found


def register(app: typer.Typer) -> None:
    group = typer.Typer(
        help="Update part of the sandbox, or list what is pending: comfyui, nodes, comfy-cli, system, setup, or all.",
        invoke_without_command=True,
    )

    @group.callback()
    def update(ctx: typer.Context) -> Outcome | None:
        """With no target: list the targets and what is pending (changes nothing)."""
        if ctx.invoked_subcommand is None:
            session = ctx.obj
            return listing(session, session.resolve_install())
        return None

    for target in TARGETS:
        target.register(group)

    @group.command("all")
    def update_all(ctx: typer.Context) -> Outcome:
        """Update every target in order (comfyui, nodes, comfy-cli, system, sandbox), stops at the first failure. ComfyUI ends stopped."""
        session = ctx.obj
        session.transcript()
        install = session.resolve_install()
        ready = prepare_all(session, install)
        return steps.ending_stopped(session, install, lambda: run_all(session, install, ready), plan_all(ready))

    app.add_typer(group, name="update")
