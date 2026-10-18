"""The cancel switch: how the TUI stops a command it runs, the way Ctrl+C stops one at a terminal.

A command's `run` and `stream` calls honour it through `Runner.cancellable(switch)`, and its pauses
through `Deps.cancellable(switch)`, which also wires up the runner. It lives in a module of its own,
not in `runner`, so the TUI's gateway can hold one without importing the platform layer (the import
contracts in pyproject.toml).
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Generator
from contextlib import contextmanager
from typing import Protocol


class Child(Protocol):
    """A running child process, as far as the switch needs one."""

    def terminate(self) -> None: ...


class CancelSwitch:
    """Stops a command from another thread, the way Ctrl+C stops it at a terminal (spec: the
    `Runner` cancel switch). Thread-safe: the command runs on a worker thread, and the TUI flips
    the switch from its own.

    `flip()` terminates the child the command is running, if any, and the command's thread then
    raises `KeyboardInterrupt`. The command unwinds through its `finally` blocks as it does on a
    Ctrl+C (the keepalive is released, the container stopped), and `main()` ends it as interrupted
    (130). The interruption is raised once: the calls its cleanup makes afterwards run normally,
    or a cancel would leave the very mess cleanup is there to tidy. A flip that lands between two
    calls isn't lost: the next call raises it before it starts anything.

    The CLI never makes one: at a terminal, Ctrl+C reaches the child and the CLI as it always has."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._flipped = False
        self._raised = False  # the interruption has been raised: from now on, calls run normally
        self._child: Child | None = None  # the child of the call running now, if any
        self._woken = threading.Event()  # set by flip(): ends a pause (wait) at once

    @property
    def flipped(self) -> bool:
        return self._flipped

    def flip(self) -> None:
        """Cancels the command: terminates its running child, and has its thread raise the
        interruption. Flipping again does nothing (a second cancel mustn't cut its cleanup short)."""
        with self._lock:
            if self._flipped:
                return
            self._flipped = True
            child = self._child
        self._woken.set()
        if child is not None:
            _terminate(child)

    def wait(self, seconds: float) -> None:
        """Pauses for seconds, or until the switch is flipped, whichever comes first: a pause
        between tries (Deps.cancellable) ends with the flip, not when its time is up."""
        self._woken.wait(seconds)

    def check(self) -> None:
        """Raises the interruption if the switch was flipped and it hasn't been raised yet. Called
        between steps: before and after each call, and around each pause."""
        with self._lock:
            if not self._flipped or self._raised:
                return
            self._raised = True
        raise KeyboardInterrupt

    @contextmanager
    def running(self, child: Child) -> Generator[None]:
        """child is the call running now: a flip meanwhile terminates it. A flip that came after
        the call's `check()` but before the child existed terminates it at once."""
        with self._lock:
            self._child = child
            late = self._flipped and not self._raised
        if late:
            _terminate(child)
        try:
            yield
        finally:
            with self._lock:
                self._child = None


def _terminate(child: Child) -> None:
    with contextlib.suppress(OSError):  # it ended on its own meanwhile
        child.terminate()
