"""The only path to subprocesses (docs/adr/0013).

- `argv` is a list, never a shell string. `env` is added to the current environment, not a replacement for it.
- `stdin=None` means no input (stdin is the null device), never the CLI's own stdin.
- `stream` merges stderr into stdout and calls `on_line` with each decoded line as it arrives; use it for long steps.
- A missing executable raises `CommandNotFound` and a timeout `CommandTimeout` (both exit 1 unless the caller
  handles them). A non-zero exit code is not an exception: check `returncode`.
- `start` runs a background process with no console and no I/O; only the keepalive uses it.
- `follow` runs a background process with no console and calls `on_line` with each line of its stdout, on a
  thread of its own, until it ends or is terminated; home's GPU meters use it (`comfyui_sandbox.gpu`).
- `cancellable(switch)` is the same runner with a `CancelSwitch` (comfyui_sandbox.cancel) on its `run` and
  `stream` calls: flipping it terminates the child running and raises `KeyboardInterrupt` (what Ctrl+C raises)
  on the command's thread. The TUI runs each command line with one (`Deps.cancellable`), so cancelling or
  quitting it stops the `wsl.exe` running. The CLI doesn't: Ctrl+C reaches the child and the CLI itself as it
  always has.

`SubprocessRunner` is the real one; tests use `tests/fakes.py: FakeRunner`.
"""

from __future__ import annotations

import os
import subprocess
import threading
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import IO, Protocol

from comfyui_sandbox.cancel import CancelSwitch
from comfyui_sandbox.errors import CliError


def clean(data: bytes) -> str:
    """Decode subprocess output: UTF-8 with errors replaced, NUL and CR removed."""
    return data.decode("utf-8", "replace").replace("\0", "").replace("\r", "")


@dataclass(frozen=True)
class Completed:
    argv: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes

    def out(self) -> str:
        return clean(self.stdout)

    def err(self) -> str:
        return clean(self.stderr)


class CommandNotFound(CliError):
    pass


class CommandTimeout(CliError):
    pass


class Process(Protocol):
    """A background process started with `Runner.start` or `Runner.follow`."""

    pid: int

    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...


class Runner(Protocol):
    def cancellable(self, switch: CancelSwitch) -> Runner:
        """This runner, with `run` and `stream` calls that `switch` cancels (see CancelSwitch).
        `start` and `follow` aren't: their processes are stopped by whoever started them."""
        ...

    def start(self, argv: Sequence[str], *, env: Mapping[str, str] | None = None) -> Process: ...

    def follow(
        self, argv: Sequence[str], on_line: Callable[[str], None], *, env: Mapping[str, str] | None = None
    ) -> Process: ...

    def run(
        self,
        argv: Sequence[str],
        *,
        stdin: bytes | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> Completed: ...

    def stream(
        self,
        argv: Sequence[str],
        on_line: Callable[[str], None],
        *,
        stdin: bytes | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> Completed: ...


def _environment(env: Mapping[str, str] | None) -> dict[str, str] | None:
    if not env:
        return None
    return {**os.environ, **env}


def _popen(
    argv: tuple[str, ...],
    *,
    stdin: int,
    stdout: int,
    stderr: int,
    env: Mapping[str, str] | None,
    creationflags: int = 0,
) -> subprocess.Popen:
    """Starts argv as every call here does (list argv, never a shell; the environment merged as
    `_environment` says), turning a missing program into the CLI's own CommandNotFound."""
    try:
        return subprocess.Popen(
            argv, stdin=stdin, stdout=stdout, stderr=stderr, env=_environment(env), creationflags=creationflags
        )
    except FileNotFoundError as exc:
        raise CommandNotFound(f"Program not found: {argv[0]}") from exc


def _pipe(stream: IO[bytes] | None) -> IO[bytes]:
    """The pipe of a process started with PIPE for it (Popen types it as optional, since it is None without)."""
    if stream is None:
        raise AssertionError("the process was started without the pipe it is read or written through")
    return stream


class SubprocessRunner:
    """The real Runner: list argv, never a shell."""

    def __init__(self, switch: CancelSwitch | None = None) -> None:
        self._switch = switch

    def cancellable(self, switch: CancelSwitch) -> SubprocessRunner:
        return SubprocessRunner(switch)

    def _check(self) -> None:
        if self._switch is not None:
            self._switch.check()

    @contextmanager
    def _waiting_on(self, proc: subprocess.Popen) -> Generator[None]:
        """Around one call's wait for proc: a flip meanwhile terminates proc, and the interruption
        is raised once the wait is over, so proc has ended and nothing is left running."""
        if self._switch is None:
            yield
            return
        with self._switch.running(proc):
            yield
        self._switch.check()

    def run(
        self,
        argv: Sequence[str],
        *,
        stdin: bytes | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> Completed:
        argv = tuple(argv)
        self._check()
        # Popen and communicate, as subprocess.run does inside, but with the process in hand for the switch.
        proc = _popen(
            argv,
            stdin=subprocess.DEVNULL if stdin is None else subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        with proc, self._waiting_on(proc):
            try:
                stdout, stderr = proc.communicate(stdin, timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                proc.kill()
                proc.communicate()
                raise CommandTimeout(f"Timed out after {timeout:g}s: {argv[0]}") from exc
            except BaseException:  # a Ctrl+C at a terminal: as subprocess.run, the child goes too
                proc.kill()
                raise
        return Completed(argv, proc.returncode, stdout, stderr)

    def start(self, argv: Sequence[str], *, env: Mapping[str, str] | None = None) -> subprocess.Popen:
        """Start a background process with no console window and no input or output."""
        argv = tuple(argv)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return _popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            creationflags=flags,
        )

    def follow(
        self, argv: Sequence[str], on_line: Callable[[str], None], *, env: Mapping[str, str] | None = None
    ) -> subprocess.Popen:
        """Start a background process with no console window and no input, and call `on_line` with each
        line of its stdout as it arrives, until it ends or is terminated. It returns at once.

        Unlike `stream`, nothing waits for it: it's for a process meant to run until its caller is done
        with it (`nvidia-smi --loop`), which then terminates it. The lines are read on a daemon thread,
        so a reader blocked on a quiet process never keeps the CLI from exiting; `on_line` runs on that
        thread and must not raise. stderr is dropped: only the lines it reports matter."""
        argv = tuple(argv)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = _popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            creationflags=flags,
        )

        stdout = _pipe(proc.stdout)

        def pump() -> None:
            with stdout:  # the loop ends when the process does, terminated or not
                for raw in stdout:
                    on_line(clean(raw).rstrip("\n"))
            proc.wait()  # reaped here, so an ended one leaves nothing behind

        threading.Thread(target=pump, daemon=True).start()
        return proc

    def stream(
        self,
        argv: Sequence[str],
        on_line: Callable[[str], None],
        *,
        stdin: bytes | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> Completed:
        argv = tuple(argv)
        self._check()
        proc = _popen(
            argv,
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
        )

        if stdin is not None:
            pipe = _pipe(proc.stdin)

            def feed() -> None:
                try:
                    pipe.write(stdin)
                    pipe.close()
                except OSError:
                    pass

            threading.Thread(target=feed, daemon=True).start()

        chunks: list[bytes] = []
        stdout = _pipe(proc.stdout)

        def pump() -> None:
            for raw in stdout:
                chunks.append(raw)
                on_line(clean(raw).rstrip("\n"))

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        with self._waiting_on(proc):
            try:
                returncode = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                proc.kill()
                proc.wait()
                raise CommandTimeout(f"Timed out after {timeout:g}s: {argv[0]}") from exc
            finally:
                reader.join()
        return Completed(argv, returncode, b"".join(chunks), b"")
