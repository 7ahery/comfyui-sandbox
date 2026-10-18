"""The real gateway's operation handle: a command line run through the CLI's `main()`, on a thread.

Every action home takes is the command line it shows, run in this process through `cli.call()`
(ADR 0017): the same parsing, transcript, exit codes and `after_failure` as at a terminal, so the
TUI can do nothing the CLI can't, and every action leaves the transcript the CLI's would.

Commands are synchronous and Textual runs on asyncio, so each command runs on a worker thread of
its own. The two sides share only the handle: the command writes its `Progress` (through the TUI's
`Output`, output.py) and finally its `Result`; the UI thread polls the progress and awaits the
result. Nothing ties a handle to the screen that started it, so background operations (several at
once) need no change here.

Two more things cross between the threads, each through the handle:

- questions: `Deps.prompter` is the TUI's Prompter (questions.py), never the terminal's, which would
  draw questionary's prompt over the screen Textual owns. It asks through `progress.question`, and
  the screen answers through `answer()` or backs out through `cancel()`.
- cancel: `cancel()` flips the operation's `CancelSwitch` (comfyui_sandbox.cancel). The
  command runs with `deps.cancellable(switch)`, so the `wsl.exe` call running is terminated, and
  its pauses (`Deps.sleep`, e.g. `start` waiting for ComfyUI) check the same switch. Either way
  the command's thread raises the interruption a Ctrl+C raises, once, and unwinds through its
  cleanup, whose calls run normally. Which screens offer a cancel is theirs to decide; quitting
  home cancels whatever is running and waits for it (SandboxApp.quit_unfinished). Leaving Show
  logs ends `logs --follow` this way, and leaving Run here ends `run`, whose own cleanup then
  stops the container. A switch, not an exception thrown into the thread: only the command knows
  which of its calls may be cut short and which cleanup must still run.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path

from comfyui_sandbox import cli
from comfyui_sandbox.cancel import CancelSwitch
from comfyui_sandbox.cli_command import PROG
from comfyui_sandbox.deps import Deps
from comfyui_sandbox.logs import Transcript
from comfyui_sandbox.output import CommandResult

from ..records import Progress, Result
from .output import OperationOutput
from .questions import TuiPrompter


class InHome:
    """The Win32 API as a command line home runs sees it: the real one, except that the console
    is never the command's own.

    `run` waits for a key after a failure when it owns its console, so a window opened just for
    it doesn't vanish with the error (`after_failure`). Home may well own its console (opened by
    double-clicking the CLI), but it shows the error itself and stays open; a key wait there would
    block the command on a key Textual reads."""

    def __init__(self, winapi) -> None:
        self._winapi = winapi

    def __getattr__(self, name: str):
        return getattr(self._winapi, name)

    def owns_console(self) -> bool:
        return False


class RealOperation:
    """An OperationHandle for a command line run in-process. `RealGateway.run` makes it, and it
    starts at once."""

    def __init__(self, deps: Deps, argv: Sequence[str], *, on_end: Callable[[Result], None] | None = None) -> None:
        """on_end(result): what the gateway does once the command has ended, on the command's
        thread, before the result is handed out (RealGateway refreshes an updated sandbox's
        update check with it)."""
        self.argv = tuple(argv)
        self._on_end = on_end
        self.progress = Progress()
        self.switch = CancelSwitch()  # cancel() flips it; the command's runner, pauses and questions honour it
        self._prompter = TuiPrompter(self.progress, self.switch)
        self._deps = deps
        self._result: Result | None = None
        # Made on the UI's event loop, which the worker wakes when the command ends.
        self._loop = asyncio.get_running_loop()
        self._ended = self._loop.create_future()

    def start(self) -> RealOperation:
        # A daemon thread: home quits by cancelling the command and waiting for it to unwind, but
        # should a cleanup call hang, the process must still be able to exit rather than hang too.
        threading.Thread(target=self._work, name=f"{PROG} {' '.join(self.argv)}", daemon=True).start()
        return self

    # -- the worker thread -------------------------------------------------------------------

    def _work(self) -> None:
        out = OperationOutput(self.progress)
        try:
            ended = cli.call(list(self.argv), self._inner_deps(out), out)
            result = Result(ended.exit_code, ended.message, ended.data, _log(ended) or self.progress.log)
        except BaseException as exc:
            result = Result(1, f"{type(exc).__name__}: {exc}", {}, self.progress.log)
        finally:
            out.close()
        out.ended(result.exit_code)  # the plan's last step closes as the command ended
        self.progress.question = None
        if self._on_end is not None:
            try:
                self._on_end(result)  # before the result is out: a screen that sees the end sees its effects
            except Exception as exc:
                self._record(exc, result)
        self._result = result  # set before waking the loop: a screen polling `result` sees it first
        with contextlib.suppress(RuntimeError):  # the loop has closed (home quit meanwhile): nobody is waiting
            self._loop.call_soon_threadsafe(self._wake)

    def _record(self, exc: Exception, result: Result) -> None:
        """Keeps a failure of the gateway's own bookkeeping after the command (on_end) where a
        failure is looked for: the command's transcript, after its own lines (the command went as
        its result says, so the screen isn't told), or, for a command that writes none (open,
        logs), the operation's output, which L shows."""
        if result.log is not None:
            Transcript(Path(result.log)).crash(exc)
        else:
            self.progress.lines.append(f"[error] after the command: {type(exc).__name__}: {exc}")

    def _inner_deps(self, out: OperationOutput) -> Deps:
        """The Deps the command line runs with: the ones home was given, with its streams going to
        the operation's output instead of the terminal Textual owns, its questions asked on the
        screen, a runner and pauses that a cancel stops (Deps.cancellable), and a console that's
        home's, not the command's own (InHome)."""
        return replace(
            self._deps.cancellable(self.switch),
            stdout=out.lines,
            stderr=out.lines,
            prompter=self._prompter,
            winapi=InHome(self._deps.winapi),
        )

    # -- the UI thread -----------------------------------------------------------------------

    def _wake(self) -> None:
        if not self._ended.done():
            self._ended.set_result(self._result)

    @property
    def result(self) -> Result | None:
        return self._result

    async def wait(self) -> Result:
        # Shielded: a screen that stops waiting (its task cancelled) mustn't cancel the future
        # another waiter, or a later look, relies on.
        return await asyncio.shield(self._ended)

    def cancel(self) -> None:
        self.switch.flip()

    def answer(self, value: str | bool) -> None:
        self._prompter.answer(value)  # wakes the worker, which waits in the Prompter's question


def _log(ended: CommandResult) -> str | None:
    return None if ended.log is None else str(ended.log)
