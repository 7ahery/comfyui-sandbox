"""The records a gateway hands the screens: sandboxes, Check this PC's items, GPU readings, and an
operation's live progress and result.

They are the TUI's own types, never the core's (`Install`, `Output`...): the screens are written
against these, so the core can change shape, and a redesign can rewrite the screens, without either
touching the other. Every field is named by what it means to someone looking at home, not by how a
gateway finds it out (a marker, a reach check's `os`, `nvidia-smi`), so a gateway for another
platform fills the same fields its own way.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum

from ..states import SandboxState, StepStatus


@dataclass(eq=False)
class Sandbox:
    """A sandbox as home lists it.

    eq=False: two are the same only if they are the same object, which is how the table and the
    menus find the one under the pointer. The TUI keeps one record per sandbox for the whole
    session and copies each new listing into it (SandboxApp.read_sandboxes), so that identity holds
    across refreshes; anything that needs a key (the table's measurements) copies out the fields
    it uses.

    A gateway reports `state` as running, stopped, incomplete or starting (spec: the state rule).
    The TUI may show another state in its place while its own command runs (SandboxApp.mark)."""

    name: str
    state: SandboxState
    port: int
    models: str = "~/comfyui-models"
    # The `update` targets with an update available, as far as the gateway's background check has
    # found so far (it checks once per session, and again after an update).
    updates: tuple[str, ...] = ()
    # For those targets that have versions, (installed, newest available), as the same check read them.
    update_versions: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    # Which ComfyUI it follows: "latest", "nightly", or the release number it's pinned to ("0.3.40").
    # None: not known (taken as latest).
    channel: str | None = None
    # Notes, not states. Whether a run window is open for it (None: not known), and who holds its
    # port when that isn't its own ComfyUI (None: no one, or not known).
    run_window: bool | None = None
    port_held_by: str | None = None

    @property
    def update_ready(self) -> bool:
        """Whether any part of it has an update available."""
        return bool(self.updates)

    @property
    def pinned(self) -> str | None:
        """The ComfyUI release it's pinned to, or None when it follows latest or nightly."""
        return self.channel if self.channel not in (None, "latest", "nightly") else None


@dataclass(frozen=True)
class CheckItem:
    """One line of Check this PC: what was checked, what was found, and whether that passes.
    The screen draws whatever list it is given, so each platform's check brings its own rows."""

    label: str
    value: str
    ok: bool = True
    fixable: bool = False  # the machine can set this right itself (WSL: `prereqs`)


@dataclass(frozen=True)
class GpuReading:
    """One reading of the whole machine's GPU: how busy it is (0..1) and its memory, in GB.
    For the whole machine, not per sandbox: per-sandbox numbers would mean asking each distro."""

    utilisation: float
    memory_used: float
    memory_total: float


@dataclass(eq=False)
class Step:
    """A step of an operation's plan, and how it's going. The gateway writes it; the screens read
    it. started and ended are time.monotonic() readings, so a screen can time each step without
    having watched it start."""

    title: str
    seconds: float  # how long it's expected to take: the overall bar is weighted by these
    status: StepStatus = StepStatus.PENDING
    started: float | None = None
    ended: float | None = None

    def took(self, now: float) -> float:
        """Seconds it has run as of now, or in all once it has ended."""
        return 0.0 if self.started is None else (self.ended or now) - self.started


class QuestionKind(StrEnum):
    """What kind of answer a Question wants: one of its choices, free text, or yes or no, as the
    core's Prompter asks them."""

    SELECT = "select"
    TEXT = "text"
    CONFIRM = "confirm"


@dataclass(frozen=True, eq=False)
class Question:
    """A question the operation is waiting on: it stops until the screen answers it
    (OperationHandle.answer) or backs out of it (OperationHandle.cancel, which ends the operation
    as interrupted). They are the core Prompter's questions, so they come in its three kinds:

    - SELECT: answered with the value of one of `choices`, (title, value) pairs as questionary's
      are. `default` is the value the pointer starts on (None: the first).
    - TEXT: answered with the text typed. `default` is the text already in the field; `validate`
      returns why a text won't do, or None when it will, so the screen can check as it's typed
      (a gateway asks again if an answer that doesn't pass gets through). `confirms`, when not
      empty, makes it a destructive action's type-the-name confirmation: the names, any of which
      confirms it, so the screen can draw it as typing the name (the core's `type_to_confirm`).
    - CONFIRM: answered with True or False. `default` is the answer Enter takes.

    lead: a line that explains it, shown above it. hint: a line that explains it, shown with it;
    hints: a line per choice, by value, shown beside the choice under the pointer (the core's
    display hints, prompts.Prompter). eq=False: a screen tells a new question from the one it's
    asking by identity, so the same question asked again is a new one."""

    message: str
    choices: tuple[tuple[str, str], ...] = ()
    lead: str = ""
    kind: QuestionKind = QuestionKind.SELECT
    hint: str = ""
    hints: Mapping[str, str] = field(default_factory=dict)
    default: str | bool | None = None
    validate: Callable[[str], str | None] | None = None
    confirms: tuple[str, ...] = ()


@dataclass(eq=False)
class Progress:
    """An operation's live progress: its plan, which step runs, what it has printed, how far the
    running step is, the question it waits on, if any, and its standing notice.

    It's state, not a stream of events, so a screen can look at an operation at any moment, also
    one it didn't start or watch from the beginning (what background operations will need). The
    gateway writes it, possibly from a worker thread; screens only read it."""

    plan: list[Step] = field(default_factory=list)  # empty for commands that declare none (start, stop)
    lines: list[str] = field(default_factory=list)  # everything it has printed so far, in order
    sub: float = 0.0  # how far the running step is, 0..1, when the command can tell
    log: str | None = None  # the transcript's path, once there is one
    question: Question | None = None
    # A standing note the command keeps true until it says another ("" for none): `run`'s stop
    # notice, which the run window keeps on screen while it holds.
    notice: str = ""

    @property
    def current(self) -> int | None:
        """The index of the step running now, if one is."""
        return next((k for k, step in enumerate(self.plan) if step.status == StepStatus.RUNNING), None)

    @property
    def through(self) -> bool:
        """Whether the command has taken its whole plan: one announced, every step done or skipped.
        `run`'s is through once ComfyUI answers and Windows reaches it (its plan_done), not before."""
        return bool(self.plan) and all(step.status in (StepStatus.DONE, StepStatus.SKIPPED) for step in self.plan)

    def begin(self, k: int) -> None:
        """Step k starts (the gateway's side)."""
        self.sub = 0.0  # reset first, so the new step is never seen with the last one's share
        self._publish(k, status=StepStatus.RUNNING, started=time.monotonic())

    def end(self, k: int, status: StepStatus = StepStatus.DONE) -> None:
        """Step k ends as status: done, failed or cancelled (the gateway's side)."""
        self._publish(k, status=status, ended=time.monotonic())

    def _publish(self, k: int, **changes) -> None:
        """Puts a new step k in the plan, with changes. The gateway writes from its own thread while
        a screen reads: changing the step's fields one by one could show it running with no start
        time, or ended and still running. Swapping in a whole new Step is one list store, which the
        reader sees either before or after, never half-made."""
        self.plan[k] = replace(self.plan[k], **changes)


@dataclass(frozen=True)
class Result:
    """How a command line ended: what `main()` hands back, the same content as its `--json`
    document. exit_code follows the CLI's exit codes (0 OK, 130 interrupted...)."""

    exit_code: int
    message: str = ""
    data: Mapping = field(default_factory=dict)
    log: str | None = None  # the transcript's path

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    @property
    def interrupted(self) -> bool:
        """Whether it was cancelled (Esc, a cancelled question) rather than failed."""
        return self.exit_code == INTERRUPTED


WSL_NOT_READY = 7  # the CLI's ExitCode.WSL_NOT_READY: `prereqs` can fix it
INTERRUPTED = 130  # the CLI's ExitCode.INTERRUPTED: Ctrl+C, or a question cancelled
