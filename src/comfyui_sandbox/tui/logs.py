"""Logs on screen: a LogSession holds a command's or ComfyUI's output as it arrives, and
WrappedLog shows it in rows, its long lines wrapped, following new lines or holding the reader's
place. Home's Show logs, Run here, an operation's full log and the run window's log all use them."""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Sequence
from typing import TYPE_CHECKING

from rich.text import Text

from .states import Outcome, SandboxState
from .theme import AMBER, GREEN, LINE, MINT, MUTED, RED, SKY, SOFT, VIOLET, st

if TYPE_CHECKING:
    from .gateway import Sandbox

# Logs read better with their landmarks lit: errors, warnings, URLs, sizes, build steps, successes.
LOG_RULES = [
    (re.compile(p), st(c))
    for p, c in [
        (r"\b\d[\d,.]*\s?(?:kB|MB|GB|B)\b", SOFT),
        (r"sha256:[0-9a-f]+", "#6f5bb8"),
        (r"https?://[^\s'\")]+", SKY),
        (r"^ ?#\d+", VIOLET),
        (r"\[\d+/\d+\]", SKY),
        (r"\[=*\s*\]\s*\d+%", MINT),
        (r"\b(?:done|ok|Successfully|Started|Created|installed|Checked:)|-> 200", GREEN),
        (r"\bWARNING\b.*", AMBER),
        (r"\b(?:ERROR|failed)\b.*", RED),
    ]
]


def highlight_log(line: str) -> Text:
    """line as a Text with LOG_RULES' landmarks lit. LogSession.add calls it once per line."""
    out = Text(line, st(MUTED), no_wrap=True, overflow="ellipsis")
    for pattern, style in LOG_RULES:
        out.highlight_regex(pattern, style)
    return out


LOG_TAIL = 500  # lines Show logs asks for when it opens (logs --tail)
LOG_KEEP = 5000  # lines it keeps while following; older ones fall off the top


class LogSession:
    """One log on screen: the lines so far, new ones as they arrive, and where the reader is. A
    page's view and the full-screen view of it share one, so switching between them keeps the
    place."""

    def __init__(self, sandbox: Sandbox | None = None) -> None:
        """sandbox: whose ComfyUI output this is (Show logs, Run here). None: a command's output (an
        operation's, the run window's), live until its owner says it has ended."""
        self.sandbox = sandbox
        # A stopped container prints nothing new; an operation's output is live until it ends.
        self.live = sandbox is None or sandbox.state == SandboxState.RUNNING
        self.outcome: Outcome | None = None  # how the operation ended, once it has
        self.lines: deque[Text] = deque(maxlen=LOG_KEEP)  # highlighted once, as they arrive
        self.dropped = 0  # lines that fell off the top of `lines`
        self.following = True
        self.top = 0  # the first line in view while paused, counted from the very first line

    def add(self, line: str) -> None:
        """A new line of output; at LOG_KEEP lines the oldest falls off (counted in `dropped`)."""
        if len(self.lines) == self.lines.maxlen:
            self.dropped += 1
        self.lines.append(highlight_log(line))

    def catch_up(self, printed: Sequence[str]) -> None:
        """Adds what's new in printed: everything a command has printed so far (an operation's
        progress.lines), of which this log holds the start. For a log fed only from there, as each
        operation's is: every line added is either kept or counted in `dropped`."""
        for line in printed[len(self.lines) + self.dropped :]:
            self.add(line)


WRAP_INDENT = 2  # a wrapped line's later rows sit this far in, so they read as its continuation


def wrap_line(line: Text, width: int) -> list[Text]:
    """line in rows of width: broken after a space when there's one in the row's second half,
    else mid-word. Nothing is cut off, and copying from the terminal keeps the whole text."""
    plain, width = line.plain, max(WRAP_INDENT + 8, width)
    if len(plain) <= width:
        return [line]
    out, start = [], 0
    while start < len(plain):
        room = width if not out else width - WRAP_INDENT
        end = start + room
        if end < len(plain):
            space = plain.rfind(" ", start + room // 2, end)
            if space > start:
                end = space + 1
        piece = line[start:end]
        piece.rstrip()
        out.append(piece if not out else Text.assemble(" " * WRAP_INDENT, piece))
        start = end
    return out


def wrapped_tail(lines: Sequence[Text], width: int, rows: int) -> list[Text]:
    """The newest whole lines that fit in rows, wrapped to width (at least the newest one's start)."""
    out: list[Text] = []
    for line in reversed(lines):
        wrapped = wrap_line(line, width)
        if out and len(out) + len(wrapped) > rows:
            break
        out = wrapped + out
    return out[:rows]


class WrappedLog:
    """A LogSession's rows on screen, its long lines wrapped: mixed into a widget that has
    rows() and session(). At the bottom it keeps up with new lines, the rows built from the
    newest line upward; scrolled back, from the line at the top downward. The position is a log
    line (session.top), not a row, so a resize, which rewraps everything, keeps the reader's place.
    The count and the scrollbar are in log lines, not rows."""

    # From the last view(), for scrolling.
    text_w = 80
    last_top = 0  # the first line in the bottom view, counted from the very first line
    last_shown = 0  # the last line in view, counted the same way
    overflow = False  # whether the log is taller than its rows

    def session(self) -> LogSession:
        raise NotImplementedError

    def rows(self) -> int:
        raise NotImplementedError

    def text_width(self, overflow: bool) -> int:
        """The width lines wrap to: the full width when everything fits, else beside the scrollbar."""
        raise NotImplementedError

    def bottom_up(self, text_w: int, rows: int) -> tuple[list[tuple[int, Text]], int]:
        """The rows at the bottom, newest last, and the first line in them. Only whole lines: a
        line that doesn't fit leaves its rows blank at the top rather than show a stray end."""
        lines, out = self.session().lines, []
        k = len(lines)
        while k > 0:
            wrapped = wrap_line(lines[k - 1], text_w)
            if len(out) + len(wrapped) > rows:
                break
            k -= 1
            out = [(k, row) for row in wrapped] + out
        if not out and lines:  # one line taller than the rows: show its start
            k = len(lines) - 1
            out = [(k, row) for row in wrap_line(lines[k], text_w)][:rows]
        return out, k

    def top_down(self, first: int, text_w: int, rows: int) -> list[tuple[int, Text]]:
        """rows rows from log line first down: the view of a reader who has scrolled back."""
        lines, out = self.session().lines, []
        for k in range(first, len(lines)):
            out += [(k, row) for row in wrap_line(lines[k], text_w)]
            if len(out) >= rows:
                break
        return out[:rows]

    def view(self) -> tuple[list[Text], int, int]:
        """The rows on screen, exactly rows() of them, and the first and last log lines in them
        (indexes into the session's lines)."""
        s, rows = self.session(), self.rows()
        view, whole = self.bottom_up(self.text_width(False), rows)
        self.overflow = whole > 0
        text_w = self.text_width(self.overflow)
        if text_w != self.text_width(False):
            view, whole = self.bottom_up(text_w, rows)
        self.text_w, self.last_top = text_w, s.dropped + whole
        if not s.following and s.top >= self.last_top:
            s.following = True  # scrolled to the end, or rewrapped there
        gap = 0
        if s.following:
            gap = rows - len(view) if self.overflow else 0  # blank rows over a bottom view
        else:
            view = self.top_down(max(0, s.top - s.dropped), text_w, rows)
        first, last = (view[0][0], view[-1][0]) if view else (0, 0)
        self.last_shown = s.dropped + last
        out = [Text("")] * gap + [row for _, row in view]
        return out + [Text("")] * (rows - len(out)), first, last

    def where(self, first: int, last: int) -> str:
        """The reader's place as text ("lines 3–9 of 40"), counted from the very first line even once
        old ones have dropped."""
        s = self.session()
        if not s.lines:
            return "no output yet"
        return f"lines {s.dropped + first + 1}–{s.dropped + last + 1} of {s.dropped + len(s.lines)}"

    def thumb(self, first: int, last: int) -> range | None:
        """The scrollbar's thumb, over rows(): sized and placed by log lines. None when it all fits."""
        if not self.overflow:
            return None
        rows, total, shown = self.rows(), len(self.session().lines), last - first + 1
        size = max(1, round(rows * shown / total))
        at = round(first / max(1, total - shown) * (rows - size))
        return range(at, at + size)

    def scroll_lines(self, step: int) -> None:
        """step log lines (a wrapped line moves as a whole)."""
        s = self.session()
        if s.following:
            if step > 0 or not self.overflow:
                return
            s.following, s.top = False, self.last_top
        s.top = max(s.dropped, s.top + step)
        if step > 0 and s.top >= self.last_top:
            s.following = True

    def page_lines(self, step: int) -> None:
        """A screenful: down, the line at the bottom goes to the top; up, the lines that fill the
        screen above the top line come into view."""
        s = self.session()
        if step > 0:
            if not s.following:
                s.top = self.last_shown
                if s.top >= self.last_top:
                    s.following = True
            return
        if not self.overflow:
            return
        if s.following:
            s.following, s.top = False, self.last_top
        filled, k = 0, s.top - s.dropped
        while k > 0 and filled < self.rows():
            k -= 1
            filled += len(wrap_line(s.lines[k], self.text_w))
        s.top = s.dropped + k

    def to_top(self) -> None:
        s = self.session()
        if self.overflow:
            s.following, s.top = False, s.dropped

    def follow(self) -> None:
        self.session().following = True

    def scrolled_rows(self, view: list[Text], thumb: range | None) -> list[Text]:
        """view's rows cut to the text width, each with its scrollbar cell (when there is one) at
        the right edge: how a full-screen log draws its rows."""
        out = []
        for k, line in enumerate(view):
            line = line.copy()
            line.truncate(self.text_w, overflow="crop", pad=True)
            if thumb is not None:
                line.append(" ")
                line.append("┃" if k in thumb else "│", st(SKY, bold=True) if k in thumb else st(LINE))
            out.append(line)
        return out
