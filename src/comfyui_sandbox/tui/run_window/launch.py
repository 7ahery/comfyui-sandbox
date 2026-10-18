"""One run window's launch: run.py's stages, ComfyUI's output, and what the screen reads from
them (what's happening now, how far along it is, the facts ComfyUI printed about itself, which
custom nodes failed). It follows `run`'s operation handle (follow): the plan is the stages, the
load stage's sub-progress is how far ComfyUI's start-up is, the output is the lines, and the result
says how it ended. The window sets the phase it asked for itself (stopping)."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from ..gateway import Progress, Result
from ..logs import LogSession
from ..states import LaunchPhase, StageState, StepStatus
from ..theme import RED, SKY, SOFT, VIOLET, clamp, st


@dataclass(eq=False)
class Stage:
    """One of run.py's stages: what it is, where it is, and when it started and ended."""

    name: str
    expected: float  # seconds it is expected to take: what the bar assumes
    state: StageState = StageState.PENDING
    started: float | None = None
    ended: float | None = None

    def took(self, t: float) -> float:
        """Seconds it has run as of t, or in all once it has ended."""
        return 0.0 if self.started is None else (self.ended or t) - self.started


# The plan's title for ComfyUI's own start-up: the real `run`'s step (commands/run.py, WAITING;
# tests/tui/test_real_run_window.py holds the two together).
LOAD_STAGE = "Waiting for ComfyUI to answer"
# What ComfyUI's start-up is doing, as the status line names it (Comfy Desktop's loading line).
LOAD_PARTS = ["Preparing Python", "Loading PyTorch and the GPU", "Loading custom nodes", "Starting the web server"]
# The lines of ComfyUI's own output that start each of LOAD_PARTS after the first. A gateway may
# also count the parts in the load stage's sub-progress (the demo does); the window takes
# whichever is further along, so the real `run`, which only passes ComfyUI's lines on, moves too.
LOAD_MARKS = [
    (re.compile(p), k)
    for p, k in [
        (r"^Checkpoint files will always be loaded safely|^Total VRAM|^pytorch version:", 1),
        (r"^### Loading:|^Import times for custom nodes", 2),
        (r"^Starting server", 3),
    ]
]

# What run prints when the port it was given is taken, when a newer ComfyUI is out, and when the
# sandbox is pinned to a release (then nothing newer is ever out).
PORT_MOVED = re.compile(r"^Using port (\d+) instead")
RELEASE_OUT = re.compile(r"^ComfyUI v(\S+) is available")
NIGHTLY_OUT = re.compile(r"^A newer ComfyUI nightly is available")
PINNED_OUT = re.compile(r"^ComfyUI is pinned to (\S+):")
# How each of the plan's step statuses reads as a stage's.
STAGE_STATE = {
    StepStatus.PENDING: StageState.PENDING,
    StepStatus.SKIPPED: StageState.DONE,
    StepStatus.RUNNING: StageState.RUNNING,
    StepStatus.DONE: StageState.DONE,
    StepStatus.FAILED: StageState.FAILED,
    StepStatus.CANCELLED: StageState.PENDING,
}

# What ComfyUI prints about itself, shown under the sandbox's name as it arrives.
FACTS = [
    (re.compile(p), key)
    for p, key in [
        (r"^ComfyUI version: (\S+)", "ComfyUI"),
        (r"^pytorch version: (\S+)", "PyTorch"),
        (r"Python version: (\S+)", "Python"),
        (r"^(?:ComfyUI frontend|comfyui-frontend-package) version: (\S+)", "Frontend"),
    ]
]
# What `run` says ComfyUI runs once it answers, from its /system_stats ("Versions: ComfyUI 0.40.0,
# Frontend 1.55.17, ..."; run.py's versions_line). It settles what FACTS read from the log.
VERSIONS_SAID = re.compile(r"^Versions: (.+)")
NODE_LINE = re.compile(r"^\s+([\d.]+) seconds( \(IMPORT FAILED\))?: \S*/custom_nodes/(\S+)")
# The level ComfyUI's logging may put in front of a line ("[INFO] Starting server"): the patterns
# above read the line without it.
LEVEL = re.compile(r"^\[(?:DEBUG|INFO|WARNING|ERROR|CRITICAL)\] ?")
# ComfyUI's output has landmarks of its own, lit on top of the logs' usual ones.
EXTRA_RULES = [
    (re.compile(p), st(c))
    for p, c in [
        (r"\(IMPORT FAILED\).*|^Traceback.*|^\w*Error:.*|^Cannot import.*|^\[error\].*", RED),
        (r"\d+\.\d+ seconds", SOFT),
        (r"^\*\* .*|^#{2,3} .*", VIOLET),
        (r"^\[[\w -]+\]", SKY),
    ]
]


class Launch:
    """One run window's life: the stages, ComfyUI's output (in a LogSession, as home's logs), and
    what the output says went wrong, if anything."""

    def __init__(self, distro: str, port: int, models: str) -> None:
        self.distro, self.port, self.models = distro, port, models
        self.born = time.monotonic()
        self.phase = LaunchPhase.STARTING
        self.stages: list[Stage] = []  # run's plan, once it has said it
        self.part = -1  # LOAD_PARTS index while Load ComfyUI runs
        self.log = LogSession()
        self.read = 0  # how many of the operation's lines have been taken in (add)
        self.section = ""  # which "... times for custom nodes" list the output is in
        self.failed_nodes: list[str] = []
        # key: (value, when it arrived). The models folder is the install's own: known from the start.
        self.facts: dict[str, tuple[str, float]] = {"Models": (models, self.born)}
        self.nodes = 0  # custom nodes that loaded
        self.error: str | None = None
        self.update: str | None = None  # what's out, when it's newer than what runs
        self.pinned: str | None = None  # the release the sandbox is pinned to, when it is
        self.ready_at: float | None = None
        self.stop_started: float | None = None
        self.final: float | None = None  # the progress when it failed: the bar stops there
        self.notice = ""  # `run`'s standing note (its stop notice), as it last said it
        self.asking = False  # `run` waits on a question (the window asks it under the loader)
        self.log_path: str | None = None  # the transcript, after a failure

    # What happens

    def follow(self, progress: Progress, result: Result | None) -> None:
        """Takes in how `run` is going: its plan as the stages, each step's status and times, the
        load stage's sub-progress as ComfyUI's part, the new lines, and once it has ended, how.
        The window calls it as it goes; each call only moves things on."""
        if not self.stages and progress.plan:
            self.stages = [Stage(step.title, step.seconds) for step in progress.plan]
        for stage, step in zip(self.stages, progress.plan, strict=False):
            state = STAGE_STATE[step.status]
            if state == StageState.FAILED and stage.state != StageState.FAILED:
                self.final = self.progress(step.ended or time.monotonic())  # the bar stops where it failed
            stage.state, stage.started, stage.ended = state, step.started, step.ended
            if stage.name == LOAD_STAGE and state == StageState.RUNNING:
                # Its first part from the moment it starts; then the sub-progress or the lines
                # (add), whichever is further along.
                self.part = max(self.part, 0, min(len(LOAD_PARTS) - 1, int(progress.sub * len(LOAD_PARTS))))
        self.notice = progress.notice
        self.asking = progress.question is not None
        # The lines may grow on another thread meanwhile (ComfyUI's log): count what was taken,
        # not what's there now, so a line appended in between isn't skipped.
        new = progress.lines[self.read :]
        for line in new:
            self.add(line)
        self.read += len(new)
        if self.phase in (LaunchPhase.STARTING, LaunchPhase.RUNNING) and any(
            s.state == StageState.FAILED for s in self.stages
        ):
            self.phase = LaunchPhase.FAILED
        elif (
            self.phase == LaunchPhase.STARTING and self.stages and all(s.state == StageState.DONE for s in self.stages)
        ):
            self.phase, self.ready_at = LaunchPhase.RUNNING, self.stages[-1].ended or time.monotonic()
        if result is not None:
            self.end(result)

    def end(self, result: Result) -> None:
        """`run` has ended: a second window only opened the browser; a stop (asked for, or ComfyUI
        stopping) leaves it stopped, as does an interruption (Ctrl+C twice: `run` ends as
        interrupted once its own cleanup has stopped ComfyUI); anything else is a failure, with
        run's own message and the transcript that has the rest."""
        if result.data.get("already_running"):
            self.phase = LaunchPhase.ELSEWHERE
        elif (result.ok or result.interrupted) and self.phase != LaunchPhase.FAILED:
            self.phase = LaunchPhase.STOPPED
        else:
            self.phase, self.error, self.log_path = LaunchPhase.FAILED, result.message, result.log

    def stopping(self) -> None:
        """The window has asked `run` to stop (Ctrl+C twice): it reads as stopping until it has."""
        self.phase, self.stop_started = LaunchPhase.STOPPING, time.monotonic()

    def add(self, line: str) -> None:
        """A line of ComfyUI's (or run.py's) output."""
        self.log.add(line)
        text = self.log.lines[-1]
        for pattern, style in EXTRA_RULES:
            text.highlight_regex(pattern, style)
        line = LEVEL.sub("", line, count=1)
        if "times for custom nodes" in line:
            self.section = line
        for pattern, k in LOAD_MARKS:
            if pattern.match(line):
                self.part = max(self.part, k)
        if m := PORT_MOVED.match(line):
            self.port = int(m.group(1))
        if m := RELEASE_OUT.match(line):
            self.update = f"ComfyUI {m.group(1)}"
        elif NIGHTLY_OUT.match(line):
            self.update = "A newer ComfyUI nightly"
        elif m := PINNED_OUT.match(line):
            self.pinned = m.group(1)
        elif m := VERSIONS_SAID.match(line):
            for name, _, value in (part.partition(" ") for part in m.group(1).split(", ")):
                if value:  # a value the log gave keeps when it arrived: it doesn't flash again
                    self.facts[name] = (value, self.facts.get(name, (value, time.monotonic()))[1])
        for pattern, key in FACTS:
            m = pattern.search(line)
            if m and key not in self.facts:
                self.facts[key] = (m.group(1), time.monotonic())
        m = NODE_LINE.match(line)
        if m and self.section.startswith("Import"):
            if m.group(2):
                self.failed_nodes.append(m.group(3))
            else:
                self.nodes += 1
            self.facts["Custom nodes"] = (str(self.nodes), self.facts.get("Custom nodes", ("", time.monotonic()))[1])

    # What the screen reads

    def url(self) -> str:
        """Where ComfyUI answers from Windows."""
        return f"http://127.0.0.1:{self.port}"

    def alive(self) -> bool:
        """Whether closing the window would stop something."""
        return self.phase in (LaunchPhase.STARTING, LaunchPhase.RUNNING)

    def doing(self) -> str:
        """The one line that says what's happening now."""
        if self.asking:
            return "Waiting for your answer"
        stage = next((s for s in self.stages if s.state == StageState.RUNNING), None)
        if stage is None:
            return ""
        if stage.name == LOAD_STAGE and self.part >= 0:
            return LOAD_PARTS[self.part]
        return stage.name

    def load_stage(self) -> Stage | None:
        """The long stage, ComfyUI's own start-up, which the banner's failure and glitch time from
        (None before the plan is known, or for a window that only opened the browser)."""
        return next((s for s in self.stages if s.name == LOAD_STAGE), None)

    def progress(self, t: float) -> float:
        """How far along the start-up is at t, 0..1: the finished stages' expected time, plus the
        running one's elapsed share (capped at 0.9 of it, so the bar waits for the stage to
        really end). In Load ComfyUI the part its output has reached sets a floor. After a
        failure, it stays where it stopped."""
        if self.final is not None:
            return self.final
        total = sum(s.expected for s in self.stages) or 1.0
        done = 0.0
        for s in self.stages:
            if s.state == StageState.DONE:
                done += s.expected
            elif s.state == StageState.RUNNING:
                if s.name == LOAD_STAGE:
                    part, n = max(0, self.part), len(LOAD_PARTS)
                    f = (part + min(0.9, s.took(t) / s.expected * n - part)) / n
                    done += s.expected * clamp(max(part / n, f))
                else:
                    done += s.expected * min(0.9, s.took(t) / s.expected)
        return clamp(done / total)
