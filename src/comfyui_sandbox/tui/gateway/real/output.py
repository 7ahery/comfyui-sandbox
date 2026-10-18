"""The TUI's `Output`: what a command says, as it says it, written into its operation's `Progress`.

A command line the real gateway runs talks through this instead of the terminal (`cli.call`). The
commands only ever talk through `Output`'s methods and the `Deps` streams, so this one class is
where the TUI sees a command's progress; nothing in a command knows which front end is listening.

It runs on the command's worker thread, and the screens read the `Progress` it writes from the UI
thread (they poll it, see records.Progress): it only ever appends to lists and sets fields, which
the screens can read at any moment without a lock.

Plans: `plan()` fills `progress.plan` with the command's announced steps, `step()` begins each as the
command says it (matched by the core's own rule, `output.Plan`) and `ended()` (called by the operation
once the command returns) closes the last one as done, failed or cancelled. Any command that
announces a plan (comfyui_sandbox.plans) is shown step by step this way with no change here: the
update targets today, backup, recreate, uninstall and install as they move to step tables.

The run window: its stages are `run`'s plan, ended by `plan_done()` once ComfyUI is up; its stop
notice is `progress.notice` (`notice()`); and ComfyUI's own output arrives as lines (`comfyui()`,
which this Output asks for with `shows_comfyui`), from which it reads ComfyUI's start-up, versions
and custom nodes.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from typing import TextIO

from rich.console import Console

from comfyui_sandbox.errors import ExitCode
from comfyui_sandbox.logs import Transcript
from comfyui_sandbox.output import Output, PlannedStep

from ...states import StepStatus
from ..records import Progress, Step


class Lines(io.TextIOBase, TextIO):  # TextIO too, for the type checker: typeshed gives io.TextIOBase no IO base
    """A text stream that hands each complete line it's written to `progress.lines`.

    It's both the `Output`'s streams and the `Deps` streams of a command line run in-process:
    whatever the command prints, through `Output` or straight to `deps.stdout` (`logs --follow`,
    the passthroughs), ends up as lines, in order. A line still being written stays here until
    its newline comes, or the command ends (`close`)."""

    def __init__(self, progress: Progress) -> None:
        super().__init__()
        self.progress = progress
        self._partial = ""

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return False  # so Rich writes plain text: no colours, no cursor moves

    def write(self, text: str) -> int:
        *done, self._partial = (self._partial + text).replace("\r\n", "\n").split("\n")
        self.progress.lines.extend(line.rstrip("\r") for line in done)
        return len(text)

    def close(self) -> None:
        if self._partial:
            self.progress.lines.append(self._partial)
            self._partial = ""
        super().close()


class OperationOutput(Output):
    """An `Output` that writes everything a command says into `progress`, for the screens to show.

    Messages become `progress.lines` as the CLI would have printed them ("==> Stopping ComfyUI",
    "Warning: ...", "Error: ...", and the result message last), so the full log (L) reads like the
    CLI's own output. Once the command has a transcript, its path is `progress.log`, so a screen
    can name it while the command still runs."""

    def __init__(self, progress: Progress) -> None:
        # Not `self.progress`: that's `Output.progress(line)`, the method long steps stream their
        # output through (`run`'s compose up and watch.sh); an attribute of that name hid it.
        self.operation_progress = progress
        self.lines = Lines(progress)
        super().__init__(self.lines, self.lines, json_mode=False)
        # Plain text whatever the environment says (FORCE_COLOR...): these lines are drawn by the
        # TUI, in its own styles, not replayed to a terminal. soft_wrap: one message, one line,
        # however wide; the screen wraps it to its own width.
        self.console = Console(
            file=self.lines, highlight=False, soft_wrap=True, force_terminal=False, color_system=None, emoji=False
        )

    @property
    def transcript(self) -> Transcript | None:
        return self._transcript

    @transcript.setter
    def transcript(self, transcript: Transcript | None) -> None:
        # Session.transcript() sets it when the command starts one; main() sets None once it's
        # closed, which mustn't forget where it was.
        self._transcript = transcript
        if transcript is not None:
            self.operation_progress.log = str(transcript.path)

    # -- the plan: progress.plan, and which of its steps runs ------------------------------------

    def plan(self, steps: Sequence[PlannedStep]) -> None:
        """The command's plan becomes the operation's: every step it will take, pending, before
        the first one runs, so the live view can draw them all and weight its bar by their
        expected seconds. Announced once per command, so it only ever fills the plan."""
        super().plan(steps)
        self.operation_progress.plan = [Step(step.title, step.seconds) for step in steps]

    def step(self, text: str) -> None:
        """A step starts: when it's one of the plan's (the same rule as the CLI's counter,
        output.Plan), the step running ends done, any the command passed over are skipped, and
        this one runs. A step outside the plan is only a line of output: the planned step running
        carries on, since it's that step's work the command is still doing."""
        k = self.planned.match(text) if self.planned is not None else None
        if k is not None:
            plan = self.operation_progress.plan
            for j in range(k):
                if plan[j].status == StepStatus.RUNNING:
                    self.operation_progress.end(j)
                elif plan[j].status == StepStatus.PENDING:
                    plan[j].status = StepStatus.SKIPPED
            self.operation_progress.begin(k)
        self.show_step(text, k)

    def plan_done(self) -> None:
        """The command has taken its plan's last step and carries on without one (`run` holding
        ComfyUI): that step ends done, so the run window reads ComfyUI as up rather than its last
        stage as running for hours."""
        super().plan_done()
        k = self.operation_progress.current
        if k is not None:
            self.operation_progress.end(k)

    def notice(self, text: str) -> None:
        """The command's standing note (`run`'s stop notice) becomes the operation's, for the
        screen to keep showing while it holds."""
        super().notice(text)
        self.operation_progress.notice = text

    # The TUI's screens show ComfyUI's own output: the run window reads its stages, versions and
    # custom nodes from it, and Run here shows it.
    shows_comfyui = True

    def comfyui(self, line: str) -> None:
        """A line of ComfyUI's log, from the thread that reads it. Appended whole, not written
        through `lines` (whose partial line belongs to the command's own thread): a list append is
        safe from any thread, so the two never mix halves of their lines."""
        self.operation_progress.lines.append(line)

    def ended(self, exit_code: int) -> None:
        """The command has ended with exit_code: the step it was on ends done, failed or
        cancelled (exit 130, the interruption). A failure between steps is put on the step it
        would have taken next, so the live view shows where it stopped. Steps it never reached
        stay pending: "not run"."""
        status = (
            StepStatus.DONE
            if exit_code == ExitCode.OK
            else StepStatus.CANCELLED
            if exit_code == ExitCode.INTERRUPTED
            else StepStatus.FAILED
        )
        k = self.operation_progress.current
        if k is None and status != StepStatus.DONE:
            k = next(
                (j for j, step in enumerate(self.operation_progress.plan) if step.status == StepStatus.PENDING), None
            )
            if k is not None:
                self.operation_progress.begin(k)
        if k is not None:
            self.operation_progress.end(k, status)

    def close(self) -> None:
        """The command has ended: a last line printed without a newline is kept too."""
        self.lines.close()
