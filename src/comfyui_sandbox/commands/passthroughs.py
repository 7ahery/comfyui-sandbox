"""`comfy`, `cm-cli` and `exec` (ticket 17): escape hatches that pass their arguments through unchanged.

`comfy` runs `comfy --skip-prompt <args>` and `cm-cli` the Manager CLI (with COMFYUI_PATH and the
working directory set) in the container; `exec [--root] -- <cmd>` runs a raw command in the distro.
Global options only count before the command name (cli.PASSTHROUGH), `--help` is not intercepted,
and the inner exit code is the CLI's exit code.
"""

from __future__ import annotations

import typer

from comfyui_sandbox.container import COMFYUI_PATH, comfy_command, run_in_container
from comfyui_sandbox.errors import CliError, UsageError
from comfyui_sandbox.output import Outcome
from comfyui_sandbox.runner import Completed
from comfyui_sandbox.wsl import ROOT

SETTINGS = {
    "allow_extra_args": True,
    "ignore_unknown_options": True,
    "allow_interspersed_args": False,
    "help_option_names": [],
}


def relay(session, name: str, done: Completed) -> Outcome:
    """Hand the inner command's output and exit code back.

    Human mode: its stdout is this command's stdout, its stderr goes to stderr. `--json`: both are in
    `data` with the exit code. A non-zero exit becomes this command's exit code.
    """
    stdout, stderr = done.out(), done.err()
    data = {"exit_code": done.returncode, "stdout": stdout, "stderr": stderr}
    if not session.json:
        if stdout:
            session.deps.stdout.write(stdout if stdout.endswith("\n") else stdout + "\n")
            session.deps.stdout.flush()
        for line in stderr.splitlines():
            session.out.info(line)
    if done.returncode != 0:
        raise CliError(f"{name} exited {done.returncode}.", code=done.returncode, data=data)
    return Outcome(f"{name} exited 0." if session.json else "", data)


def register(app: typer.Typer) -> None:
    @app.command("comfy", context_settings=SETTINGS)
    def comfy(ctx: typer.Context) -> Outcome:
        """Run comfy-cli in the container: `comfy --skip-prompt <args...>` (arguments pass through unchanged)."""
        session = ctx.obj
        session.transcript()
        install = session.resolve_install()
        done = run_in_container(
            session.wsl(), install, comfy_command(ctx.args), timeout=None
        )  # the user's own command: no limit
        return relay(session, "comfy", done)

    @app.command("cm-cli", context_settings=SETTINGS)
    def cm_cli(ctx: typer.Context) -> Outcome:
        """Run the ComfyUI-Manager CLI in the container, with COMFYUI_PATH and the working directory set."""
        session = ctx.obj
        session.transcript()
        install = session.resolve_install()
        done = run_in_container(
            session.wsl(),
            install,
            ["cm-cli", *ctx.args],
            env={"COMFYUI_PATH": COMFYUI_PATH},
            workdir=COMFYUI_PATH,
            timeout=None,
        )
        return relay(session, "cm-cli", done)

    @app.command("exec", context_settings=SETTINGS)
    def exec_(
        ctx: typer.Context,
        root: bool = typer.Option(False, "--root", help="Run as root instead of the Linux user."),
    ) -> Outcome:
        """Run a raw command in the distro: `exec [--root] -- <cmd...>` (the Linux user by default)."""
        session = ctx.obj
        if not ctx.args:
            raise UsageError("exec needs a command: comfyui-sandbox exec [--root] -- <cmd...>")
        session.transcript()
        install = session.resolve_install()
        done = session.wsl().run_command(install.distro, ROOT if root else install.linux_user, ctx.args, timeout=None)
        return relay(session, "exec", done)
