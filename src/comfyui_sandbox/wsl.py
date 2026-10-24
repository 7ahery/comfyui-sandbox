"""The wsl.exe wrapper: the only module that builds wsl.exe argv (docs/adr/0002, docs/adr/0003).

Distro level: detection, version, list, list running, online list, exists, install, terminate,
shutdown and unregister. `wsl.exe` is never run when WSL isn't installed (the inbox stub would start
an install): detection looks for `%ProgramFiles%\\WSL\\wsl.exe`, and every method raises `WslNotInstalled`
(exit 7) instead.

In the distro: `run_script` streams a package script (the `distro/` folder) over stdin to `bash -s`,
because with automount off the distro can't read the package (see `script_stdin`); `run_command` runs
argv without a shell (`--exec env KEY=value ... argv`). Both run as root (`ROOT`) or as the Linux user,
with env vars, `--cd /` and `WSL_UTF8=1`; the exit code is the script's or command's. Scripts talk back
only through their exit code and output lines. With a transcript (`Wsl(..., log=)`), every call and its
output is logged. In a command, get one with `session.wsl()`.

Timeouts: in-distro calls default to `CALL_TIMEOUT`, so a hung Docker can't hang the CLI; long steps pass
their own (`steps.LONG_STEP_TIMEOUT`, `steps.STOP_TIMEOUT`). `timeout=None` is only for a call meant to run
until the user stops it: `logs --follow`, the passthroughs and `watch.sh` in `run`.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from comfyui_sandbox import wslconfig
from comfyui_sandbox.errors import CliError, ExitCode
from comfyui_sandbox.logs import Transcript
from comfyui_sandbox.runner import Completed, Process, Runner

WSL = "wsl.exe"
ENV = {"WSL_UTF8": "1"}
# Ubuntu 26.04 ships only in the .wsl format, which (with `wsl --install --name/--location`)
# needs WSL 2.4.4 or newer. An Ubuntu startup failure was reported with WSL 2.4.10
# and its bundled 5.15 kernel. Require bundled kernel 6.6 as this tool's compatibility
# floor, without claiming E_UNEXPECTED establishes the cause or inspecting custom kernels.
MIN_VERSION = (2, 4, 4)
MIN_KERNEL = (6, 6)
TIMEOUT = 60
# A call in the distro that passes no timeout gets this one, so a hung Docker can't hang the CLI.
# Long steps pass their own; pass `timeout=None` only for a call meant to run until the user stops it.
CALL_TIMEOUT = 300
ROOT = "root"
UBUNTU = "Ubuntu-26.04"
# `wsl --install` downloads the image: allow for a slow connection.
INSTALL_TIMEOUT = 3600
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
KERNEL_NUMBER = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?")
# Run elevated by `prereqs` (through winapi.run_elevated, not the Runner). Both work with
# only the Windows inbox stub, which is all there is before WSL is installed.
INSTALL_ARGV = (WSL, "--install", "--no-distribution")
UPDATE_ARGV = (WSL, "--update")

OnLine = Callable[[str], None]


class WslNotInstalled(CliError):
    def __init__(self) -> None:
        super().__init__("WSL is not installed. Run 'comfyui-sandbox prereqs'.", ExitCode.WSL_NOT_READY)


class ScriptNotFound(CliError):
    pass


@dataclass(frozen=True)
class Version:
    """`wsl --version`: package and bundled kernel, not a configured custom kernel.

    `number` and `kernel` are None when WSL didn't answer with one.
    """

    returncode: int
    first_line: str
    number: tuple[int, ...] | None
    kernel: tuple[int, ...] | None = None

    @property
    def text(self) -> str | None:
        return ".".join(map(str, self.number)) if self.number else None

    @property
    def kernel_text(self) -> str | None:
        return ".".join(map(str, self.kernel)) if self.kernel else None


def script_names() -> list[str]:
    """Every in-distro script shipped in the package."""
    folder = resources.files("comfyui_sandbox") / "distro"
    return sorted(item.name for item in folder.iterdir() if item.name.endswith(".sh"))


def script_text(name: str) -> str:
    """A package script from `comfyui_sandbox/distro/`, CR stripped."""
    resource = resources.files("comfyui_sandbox") / "distro" / name
    if not resource.is_file():
        raise ScriptNotFound(f"In-distro script not found in the package: {name}")
    return resource.read_text("utf-8").replace("\r", "")


def _check_env(env: Mapping[str, str]) -> dict[str, str]:
    for key in env:
        if not ENV_NAME.fullmatch(key):
            raise ValueError(f"bad environment variable name: {key!r}")
    return {k: str(v) for k, v in env.items()}


def _kernel(text: str) -> tuple[int, ...] | None:
    """The second slot in WSL's localized MessagePackageVersions resource.

    Match its numeric value after the colon, never another component's version.
    The trailing "-1" is a package revision, not part of the kernel version.
    """
    lines = text.strip().splitlines()
    if len(lines) < 2:
        return None
    _, separator, value = lines[1].replace("：", ":").partition(":")
    found = KERNEL_NUMBER.fullmatch(value.strip().split("-", 1)[0]) if separator else None
    return tuple(int(part) for part in found.groups() if part is not None) if found else None


def kernel_warning(version: Version, home: Path) -> str | None:
    """Why the bundled-kernel check cannot establish compatibility, shared by both commands.

    Detection stays read-only: do not start any distro or update a package to resolve
    unknown output or the user's custom-kernel override.
    """
    minimum = ".".join(map(str, MIN_KERNEL))
    try:
        custom = wslconfig.has_custom_kernel(home)
    except OSError:
        return "Could not read .wslconfig. WSL kernel compatibility is not verified. Check the file's permissions."
    if custom:
        return (
            "A custom WSL kernel is configured in .wslconfig. Kernel compatibility is not verified. "
            f"Use kernel {minimum} or newer, or remove the override to use WSL's bundled kernel. "
            "Updating WSL does not replace a custom kernel."
        )
    if version.kernel is None:
        return (
            "WSL did not report a readable bundled kernel version. Kernel compatibility is not verified. "
            f"Check 'wsl --version' and confirm kernel {minimum} or newer."
        )
    return None


def script_stdin(name: str, text: str, env: Mapping[str, str]) -> bytes:
    """What `bash -s` reads: a name comment, strict mode, the exports, then the script as
    one group. The group is read fully before it runs, and its stdin is /dev/null so
    commands inside can't eat the rest of the script."""
    lines = [f"# comfyui-sandbox: {name}", "set -euo pipefail"]
    lines += [f"export {key}={shlex.quote(value)}" for key, value in _check_env(env).items()]
    lines += ["{", text.rstrip("\n"), "} </dev/null", ""]
    return "\n".join(lines).encode("utf-8")


class Wsl:
    """Every wsl.exe call. Never runs wsl.exe when WSL isn't installed."""

    def __init__(self, runner: Runner, environ: Mapping[str, str], *, log: Transcript | None = None) -> None:
        self._runner, self._environ, self._log = runner, environ, log

    def installed(self) -> bool:
        """Detection by file, without running wsl.exe: WSL 2.x installs to Program Files\\WSL;
        without it, wsl.exe is only the Windows inbox stub, which can start an install."""
        program_files = self._environ.get("ProgramFiles") or r"C:\Program Files"
        return (Path(program_files) / "WSL" / "wsl.exe").is_file()

    def _call(
        self,
        argv: list[str],
        *,
        timeout: float | None,
        stdin: bytes | None = None,
        on_line: OnLine | None = None,
        script: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> Completed:
        """One wsl.exe call through the Runner, logged when there is a transcript.
        With `on_line` the output is streamed (stderr merged into stdout) line by line."""
        if not self.installed():
            raise WslNotInstalled()
        log = self._log
        if log is not None:
            log.run(argv, script=script, env=env)
        if on_line is None:
            done = self._runner.run(argv, stdin=stdin, env=ENV, timeout=timeout)
            if log is not None:
                for line in (done.out() + done.err()).splitlines():
                    log.out(line)
        else:

            def each(line: str) -> None:
                if log is not None:
                    log.out(line)
                on_line(line)

            done = self._runner.stream(argv, each, stdin=stdin, env=ENV, timeout=timeout)
        if log is not None:
            log.exit(done.returncode)
        return done

    def _run(self, *args: str) -> Completed:
        return self._call([WSL, *args], timeout=TIMEOUT)

    # -- in the distro --------------------------------------------------------
    def run_script(
        self,
        distro: str,
        user: str,
        script: str,
        env: Mapping[str, str] | None = None,
        *,
        on_line: OnLine | None = None,
        timeout: float | None = CALL_TIMEOUT,
    ) -> Completed:
        """Run package script `script` (e.g. "harden.sh") in `distro` as `user` (or ROOT).

        `env` is exported before the script. The exit code is the script's. Pass `on_line`
        to stream its output (stdout and stderr merged) as it comes.
        """
        env = _check_env(env or {})
        argv = [WSL, "-d", distro, "-u", user, "--cd", "/", "--", "bash", "-s"]
        stdin = script_stdin(script, script_text(script), env)
        return self._call(argv, stdin=stdin, timeout=timeout, on_line=on_line, script=script, env=env)

    def run_command(
        self,
        distro: str,
        user: str,
        argv: Sequence[str],
        env: Mapping[str, str] | None = None,
        *,
        on_line: OnLine | None = None,
        timeout: float | None = CALL_TIMEOUT,
    ) -> Completed:
        """Run `argv` in `distro` as `user` (or ROOT) without a shell, with `env` set."""
        env = _check_env(env or {})
        full = [
            WSL,
            "-d",
            distro,
            "-u",
            user,
            "--cd",
            "/",
            "--exec",
            "env",
            *(f"{k}={v}" for k, v in env.items()),
            *argv,
        ]
        return self._call(full, timeout=timeout, on_line=on_line)

    def follow_command(self, distro: str, user: str, argv: Sequence[str], on_line: OnLine) -> Process:
        """Start `argv` in `distro` as `user` in a background wsl.exe and call `on_line` with each line
        of its stdout, on a thread of its own, until it ends or the caller terminates it (`Runner.follow`).

        For output that runs as long as its caller wants it (`run`'s window following ComfyUI's log).
        The call is logged, its lines aren't: they're the container's log, which it keeps itself. It
        isn't a step's call, so a cancel doesn't end it: its caller terminates it when it's done."""
        if not self.installed():
            raise WslNotInstalled()
        full = [WSL, "-d", distro, "-u", user, "--cd", "/", "--exec", "env", *argv]  # as run_command's
        if self._log is not None:
            self._log.run(full)
        return self._runner.follow(full, on_line, env=ENV)

    def start_sleeper(self, distro: str) -> Process:
        """Start `sleep infinity` in `distro` as root in a background wsl.exe (the keepalive).
        The caller owns the process; see `comfyui_sandbox.keepalive`."""
        if not self.installed():
            raise WslNotInstalled()
        argv = [WSL, "-d", distro, "-u", ROOT, "--cd", "/", "--exec", "env", "sleep", "infinity"]
        if self._log is not None:
            self._log.run(argv)
        return self._runner.start(argv, env=ENV)

    # -- distro level ---------------------------------------------------------
    def version(self) -> Version:
        done = self._run("--version")
        lines = [line for line in done.out().splitlines() if line.strip()]
        first = lines[0].strip() if lines else ""
        # The first line is localized ("WSL version: 2.6.1.0", "WSL-Version: ..."): match the number.
        found = re.search(r"(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?", first)
        answered = done.returncode == 0
        number = tuple(int(g) for g in found.groups() if g is not None) if found and answered else None
        return Version(done.returncode, first, number, _kernel(done.out()) if answered else None)

    def _names(self, *args: str) -> list[str]:
        done = self._run(*args)
        return [line.strip() for line in done.out().splitlines() if line.strip()] if done.returncode == 0 else []

    def list_distros(self) -> list[str]:
        return self._names("--list", "--quiet")

    def list_running(self) -> list[str]:
        return self._names("--list", "--running", "--quiet")

    def exists(self, distro: str) -> bool:
        """Is a distro with this name registered (case-insensitive, whole name)?"""
        return any(name.lower() == distro.lower() for name in self.list_distros())

    def online_has(self, name: str) -> bool:
        """Is `name` in `wsl --list --online`?"""
        return any(
            line.split()[0].lower() == name.lower() for line in self._names("--list", "--online") if line.split()
        )

    def install(
        self,
        distro: str,
        *,
        image: str = UBUNTU,
        location: str | None = None,
        on_line: OnLine | None = None,
        timeout: float | None = INSTALL_TIMEOUT,
    ) -> Completed:
        """`wsl --install <image> --name <distro> --no-launch [--location <path>]`."""
        argv = [WSL, "--install", image, "--name", distro, "--no-launch"]
        if location:
            argv += ["--location", location]
        return self._call(argv, timeout=timeout, on_line=on_line)

    def terminate(self, distro: str) -> Completed:
        return self._run("--terminate", distro)

    def shutdown(self) -> Completed:
        """`wsl --shutdown`: stops every distro and the WSL VM. Only after the destructive gate
        (`comfyui_sandbox.destructive`) when distros other than ours run; otherwise terminate."""
        return self._run("--shutdown")

    def unregister(self, distro: str) -> Completed:
        """`wsl --unregister <distro>`: deletes the distro and its virtual disk. Only after the
        destructive gate (`comfyui_sandbox.destructive`)."""
        return self._run("--unregister", distro)
