"""Does http://localhost:<port> on Windows reach the ComfyUI container? Ports check_windows_access.sh.

An HTTP 200 alone proves nothing: a Windows ComfyUI (e.g. the portable build, also on 8188 by
default) holding the port gets the request instead of WSL's forwarding. ComfyUI's /system_stats
`os` field tells them apart: 0.38+ reports sys.platform ("linux" in the container, "win32" on
Windows), older builds os.name ("posix" / "nt"). Used by `verify` and, in ticket 10, `run`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from comfyui_sandbox.deps import Deps

CONTAINER_OS = ("linux", "posix")
WINDOWS_OS = ("win32", "nt")
# The program that holds a Windows port WSL forwards to a distro (localhost forwarding): it's a
# distro listening on that port, not a Windows program, and there is nothing of it to close.
WSL_RELAY = "wslrelay.exe"


@dataclass(frozen=True)
class Reach:
    ok: bool
    url: str
    os: str | None  # what /system_stats said, None when it said nothing usable
    message: str
    hint: str | None = None
    # Who listens on the port when it isn't the container, as "<program> (PID <n>)"; None when it
    # is the container, when nothing listens, or when Windows won't say. Not in to_json(): the
    # message already names it there, and `verify --json`'s document stays as it was.
    holder: str | None = None
    # Whether that holder is WSL's own relay (WSL_RELAY): the port is a distro's, forwarded.
    relayed: bool = False
    # What the container's ComfyUI runs, from the same answer (versions_of); empty when it isn't the
    # container. Not in to_json(), as holder.
    versions: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"ok": self.ok, "url": self.url, "os": self.os, "message": self.message, "hint": self.hint}


def _os_of(body: str) -> str | None:
    try:
        doc = json.loads(body)
    except ValueError:
        return None
    if not isinstance(doc, dict):
        return None
    system = doc.get("system")
    found = system.get("os") if isinstance(system, dict) else doc.get("os")
    return found if isinstance(found, str) and found else None


def versions_of(body: str) -> dict[str, str]:
    """ComfyUI's, its frontend's, PyTorch's and Python's versions from a /system_stats answer, by
    those names, each only when it says it (older builds have no package list, so no frontend).
    Python's is its number alone: the field is sys.version, with the build after it."""
    try:
        doc = json.loads(body)
    except ValueError:
        return {}
    system = doc.get("system") if isinstance(doc, dict) else None
    if not isinstance(system, dict):
        return {}
    packages = system.get("comfy_package_versions")
    frontend = (
        next(
            (
                p.get("installed")
                for p in packages
                if isinstance(p, dict) and p.get("name") == "comfyui-frontend-package"
            ),
            None,
        )
        if isinstance(packages, list)
        else None
    )
    python = system.get("python_version")
    found = {
        "ComfyUI": system.get("comfyui_version"),
        "Frontend": frontend,
        "PyTorch": system.get("pytorch_version"),
        "Python": python.split()[0] if isinstance(python, str) and python.strip() else None,
    }
    return {name: value for name, value in found.items() if isinstance(value, str) and value}


def check_windows_reach(deps: Deps, port: int, *, timeout: float = 10) -> Reach:
    """One look at /system_stats from Windows. Never raises."""
    url = f"http://127.0.0.1:{port}"
    try:
        reply = deps.http.get(f"{url}/system_stats", timeout=timeout)
    except OSError:
        reply = None
    answered = _os_of(reply.body) if reply is not None else None
    if reply is not None and answered in CONTAINER_OS:  # answered is None without a reply
        return Reach(
            True,
            url,
            answered,
            f"{url} reaches the ComfyUI container (system_stats os={answered})",
            versions=versions_of(reply.body),
        )
    owner = deps.winapi.port_owner(port)
    holder = f"{owner.name} (PID {owner.pid})" if owner else None
    holding = f" Listening on {port}: {holder}." if holder else ""
    relayed = owner is not None and owner.name.lower() == WSL_RELAY
    if answered in WINDOWS_OS:
        return Reach(
            False,
            url,
            answered,
            f"{url} is answered by a ComfyUI running on Windows (os={answered}), not the container.{holding}",
            "Close that ComfyUI, or move the container to another port.",
            holder,
            relayed,
        )
    if answered is not None:
        return Reach(
            False,
            url,
            answered,
            f"{url} answers with os={answered}, not the container (expected linux or posix).",
            "Another program is using the port.",
            holder,
            relayed,
        )
    if reply is None:
        if holder:
            return Reach(
                False,
                url,
                None,
                f"{url} gives no HTTP answer, port {port} is held by: {holder}.",
                "If that is wslrelay, the container may still be starting: wait a minute and run verify again.",
                holder,
                relayed,
            )
        return Reach(
            False,
            url,
            None,
            f"nothing answers on {url} from Windows.",
            "Start ComfyUI, then check WSL's localhost forwarding ('localhostForwarding' in "
            ".wslconfig must not be false).",
        )
    return Reach(
        False,
        url,
        None,
        f"{url} answers, but not with ComfyUI's /system_stats: another program holds port {port}.{holding}",
        "Close that program, or move the container to another port.",
        holder,
        relayed,
    )
