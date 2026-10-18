"""Small drawn parts both screens use: spinners, bars, keycaps, headings, cards, and fitting text
to a width."""

from __future__ import annotations

import math
import time

from rich import box
from rich.box import Box
from rich.cells import cell_len
from rich.panel import Panel
from rich.text import Text

from .noise import NOISE, NOISE_DASHES, hash01
from .theme import AMBER, AURORA, FAINT, LINE, MUTED, SKY, TEXT, WHITE, clamp, cycle, mix, ramp, st

SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def spinner(color: str | None = None, t: float | None = None) -> Text:
    """A braille spinner; without a colour it drifts through the palette as it turns. t: the
    time to draw it at (now, by default)."""
    t = time.monotonic() if t is None else t
    return Text(SPIN[int(t * 12) % len(SPIN)], st(color or cycle(t * 0.35), bold=True))


def bar_cells(
    fraction: float,
    width: int,
    *,
    stops: tuple[str, ...] = AURORA,
    track: str = LINE,
    sheen: bool = True,
    edge: bool = False,
) -> list[tuple[str, str]]:
    """bar's cells, as (glyph, colour): for drawing it on a Canvas. edge: the track just past the
    fill is latent noise, resolving as the fill reaches it (the splash's sampler)."""
    fraction = clamp(fraction)
    exact = fraction * width
    full, half = int(exact), exact - int(exact) >= 0.5  # whole cells, and a half cell for the rest
    t = time.monotonic()
    head = (t * 0.6 % 1.8 - 0.4) * width  # the sheen's centre; off the bar for part of each pass
    frame = int(t * 14)
    cells = []
    for x in range(width):
        color = ramp(x / max(1, width - 1), stops)
        if x < full:
            if sheen:
                color = mix(color, WHITE, 0.5 * math.exp(-(((x - head) / 2.2) ** 2)))
            cells.append(("━", color))
        elif x == full and half:
            cells.append(("╸", color))
        elif edge and 0 < fraction < 1 and x - exact < 5:
            # Just past the fill, the track is noise that the fill resolves as it arrives.
            near = math.exp(-(x - exact) / 1.8)
            hue = NOISE[int(hash01(frame, x, 3) * len(NOISE))]
            cells.append((NOISE_DASHES[int(hash01(x, frame, 7) * len(NOISE_DASHES))], mix(track, hue, 0.8 * near)))
        else:
            cells.append(("━", track))
    return cells


def bar(fraction: float, width: int, *, stops: tuple[str, ...] = AURORA, track: str = LINE, sheen: bool = True) -> Text:
    """A thin progress bar: a gradient fill, a half cell for precision, and a soft sheen that
    slides along the fill now and then."""
    out = Text(no_wrap=True)
    for ch, color in bar_cells(fraction, width, stops=stops, track=track, sheen=sheen):
        out.append(ch, st(color))
    return out


def segments(done: int, total: int, color: str = AMBER) -> Text:
    """Steps as cells: ▰ done, ▱ not yet."""
    return Text.assemble(("▰" * done, st(color)), ("▱" * (total - done), st(LINE)))


def keycap(key: str) -> Text:
    """A key drawn as a cap."""
    return Text(f" {key} ", st(TEXT, LINE, bold=True))


def keys_line(keys: list[tuple[str, str]]) -> Text:
    """A bottom bar's keys: each keycap and what it does."""
    out = Text(no_wrap=True)
    for k, (cap, label) in enumerate(keys):
        out.append("   " if k else " ")
        out.append_text(keycap(cap))
        if label:
            out.append(f" {label}", st(MUTED))
    return out


def heading(title: str, note: str = "", accent: str = SKY) -> Text:
    """A section's first line: an accent bar, the title, and a quiet note."""
    out = Text.assemble(("▍", st(accent)), " ", (title, st(TEXT, bold=True)))
    if note:
        out.append(f"   {note}", st(FAINT))
    return out


def card(body, title: str, accent: str = SKY, border: str = LINE, *, expand: bool = False) -> Panel:
    """body in a rounded box, as wide as it needs (expand: as wide as there is room); title in the accent colour,
    border's colour for the lines."""
    return Panel(
        body,
        title=Text(f" {title} ", st(accent, bold=True)),
        title_align="left",
        box=box.ROUNDED,
        border_style=st(border),
        expand=expand,
        padding=(0, 2),
    )


# A dashed box for the empty table: there's nothing here yet, but there will be.
DASHED = Box("╭┄┄╮\n┆ ┆┆\n├┄┼┤\n┆ ┆┆\n├┄┼┤\n├┄┼┤\n┆ ┆┆\n╰┄┄╯\n")


# -- fitting text ----------------------------------------------------------------------------


def elide(s: str, width: int) -> str:
    """Cuts the middle out of s to fit width, so both ends stay readable: a path's drive and
    last folder, a name's prefix and suffix."""
    if cell_len(s) <= width:
        return s
    tail = (width - 1) * 2 // 3
    head = width - 1 - tail
    return s[:head] + "…" + (s[-tail:] if tail else "")


def pad(text: Text, width: int) -> Text:
    """text cut or padded to exactly width."""
    text = text.copy()
    text.truncate(width, overflow="ellipsis", pad=True)
    return text


def center(text: Text, width: int) -> Text:
    """text indented to the middle of width (no padding on its right)."""
    return Text.assemble(" " * max(0, (width - text.cell_len) // 2), text)


def fmt(seconds: float) -> str:
    """A duration, short: 42s, 3m 07s."""
    seconds = int(seconds)
    return f"{seconds}s" if seconds < 60 else f"{seconds // 60}m {seconds % 60:02d}s"
