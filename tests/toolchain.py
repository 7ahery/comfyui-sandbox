"""Dev tools the suite shells out to: bash and shellcheck are declared dev requirements.

Missing or broken tools raise ToolchainError with install instructions; nothing is skipped.
Override with COMFYUI_SANDBOX_TEST_BASH / COMFYUI_SANDBOX_TEST_SHELLCHECK.
"""

import os
import shutil
import subprocess
import sys
from functools import cache
from pathlib import Path

BASH_VAR = "COMFYUI_SANDBOX_TEST_BASH"
SHELLCHECK_VAR = "COMFYUI_SANDBOX_TEST_SHELLCHECK"


class ToolchainError(Exception):
    pass


def _is_wsl_launcher(path: str) -> bool:
    return "windowsapps" in path.replace("\\", "/").lower().split("/")


def _works(path: str, run) -> bool:
    try:
        done = run([path, "-c", "echo ok"], capture_output=True, text=True, timeout=30, check=False)
    except OSError, subprocess.SubprocessError:
        return False
    return done.returncode == 0 and done.stdout.strip() == "ok"


def _git_for_windows_bashes(git: str | None) -> list[str]:
    if not git:
        return []
    found = []
    for parent in Path(git).parents:
        # usr\bin first: the real MSYS bash. bin\bash.exe is the launcher, which rewrites PATH
        # and breaks the fake-docker/curl dirs wait_ready tests put in front.
        found += [str(parent / "usr" / "bin" / "bash.exe"), str(parent / "bin" / "bash.exe")]
    return found


def resolve_bash(
    environ=os.environ, *, platform=sys.platform, which=shutil.which, is_file=os.path.isfile, run=subprocess.run
) -> str:
    override = environ.get(BASH_VAR)
    if override:
        if is_file(override) and not _is_wsl_launcher(override) and _works(override, run):
            return override
        raise ToolchainError(
            f"{BASH_VAR}={override!r} is not a working bash (missing, the WSL launcher in "
            f"WindowsApps, or `bash -c 'echo ok'` failed). Fix or unset it."
        )
    candidates = []
    if platform == "win32":
        candidates += [p for p in _git_for_windows_bashes(which("git")) if is_file(p)]
    on_path = which("bash")
    rejected = None
    if on_path:
        if _is_wsl_launcher(on_path):
            rejected = on_path
        else:
            candidates.append(on_path)
    for candidate in candidates:
        if _works(candidate, run):
            return candidate
    why = f" (rejected {rejected}: it is the WindowsApps WSL launcher, not a real bash)" if rejected else ""
    raise ToolchainError(
        f"No working bash found{why}. The tests need bash: install Git for Windows "
        f"(https://git-scm.com), or set {BASH_VAR} to the path of a bash that runs "
        f"`bash -c 'echo ok'`."
    )


def resolve_shellcheck(environ=os.environ, *, which=shutil.which, is_file=os.path.isfile) -> str:
    override = environ.get(SHELLCHECK_VAR)
    if override:
        if is_file(override):
            return override
        raise ToolchainError(f"{SHELLCHECK_VAR}={override!r} does not exist. Fix or unset it.")
    found = which("shellcheck")
    if found:
        return found
    raise ToolchainError(
        "shellcheck not found. It is a dev requirement: `uv sync` (shellcheck-py), "
        "`winget install koalaman.shellcheck`, `scoop install shellcheck`, or "
        f"`apt install shellcheck`; or set {SHELLCHECK_VAR} to the binary."
    )


@cache
def bash() -> str:
    return resolve_bash()


@cache
def shellcheck() -> str:
    return resolve_shellcheck()
