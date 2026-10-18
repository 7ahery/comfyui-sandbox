"""A long operation on screen: its steps under an overall progress bar, the log on a failure, and
its outcome in a card once it's over."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from rich import box
from rich.console import Group
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.widget import Widget

from ..gateway import Progress, Step
from ..links import path_link
from ..logs import LogSession, wrapped_tail
from ..measure import rows
from ..parts import bar, fmt, spinner
from ..press_twice import PressTwice, armed_line
from ..states import Outcome, StepStatus
from ..theme import AMBER, AURORA, FAINT, GREEN, INK, LINE, MINT, MUTED, RED, SKY, SOFT, TEXT, WHITE, clamp, mix, st
from .table import table_width
from .widgets import Printed

if TYPE_CHECKING:
    from .app import SandboxApp

SHOW_ELAPSED = False  # the overall timer by a long operation's title; off while deciding on it
LOG_PANE_MIN = 3  # lines; with less room the pane isn't shown
CARD_FLARE = 1.6  # seconds the outcome card's border takes to settle
CURRENT = (StepStatus.RUNNING, StepStatus.FAILED, StepStatus.CANCELLED)  # the steps the view keeps in sight
# How each outcome colours the overall bar (from, to); its card and its border take the last.
OUTCOME_STOPS = {
    Outcome.FAILED: (mix(RED, INK, 0.3), RED),
    Outcome.CANCELLED: (mix(AMBER, INK, 0.3), AMBER),
    Outcome.OK: (MINT, GREEN),
}
OUTCOME_COLOR = {outcome: stops[-1] for outcome, stops in OUTCOME_STOPS.items()}


class LiveView:
    """The steps of a long operation (folded around the current one when they don't all fit,
    see step_rows) under an overall progress bar, with the log pane (shown on failure; L shows
    the full log) in the rows left under them: at least LOG_PANE_MIN lines, or none. Once the
    operation has ended, its outcome (result) is drawn here too, in a card, so it's counted in
    the same budget.

    It draws an operation's Progress, which the gateway writes as the command runs: the view
    only reads it, and follow() copies the new lines into its log."""

    def __init__(self, app: SandboxApp, title: str, progress: Progress) -> None:
        self.app = app
        self.title, self.source = title, progress  # what the gateway reports, which this view draws
        self.started = time.monotonic()
        self.ended: float | None = None
        self.session = LogSession()  # the output, highlighted once as it arrives; also the full log (L)
        self.result: list = []  # the outcome's lines, set when the operation ends
        # How it ended, from its result (run_steps sets it): a command can fail before its first
        # step, or before it announced any plan, and the steps alone would then read as a success.
        self.ended_as: Outcome | None = None
        self.widget: Widget | None = None  # the Live showing this view, to measure what's above it

    @property
    def steps(self) -> list[Step]:
        """The plan, as the gateway announced it."""
        return self.source.plan

    @property
    def status(self) -> list[StepStatus]:
        """Each step's status, in the plan's order."""
        return [step.status for step in self.source.plan]

    def follow(self) -> None:
        """Takes in what the operation has printed since the last call. Each line is highlighted
        once here, not on every frame; whoever waits on the operation calls this as it goes."""
        self.session.catch_up(self.source.lines)

    def _weights(self) -> tuple[float, float]:
        """(the whole, what's done), in the steps' expected seconds."""
        total = done = 0.0
        for step in self.steps:
            total += step.seconds
            if step.status in (StepStatus.DONE, StepStatus.SKIPPED):
                done += step.seconds
            elif step.status == StepStatus.RUNNING:
                done += step.seconds * self.source.sub
        return total or 1.0, done

    def width(self) -> int:
        """As wide as the sandboxes table, so the bar and the steps' times end where it does, but
        never so narrow that the steps' names are cut (a one-sandbox table is narrow)."""
        names = max(
            (len(step.title) + (15 if step.status == StepStatus.SKIPPED else 0) for step in self.steps), default=0
        )
        cols = self.app.cols()
        # 19: the mark, bar and time columns, the gaps
        return min(cols, max(table_width(self.app.sandboxes, cols), names + 19))

    def moving(self) -> bool:
        """Whether the view is changing fast: while its steps run, and while its card flares in.
        After that only the finished bar's sheen moves, which is fine slowly."""
        return self.ended is None or time.monotonic() - self.ended < CARD_FLARE

    def progress(self) -> float:
        total, done = self._weights()
        return done / total

    def room(self) -> int:
        """Rows this view can take before the screen would scroll."""
        above, cols = 0, self.app.cols()
        for child in self.app.out.children:
            # The widget drawing this view: also known by its source, because Textual can lay a
            # Live out while it's being mounted, before show_live has returned it to be
            # remembered as self.widget, and rendering it here would draw this view again, and
            # again (the RecursionError seen under load).
            if child is self.widget or getattr(child, "source", None) == self.__rich__:
                break
            # Lines mounted a moment ago have no size until the next layout: measure them, or
            # the first frames would think there's more room than there is.
            above += child.outer_size.height or rows(
                child.content if isinstance(child, Printed) else child.render(), cols
            )
        return self.app.lines() - above - (2 if self.ended else 0)  # once ended, a blank and the pause line follow

    def step_row(self, k: int, now: float) -> tuple:
        """Step k as the grid's four cells: mark, name, its own bar while it runs, and its time."""
        step = self.steps[k]
        name, state = step.title, step.status
        if state == StepStatus.DONE:
            return Text("✓", st(MINT, bold=True)), Text(name, st(SOFT)), Text(""), Text(fmt(step.took(now)), st(FAINT))
        if state == StepStatus.SKIPPED:
            return Text("✓", st(mix(MINT, INK, 0.5))), Text(f"{name} (already done)", st(FAINT)), Text(""), Text("")
        if state == StepStatus.RUNNING:
            return (spinner(SKY), Text(name, st(TEXT, bold=True)), Text(""), Text(fmt(step.took(now)), st(MUTED)))
        if state == StepStatus.FAILED:
            return (
                Text("✗", st(RED, bold=True)),
                Text(name, st(RED, bold=True)),
                Text(""),
                Text(fmt(step.took(now)), st(FAINT)),
            )
        if state == StepStatus.CANCELLED:
            return Text("■", st(AMBER)), Text(f"{name} (cancelled)", st(AMBER)), Text(""), Text("")
        return Text("○", st(LINE)), Text(name, st(FAINT)), Text(""), Text("")

    def step_rows(self, now: float, tail: bool, budget: int) -> list:
        """The steps' rows, at most budget of them (as long as budget is 3 or more). When they
        don't all fit (update all has 20-odd), a window of them stays around the current step and
        the ones before and after it fold into a row each: "✓ 9 steps done", "○ 11 more steps"."""
        n = len(self.status)
        focus = next(
            (k for k, s in enumerate(self.status) if s in CURRENT),
            n - 1 if self.ended else next((k for k, s in enumerate(self.status) if s == StepStatus.PENDING), 0),
        )
        # No plan yet (n == 0) while it has lines: a destructive command's warning, said before the
        # typed name is asked and its plan announced. Nothing runs, so nothing tails. Without the
        # `n and` guard, self.status[focus] raised IndexError (a real crash, seen once and confirmed).
        tailing = int(bool(tail and n and self.session.lines and self.status[focus] == StepStatus.RUNNING))
        first, last = 0, n
        rows_ = budget - tailing
        if n > rows_:
            slots = max(1, rows_ - 2)  # with both folded rows; a third of them above the current step
            first = max(0, min(focus - (slots - 1) // 3, n - slots))
            if first == 0:
                last = min(n, slots + 1)  # nothing folds above: its row is a step's (a tiny budget can exceed n)
            elif first + slots == n:
                first -= 1  # nothing folds below
            else:
                last = first + slots
            if first == 1:  # a folded row for one step: show the step instead
                first = 0
            if n - last == 1:
                last = n
        out = []
        if first:
            out.append(
                (
                    Text("✓", st(MINT, bold=True)),
                    Text(f"{first} steps done", st(MUTED)),
                    Text(""),
                    Text(fmt(sum(step.took(now) for step in self.steps[:first])), st(FAINT)),
                )
            )
        for k in range(first, last):
            state = self.status[k]
            out.append(self.step_row(k, now))
            if state == StepStatus.RUNNING and tail and self.session.lines:
                out.append(
                    (
                        "",
                        Text.assemble(("└ ", st(LINE)), self.session.lines[-1], no_wrap=True, overflow="ellipsis"),
                        "",
                        "",
                    )
                )
        if last < n:
            out.append(
                (
                    Text("○", st(LINE)),
                    Text(f"{n - last} {'steps not run' if self.ended else 'more steps'}", st(FAINT)),
                    Text(""),
                    Text(""),
                )
            )
        return out

    def outcome(self) -> Outcome:
        """How the operation is going or ended: as its result said, once it has ended (ended_as),
        else read off the steps."""
        if self.ended_as is not None:
            return self.ended_as
        if StepStatus.FAILED in self.status:
            return Outcome.FAILED
        if StepStatus.CANCELLED in self.status:
            return Outcome.CANCELLED
        return Outcome.OK if self.ended else Outcome.RUNNING

    def header(self, now: float) -> Group:
        """The title with how it's going on its right, the overall bar, and a blank line."""
        outcome = self.outcome()
        if outcome == Outcome.RUNNING and not self.steps:
            # No plan yet: the command is still getting ready (a stopped WSL distro booting for it
            # to look inside). Nothing has changed, so neither "Uninstalling x" nor a bar that
            # moves, which read as the deletion already running.
            return Group(
                Text.assemble(
                    spinner(SKY), " ", ("Waiting for WSL to start. Nothing has been changed yet.", st(MUTED))
                ),
                Text(""),
            )
        stops = OUTCOME_STOPS.get(outcome, AURORA)
        k = next((k for k, s in enumerate(self.status) if s in CURRENT), None)
        where = (
            f" at step {k + 1} of {len(self.steps)}"
            if k is not None
            else f" at {len(self.steps)} steps"
            if self.steps
            else ""
        )  # no plan: it ended before one
        tail = {
            Outcome.RUNNING: "",
            Outcome.OK: f"done in {fmt(now - self.started)}",
            Outcome.FAILED: f"failed{where}",
            Outcome.CANCELLED: f"cancelled{where}",
        }[outcome]
        # As wide as the table above (see width), so the bar ends where the table does. How it
        # ended goes on the title's line, so the bar keeps its length.
        width = self.width()
        title = Table.grid(expand=True)
        title.width = width
        title.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        title.add_column(justify="right", no_wrap=True)
        right = (
            Text(tail, st(MUTED))
            if tail
            else Text(f"⏱ {fmt(now - self.started)}", st(MUTED))
            if SHOW_ELAPSED
            else Text("")
        )
        title.add_row(Text(self.title, st(TEXT, bold=True)), right)
        pct = round(self.progress() * 100)
        meter = Text.assemble(
            bar(self.progress(), max(10, width - 6), stops=stops, sheen=outcome in (Outcome.RUNNING, Outcome.OK)),
            (f"  {pct:3d}%", st(TEXT, bold=True)),
            no_wrap=True,
            overflow="crop",
        )
        return Group(title, meter, Text(""))

    def card(self) -> list:
        """The outcome, in a card whose border flares as it appears."""
        body = list(self.result)
        while body and isinstance(body[0], Text) and not body[0].plain.strip():
            body.pop(0)
        if not body:
            return []
        color = OUTCOME_COLOR.get(self.outcome(), GREEN)
        flare = clamp(1 - (time.monotonic() - (self.ended or 0)) / CARD_FLARE)
        border = st(mix(mix(color, INK, 0.35), WHITE, flare * 0.7))
        return [Text(""), Panel(Group(*body), box=box.ROUNDED, border_style=border, expand=False, padding=(0, 2))]

    def __rich__(self) -> Group:
        """One frame: header, steps, the log pane (on a failure) or a blank, the footer, the card.
        Live calls it once per tick, and the frame it gets is the one measured and drawn."""
        now = self.ended or time.monotonic()
        failed = StepStatus.FAILED in self.status
        header = self.header(now)
        op, cols = self.app.op, self.app.cols()
        armed = op.armed if op is not None else PressTwice()
        notice = self.app.quit_notice() if self.ended is None else None
        if notice is not None:  # Ctrl+C's warning, or home quitting once this has stopped
            footer = notice
        elif self.ended is None and armed.key():
            footer = armed_line(f"Press {armed.key()} again to cancel", armed)
        else:
            footer = Text.from_markup(
                f"L shows the full log  ·  saved to {path_link(self.source.log or '')}", style=st(FAINT)
            )
        outcome = self.card()
        room = self.room() - rows(Group(header, footer, *outcome), cols)
        # The steps get what's left under the header: less a blank line, or on a failure less
        # the log pane at its smallest (with its border), as long as a few steps still show.
        pane = failed and room - 2 - LOG_PANE_MIN >= min(len(self.steps), 3)
        steps = self.step_rows(now, tail=not failed, budget=room - 2 - LOG_PANE_MIN if pane else room - 1)
        space = room - len(steps) - 2  # the pane's border
        grid = Table.grid(padding=(0, 1), pad_edge=False, expand=True)
        grid.width = self.width() - 1  # 1: the Padding's
        grid.add_column(width=2)
        grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        grid.add_column(width=6)
        grid.add_column(justify="right", width=7)
        for row in steps:
            grid.add_row(*row)
        parts: list = [header, Padding(grid, (0, 0, 0, 1))]
        if pane:
            # The newest whole lines that fit, wrapped as the full log wraps them (L), so an
            # error reads whole. The panel's insides: the column less its borders and padding.
            shown = wrapped_tail(self.session.lines, cols - 4, min(14, space))
            body = Text("\n").join(shown) or Text(" ")
            parts.append(
                Panel(
                    body,
                    title=Text(" logs ", st(RED if failed else FAINT, bold=True)),
                    title_align="left",
                    box=box.ROUNDED,
                    border_style=st(mix(RED, INK, 0.3) if failed else LINE),
                )
            )
        else:
            parts.append(Text(""))
        return Group(*parts, footer, *outcome)
