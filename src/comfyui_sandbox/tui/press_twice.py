"""Keys that act only on their second press, so a stray key doesn't cancel an operation or stop
ComfyUI. The first press arms the key and the screen says to press it again; the second, within
the window, acts."""

from __future__ import annotations

import time

from rich.text import Text

from .parts import bar
from .theme import AMBER, FAINT, st

CANCEL_WINDOW = 3.0  # seconds for the second press


class PressTwice:
    """One key's two-step press. press(key) says whether this was the second, within `window`."""

    def __init__(self, window: float = CANCEL_WINDOW) -> None:
        self.window = window
        self.armed: tuple[str, float] | None = None  # (key, until)

    def key(self) -> str | None:
        """The key pressed once, while a second press would still count."""
        if self.armed is not None and time.monotonic() < self.armed[1]:
            return self.armed[0]
        return None

    def press(self, key: str) -> bool:
        """key was pressed. True on the second press (and it's disarmed); else it's armed."""
        if self.key() == key:
            self.armed = None
            return True
        self.armed = (key, time.monotonic() + self.window)
        return False

    def left(self) -> float:
        """Seconds left for the second press."""
        return max(0.0, (self.armed[1] - time.monotonic()) if self.armed else 0.0)

    def clear(self) -> None:
        self.armed = None


def armed_line(message: str, armed: PressTwice) -> Text:
    """The "press again" warning, with the time left to do it draining away beside it."""
    left = armed.left()
    return Text.assemble(
        ("▲ ", st(AMBER, bold=True)),
        (message, st(AMBER, bold=True)),
        "  ",
        bar(left / armed.window, 12, stops=(AMBER, AMBER), sheen=False),
        (f" {left:.1f}s", st(FAINT)),
    )
