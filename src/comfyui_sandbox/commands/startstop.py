"""`comfyui-sandbox start` / `stop` / `open` (docs/scripting.md, "`run`, `start`, `stop` and `open`").

`start` opens a detached `run` window (or, if one exists, only the browser) and waits until Windows
reaches the container. `stop` runs `docker compose stop`; the run window sees the container stop
and closes itself, so `stop` never touches the window or its marker. `open` only opens a running
sandbox's ComfyUI in the browser: it starts nothing, so it's safe to run on a whim (home's Open in
browser), and it fails, saying how to start it, when the sandbox's own ComfyUI doesn't answer.
"""

from __future__ import annotations

import typer

from comfyui_sandbox import cli_command, ports, reach, steps
from comfyui_sandbox.cli_command import PROG
from comfyui_sandbox.errors import CliError, NotReady
from comfyui_sandbox.instance import instance_name
from comfyui_sandbox.output import Outcome
from comfyui_sandbox.state import Install

START_TRIES = 200  # x START_PAUSE: a little over `run`'s 10 minute readiness wait
START_PAUSE = 3
# A run window that still has not taken its run-window marker after this many polls has died.
WINDOW_GRACE_POLLS = 5
# The step start says while it waits for the run window's ComfyUI (install plans it as its last).
START_STEP = "Starting ComfyUI in a new window"


def start_comfyui(session, install: Install, *, from_env: bool = True) -> Outcome:
    """Open a run window for `install` (or only the browser if one exists) and wait until Windows reaches it.
    `install` runs this as its last step, with `from_env=False`: it has just written `.env` itself."""
    deps, out = session.deps, session.out
    url = f"http://localhost:{install.port}"
    if deps.winapi.instance_exists(instance_name(install.distro)):
        # A running ComfyUI keeps the port it started with: the record's.
        deps.winapi.open_url(url)
        out.info(f"ComfyUI is already running: opened {url}.")
        return Outcome(
            f"ComfyUI is already running, opened {url}.",
            {"distro": install.distro, "url": url, "already_running": True},
        )
    port = ports.port_to_start(session, install, fix=ports.PORT_FIX) if from_env else install.port
    url = f"http://localhost:{port}"
    data = {"distro": install.distro, "url": url}
    deps.winapi.open_console(cli_command.run_argv(install.distro))
    out.step(START_STEP)
    for attempt in range(START_TRIES):
        found = reach.check_windows_reach(deps, port)
        if found.ok:
            ports.record_port(session, install, port)
            return Outcome(f"ComfyUI is running at {url}.", {**data, "already_running": False})
        if found.os in reach.WINDOWS_OS:
            hint = f" {found.hint}" if found.hint else ""
            raise CliError(
                f"{found.message[0].upper()}{found.message[1:]}.{hint}".replace("..", "."),
                data={"windows_reach": found.to_json()},
            )
        if attempt >= WINDOW_GRACE_POLLS and not deps.winapi.instance_exists(instance_name(install.distro)):
            raise CliError(
                "The ComfyUI run window closed before ComfyUI answered. "
                f"Read what it did with '{PROG} logs --cli --last' "
                f"(and '{PROG} logs' for the container)."
            )
        if attempt < START_TRIES - 1:
            deps.sleep(START_PAUSE)
    raise NotReady(
        "ComfyUI is not reachable yet. It may still be starting (the run window shows progress): "
        + steps.keep_waiting_hint("start", session, install.distro)
    )


def open_comfyui(session, install: Install) -> Outcome:
    """Open `install`'s ComfyUI in the browser, if its own ComfyUI answers on its port.

    It asks the port first, with the same reach check `run` and `start` use, so it never opens a
    page that would fail to load, or a ComfyUI on Windows that happens to hold the port. It never
    starts anything: `start` is the command that does, and the failure names it. The port is the
    record's: a running ComfyUI keeps the port it was started with, which `run` recorded.
    """
    found = reach.check_windows_reach(session.deps, install.port)
    if not found.ok:
        hint = f" {found.hint}" if found.hint and found.os is not None else ""
        raise CliError(
            f"ComfyUI isn't running in {install.distro}: {found.message.rstrip('.')}.{hint} "
            f"Start it with '{PROG} start --distro {install.distro}'.",
            data={"distro": install.distro, "windows_reach": found.to_json()},
        )
    url = f"http://localhost:{install.port}"
    session.deps.winapi.open_url(url)
    return Outcome(f"Opened {url} in the browser.", {"distro": install.distro, "url": url})


def register(app: typer.Typer) -> None:
    @app.command()
    def start(ctx: typer.Context) -> Outcome:
        """Start ComfyUI in its own window and wait until it answers (opens the browser if it is already running)."""
        session = ctx.obj
        session.transcript()
        return start_comfyui(session, session.resolve_install())

    @app.command()
    def stop(ctx: typer.Context) -> Outcome:
        """Stop ComfyUI (a run window closes by itself)."""
        session = ctx.obj
        session.transcript()
        install = session.resolve_install()
        session.out.step("Stopping ComfyUI")
        done = session.wsl().run_command(
            install.distro, install.linux_user, steps.stop_argv(install), timeout=steps.STOP_TIMEOUT
        )
        if done.returncode != 0:
            raise CliError(
                f"docker compose stop failed (exit {done.returncode}): {done.err().strip() or done.out().strip()}"
            )
        return Outcome("ComfyUI has been stopped.", {"distro": install.distro})

    @app.command("open")
    def open_command(ctx: typer.Context) -> Outcome:
        """Open a running sandbox's ComfyUI in the browser (starts nothing, see start)."""
        # Read-only, like `logs`: no transcript, so opening a browser never pushes a failed
        # command's transcript out of the kept ones.
        session = ctx.obj
        return open_comfyui(session, session.resolve_install())
