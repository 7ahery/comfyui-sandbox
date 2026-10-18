"""How much room a Rich renderable takes at a width, so a screen can fit what's under it."""

from __future__ import annotations

import io
from functools import lru_cache

from rich.console import Console


@lru_cache(maxsize=8)
def _measurer(width: int) -> Console:
    # A Console that renders to nowhere: it only counts. Kept per width, as measuring is repeated.
    return Console(width=width, file=io.StringIO(), legacy_windows=False, color_system=None)


def text_lines(renderable, width: int) -> list[str]:
    """The plain text of a renderable's lines at width."""
    measure = _measurer(width)
    return ["".join(seg.text for seg in line) for line in measure.render_lines(renderable, measure.options, pad=False)]


def rows(renderable, width: int) -> int:
    """How many lines a renderable takes at width."""
    measure = _measurer(width)
    return len(measure.render_lines(renderable, measure.options, pad=False))
