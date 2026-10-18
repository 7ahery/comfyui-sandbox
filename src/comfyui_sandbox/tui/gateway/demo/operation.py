"""FAKE: the demo's operation handle. A command's play (a coroutine) runs as a task on the screen's
own event loop, writing the handle's progress as it goes, and returns the result.

The real gateway runs commands on a worker thread instead; the screens can't tell, because both
only write Progress and set the result. The demo's waits are the fakes' expected seconds divided by
the demo's speed, and a cancel wakes whatever wait the play is in, as the real Runner's cancel
switch interrupts its child process.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Coroutine, Sequence
from typing import Any

from ...states import StepStatus
from ..records import INTERRUPTED, Progress, Question, Result, Step


class Cancelled(Exception):
    """Raised inside a play by the wait a cancel woke: the command is being stopped."""


def log_path(command: str) -> str:
    """Where a command's transcript would be saved (the demo writes none)."""
    return rf"~\.comfyui-sandbox\logs\{command}-{time.strftime('%Y%m%d-%H%M%S')}.log"


Play = Coroutine[Any, Any, Result]  # a coroutine object, already made (see DemoOperation.start)


class DemoOperation:
    """An OperationHandle for a play. Made by DemoGateway.run, which starts it at once."""

    def __init__(self, argv: Sequence[str], speed: float) -> None:
        self.argv = tuple(argv)
        self.speed = speed
        self.progress = Progress(log=log_path(self.argv[0]))
        self._result: Result | None = None
        self._cancelled = asyncio.Event()
        self._ended = asyncio.Event()
        self._answer: asyncio.Future[str | bool] | None = None
        self._task: asyncio.Task | None = None

    def start(self, play: Play) -> DemoOperation:
        """Runs play as a task. play is a coroutine object, already made: what the command does
        before its first wait (adding a sandbox, drawing random output) has happened by now."""
        self._task = asyncio.get_running_loop().create_task(self._run(play))
        return self

    async def _run(self, play: Play) -> None:
        try:
            result = await play
        except Cancelled:
            for step in self.progress.plan:
                if step.status == StepStatus.RUNNING:
                    step.status, step.ended = StepStatus.CANCELLED, time.monotonic()
            result = Result(INTERRUPTED, "Interrupted.")
        self.progress.question = None
        self._result = Result(result.exit_code, result.message, result.data, self.progress.log)
        self._ended.set()

    # -- the handle --------------------------------------------------------------------------

    @property
    def result(self) -> Result | None:
        return self._result

    async def wait(self) -> Result:
        await self._ended.wait()
        assert self._result is not None
        return self._result

    def cancel(self) -> None:
        self._cancelled.set()
        if self._answer is not None and not self._answer.done():
            self._answer.set_exception(Cancelled())

    def answer(self, value: str | bool) -> None:
        if self._answer is not None and not self._answer.done():
            self._answer.set_result(value)

    # -- what a play calls -------------------------------------------------------------------

    def plan(self, steps: list[tuple[str, float]]) -> None:
        """Announces the steps, as (title, expected seconds at speed 1)."""
        self.progress.plan = [Step(title, seconds / self.speed) for title, seconds in steps]

    def replan(self, steps: list[tuple[str, float]]) -> None:
        """Announces the plan again (as install does once its answers are in, Output.plan): the
        steps it has reached so far keep how they went, the rest are replaced."""
        plan = self.progress.plan
        reached = next((k for k in range(len(plan) - 1, -1, -1) if plan[k].status != StepStatus.PENDING), -1) + 1
        self.progress.plan = [
            *plan[:reached],
            *(Step(title, seconds / self.speed) for title, seconds in steps[reached:]),
        ]

    def say(self, line: str) -> None:
        """A line of the command's output."""
        self.progress.lines.append(line)

    async def nap(self, seconds: float) -> None:
        """Waits seconds (at speed 1). Raises Cancelled as soon as the operation is cancelled."""
        if self._cancelled.is_set():
            raise Cancelled
        try:
            await asyncio.wait_for(self._cancelled.wait(), seconds / self.speed)
        except TimeoutError:
            return
        raise Cancelled

    async def sleep(self, seconds: float) -> None:
        """Waits seconds (at speed 1), cancel or not: for a command's cleanup, which runs anyway."""
        await asyncio.sleep(seconds / self.speed)

    async def ask(self, question: Question) -> str | bool:
        """Waits for the screen's answer to question. A cancel meanwhile raises Cancelled."""
        if self._cancelled.is_set():
            raise Cancelled
        self._answer = asyncio.get_running_loop().create_future()
        self.progress.question = question
        try:
            return await self._answer
        finally:
            self._answer = None

    def asked(self) -> None:
        """The answer has been acted on: the question is no longer waiting. A play calls it once
        the answer's effect is in place, so a screen that sees the question gone and reads the
        sandboxes again finds the change."""
        self.progress.question = None
