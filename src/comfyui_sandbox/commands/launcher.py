"""`comfyui-sandbox launcher install|remove`: Desktop and Start menu shortcuts.

Each shortcut is named `ComfyUI (<distro>)` and runs `comfyui-sandbox run --distro <distro>`.
Shortcuts are made through winapi. Ports install_launcher.sh.
"""

from __future__ import annotations

import re
from pathlib import PureWindowsPath

import typer

from comfyui_sandbox import cli_command
from comfyui_sandbox.errors import CliError
from comfyui_sandbox.output import Outcome
from comfyui_sandbox.winapi import Folder, Shortcut, WinApi

FOLDERS: tuple[Folder, ...] = ("desktop", "programs")
_DISTRO_NAME = re.compile(r"[A-Za-z0-9._-]+")


def shortcut_name(distro: str) -> str:
    return f"ComfyUI ({distro}).lnk"


def shortcut_paths(winapi: WinApi, distro: str) -> list[PureWindowsPath]:
    return [winapi.special_folder(folder) / shortcut_name(distro) for folder in FOLDERS]


def install_shortcuts(winapi: WinApi, distro: str) -> list[PureWindowsPath]:
    """Create (or replace) the Desktop and Start menu shortcuts for `distro`."""
    if winapi.is_packaged_app():
        raise CliError(
            "The launcher can't be installed from a packaged (MSIX) app: a shortcut would point "
            "at a private AppData copy that Explorer can't see. Reinstall comfyui-sandbox from "
            "your own terminal (PowerShell or Windows Terminal), then run "
            "'comfyui-sandbox launcher install' there."
        )
    exe, arguments = cli_command.shortcut_command(distro)
    paths = shortcut_paths(winapi, distro)
    for path in paths:
        winapi.create_shortcut(
            Shortcut(
                path=path,
                target=exe,
                arguments=arguments,
                working_dir=str(PureWindowsPath(exe).parent),
                description=f"Start the sandboxed ComfyUI ({distro}), close the window to stop it",
                icon=r"C:\Windows\System32\wsl.exe,0",
            )
        )
    return paths


def remove_shortcuts(winapi: WinApi, distro: str) -> list[PureWindowsPath]:
    """Delete the shortcuts for `distro`; returns the ones that existed."""
    return [path for path in shortcut_paths(winapi, distro) if winapi.remove_shortcut(path)]


def register(app: typer.Typer) -> None:
    launcher = typer.Typer(help="Desktop and Start menu shortcuts that start ComfyUI.", no_args_is_help=True)
    app.add_typer(launcher, name="launcher")

    @launcher.command("install")
    def install(ctx: typer.Context) -> Outcome:
        """Create the 'ComfyUI (<distro>)' shortcuts on the Desktop and in the Start menu."""
        session = ctx.obj
        session.transcript()
        distro = session.resolve_install().distro
        paths = install_shortcuts(session.deps.winapi, distro)
        for path in paths:
            session.out.info(f"Shortcut {path}")
        return Outcome(
            f"Created {len(paths)} shortcuts for {distro}.", {"distro": distro, "shortcuts": [str(p) for p in paths]}
        )

    @launcher.command("remove")
    def remove(ctx: typer.Context) -> Outcome:
        """Delete the shortcuts (already-missing ones are fine)."""
        session = ctx.obj
        session.transcript()
        # The one command that takes a distro name outside installs.json (docs/scripting.md, "Global options and choosing a sandbox"): the install
        # record may be gone already (uninstall drops it first). It only ever deletes the shortcuts this CLI
        # names `ComfyUI (<distro>).lnk` in the two folders, so the name must be a plausible distro name.
        distro = session.distro or session.resolve_install().distro
        if not _DISTRO_NAME.fullmatch(distro):
            raise CliError(f"'{distro}' is not a valid distro name, so no shortcuts were removed.")
        removed = remove_shortcuts(session.deps.winapi, distro)
        for path in removed:
            session.out.info(f"Removed {path}")
        message = (
            f"Removed {len(removed)} shortcuts for {distro}."
            if removed
            else f"No shortcuts for {distro}, nothing to remove."
        )
        return Outcome(message, {"distro": distro, "removed": [str(p) for p in removed]})
