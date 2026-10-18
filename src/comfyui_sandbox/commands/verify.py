"""`comfyui-sandbox verify`: the security and health checklist, plus the Windows reach check.

Runs verify.sh in the distro as root (it prints `== Section ==`, `[PASS|WARN|FAIL|INFO] text` and an
optional `[HINT] ...` per check) and then asks Windows whether the port reaches the container
(`reach.check_windows_reach`). Any failed check gives exit 1. Read-only: no transcript when run
by itself; `install` runs the same function as its last check step. Ports verify.sh and
check_windows_access.sh.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import typer

from comfyui_sandbox import reach
from comfyui_sandbox.errors import CliError
from comfyui_sandbox.output import Outcome, Output
from comfyui_sandbox.state import Install
from comfyui_sandbox.steps import models_in_distro
from comfyui_sandbox.wsl import ROOT

LINE = re.compile(r"^\[(PASS|WARN|FAIL|INFO|HINT)\]\s?(.*)$")
SECTION = re.compile(r"^==\s*(.*?)\s*==$")
LABEL = {"pass": "[PASS]", "warn": "[WARN]", "fail": "[FAIL]", "info": "[INFO]"}


@dataclass
class Check:
    section: str
    status: str  # pass | warn | fail | info
    message: str
    hint: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"section": self.section, "status": self.status, "message": self.message, "hint": self.hint}


def parse(text: str) -> list[Check]:
    checks: list[Check] = []
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if match := SECTION.match(line):
            section = match.group(1)
        elif match := LINE.match(line):
            kind, rest = match.groups()
            if kind == "HINT":
                if checks:
                    checks[-1].hint = rest
            else:
                checks.append(Check(section, kind.lower(), rest))
    return checks


def models_mount(install: Install) -> str:
    """Where the models folder is inside the distro."""
    return models_in_distro(install.models.mode, install.models.path)


def _show(out: Output, check: Check) -> None:
    out.info(f"{LABEL[check.status]} {check.message}")
    if check.hint and check.status in ("warn", "fail"):
        out.info(f"       Hint: {check.hint}")


def run_verify(session, install: Install, wsl=None) -> dict[str, Any]:
    """Run both halves, show every result, and return the `data` document.
    Raises CliError (exit 1, with the same data) when any check failed."""
    out, deps = session.out, session.deps
    wsl = wsl or session.wsl()
    env = {"GPU_MODE": install.gpu.mode, "MODELS_DIR": models_mount(install)}
    done = wsl.run_script(install.distro, ROOT, "verify.sh", env)
    checks = parse(done.out())
    shown = None
    for check in checks:
        if check.section != shown:
            shown = check.section
            out.info(f"== {shown} ==")
        _show(out, check)
    if done.returncode != 0 and not any(c.status == "fail" for c in checks):
        problem = Check(
            "",
            "fail",
            f"verify.sh exited {done.returncode} without naming a failed check",
            "Run 'comfyui-sandbox verify' again, if it repeats, see the transcript.",
        )
        checks.append(problem)
        _show(out, problem)

    out.info("== Windows reach ==")
    found = reach.check_windows_reach(deps, install.port)
    if found.ok:
        out.info(f"[ OK ] {found.message}")
    else:
        out.info(f"[FAIL] {found.message}")
        if found.hint:
            out.info(f"       Hint: {found.hint}")

    counts = {kind: sum(c.status == kind for c in checks) for kind in ("pass", "warn", "fail")}
    if found.ok:
        counts["pass"] += 1
    else:
        counts["fail"] += 1
    data = {
        "distro": install.distro,
        "checks": [c.to_json() for c in checks],
        "counts": counts,
        "windows_reach": found.to_json(),
    }
    if counts["fail"]:
        noun = "check" if counts["fail"] == 1 else "checks"
        why = "" if found.ok else f" {found.message[0].upper()}{found.message[1:]}"
        raise CliError(f"Verify: {counts['fail']} {noun} failed.{why} Each failed check above has a hint.", data=data)
    return data


def register(app: typer.Typer) -> None:
    @app.command()
    def verify(ctx: typer.Context) -> Outcome:
        """Run the security and health checklist in the distro and check that Windows reaches the container."""
        session = ctx.obj
        install = session.resolve_install()
        data = run_verify(session, install)
        counts = data["counts"]
        warned = f", {counts['warn']} with warnings" if counts["warn"] else ""
        return Outcome(f"Verify passed: {counts['pass']} checks ok{warned}.", data)
