"""The palette, "aurora": ink for the ground, three cool hues for everything that's fine or in
focus, warm ones for what needs attention. Every colour on screen comes from here, and so do the
helpers that mix them and the motion curves that animate them."""

from __future__ import annotations

import math
import time
from functools import lru_cache

from rich.style import Style as RichStyle
from rich.terminal_theme import TerminalTheme
from rich.text import Text

INK = "#0a0d16"  # the screen
PANEL = "#111625"  # the bar, cards and fields
RAISED = "#1b2443"  # the row or choice under the pointer
STRIPE = "#0e1220"  # every other table row, so a row reads across
LINE = "#283152"  # borders, rules, empty tracks
FAINT = "#535d80"
MUTED = "#8a94b4"
SOFT = "#bcc4dc"  # choices not under the pointer
TEXT = "#e8ebf6"
VIOLET, SKY, MINT = "#8f6bff", "#45c3ff", "#45f0c3"
GREEN, AMBER, RED, PINK = "#5bef94", "#ffc35e", "#ff5f7f", "#ff7ad6"
WHITE = "#ffffff"
AURORA = (VIOLET, SKY, MINT)
HEAT = (MINT, SKY, VIOLET, PINK)  # load, low to high


def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


# The flows' markup ([green], [red], [yellow], [dim]) stays as it was; Textual converts ANSI
# colours to truecolor through this theme, so they land on the palette too.
TERMINAL = TerminalTheme(
    _rgb(INK),
    _rgb(TEXT),
    [_rgb(c) for c in (INK, RED, GREEN, AMBER, SKY, VIOLET, MINT, TEXT)],
    [_rgb(c) for c in (FAINT, RED, GREEN, AMBER, SKY, PINK, MINT, WHITE)],
)


def mix(a: str, b: str, t: float) -> str:
    """The colour t of the way from a to b. t is rounded to 1/48ths so styles cache well."""
    return _mix(a, b, round(max(0.0, min(1.0, t)) * 48) / 48)


@lru_cache(maxsize=16384)
def _mix(a: str, b: str, t: float) -> str:
    (r1, g1, b1), (r2, g2, b2) = _rgb(a), _rgb(b)
    return f"#{round(r1 + (r2 - r1) * t):02x}{round(g1 + (g2 - g1) * t):02x}{round(b1 + (b2 - b1) * t):02x}"


def ramp(t: float, stops: tuple[str, ...] = AURORA) -> str:
    """The colour at t (0..1) along stops."""
    t = max(0.0, min(1.0, t)) * (len(stops) - 1)
    k = min(int(t), len(stops) - 2)
    return mix(stops[k], stops[k + 1], t - k)


def cycle(t: float, stops: tuple[str, ...] = AURORA) -> str:
    """Like ramp, but it wraps: t and t + 1 are the same colour."""
    return ramp(t % 1.0, (*stops, stops[0]))


@lru_cache(maxsize=16384)
def st(
    fg: str | None = None, bg: str | None = None, bold: bool = False, italic: bool = False, underline: bool = False
) -> RichStyle:
    """A cached Rich style. Unset attributes stay unset (None), so they combine."""
    return RichStyle(color=fg, bgcolor=bg, bold=bold or None, italic=italic or None, underline=underline or None)


def blend(text: str, stops: tuple[str, ...] = AURORA, *, bold: bool = False) -> Text:
    """text with a gradient across it."""
    n = max(1, len(text) - 1)
    out = Text()
    for k, ch in enumerate(text):
        out.append(ch, st(ramp(k / n, stops), bold=bold))
    return out


# -- motion ----------------------------------------------------------------------------------


def breathe(period: float = 2.4, phase: float = 0.0) -> float:
    """0 to 1 and back, smoothly, every period seconds."""
    return 0.5 - 0.5 * math.cos(2 * math.pi * (time.monotonic() / period + phase))


def clamp(x: float) -> float:
    """x limited to 0..1."""
    return max(0.0, min(1.0, x))


def ease(x: float) -> float:
    """Ease out (cubic): fast, then settling."""
    return 1 - (1 - clamp(x)) ** 3
