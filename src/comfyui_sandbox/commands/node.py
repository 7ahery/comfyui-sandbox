"""`comfyui-sandbox node install <registry-id|git-url>` (ticket 21).

A registry id is installed by comfy-cli (`comfy --skip-prompt node install <id>`), a git URL by the
Manager CLI (`cm-cli install --exit-on-fail <url>`, which works at the Manager's default security
level `normal`: this command never changes the level). Either way ComfyUI is then restarted and
the container log is checked for an import failure of that node (exit 1 when it failed to load).
Like every command that changes the sandbox it ends with ComfyUI stopped (`steps.ending_stopped`): the
install step starts it just for itself.
"""

from __future__ import annotations

import re
from collections.abc import Collection

import typer

from comfyui_sandbox import steps
from comfyui_sandbox.container import COMFYUI_PATH, comfy_command, run_in_container
from comfyui_sandbox.errors import CliError, UsageError
from comfyui_sandbox.output import Outcome
from comfyui_sandbox.steps import project_path

INSTALL_TIMEOUT = 1800
LOG_LINES = 500
REGISTRY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(@[A-Za-z0-9._-]+)?")
FAILED = re.compile(r"\(IMPORT FAILED\):\s*(?P<path>\S.*?)\s*$")
CANNOT = re.compile(r"Cannot import (?P<path>\S+) module for custom nodes")


def is_git_url(target: str) -> bool:
    return "://" in target or target.startswith("git@")


def validate(target: str) -> None:
    if not target or target.startswith("-") or any(c.isspace() for c in target):
        raise UsageError(f"'{target}' is not a node registry id or a git URL.")
    if not is_git_url(target) and not REGISTRY_ID.fullmatch(target):
        raise UsageError(
            f"'{target}' is not a node registry id (letters, digits, - _ . and an optional @version) or a git URL."
        )


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def folder_key(target: str) -> str:
    """What the node's folder under custom_nodes should look like (compared ignoring case and punctuation)."""
    if is_git_url(target):
        name = re.split(r"[/:]", target.rstrip("/"))[-1].removesuffix(".git")
    else:
        name = target.split("@")[0]
    return _key(name)


def custom_nodes(wsl, install) -> set[str]:
    """The folder names under the install's `custom_nodes` (empty when it can't be listed)."""
    done = wsl.run_command(
        install.distro,
        install.linux_user,
        ["ls", "-1", f"{project_path(install.linux_user)}/data/ComfyUI/custom_nodes"],
    )
    return {line.strip() for line in done.out().splitlines() if line.strip()} if done.returncode == 0 else set()


def import_failures(log: str, target: str, new_folders: Collection[str] = ()) -> list[str]:
    """The log lines that say this node failed to import (empty when it imported).

    ComfyUI prints `Cannot import <path> module for custom nodes: <why>` and later
    `<n> seconds (IMPORT FAILED): <path>`; both are kept for the node's folder. The folder is the one
    whose name looks like `target`, or one the install just added to `custom_nodes` (`new_folders`):
    a registry id does not always match its folder name.
    """
    wanted = folder_key(target)
    lines = log.splitlines()
    failed = {m["path"] for line in lines if (m := FAILED.search(line))}
    mine = {
        path for path in failed if (name := path.rstrip("/").rsplit("/", 1)[-1]) in new_folders or _key(name) == wanted
    }
    return [
        line.strip()
        for line in lines
        if (found := FAILED.search(line) or CANNOT.search(line)) and found["path"] in mine
    ]


def register(app: typer.Typer) -> None:
    node = typer.Typer(help="Manage custom nodes.", no_args_is_help=True)
    app.add_typer(node, name="node")

    @node.command("install")
    def node_install(
        ctx: typer.Context,
        target: str = typer.Argument(
            ..., metavar="REGISTRY_ID|GIT_URL", help="A node's registry id (comfy-cli) or a git URL (Manager CLI)."
        ),
    ) -> Outcome:
        """Install a custom node and report whether it imported. ComfyUI ends stopped."""
        session = ctx.obj
        validate(target)
        session.transcript()
        install = session.resolve_install()
        out, wsl = session.out, session.wsl()
        via = "cm-cli" if is_git_url(target) else "comfy-cli"

        def install_node() -> Outcome:
            with steps.container_up(wsl, out, install, for_step=True):
                before = custom_nodes(wsl, install)
                out.step(f"Installing {target} with {via}")
                if via == "cm-cli":
                    done = run_in_container(
                        wsl,
                        install,
                        ["cm-cli", "install", "--exit-on-fail", target],
                        env={"COMFYUI_PATH": COMFYUI_PATH},
                        workdir=COMFYUI_PATH,
                        on_line=out.progress,
                        timeout=INSTALL_TIMEOUT,
                    )
                else:
                    done = run_in_container(
                        wsl,
                        install,
                        comfy_command(["node", "install", target]),
                        on_line=out.progress,
                        timeout=INSTALL_TIMEOUT,
                    )
                if done.returncode != 0:
                    raise CliError(
                        f"{via} could not install {target} (exit {done.returncode}). ComfyUI was not restarted.",
                        data={"node": target, "via": via, "exit_code": done.returncode},
                    )

                new_folders = sorted(custom_nodes(wsl, install) - before)
                out.step("Restarting ComfyUI")
                steps.restart_container(wsl, out, install)
                steps.wait_after_restart(wsl, out, install)

                out.step("Checking that the node imported")
                logs = wsl.run_command(
                    install.distro,
                    install.linux_user,
                    steps.compose_argv(install, "logs", "--no-color", "--tail", str(LOG_LINES)),
                )
                failures = import_failures(logs.out(), target, new_folders)
                data = {
                    "distro": install.distro,
                    "node": target,
                    "via": via,
                    "imported": not failures,
                    "new_folders": new_folders,
                }
                if failures:
                    raise CliError(
                        f"{target} was installed but failed to import:\n" + "\n".join(failures),
                        data={**data, "import_errors": failures},
                    )
                return Outcome(f"{target} is installed and imported.", data)

        return steps.ending_stopped(session, install, install_node)
