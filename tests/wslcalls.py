"""Read the fake Runner's recorded wsl.exe calls by meaning (`comfyui_sandbox.wsl`).

Later tickets use these to assert "the harden script ran as root with MODELS_MODE=wsl"
without matching argv strings exactly.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

from fakes import Call

EXPORT = re.compile(r"^export ([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
NAME = re.compile(r"^# comfyui-sandbox: (\S+)$")

# A stop comfyui-sandbox asks for, for the user `comfy` (steps.stop_argv): it leaves the sign that
# ends an open run window's watch at once.
COMPOSE_STOP = (
    "env",
    "--chdir",
    "/home/comfy/comfyui-docker",
    "sh",
    "-c",
    'touch .stop-asked; exec "$@"',
    "sh",
    "docker",
    "compose",
    "stop",
)


def compose_verb(argv: tuple[str, ...]) -> str | None:
    """The `docker compose` verb an argv runs (`stop`, `up`, ...), however it's wrapped; None for no compose."""
    for i in range(len(argv) - 2):
        if argv[i : i + 2] == ("docker", "compose"):
            return argv[i + 2]
    return None


@dataclass(frozen=True)
class ScriptRun:
    """One in-distro script streamed over stdin."""

    distro: str | None  # None for both when the call has no -d or -u
    user: str | None
    script: str  # package script name, e.g. "harden.sh"
    env: dict[str, str]  # the exported variables, unquoted
    body: str  # the script text inside the group
    call: Call


@dataclass(frozen=True)
class CommandRun:
    """One in-distro command run without a shell (`--exec env K=V ... argv`)."""

    distro: str | None  # None for both when the call has no -d or -u
    user: str | None
    env: dict[str, str]
    argv: tuple[str, ...]
    call: Call


def _option(argv: tuple[str, ...], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv else None


def is_wsl(call: Call) -> bool:
    return bool(call.argv) and call.argv[0].lower() == "wsl.exe"


def scripts(calls: list[Call]) -> list[ScriptRun]:
    runs = []
    for call in calls:
        argv = call.argv
        if not is_wsl(call) or argv[-3:] != ("--", "bash", "-s"):
            continue
        text = (call.stdin or b"").decode("utf-8")
        lines = text.split("\n")
        env, name, body_start = {}, "", 0
        for i, line in enumerate(lines):
            if m := NAME.match(line):
                name = m.group(1)
            elif m := EXPORT.match(line):
                env[m.group(1)] = shlex.split(m.group(2))[0] if m.group(2) else ""
            elif line == "{":
                body_start = i + 1
                break
        end = len(lines) - 1 - lines[::-1].index("} </dev/null")
        runs.append(
            ScriptRun(_option(argv, "-d"), _option(argv, "-u"), name, env, "\n".join(lines[body_start:end]), call)
        )
    return runs


def script(calls: list[Call], name: str) -> ScriptRun:
    found = [run for run in scripts(calls) if run.script == name]
    assert len(found) == 1, f"expected one run of {name}, got {len(found)}"
    return found[0]


def commands(calls: list[Call]) -> list[CommandRun]:
    runs = []
    for call in calls:
        argv = call.argv
        if not is_wsl(call) or "--exec" not in argv:
            continue
        rest = list(argv[argv.index("--exec") + 1 :])
        assert rest[0] == "env", argv
        rest = rest[1:]
        env = {}
        while rest and "=" in rest[0] and not rest[0].startswith("-"):
            key, _, value = rest.pop(0).partition("=")
            env[key] = value
        runs.append(CommandRun(_option(argv, "-d"), _option(argv, "-u"), env, tuple(rest), call))
    return runs


@dataclass(frozen=True)
class DockerExec:
    """One `docker exec` into a container, run in the distro as `user` (ticket 17)."""

    distro: str | None  # None for both when the call has no -d or -u
    user: str | None
    container: str
    env: dict[str, str]  # the `-e K=V` pairs
    workdir: str | None  # the `-w` directory
    command: tuple[str, ...]  # what runs inside the container
    call: Call


def docker_execs(calls: list[Call]) -> list[DockerExec]:
    runs = []
    for run in commands(calls):
        if run.argv[:2] != ("docker", "exec"):
            continue
        rest, env, workdir = list(run.argv[2:]), {}, None
        while rest and rest[0] in ("-e", "-w"):
            flag, value = rest.pop(0), rest.pop(0)
            if flag == "-e":
                key, _, val = value.partition("=")
                env[key] = val
            else:
                workdir = value
        if rest[1:2] == ["git"]:  # the version reads (update comfyui's before and after), not a change
            continue
        runs.append(DockerExec(run.distro, run.user, rest[0], env, workdir, tuple(rest[1:]), run.call))
    return runs


def distro_ops(calls: list[Call], op: str) -> list[Call]:
    """Distro-level calls with this option, e.g. "--install", "--terminate"."""
    return [call for call in calls if is_wsl(call) and op in call.argv and "-d" not in call.argv]


def index(calls: list[Call], call: Call) -> int:
    return next(i for i, c in enumerate(calls) if c is call)
