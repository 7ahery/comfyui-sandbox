"""Logs on home's screen: Show logs' page and its full-screen view, an operation's full log (L),
and Run here's page."""

from __future__ import annotations

from typing import NamedTuple

from rich.text import Text
from textual import events
from textual.binding import Binding

from ..gateway import Progress
from ..links import url_link
from ..logs import LogSession, WrappedLog
from ..parts import heading
from ..press_twice import armed_line
from ..states import Outcome
from ..theme import AMBER, FAINT, GREEN, INK, LINE, MUTED, PANEL, RED, SKY, breathe, mix, st
from .model import Interrupt
from .prompts import Prompt


class LogView(WrappedLog, Prompt):
    """A LogSession on screen: in a box on the Show logs page, or (full) in a pane over
    everything, its long lines wrapped (WrappedLog). It follows new output as it arrives
    (docker logs -f); scrolling back pauses that, End resumes it. Only the rows on screen are
    wrapped and drawn, so a long log costs no more than a short one. The answer is "full" (open
    the full-screen view) or None (back)."""

    BINDINGS = [
        Binding("up", "scroll(-1)", show=False),
        Binding("down", "scroll(1)", show=False),
        Binding("pageup", "page(-1)", show=False),
        Binding("pagedown", "page(1)", show=False),
        Binding("home", "top", show=False),
        Binding("end", "follow", show=False),
        Binding("l", "full", show=False),
    ]
    FADE = False

    def __init__(self, session: LogSession, full: bool = False, command: str = "") -> None:
        """command: the command line it shows the output of, as home echoed it (without the
        program's name); the full-screen view's title names it."""
        super().__init__(f"{session.sandbox.name} · logs" if session.sandbox else "logs")
        self.s, self.full, self.command = session, full, command

    def session(self) -> LogSession:
        return self.s

    def rows(self) -> int:
        """Log rows on screen. Full screen: the pane less its title and the blank under it. On
        the page: what's left under the table and the command, less the box's borders."""
        if self.full:
            return max(1, self.size.height - 2)
        return max(3, self.app.lines() - self.app.header_rows - 2 - len(self.above()) - 2)

    def text_width(self, overflow: bool) -> int:
        if self.full:
            return self.size.width - (2 if overflow else 0)  # the scrollbar and a space
        return self.size.width - 4  # inside the box, whose right border is the scrollbar's track

    def above(self) -> list[Text]:
        """Lines the page shows above the box."""
        return []

    def title(self) -> Text:
        """The full-screen pane's first line, less the line numbers (render puts them at the right)."""
        return heading(self.message, f"comfyui-sandbox {self.command}")

    def action_scroll(self, step: int) -> None:
        self.scroll_lines(step)
        self.refresh()

    def action_page(self, step: int) -> None:
        self.page_lines(step)
        self.refresh()

    def action_top(self) -> None:
        self.to_top()
        self.refresh()

    def action_follow(self) -> None:
        self.follow()
        self.refresh()

    def action_full(self) -> None:
        self.answer(None if self.full else "full")  # l again leaves full screen

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        event.stop()
        self.action_scroll(-3)

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        event.stop()
        self.action_scroll(3)

    def escape(self) -> None:
        self.answer(None)  # full screen: back to the page; the page: back to the sandbox's menu

    def keys(self) -> list[tuple[str, str]]:
        # Home and End still jump to the oldest and newest lines; the bar leaves them out, and
        # says nothing of following or pausing: the log simply keeps up unless scrolled back.
        return [("↑↓ pgup pgdn", "scroll"), ("l", "exit full" if self.full else "full screen"), ("esc", "back")]

    def status(self) -> Text | None:
        if not self.s.live:
            return Text("○ stopped · no new output", st(FAINT))
        return None

    def render(self) -> Text:
        width, rows = self.size.width, self.rows()
        view, first, last = self.view()
        where, thumb = self.where(first, last), self.thumb(first, last)
        if self.full:
            # A pane over everything: a title, then the log, the scrollbar at the right edge.
            head = self.title()
            gap = max(2, width - head.cell_len - len(where))
            head.append(" " * gap + where, st(FAINT))
            out = [head, Text(""), *self.scrolled_rows(view, thumb)]
            return Text("\n", no_wrap=True).join(out)
        # On the page: a box, as the logs looked before, its right border the scrollbar's track.
        border = st(LINE)
        title = Text.assemble(" ", ("ComfyUI output", st(FAINT, bold=True)), " ")
        note = Text(f" {where} ", st(FAINT))
        fill = max(0, width - 3 - title.cell_len - note.cell_len - 1)
        out = [*self.above(), Text.assemble(("╭─", border), title, ("─" * fill, border), note, ("─╮", border))]
        for k, line in enumerate(view[:rows]):
            line = line.copy()
            line.truncate(self.text_w, overflow="crop", pad=True)
            right = Text("┃", st(SKY, bold=True)) if thumb is not None and k in thumb else Text("│", border)
            out.append(Text.assemble(("│ ", border), line, " ", right))
        out.append(Text("╰" + "─" * (width - 2) + "╯", border))
        return Text("\n", no_wrap=True).join(out)


class OpLogEntry(NamedTuple):
    """The last operation's output, for L, until the next screen."""

    session: LogSession
    title: str
    cmd: str


class OpLog(LogView):
    """An operation's full output (L), as it prints it, in a pane over its steps and summary.
    The operation carries on underneath; L or Esc goes back to it. It's opened from a key
    handler, not awaited, so it never answers: the app's open_overlay and close_overlay put it
    up and take it down."""

    BINDINGS = [Binding("l", "close", show=False)]

    def __init__(self, session: LogSession, title: str, cmd: str) -> None:
        super().__init__(session, full=True)
        self.message, self.cmd = title, cmd

    def title(self) -> Text:
        return heading(self.message, f"comfyui-sandbox {self.cmd}")

    def action_close(self) -> None:
        self.app.close_overlay()

    def action_full(self) -> None:
        pass  # already full screen

    def escape(self) -> None:
        self.app.close_overlay()

    def interrupt(self) -> None:
        self.app.interrupt_running()  # as if the operation's own screen were up

    def keys(self) -> list[tuple[str, str]]:
        out = [("↑↓ pgup pgdn", "scroll"), ("l esc", "back")]
        op = self.app.op
        if self.s.live and op is not None and op.live_view is not None:  # a long operation: Ctrl+C takes two presses
            out.append(("ctrl+c ×2", "quit"))
        return out

    def status(self) -> Text | None:
        key = self.app.armed_key()
        if key and self.s.live:
            what = "quit, leaving it unfinished" if key == "Ctrl+C" else "cancel"
            return Text(f"▲ press {key} again to {what}", st(AMBER, bold=True))
        if not self.s.live:
            outcome = self.s.outcome
            colors = {Outcome.OK: GREEN, Outcome.FAILED: RED, Outcome.CANCELLED: AMBER}
            color = colors.get(outcome, FAINT) if outcome else FAINT
            label = "finished" if outcome is None else "done" if outcome == Outcome.OK else outcome
            return Text(f"■ {label} · no new output", st(color))
        return super().status()


class RunView(LogView):
    """Run here's screen: the LIVE line over ComfyUI's output, in the same
    scrollable box as Show logs (L: full screen). Until `run` has taken its whole plan (progress,
    ComfyUI answering and reachable) the line says it's starting, and which step it's on. Stopping
    takes a second press, so a stray key doesn't stop ComfyUI: Esc twice on the page, Ctrl+C twice
    anywhere. Stopping arrives as Interrupt; the answer is "full" or None (back from full screen)."""

    def __init__(self, session: LogSession, progress: Progress, full: bool = False) -> None:
        super().__init__(session, full)
        assert session.sandbox is not None  # Run here's session is of its sandbox
        self.sandbox = session.sandbox
        self.progress = progress
        self.message = f"{self.sandbox.name} · running here"

    def above(self) -> list[Text]:
        op = self.app.op
        key = op.armed.key() if op is not None else None
        if op is not None and key:
            line = armed_line(f"Press {key} again to stop ComfyUI.", op.armed)
        elif self.progress.through:
            line = Text.assemble(
                (" ● LIVE ", st(INK, mix(GREEN, PANEL, 0.45 * breathe(1.6)), bold=True)),
                "  ComfyUI is running at ",
                Text.from_markup(url_link(self.sandbox.port), style=st(SKY)),
                (". Esc twice stops it.", st(MUTED)),
            )
        else:
            k = self.progress.current
            step = f": {self.progress.plan[k].title}" if k is not None else ""
            line = Text.assemble(
                (" ● STARTING ", st(INK, mix(AMBER, PANEL, 0.45 * breathe(1.6)), bold=True)),
                f"  Starting ComfyUI{step}…",
                (" Esc twice stops it.", st(MUTED)),
            )
        return [line, Text("")]

    def title(self) -> Text:
        return heading(self.message, accent=GREEN)

    def press_twice(self, key: str) -> None:
        if self.app.op is not None and self.app.op.armed.press(key):
            self.interrupt_now()

    def interrupt_now(self) -> None:
        if not self.future.done():
            self.future.set_exception(Interrupt())

    def interrupt(self) -> None:
        self.press_twice("Ctrl+C")

    def escape(self) -> None:
        if self.full:
            self.answer(None)  # back to the page, still running
        else:
            self.press_twice("Esc")

    def keys(self) -> list[tuple[str, str]]:
        # Home still works; there's no room to say so beside the stop key.
        if self.full:
            return [("↑↓ pgup pgdn", "scroll"), ("l", "exit full"), ("esc", "back"), ("ctrl+c ×2", "stop")]
        return [("↑↓ pgup pgdn", "scroll"), ("l", "full screen"), ("esc ×2", "stop")]

    def status(self) -> Text | None:
        key = self.app.armed_key()
        if key and self.full:  # the page says it above the box; full screen says it here
            return Text(f"▲ press {key} again to stop", st(AMBER, bold=True))
        return super().status()
