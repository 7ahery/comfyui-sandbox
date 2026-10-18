"""How often a screen redraws: fast while something moves, slowly when nothing does.

A Pacer is a widget's (or an app's) redraw timer. It runs at `busy_hz` while `moving()` says
something is animating, and for GRACE seconds after; then it drops to `idle_hz`. Input (a key, a
click, the wheel, a resize) wakes every Pacer of the app at once, so an idle window answers it at
the busy rate, not at its next slow tick. Animation is time-based (theme.ease, Banner.advance),
so the rate changes how smooth it looks, not how fast it goes."""

from __future__ import annotations

import time
import weakref
from collections.abc import Callable

from textual import events
from textual.timer import Timer

from .appmixin import AppMixin

# A Pacer only looks at moving() when its timer fires, and a glow or fade is still settling just
# after moving() turns false. So it stays at the busy rate for this long past the last motion,
# instead of dropping to the idle rate in the middle of the tail.
GRACE = 1.5  # seconds a Pacer stays busy after anything moved
IDLE_HZ = 4.0  # home: slow enough to cost nothing, fast enough for its slow glows (periods of 2 s)


class Pacer:
    """One redraw timer. Each widget (or app) that animates owns one; busy_hz is what that widget
    needs to look smooth, and it is chosen at the widget (the run window's banner 20, home's hero 15)."""

    def __init__(
        self,
        owner,
        callback: Callable[[], object],  # its result is dropped: a widget's refresh returns the widget
        moving: Callable[[], bool] = lambda: False,
        *,
        busy_hz: float,
        idle_hz: float = IDLE_HZ,
    ) -> None:
        """owner: the widget or app whose timer this is. callback: the redraw."""
        self.owner, self.callback, self.moving = owner, callback, moving
        self.busy_hz, self.idle_hz = busy_hz, idle_hz
        self.busy_until = 0.0
        self.timer: Timer | None = None
        self.fast: bool | None = None  # the rate the timer runs at now
        self.registry().add(self)

    def registry(self) -> weakref.WeakSet:
        """The app's set of Pacers, made on first use (WakesOnInput wakes them all). Weak, so a
        widget that goes away takes its Pacer out of it."""
        app = self.owner.app
        if not hasattr(app, "pacers"):
            app.pacers = weakref.WeakSet()
        return app.pacers

    def start(self) -> None:
        """Starts the timer, busy: it is on_mount's call."""
        self.busy_until = time.monotonic() + GRACE
        self.set_rate(True)

    def set_rate(self, fast: bool) -> None:
        """Runs the timer at the busy rate (fast) or the idle one. Does nothing if it already does."""
        if fast == self.fast:
            return
        if self.timer is not None:
            self.timer.stop()
        self.fast = fast
        self.timer = self.owner.set_interval(1 / (self.busy_hz if fast else self.idle_hz), self.fire)

    def fire(self) -> None:
        """The timer's tick: asks moving(), redraws, then picks the rate for the next tick."""
        if self.moving():
            self.busy_until = time.monotonic() + GRACE
        self.callback()
        self.set_rate(time.monotonic() < self.busy_until)

    def wake(self) -> None:
        """Something changed: redraw now, and keep drawing fast for a while."""
        self.busy_until = time.monotonic() + GRACE
        if not self.fast:
            self.set_rate(True)
            self.callback()


class WakesOnInput(AppMixin):
    """Mixed into an App: input wakes the app's Pacers. Put it before TracksHover.

    Without it, a key press in an idle window (its Pacers at 2-4 Hz) would show its effect at the
    next slow tick. Before TracksHover so its on_event runs first and then hands the event on."""

    # Mouse moves aren't listed: a hover redraws the links it touches itself (TracksHover.hover).
    WAKING = (events.Key, events.MouseDown, events.MouseScrollDown, events.MouseScrollUp, events.Resize)

    async def on_event(self, event: events.Event) -> None:
        if isinstance(event, self.WAKING):
            for pacer in list(getattr(self, "pacers", ())):
                pacer.wake()
        await super().on_event(event)
