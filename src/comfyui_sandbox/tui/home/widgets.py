"""What the screen's lines are made of: a printed line, and a line that redraws itself (Rich's
console.print and Live, on Textual), and the motions new or wrong lines make."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from textual import getters
from textual.widget import Widget
from textual.widgets import Static

from ..links import HoverLinks
from ..measure import rows
from ..pacing import Pacer

if TYPE_CHECKING:
    from .app import SandboxApp


class HomeWidget(Widget):
    """A widget of the home screen, whose `app` is the SandboxApp it runs in."""

    if TYPE_CHECKING:
        app = getters.app(SandboxApp)  # at runtime, Textual's own `app` finds the same App


class Printed(Static, HoverLinks):
    """A printed line (Console.say). Static first: Textual takes CSS (height: auto) from the first base."""


class Live(HomeWidget, HoverLinks):
    """Re-renders a Rich renderable, like rich.live.Live / console.status: 12 times a second
    while moving() says its content is changing, and slowly otherwise (see Pacer). A source
    whose changes are only slow glows says not, so a screen waiting on a question is cheap."""

    DEFAULT_CSS = "Live { height: auto; }"

    def __init__(self, source: Callable[[], object] | object, moving: Callable[[], bool] = lambda: True) -> None:
        super().__init__()
        self.source, self.moving = source, moving
        self.frame: object | None = None  # the renderable of the last tick, which render() shows
        self._height = -1  # the height last laid out for

    def on_mount(self) -> None:
        Pacer(self, self.tick, self.moving, busy_hz=12).start()

    def tick(self) -> None:
        # The source is read once per tick, and the same frame is measured and then drawn. A
        # layout re-flows the whole scroll container, so only a height change asks for one (and
        # the live view's room calculation, which reads the layout's heights, still gets it then).
        self.frame = self.read()
        height = rows(self.frame, self.size.width) if self.size.width else -1  # -1: not laid out yet
        self.refresh(layout=height != self._height)
        self._height = height
        # Like a terminal: while something is live, the newest lines stay in view.
        self.call_after_refresh(self.app.out.scroll_end, animate=False)

    def read(self):
        """The source's renderable now: it is called if it's callable, else it is the renderable."""
        return self.source() if callable(self.source) else self.source

    def render(self):
        # Shows the frame tick() read and measured; it never calls the source again, so what is
        # drawn is what was measured. Only before the first tick does it read.
        return self.frame if self.frame is not None else self.read()


def fade_in(widget: Widget, duration: float = 0.22) -> None:
    """New questions arrive with a short fade instead of a pop."""
    widget.styles.opacity = 0.0
    widget.styles.animate("opacity", value=1.0, duration=duration, easing="out_cubic")


def shake(widget: Widget) -> None:
    """A wrong answer shakes its line, as a login box does at a wrong password."""
    for k, dx in enumerate([2, -2, 1, -1, 0]):
        widget.set_timer(0.045 * (k + 1), lambda dx=dx: setattr(widget.styles, "offset", (dx, 0)))
