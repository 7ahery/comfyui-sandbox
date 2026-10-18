"""Run a command inside the ComfyUI container.

The one place that builds `docker exec ... comfyui <command>`. The passthroughs (`comfy`, `cm-cli`)
and the maintenance commands (`update`, `node`, `backup`, `restore`) call it instead of writing
the docker argv themselves. It always runs as the install's Linux user (the container has no root
and the user has no sudo), without a TTY (`-it` would hang under Claude Code).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from comfyui_sandbox.runner import Completed
from comfyui_sandbox.state import Install
from comfyui_sandbox.wsl import CALL_TIMEOUT, OnLine, Wsl

CONTAINER = "comfyui"
COMFYUI_PATH = "/workspace/ComfyUI"


def docker_exec_argv(
    command: Sequence[str], *, env: Mapping[str, str] | None = None, workdir: str | None = None
) -> list[str]:
    """`docker exec [-e K=V ...] [-w DIR] comfyui <command...>`."""
    argv = ["docker", "exec"]
    for key, value in (env or {}).items():
        argv += ["-e", f"{key}={value}"]
    if workdir:
        argv += ["-w", workdir]
    return [*argv, CONTAINER, *command]


def run_in_container(
    wsl: Wsl,
    install: Install,
    command: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    workdir: str | None = None,
    on_line: OnLine | None = None,
    timeout: float | None = CALL_TIMEOUT,
) -> Completed:
    """Run `command` in the container of `install` as its Linux user. A non-zero exit is not
    an exception: check `returncode`."""
    return wsl.run_command(
        install.distro,
        install.linux_user,
        docker_exec_argv(command, env=env, workdir=workdir),
        on_line=on_line,
        timeout=timeout,
    )


def comfy_command(args: Sequence[str]) -> list[str]:
    """comfy-cli never prompts (there is no TTY): `comfy --skip-prompt <args>`."""
    return ["comfy", "--skip-prompt", *args]
