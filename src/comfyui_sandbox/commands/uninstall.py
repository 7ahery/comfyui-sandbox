"""`comfyui-sandbox uninstall`: delete the distro, its shortcuts and its `installs.json` entry.

First a counts-only report (uninstall_report.sh; no file name or path is ever shown), then the
shared destructive-action gate (`--confirm <sandbox>` or the typed name; exit 8 without it).
Only then: stop the distro (which ends any run window), unregister it, remove the shortcuts and
drop the install. Windows models and `.wslconfig` are never touched. Ports uninstall.sh and
uninstall_report.sh.

The steps after the gate are a step table (comfyui_sandbox.plans), announced as its plan once the
name is confirmed: what runs is then known (a distro already gone has only its leftovers removed).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import typer

from comfyui_sandbox import plans, steps
from comfyui_sandbox.commands.launcher import remove_shortcuts
from comfyui_sandbox.destructive import confirm_destructive
from comfyui_sandbox.errors import CliError
from comfyui_sandbox.output import Outcome
from comfyui_sandbox.steps import project_path

COUNTS = (
    ("workflows", "Workflows"),
    ("input_files", "Input files"),
    ("output_files", "Output files"),
    ("custom_nodes", "Custom nodes"),
    ("backups", "Backups"),
)
LINE = re.compile(r"^([a-z_]+)=(.*)$")
NUMBER = re.compile(r"^\d+$")
DISK = re.compile(r"^[0-9.,]+[A-Za-z]{0,2}$")


def parse_report(text: str) -> dict[str, Any]:
    """Keep only the known `key=value` lines whose value is a number, a disk size or a fixed word.
    Anything else the script printed is dropped, so no name or path can reach the screen."""
    found: dict[str, Any] = {}
    for raw in text.splitlines():
        match = LINE.match(raw.strip())
        if not match:
            continue
        key, value = match.groups()
        if key in {k for k, _ in COUNTS} | {"model_files"} and NUMBER.match(value):
            found[key] = int(value)
        elif (key == "models" and value in ("windows", "wsl", "unknown")) or (key == "disk_used" and DISK.match(value)):
            found[key] = value
    return found


def show_report(out, distro: str, counts: dict[str, Any]) -> None:
    out.info(f"Deleted by the uninstall (everything inside the distro {distro}):")
    for key, label in COUNTS:
        out.info(f"  {label:<28} {counts.get(key, '?'):>6}")
    models = counts.get("models")
    if models == "windows":
        out.info("Kept: models are in a Windows folder, the uninstall doesn't touch it.")
    elif models == "wsl":
        out.info(f"  {'Model files in WSL':<28} {counts.get('model_files', '?'):>6}")
    else:
        out.info("  Models: the models folder could not be determined.")
    out.info(f"  {'Distro disk in use':<28} {counts.get('disk_used', '?'):>6}")


# -- uninstall's step table (comfyui_sandbox.plans), announced once the name is confirmed ---------------


@dataclass
class _Uninstall(steps.Context):
    registered: bool = True  # whether WSL still has the distro (else only the leftovers go)
    removed: list = field(default_factory=list)  # the shortcuts removed


def _registered(context: _Uninstall) -> bool:
    return context.registered


def _stop(context: _Uninstall) -> None:
    context.wsl().terminate(context.install.distro)  # ends any run window with it


def _unregister(context: _Uninstall) -> None:
    wsl, distro = context.wsl(), context.install.distro
    done = wsl.unregister(distro)
    if done.returncode != 0:
        raise CliError(
            f"wsl --unregister {distro} failed (exit {done.returncode}). "
            "The shortcuts and the installs.json entry were left in place."
        )
    if wsl.exists(distro):
        raise CliError(
            f"{distro} is still registered after wsl --unregister. "
            "The shortcuts and the installs.json entry were left in place."
        )


def _forget(context: _Uninstall) -> None:
    session, distro = context.session, context.install.distro
    context.removed = remove_shortcuts(session.deps.winapi, distro)
    installs = session.installs()
    installs.remove(distro)
    installs.save()


def _table(distro: str) -> list[plans.Step[_Uninstall]]:
    # Hand-set seconds: unregistering deletes the distro's virtual disk, which takes a while when it's large.
    return [
        plans.Step(f"Stop {distro}", 5, _stop, when=_registered),
        plans.Step(f"Unregister {distro}", 30, _unregister, when=_registered),
        plans.Step("Remove the shortcuts and the install record", 1, _forget),
    ]


def register(app: typer.Typer) -> None:
    @app.command()
    def uninstall(
        ctx: typer.Context,
        confirm: str | None = typer.Option(
            None, "--confirm", help="Type the sandbox name to allow the deletion (needed with --non-interactive)."
        ),
    ) -> Outcome:
        """Delete the distro (everything in it), its shortcuts and its installs.json entry."""
        session = ctx.obj
        out = session.out
        session.transcript()
        install = session.resolve_install(require_registered=False)
        distro = install.distro
        wsl = session.wsl()
        registered = wsl.exists(distro)

        counts: dict[str, Any] = {}
        if registered:
            done = wsl.run_script(
                distro, install.linux_user, "uninstall_report.sh", {"PROJECT_DIR": project_path(install.linux_user)}
            )
            counts = parse_report(done.out())
            if done.returncode != 0 and not counts:
                out.warn("The report of what will be deleted could not be made.")
            show_report(out, distro, counts)
        else:
            out.info(
                f"The distro {distro} is not registered in WSL (already removed). "
                "Only its shortcuts and its installs.json entry are left to remove."
            )
        out.info("Kept: Windows models folders and the .wslconfig file are not touched.")

        confirm_destructive(
            session,
            [distro],
            confirm=confirm,
            warning=f"Uninstall deletes everything inside the distro {distro} (its virtual disk), "
            "and cannot be undone.",
        )

        context = _Uninstall(session, install, registered=registered)
        chosen = plans.select(_table(distro), context)
        out.plan(plans.planned(chosen))
        context.run(chosen)
        removed = context.removed
        out.info(f"Uninstalled {distro}.")

        notes = []
        if install.location:
            notes.append(
                f"The disk location folder ({install.location}) is now empty but remains. "
                "Delete it yourself if you don't need it."
            )
        if (session.deps.home / ".wslconfig").is_file():
            notes.append("The .wslconfig limits (memory, processors, swap) still apply to your other WSL distros.")
        for note in notes:
            out.info(note)
        data = {
            "distro": distro,
            "unregistered": registered,
            "shortcuts_removed": len(removed),
            "report": counts,
            "location": install.location,
            "wslconfig_kept": (session.deps.home / ".wslconfig").is_file(),
        }
        return Outcome(f"Uninstalled {distro}.", data)
