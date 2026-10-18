"""Human output on stderr; the result on stdout (docs/scripting.md, "Streams" and "The --json result document")."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from rich.console import Console

from comfyui_sandbox.errors import ExitCode
from comfyui_sandbox.logs import Transcript


@dataclass
class Outcome:
    """What a successful command returns."""

    message: str
    data: dict[str, Any] = field(default_factory=dict)
    log: Path | None = None


@dataclass(frozen=True)
class CommandResult:
    """How a command line ended: the content of its `--json` document (docs/scripting.md, "The --json
    result document"), as a value.

    `main()` prints it (under --json) and turns it into the exit code; `cli.call()` hands it back
    to a front end running the command line in-process, which shows it its own way. Building the
    printed document from this same value (`document()`) is what keeps the two from drifting apart.
    """

    command: str  # the command words ("update comfyui"); "" when none could be found
    exit_code: int
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    log: Path | None = None  # the transcript, when one was written
    flag: str | None = None  # the flag that answers a missing answer (exit 9, a refused config value)

    @property
    def ok(self) -> bool:
        return self.exit_code == ExitCode.OK

    @property
    def error(self) -> str | None:
        """The error message: the message itself, on a failure."""
        return None if self.ok else self.message

    def document(self) -> dict[str, Any]:
        """The `--json` document, key for key."""
        doc: dict[str, Any] = {
            "command": self.command,
            "ok": self.ok,
            "exit_code": int(self.exit_code),
            "message": self.message,
            "data": self.data,
        }
        if not self.ok:
            doc["error"] = self.message
        if self.log is not None:
            doc["log"] = str(self.log)
        if self.flag is not None:
            doc["flag"] = self.flag
        return doc


@dataclass(frozen=True)
class PlannedStep:
    """A step of a long command's plan (`Output.plan`): its title, as the command's `step(...)`
    will say it, and how many seconds it's expected to take.

    The seconds are hand-set estimates that live next to each step in its command's step table
    (`comfyui_sandbox.plans`). They weight a progress bar and are never a timeout: a step that
    takes longer just takes longer."""

    title: str
    seconds: float


class Plan:
    """The plan a command announced, and how far its steps have got through it.

    `match(title)` is the one rule for which planned step a `step(...)` is: the next planned step
    with that title, at or after the one after the last match. Steps passed over on the way were
    left out by the command as it ran (a container already up needs no start); a title the plan
    doesn't have further on is a step outside the plan, and matches nothing. Every `Output` (the
    CLI's counter, the TUI's live view) follows this one rule, so they can't disagree on where a
    command is."""

    def __init__(self, steps: Sequence[PlannedStep]) -> None:
        self.steps = tuple(steps)
        self._next = 0  # the first step a step(...) may still be

    def match(self, title: str) -> int | None:
        """The index of the planned step `title` starts, or None; moves past it."""
        for k in range(self._next, len(self.steps)):
            if self.steps[k].title == title:
                self._next = k + 1
                return k
        return None


class Output:
    """The two streams. stdout gets only the result; everything for people goes to stderr.

    A front end that runs command lines in-process (`cli.call`) passes a subclass of its own: the
    commands only ever talk through these methods, so overriding them is how a front end sees a
    command's progress as it happens (the TUI's is in tui/gateway/real/output.py).
    """

    def __init__(self, stdout: TextIO, stderr: TextIO, *, json_mode: bool) -> None:
        self.stdout, self.json_mode = stdout, json_mode
        self.console = Console(file=stderr, highlight=False, soft_wrap=True)
        # Set by Session.transcript(): every message below is also written there.
        self.transcript: Transcript | None = None
        self.planned: Plan | None = None  # what plan() announced, when the command declares a plan

    # -- progress and messages (stderr, and the transcript) -------------------
    def plan(self, steps: Sequence[PlannedStep]) -> None:
        """A long command announces every step it will take, before the first one runs.

        `steps` are already filtered by the command's situation (its answers, whether ComfyUI is
        running...), so each is one the command expects to say through `step(...)`, in this
        order. Short single actions (start, stop) announce none. The CLI only numbers the steps
        that follow ("==> [3/5] ..."); a front end can show the whole plan as it goes (the TUI's
        live view, tui/gateway/real/output.py).

        A plan is announced exactly once per command, where its steps are known. A command whose
        steps depend on answers it has to ask for first (install) runs those questions unplanned
        and announces afterwards, so the count never resets."""
        self.planned = Plan(steps)

    def step(self, text: str) -> None:
        """A user-visible step starts. A step of the announced plan is numbered."""
        self.show_step(text, self.planned.match(text) if self.planned is not None else None)

    def show_step(self, text: str, k: int | None) -> None:
        """Prints the step `text`, which is the plan's step `k` (None: outside any plan), and logs
        it. Apart from step() so that an `Output` that follows the plan itself (the TUI's) matches
        each step once and still prints it the CLI's way. The transcript keeps the bare title."""
        counter = f"[{k + 1}/{len(self.planned.steps)}] " if k is not None and self.planned is not None else ""
        self.console.print(f"==> {counter}{text}", style="bold", markup=False)
        if self.transcript is not None:
            self.transcript.step(text)

    def plan_done(self) -> None:
        """Every step of the plan is behind the command, which carries on without one: `run`,
        holding ComfyUI in the foreground until its window ends. The CLI has nothing to print (the
        command's own lines say what it's doing now); a front end that shows the plan ends the
        last step here instead of leaving it running for hours."""

    def notice(self, text: str) -> None:
        """A standing note that holds from now until the command says another ("" for none):
        `run`'s stop notice, "Closing this window stops ComfyUI.", true for as long as closing the
        window would stop it. A front end that keeps a note on screen (the TUI's run window) shows
        it; the CLI, whose output scrolls, prints nothing, since the command says what matters in
        its own lines where it matters (`run`'s "Close this window to stop ComfyUI.")."""

    # Whether this Output shows a running ComfyUI's own output as it comes. The CLI doesn't (its
    # `run` window prints its steps; `logs --follow` shows the log), so a command only follows
    # ComfyUI's log, or looks for what it would report from it, when this is True (the TUI's run
    # window, which reads its start-up steps, versions and custom nodes from those lines).
    shows_comfyui: bool = False

    def comfyui(self, line: str) -> None:
        """One line of ComfyUI's own output (its container log), as it comes. Only called when
        `shows_comfyui`; it may be called from a thread of its own, the one reading the log, while
        the command says other things on its own thread. Not written to the transcript: the
        container keeps its log, and a run window's would fill the transcript for hours."""
        self.progress(line)

    def progress(self, line: str) -> None:
        """One line of a long step's output. stderr only: the wsl call already logged it
        to the transcript."""
        self.console.print(line, style="dim", markup=False)

    def info(self, text: str) -> None:
        self.console.print(text, markup=False)
        if self.transcript is not None:
            self.transcript.info(text)

    def warn(self, text: str) -> None:
        self.console.print(f"Warning: {text}", style="yellow", markup=False)
        if self.transcript is not None:
            self.transcript.warn(text)

    def error(self, text: str) -> None:
        self.console.print(f"Error: {text}", style="red", markup=False)
        if self.transcript is not None:
            self.transcript.error(text)

    # -- the result (stdout) -------------------------------------------------
    def _document(self, doc: dict[str, Any]) -> None:
        self.stdout.write(json.dumps(doc, ensure_ascii=False) + "\n")
        self.stdout.flush()

    def result(self, command: str, outcome: Outcome) -> CommandResult:
        """The command succeeded: print its result (the message, or the document under --json)."""
        done = CommandResult(command, ExitCode.OK, outcome.message, outcome.data, outcome.log)
        if self.json_mode:
            self._document(done.document())
        elif outcome.message:
            print(outcome.message, file=self.stdout)
            self.stdout.flush()
        return done

    def failure(
        self,
        command: str,
        code: int,
        message: str,
        *,
        data: dict[str, Any] | None = None,
        log: Path | None = None,
        flag: str | None = None,
    ) -> CommandResult:
        """The command failed: say so on stderr with the transcript's path, and print the document
        under --json."""
        done = CommandResult(command, int(code), message, data or {}, log, flag)
        self.error(message)
        if log is not None:
            self.console.print(f"Log: {log}", markup=False)
        if self.json_mode:
            self._document(done.document())
        return done
