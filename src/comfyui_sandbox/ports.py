"""The port a start uses, and the record of it (docs/adr/0005).

`.env` `COMFY_PORT` is what the next start uses; `installs.json` holds the port as of the last
start. The commands that start ComfyUI (`start`, `run`) read `.env`, and write the record once
ComfyUI answers. A running ComfyUI keeps the port it started with, so the commands that report on
one (`status`, `verify`, `stop`) show the record.
"""

from __future__ import annotations

from comfyui_sandbox import envfile, intake, preflight
from comfyui_sandbox.errors import CliError
from comfyui_sandbox.state import Install
from comfyui_sandbox.steps import backup_stamp

PORT_FIX = "Free it, or set another with `comfyui-sandbox config --port <n>`"


def env_port(session, install: Install) -> int | None:
    """`COMFY_PORT` from the install's `.env`; None when `.env` cannot be read or holds no usable port."""
    try:
        text = envfile.read(session.wsl(), install)
        return intake.parse_port(envfile.parse(text).get("COMFY_PORT", ""))
    except CliError, intake.InvalidAnswer:
        return None


def port_to_start(session, install: Install, *, fix: str) -> int:
    """The port to start `install` on: the `.env` port (the record's when `.env` has none). A port that
    differs from the record is first checked against Windows and the other installs; when the user
    picks another, it is written to `.env` too."""
    wanted = env_port(session, install)
    if wanted is None or wanted == install.port:
        return install.port
    port = preflight.check_port(session, wanted, other_than=install.distro, fix=fix)
    if port != wanted:
        envfile.apply(session.wsl(), install, {"COMFY_PORT": str(port)}, stamp=backup_stamp())
        # Said, not only done: the answer was a question's, and a front end that shows the port
        # (the Textual run window) reads the one it now starts on from this line.
        session.out.info(f"Using port {port} instead, saved to .env")
    return port


def record_port(session, install: Install, port: int) -> None:
    """Write `port` to the install's record (it answered on it)."""
    installs = session.installs()
    current = installs.find(install.distro)
    if current is None or current.port == port:
        return
    current.port = port
    installs.put(current)
    installs.save()
    install.port = port
