"""`comfyui-sandbox backup` / `restore`.

`backup` saves a custom-node snapshot (comfy-cli, inside the container) and a tar of `user/`,
`input/` and `output/` from the project's `data/` into `~/comfyui-backups` in the distro (outside
`data/`, so it survives `recreate`). `restore` is destructive (the shared gate): it stops
ComfyUI, extracts the tar over `data/` (same-named files are overwritten, nothing else is
deleted), starts it, reinstalls the snapshot's nodes and restarts so they load. With no
`--backup` it asks which backup in a terminal, even when there is only one, so the user sees
which (newest first, the newest the default; home's Restore… is this question); `--yes` and
`--non-interactive` take the newest.

`backup` changes nothing, so it leaves ComfyUI as it found it: it starts it when it is stopped
(`steps.container_up`) and stops it again afterwards, and leaves a running one running. `restore`
changes the sandbox, so it follows the rule every such command follows (`steps.ending_stopped`): it
ends with ComfyUI stopped and says so, and an open run window closes (it says how to reopen it).

Both are step tables (comfyui_sandbox.plans): each announces its plan before its first step, and
backup's outcome names the `restore` command line that brings that backup back.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

import typer

from comfyui_sandbox import plans, steps
from comfyui_sandbox.cli_command import PROG
from comfyui_sandbox.container import COMFYUI_PATH, comfy_command, run_in_container
from comfyui_sandbox.destructive import confirm_destructive
from comfyui_sandbox.errors import CliError
from comfyui_sandbox.output import Outcome
from comfyui_sandbox.state import Install
from comfyui_sandbox.steps import LONG_STEP_TIMEOUT, START_FOR_STEP, backup_stamp, compose_argv, ensure_ok, project_path

SNAPSHOT = f"{COMFYUI_PATH}/user/node-snapshot.json"
KEEP = ("ComfyUI/user", "ComfyUI/input", "ComfyUI/output")
BACKUP_NAME = re.compile(r"^comfyui-[0-9]{8}-[0-9]{4,6}\.tar$")
WAIT_MINUTES = 10
WHICH_BACKUP = "Which backup do you want to restore?"


def backups_dir(install: Install) -> str:
    return f"/home/{install.linux_user}/comfyui-backups"


def made(name: str) -> str:
    """When backup `name` was made, from its name's UTC stamp: "1 Mar 2026, 09:00 UTC"."""
    when = datetime.strptime(name[len("comfyui-") :][:13], "%Y%m%d-%H%M").replace(tzinfo=UTC)
    return f"{when.day} {when:%b %Y, %H:%M} UTC"


def _which_backup(session, available: list[str]) -> str:
    """The backup to restore when none was named: asked in a terminal, even when there is only
    one, so the user sees which; else the newest. available: the backups, oldest first (their stamps sort by time)."""
    newest = available[-1]
    if session.non_interactive or session.yes:
        return newest
    choices = available[::-1]
    return session.deps.prompter.select(
        WHICH_BACKUP,
        choices,
        default=newest,
        hint="Newest first, they're kept in ~/comfyui-backups.",
        hints={name: f"made {made(name)}" + (" · the newest" if name == newest else "") for name in choices},
    )


def _wait_restored(session, install: Install, what: str) -> None:
    steps.wait_for_comfyui(
        session.wsl(),
        session.out,
        install,
        WAIT_MINUTES,
        what,
        retry="Safe to retry, the restored files are already in place.",
    )


def _compose(session, install: Install, verb: str) -> None:
    done = session.wsl().run_command(
        install.distro, install.linux_user, compose_argv(install, verb), timeout=LONG_STEP_TIMEOUT
    )
    ensure_ok(done, f"docker compose {verb}")


# -- backup's step table (comfyui_sandbox.plans) ------------------------------------------------------


@dataclass
class _Backup(steps.Context):
    name: str = ""  # the tar's file name, chosen up front: the plan names it
    was_running: bool = True  # whether ComfyUI ran when the backup began
    started: dict = field(default_factory=lambda: {"started_comfyui": False})  # container_up's state


def _stopped(context: _Backup) -> bool:
    return not context.was_running


def _start(context: _Backup) -> None:
    # Started for the backup only, and stopped again once its last step has run (also on a failure).
    context.started = context.stack.enter_context(
        steps.container_up(context.wsl(), context.out, context.install, announce=False)
    )


def _wait_started(context: _Backup) -> None:
    # `up -d` returns while the entrypoint is still running; the snapshot needs it done.
    steps.wait_after_restart(context.wsl(), context.out, context.install)


def _snapshot(context: _Backup) -> None:
    done = run_in_container(
        context.wsl(),
        context.install,
        comfy_command(["node", "save-snapshot", "--output", SNAPSHOT]),
        timeout=LONG_STEP_TIMEOUT,
    )
    ensure_ok(done, "Saving the node snapshot")


def _write(context: _Backup) -> None:
    wsl, install, folder = context.wsl(), context.install, backups_dir(context.install)
    ensure_ok(wsl.run_command(install.distro, install.linux_user, ["mkdir", "-p", folder]), f"Creating {folder}")
    done = wsl.run_command(
        install.distro,
        install.linux_user,
        ["tar", "-C", f"{project_path(install.linux_user)}/data", "-cf", f"{folder}/{context.name}", *KEEP],
        timeout=LONG_STEP_TIMEOUT,
    )
    ensure_ok(done, "Writing the backup")


def _backup_table(name: str) -> list[plans.Step[_Backup]]:
    """backup's steps; a stopped ComfyUI is started for them first (the stop after is no step: quick)."""
    return [
        plans.Step("Start ComfyUI for the backup", 20, _start, when=_stopped),
        plans.Step("Wait for ComfyUI to answer", 45, _wait_started, when=_stopped),
        plans.Step("Save the custom-node snapshot", 15, _snapshot),
        plans.Step(f"Write {name}", 60, _write),  # user/, input/ and output/: the outputs can be large
    ]


# -- restore's step table -------------------------------------------------------------------------------


@dataclass
class _Restore(steps.Context):
    name: str = ""  # the backup restored


def _extract(context: _Restore) -> None:
    # ComfyUI is stopped here (`ending_stopped` stopped it), so the files can be replaced.
    install = context.install
    done = context.wsl().run_command(
        install.distro,
        install.linux_user,
        ["tar", "-C", f"{project_path(install.linux_user)}/data", "-xf", f"{backups_dir(install)}/{context.name}"],
        timeout=LONG_STEP_TIMEOUT,
    )
    ensure_ok(done, "Extracting the backup")


def _start_restored(context: _Restore) -> None:
    context.start_for_these_steps()


def _wait_after_start(context: _Restore) -> None:
    _wait_restored(context.session, context.install, "after the restore")


def _reinstall_nodes(context: _Restore) -> None:
    done = run_in_container(
        context.wsl(), context.install, comfy_command(["node", "restore-snapshot", SNAPSHOT]), timeout=LONG_STEP_TIMEOUT
    )
    ensure_ok(done, "Reinstalling the custom nodes (the files are restored)")


def _restart(context: _Restore) -> None:
    _compose(context.session, context.install, "restart")


def _wait_after_restart(context: _Restore) -> None:
    _wait_restored(context.session, context.install, "after the restart")


def _restore_table(name: str) -> list[plans.Step[_Restore]]:
    """restore's steps, after `ending_stopped` has stopped ComfyUI. Every step after the extract
    needs the container, so it's started for them (and stopped once they've run)."""
    return [
        plans.Step(f"Restore {name}", 60, _extract),
        plans.Step(START_FOR_STEP.title, START_FOR_STEP.seconds, _start_restored),
        plans.Step("Wait for ComfyUI to answer", 45, _wait_after_start),
        plans.Step("Reinstall the custom nodes from the snapshot", 180, _reinstall_nodes),  # downloads them
        plans.Step("Restart ComfyUI to load the nodes", 20, _restart),
        plans.Step("Wait for ComfyUI to answer again", 45, _wait_after_restart),
    ]


def register(app: typer.Typer) -> None:
    @app.command()
    def backup(ctx: typer.Context) -> Outcome:
        """Save a custom-node snapshot and a tar of user/, input/ and output/ into ~/comfyui-backups."""
        session = ctx.obj
        out = session.out
        session.transcript()
        install = session.resolve_install()
        context = _Backup(
            session,
            install,
            name=f"comfyui-{backup_stamp()}.tar",
            was_running=steps.container_running(session.wsl(), install),
        )
        chosen = plans.select(_backup_table(context.name), context)
        out.plan(plans.planned(chosen))
        context.run(chosen)
        folder, name = backups_dir(install), context.name
        out.info(f"Backup saved in the distro: {folder}/{name}")
        out.info("Copy it from \\\\wsl.localhost\\" + install.distro + folder.replace("/", "\\"))
        # The exact command that brings this backup back, so a front end (home's Back up) can show it.
        # It always names the sandbox: it may be run later, when there are others.
        restore = f"{PROG} restore --distro {install.distro} --backup {name}"
        return Outcome(
            f"Backed up to {name}. Restore it with '{restore}'.",
            {
                "distro": install.distro,
                "name": name,
                "path": f"{folder}/{name}",
                "snapshot": SNAPSHOT,
                "restore": restore,
                "started_comfyui": context.started["started_comfyui"],
            },
        )

    @app.command()
    def restore(
        ctx: typer.Context,
        backup_name: str | None = typer.Option(
            None, "--backup", help="Backup to restore (default: asked, the newest with --yes)."
        ),
        confirm: str | None = typer.Option(
            None, "--confirm", help="Type the sandbox name to allow the restore (needed with --non-interactive)."
        ),
    ) -> Outcome:
        """Restore a backup over the current work and reinstall the snapshot's nodes. ComfyUI ends stopped."""
        session = ctx.obj
        session.transcript()
        install = session.resolve_install()
        distro, user = install.distro, install.linux_user
        wsl = session.wsl()
        folder = backups_dir(install)

        done = wsl.run_command(distro, user, ["ls", "-1", folder])
        available = sorted(n.strip() for n in done.out().splitlines() if BACKUP_NAME.match(n.strip()))
        if backup_name is None:
            if not available:
                # data says it by meaning, for a front end (home's Restore…) that says so rather than fails
                raise CliError(
                    f"There is no backup in {folder}. Run 'comfyui-sandbox backup' first.", data={"backups": []}
                )
            backup_name = _which_backup(session, available)
        elif backup_name not in available:
            raise CliError(
                f"There is no backup named {backup_name} in {folder}. Available: {', '.join(available) or 'none'}."
            )

        confirm_destructive(
            session,
            [distro],
            confirm=confirm,
            warning=f"Restoring {backup_name} overwrites the workflows, settings, input and output files "
            f"in {distro} that have the same names, and cannot be undone.",
        )

        context = _Restore(session, install, name=backup_name)
        chosen = plans.select(_restore_table(backup_name), context)

        def restore_backup() -> Outcome:
            context.run(chosen)
            return Outcome(
                f"Restored {backup_name}.", {"distro": distro, "name": backup_name, "path": f"{folder}/{backup_name}"}
            )

        return steps.ending_stopped(session, install, restore_backup, plans.planned(chosen))
