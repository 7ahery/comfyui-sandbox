"""The TUI's `Prompter`: the questions a command asks while home runs it, asked on home's screen.

A command line home runs calls the core's Prompter as it would at a terminal (`config`'s menu, the
destructive typed confirmation, "Which sandbox?"). It runs on its operation's worker thread
(operation.py), so asking is a hand-over between two threads:

1. the worker puts the question on the operation's progress (`progress.question`) and blocks;
2. the UI thread, which polls progress, sees it, asks it on screen, and answers it through the
   operation handle (`answer()`), which wakes the worker with the answer;
3. or it backs out (Esc, Ctrl+C on the question) through the handle's `cancel()`: the worker
   wakes and raises the interruption a Ctrl+C at a terminal raises, so the command ends as
   interrupted (exit 130), exactly as a cancelled terminal prompt ends it.

The question's display hints (prompts.Prompter) travel with it, for the screen to show.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence

from comfyui_sandbox.cancel import CancelSwitch

from ..records import Progress, Question, QuestionKind

# How often a waiting question looks at the cancel switch. The switch is the operation's (its
# runner and pauses honour it too) and tells no one when it's flipped, so it's polled: often
# enough that backing out feels immediate.
CANCEL_POLL = 0.05


class TuiPrompter:
    """A `Prompter` (comfyui_sandbox.prompts) whose questions go to the screen through `progress`.

    switch: the operation's cancel switch; a question waiting when it's flipped (or asked after) is
    backed out of."""

    def __init__(self, progress: Progress, switch: CancelSwitch) -> None:
        self.progress = progress
        self.switch = switch
        self._lock = threading.Lock()  # guards the question being answered and its answer
        self._waiting: Question | None = None
        self._answer: str | bool | None = None
        self._answered = threading.Event()

    # -- the Prompter, on the worker thread ------------------------------------------------------

    def select(
        self,
        message: str,
        choices: Sequence[str],
        default: str | None = None,
        *,
        hint: str = "",
        hints: Mapping[str, str] | None = None,
    ) -> str:
        # The core's choices are plain strings: each is both what's shown and the answer.
        return str(
            self._ask(
                Question(
                    message,
                    tuple((c, c) for c in choices),
                    kind=QuestionKind.SELECT,
                    hint=hint,
                    hints=dict(hints or {}),
                    default=default,
                )
            )
        )

    def text(
        self,
        message: str,
        default: str = "",
        validate: Callable[[str], str | None] | None = None,
        *,
        hint: str = "",
        type_to_confirm: Sequence[str] = (),
    ) -> str:
        while True:  # the Prompter's contract: asked again until validate passes it
            value = str(
                self._ask(
                    Question(
                        message,
                        kind=QuestionKind.TEXT,
                        hint=hint,
                        default=default,
                        validate=validate,
                        confirms=tuple(type_to_confirm),
                    )
                )
            )
            if validate is None or validate(value) is None:
                return value

    def confirm(self, message: str, default: bool = True, *, hint: str = "") -> bool:
        return bool(self._ask(Question(message, kind=QuestionKind.CONFIRM, hint=hint, default=default)))

    def _ask(self, question: Question) -> str | bool:
        """Puts question on the progress and blocks until it's answered, or backed out of."""
        self._back_out_if_cancelled()
        with self._lock:
            self._waiting, self._answer = question, None
            self._answered.clear()
        self.progress.question = question
        try:
            while not self._answered.wait(CANCEL_POLL):
                self._back_out_if_cancelled()
            if self._answer is None:  # answer() sets it before it sets the event
                raise AssertionError("the question was answered without an answer")
            return self._answer
        finally:
            with self._lock:
                self._waiting = None
            # Gone once answered: a screen waiting for it to go then knows the command carries on.
            self.progress.question = None

    def _back_out_if_cancelled(self) -> None:
        """Raises the interruption when the operation has been cancelled. Through the switch, so it
        counts as the cancel's one interruption and the cleanup's calls then run normally; and
        raised even after that, since a question can't be answered once its screen has let go."""
        if self.switch.flipped:
            self.switch.check()
            raise KeyboardInterrupt

    # -- the UI thread ---------------------------------------------------------------------------

    def answer(self, value: str | bool) -> None:
        """Answers the question waiting now. One that comes when nothing waits (a second press,
        or after a cancel) is dropped: it can't be meant for a question not yet asked."""
        with self._lock:
            if self._waiting is None or self._answered.is_set():
                return
            self._answer = value
            self._answered.set()
