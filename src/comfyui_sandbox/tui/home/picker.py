"""Home's picker: the sandboxes table and the actions under it, as one question."""

from __future__ import annotations

import time
from typing import NamedTuple

from rich.console import Group
from rich.text import Text
from textual import events
from textual.binding import Binding

from ..gateway import Sandbox
from ..measure import rows
from ..states import SandboxState
from .prompts import SelectPrompt
from .table import measure_table, sandboxes_table

TABLE_ROWS = 10  # the most sandbox rows home shows before its table scrolls


class HomeLayout(NamedTuple):
    """Where the picker's parts are, for drawing and for the mouse."""

    visible: int  # sandbox rows in view
    top: int  # the first of them
    window: tuple[int, int] | None  # (top, visible) when the table scrolls
    first_row: int  # the line of the first row in view, under the table's header
    first_action: int  # the line of the first action
    bar_x: int | None  # the scrollbar's column (None when everything fits)


class HomePrompt(SelectPrompt):
    """The table's rows are its first choices and the actions follow, so a sandbox is opened
    from its row (arrows and Enter, or a click) and isn't listed twice."""

    BINDINGS = [
        Binding("pageup", "page(-1)", show=False),
        Binding("pagedown", "page(1)", show=False),
        Binding("home", "jump(0)", show=False),
        Binding("end", "jump(-1)", show=False),
    ]
    FADE = False  # the table is the same one as a moment ago; only new things fade in

    def __init__(self, message: str, sandboxes: list[Sandbox], actions: list, notes: list) -> None:
        super().__init__(message, [*sandboxes, *actions])
        self.sandboxes, self.notes = sandboxes, notes  # notes: the last action's result (Console.flash)
        self.actions = actions
        self.top = 0  # the first sandbox row wanted in view, when they don't all fit (geometry() clamps it)

    def resync(self) -> None:
        """The sandboxes changed while this was up (home's poll found one installed or removed
        elsewhere): its rows follow them. The pointer stays on what it was on, or, if that's gone,
        on the row that took its place, so a key pressed just then still lands near where it was aimed."""
        picked = self.choices[self.index]
        self.choices = [*self.sandboxes, *self.actions]
        self.index = next(
            (k for k, c in enumerate(self.choices) if c is picked), min(self.index, len(self.choices) - 1)
        )
        self.follow()
        self.refresh(layout=True)  # a row more or less: its height changes

    def breathing(self) -> bool:
        """A running row's dot breathes (2.6 s, a wave down the rows): smooth needs ~12 Hz.
        So while any sandbox is running, home's question keeps its busy rate instead of idling
        at IDLE_HZ, which would make the dots step. With none running it idles."""
        return any(i.state == SandboxState.RUNNING for i in self.sandboxes)

    def keys(self) -> list[tuple[str, str]]:
        on_row = self.index < len(self.sandboxes)
        paging = [("pgup pgdn", "page")] if self.shown_rows() < len(self.sandboxes) else []
        return [("↑↓", "move"), *paging, ("enter", "open" if on_row else "choose"), ("ctrl+c", "quit")]

    def shown_rows(self) -> int:
        """How many sandbox rows show between the header and the actions: all of them up to
        TABLE_ROWS, as long as they fit; past that the table scrolls."""
        n = len(self.sandboxes)
        # A blank line, the notes, the question and the actions.
        below = 1 + rows(Group(*self.notes), self.app.cols()) + 1 + (len(self.choices) - n)
        room = self.app.lines() - self.app.header_rows - 2 - below  # 2: the table's header and rule
        if n <= min(room, TABLE_ROWS):
            return n
        return max(3, min(TABLE_ROWS, room - 1))  # 1: the caption under a scrolled table

    def geometry(self) -> HomeLayout:
        """Where everything is now. Render and the mouse both ask; it changes nothing."""
        n, width = len(self.sandboxes), self.app.cols()
        vis = self.shown_rows()
        top = max(0, min(self.top, n - vis))
        window = (top, vis) if vis < n else None
        size = measure_table(self.sandboxes, width, window=window)
        first_row = size.rows - (1 if window else 0) - min(n, vis)  # under the table's header
        first_action = size.rows + 1 + rows(Group(*self.notes), width) + 1
        return HomeLayout(
            vis, top, window, first_row, first_action, size.line_widths[first_row] - 1 if window else None
        )

    def follow(self) -> None:
        """Scrolls just enough to bring the pointer's row into view."""
        if self.index < len(self.sandboxes):
            vis = self.shown_rows()
            self.top = min(self.top, self.index)
            self.top = max(self.top, self.index - vis + 1)

    def render(self) -> Group:
        # Laid out like the sandbox menu's screen (table, blank line, notes, question, choices),
        # so opening a sandbox leaves the table where it is.
        n = len(self.sandboxes)
        picked = self.choices[self.index]
        # On start the rows cascade in, one after another.
        reveal = (time.monotonic() - self.app.started - 0.35) * 28
        table = sandboxes_table(
            self.sandboxes,
            self.app.cols(),
            highlight=picked if self.index < n else None,
            window=self.geometry().window,
            reveal=reveal if reveal < n + 1 else None,
        )
        return Group(
            table, Text(""), *self.notes, self.question(), *(self.choice_line(k) for k in range(n, len(self.choices)))
        )

    def action_move(self, step: int) -> None:
        super().action_move(step)
        self.follow()
        self.refresh()

    def action_page(self, step: int) -> None:
        if self.sandboxes:
            n = len(self.sandboxes)
            self.index = max(0, min(n - 1, min(self.index, n - 1) + step * self.shown_rows()))
            self.follow()
            self.refresh()

    def action_jump(self, end: int) -> None:
        if self.sandboxes:
            self.index = len(self.sandboxes) - 1 if end else 0
            self.follow()
            self.refresh()

    def scroll_rows_to(self, top: int) -> None:
        self.top = max(0, min(top, len(self.sandboxes) - self.shown_rows()))
        self.refresh()

    def scroll_by(self, step: int) -> None:
        self.scroll_rows_to(self.geometry().top + step)

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        event.stop()  # the table scrolls, not the screen
        self.scroll_by(1)

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        event.stop()
        self.scroll_by(-1)

    def _on_bar(self, x: int, y: int) -> bool:
        lay = self.geometry()
        return lay.bar_x is not None and abs(x - lay.bar_x) <= 1 and lay.first_row <= y < lay.first_row + lay.visible

    # Textual also runs SelectPrompt's handlers for these; prevent_default() keeps a scrollbar
    # click from choosing a row, and a hover there from moving the pointer.
    def on_click(self, event: events.Click) -> None:
        if self.app.open_link(event):  # a running row's URL: open it rather than pick the row
            return
        if self._on_bar(event.x, event.y):  # a click on the scrollbar scrolls to that point
            event.prevent_default()
            n, lay = len(self.sandboxes), self.geometry()
            self.scroll_rows_to(round((event.y - lay.first_row) * (n - lay.visible) / max(1, lay.visible - 1)))

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self._on_bar(event.x, event.y):
            event.prevent_default()

    def escape(self) -> None:
        pass  # home is the top: nothing to go back to

    def _choice_at(self, y: int) -> int | None:
        n, lay = len(self.sandboxes), self.geometry()
        if n and lay.first_row <= y < lay.first_row + min(n, lay.visible):  # every row is one line
            return lay.top + y - lay.first_row
        k = n + y - lay.first_action
        return k if n <= k < len(self.choices) else None
