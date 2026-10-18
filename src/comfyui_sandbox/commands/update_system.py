"""`comfyui-sandbox update system`: update Docker Engine (and the NVIDIA container toolkit on the
NVIDIA path only) inside the distro, as root, then restart Docker.

Like every command that changes the sandbox it ends with ComfyUI stopped and says to run `start`
(`steps.ending_stopped`, docs/adr/0015). Restarting Docker stops ComfyUI anyway.
"""

from __future__ import annotations

import typer

from comfyui_sandbox import plans
from comfyui_sandbox.commands.update_support import (
    CHECK_TIMEOUT,
    Context,
    Pending,
    Prepared,
    prepared,
    run_ending_stopped,
    unknown,
)
from comfyui_sandbox.output import Outcome
from comfyui_sandbox.steps import LONG_STEP_TIMEOUT, ensure_ok
from comfyui_sandbox.wsl import ROOT

NAME = "system"
DOCKER_PACKAGES = ("docker-ce", "docker-ce-cli", "containerd.io", "docker-buildx-plugin", "docker-compose-plugin")
NVIDIA_PACKAGES = ("nvidia-container-toolkit",)
APT_ENV = {"DEBIAN_FRONTEND": "noninteractive"}


def packages(install) -> tuple[str, ...]:
    return DOCKER_PACKAGES + (NVIDIA_PACKAGES if install.gpu.mode == "nvidia" else ())


def pending(session, install) -> Pending:
    """Which of the packages `update system` would touch have an upgrade (as of the last `apt update`)."""
    done = session.wsl().run_command(
        install.distro, install.linux_user, ["apt", "list", "--upgradable"], timeout=CHECK_TIMEOUT
    )
    if done.returncode != 0:
        return unknown("could not read the list of upgradable packages")
    names = [line.split("/")[0] for line in done.out().splitlines() if "/" in line]
    found = [name for name in names if name in packages(install)]
    if found:
        return Pending(True, f"upgrades available: {', '.join(found)}")
    return Pending(
        False,
        "Docker Engine"
        + (" and the NVIDIA toolkit" if install.gpu.mode == "nvidia" else "")
        + " up to date (as of the last apt update)",
    )


APT = ["apt-get", "-y", "-o", "Dpkg::Options::=--force-confdef", "-o", "Dpkg::Options::=--force-confold"]


def _refresh(context: Context) -> None:
    context.keep_distro_up()  # until Docker has restarted
    ensure_ok(
        context.wsl().run_command(
            context.install.distro,
            ROOT,
            ["apt-get", "update"],
            APT_ENV,
            on_line=context.out.progress,
            timeout=LONG_STEP_TIMEOUT,
        ),
        "apt-get update",
    )


def _upgrade(context: Context) -> None:
    ensure_ok(
        context.wsl().run_command(
            context.install.distro,
            ROOT,
            [*APT, "install", "--only-upgrade", *packages(context.install)],
            APT_ENV,
            on_line=context.out.progress,
            timeout=LONG_STEP_TIMEOUT,
        ),
        "Upgrading Docker",
    )


def _restart_docker(context: Context) -> None:
    ensure_ok(
        context.wsl().run_command(
            context.install.distro, ROOT, ["systemctl", "restart", "docker"], on_line=context.out.progress, timeout=600
        ),
        "systemctl restart docker",
    )


def _updated(context: Context) -> Outcome:
    install = context.install
    nvidia = install.gpu.mode == "nvidia"
    return Outcome(
        f"Updated Docker Engine{' and the NVIDIA toolkit' if nvidia else ''} in {install.distro}.",
        {"distro": install.distro, "packages": list(packages(install)), "nvidia_toolkit": nvidia},
    )


def prepare(session, install) -> Prepared:
    """The update, made ready. The upgrade's title names the packages, which follow the GPU mode."""
    table = [
        plans.Step("Refresh the package lists", 20, _refresh),
        plans.Step("Upgrade " + ", ".join(packages(install)), 90, _upgrade),
        plans.Step("Restart Docker", 15, _restart_docker),
    ]
    return prepared(NAME, table, Context(session, install), _updated)


def register(group: typer.Typer) -> None:
    @group.command(NAME)
    def update_system(ctx: typer.Context) -> Outcome:
        """Update Docker Engine (and the NVIDIA toolkit on NVIDIA) inside the distro. ComfyUI ends stopped."""
        return run_ending_stopped(ctx.obj, prepare)
