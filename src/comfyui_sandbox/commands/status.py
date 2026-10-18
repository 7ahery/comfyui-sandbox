"""`comfyui-sandbox status`: the state at a glance. Read-only (no transcript, no changes).

Reports the container state (status.sh in the distro, never starting a stopped distro), whether
Windows reaches ComfyUI (`reach.check_windows_reach`), whether a run window is open (the
run-window marker, docs/adr/0010) and whether the deployed assets are older
than this CLI. Ports status.sh.
"""

from __future__ import annotations

import re
from importlib.metadata import version as package_version

import typer

from comfyui_sandbox import ports, reach
from comfyui_sandbox.instance import instance_name
from comfyui_sandbox.output import Outcome
from comfyui_sandbox.runner import CommandTimeout
from comfyui_sandbox.steps import project_path

STATE = re.compile(r"^container=(\S+)$")
PROBE_TIMEOUT = 60  # seconds; a read-only look at the container must not hang
LOG_MARK = "----- last log lines -----"


def parse(text: str) -> tuple[str, list[str]]:
    """(container state, log lines) from status.sh's output."""
    state, logs, in_logs = "missing", [], False
    for line in text.splitlines():
        if in_logs:
            if line.strip():
                logs.append(line.rstrip())
        elif line.strip() == LOG_MARK:
            in_logs = True
        elif match := STATE.match(line.strip()):
            state = match.group(1)
    return state, logs


def version_key(text: str) -> tuple[int, ...] | None:
    """Release numbers of a version ("0.1.0" -> (0, 1, 0)); None when there are none."""
    numbers = re.match(r"^\d+(?:\.\d+)*", text.strip())
    return tuple(int(n) for n in numbers.group(0).split(".")) if numbers else None


def is_older(deployed: str, running: str) -> bool:
    old, new = version_key(deployed), version_key(running)
    if new is None:
        return False
    if old is None:
        return True  # nothing recorded: the deploy predates version tracking
    width = max(len(old), len(new))
    return old + (0,) * (width - len(old)) < new + (0,) * (width - len(new))


def register(app: typer.Typer) -> None:
    @app.command()
    def status(ctx: typer.Context) -> Outcome:
        """Show the container state, whether ComfyUI is ready, and whether a run window is open."""
        session = ctx.obj
        install = session.resolve_install()
        deps, out = session.deps, session.out
        wsl = session.wsl()

        container, logs, in_env, hung = "stopped", [], None, None
        if any(name.lower() == install.distro.lower() for name in wsl.list_running()):
            try:
                done = wsl.run_script(
                    install.distro,
                    install.linux_user,
                    "status.sh",
                    {"PROJECT_DIR": project_path(install.linux_user)},
                    timeout=PROBE_TIMEOUT,
                )
                container, logs = parse(done.out())
                in_env = ports.env_port(session, install)  # only when the distro is up: never start it for this
            except CommandTimeout:
                container, hung = (
                    "unknown",
                    "the container state was not read: Docker in the distro did not answer in time",
                )
        found = reach.check_windows_reach(deps, install.port)
        window = deps.winapi.instance_exists(instance_name(install.distro))
        cli_version = package_version("comfyui-sandbox")
        drift = is_older(install.deployed_by, cli_version)
        url = f"http://localhost:{install.port}"

        out.info(f"Container: {container}")
        out.info(f"ComfyUI: ready at {url}" if found.ok else f"ComfyUI: not ready ({found.message})")
        out.info(f"Run window: {'open' if window else 'not open'}")
        differs = in_env is not None and in_env != install.port
        if differs:
            out.info(
                f"Port {install.port} (in use since the last start). .env now says {in_env}, "
                "which takes effect the next time ComfyUI starts."
            )
        elif in_env is None:
            out.info(f"Port {install.port} (as of the last start)")
        else:
            out.info(f"Port {install.port}")
        if logs:
            out.info("Last log lines:")
            for line in logs:
                out.info(f"  {line}")
        if hung:
            out.warn(f"Timed out: {hung}.")
        if drift:
            out.warn(
                f"This sandbox's setup is from CLI {install.deployed_by or 'an unknown version'}, "
                f"older than this CLI ({cli_version}). Run 'comfyui-sandbox update setup' to bring it up to date."
            )
        data = {
            "distro": install.distro,
            "container": container,
            "ready": found.ok,
            "url": url,
            "run_window": window,
            "deployed_by": install.deployed_by,
            "cli_version": cli_version,
            "drift": drift,
            "windows_reach": found.to_json(),
            "log_tail": logs,
        }
        if differs:
            data["env_port"] = in_env
        return Outcome(f"{container}, {'ready' if found.ok else 'not ready'}.", data)
