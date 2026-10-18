"""`comfyui-sandbox prereqs`: get WSL ready without an admin shell.

Elevates through UAC (winapi.run_elevated) to run `wsl --install --no-distribution`
when WSL is missing, or `wsl --update` when it is too old or broken. Asks first (`--yes`
approves; `--non-interactive` without it exits 9). Reports whether a reboot is needed;
never reboots.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

import typer

from comfyui_sandbox import hostinfo
from comfyui_sandbox.commands.check import SUMMARY
from comfyui_sandbox.errors import AnswerMissing, CliError, ExitCode
from comfyui_sandbox.output import Outcome
from comfyui_sandbox.runner import Runner
from comfyui_sandbox.winapi import ElevationDeclined
from comfyui_sandbox.wsl import INSTALL_ARGV, MIN_VERSION, UPDATE_ARGV, Wsl

REBOOT_REQUIRED = 3010  # ERROR_SUCCESS_REBOOT_REQUIRED


@dataclass(frozen=True)
class Plan:
    action: str  # "install" | "update" | "none"
    reason: str


WHY = {
    "install": "The sandbox runs ComfyUI inside WSL (Windows Subsystem for Linux), and installing WSL needs administrator rights.",
    "update": "The sandbox runs ComfyUI inside WSL (Windows Subsystem for Linux), and updating WSL needs administrator rights.",
}

MINIMUM = ".".join(map(str, MIN_VERSION))


def _plan(wsl: Wsl, runner: Runner) -> Plan:
    """The same WSL tests as `check`: missing WSL or Virtual Machine Platform means install
    (it turns the feature on); broken or too old WSL means update."""
    if not wsl.installed():
        return Plan("install", "WSL is not installed")
    if hostinfo.probe(runner).vmp_state in (2, 3):
        return Plan("install", "Virtual Machine Platform is off")
    version = wsl.version()
    if version.number is None:
        return Plan("update", f"WSL isn't working ('wsl --version' exit {version.returncode})")
    if version.number < MIN_VERSION:
        return Plan("update", f"WSL {version.text} is older than {MINIMUM}")
    return Plan("none", f"WSL {version.text} is ready")


def register(app: typer.Typer) -> None:
    @app.command()
    def prereqs(ctx: typer.Context) -> Outcome:
        """Install or update WSL through UAC, after asking (--yes skips the question; never reboots)."""
        session = ctx.obj
        deps = session.deps
        if deps.platform != "win32":
            raise CliError(SUMMARY[ExitCode.UNSUPPORTED_HOST], ExitCode.UNSUPPORTED_HOST)
        log = session.transcript()
        plan = _plan(session.wsl(), deps.runner)
        log.info(plan.reason)
        session.out.info(plan.reason + ".")
        if plan.action == "none":
            return Outcome("WSL is ready, nothing to do.", {"action": "none", "reboot_needed": False})
        argv = list(INSTALL_ARGV if plan.action == "install" else UPDATE_ARGV)
        command = subprocess.list2cmdline(argv)
        question = f"Run '{command}' as administrator? (Windows will ask you to approve it)"
        if not session.yes and not session.non_interactive:
            session.out.info(WHY[plan.action])
        if not session.yes:
            if session.non_interactive:
                raise AnswerMissing(
                    f"This needs your OK to run '{command}' as administrator. Nothing was changed. "
                    "Re-run with --yes to approve it.",
                    "--yes",
                    data={"action": plan.action, "reboot_needed": False},
                )
            if not session.deps.prompter.confirm(question, True):
                raise CliError(
                    "WSL was not changed. Run prereqs again and answer yes to continue.",
                    data={"action": plan.action, "reboot_needed": False},
                )
        log.step(f"{plan.action.capitalize()} WSL (elevated)")
        log.run(argv)
        session.out.info(
            f"Running '{command}' as administrator: "
            "choose Yes in the Windows (UAC) prompt, then wait for its window to close."
        )
        try:
            returncode = deps.winapi.run_elevated(argv)
        except ElevationDeclined as exc:
            raise CliError(
                f"{exc.message} WSL was not changed. Run prereqs again and choose Yes in the Windows prompt.",
                data={"action": plan.action, "reboot_needed": False},
            ) from exc
        log.exit(returncode)
        data = {"action": plan.action, "reboot_needed": False}
        if returncode not in (0, REBOOT_REQUIRED):
            raise CliError(f"'{subprocess.list2cmdline(argv)}' failed with exit code {returncode}.", data=data)
        # Installing WSL turns on Windows features that only start after a reboot.
        data["reboot_needed"] = plan.action == "install" or returncode == REBOOT_REQUIRED
        done = "installed" if plan.action == "install" else "updated"
        then = "Reboot Windows, then run" if data["reboot_needed"] else "Next, run"
        return Outcome(f"WSL {done}. {then} 'comfyui-sandbox check'.", data)
