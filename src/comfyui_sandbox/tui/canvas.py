"""A grid of cells to paint on: what the hero, the splash and the run window's banner draw with."""

from __future__ import annotations

from rich.text import Text

from .theme import INK, mix, st


class Canvas:
    """A grid of cells (glyph, colour, background, bold). A colour or background of None is the
    screen's."""

    def __init__(self, width: int, height: int) -> None:
        self.width, self.height = width, height
        self.cells: list[list[tuple[str, str | None, str | None, bool]]] = [
            [(" ", None, None, False)] * width for _ in range(height)
        ]

    def put(self, x: int, y: int, text: str, fg: str | None = None, bg: str | None = None, bold: bool = False) -> int:
        """Writes text at (x, y); bg None keeps the background that's there. Returns the x after it."""
        if 0 <= y < self.height:
            row = self.cells[y]
            for k, ch in enumerate(text):
                if 0 <= x + k < self.width:
                    row[x + k] = (ch, fg, row[x + k][2] if bg is None else bg, bold)
        return x + len(text)

    def put_parts(self, x: int, y: int, parts: list[tuple[str, str, bool]]) -> int:
        """Writes (text, colour, bold) parts one after another."""
        for s, fg, bold in parts:
            x = self.put(x, y, s, fg, bold=bold)
        return x

    def fade(self, f: float) -> None:
        """Mixes everything toward ink by f: a fade to black."""
        if f <= 0:
            return
        for row in self.cells:
            for x, (ch, fg, bg, bold) in enumerate(row):
                row[x] = (ch, fg and mix(fg, INK, f), bg and mix(bg, INK, f), bold)

    def text(self) -> Text:
        """The grid as one Text, a style run for each stretch of cells that share one."""
        out = Text(no_wrap=True, overflow="crop")
        for y, row in enumerate(self.cells):
            if y:
                out.append("\n")
            run, key = "", ()
            for ch, *rest in row:
                k = tuple(rest)
                if k != key and run:
                    out.append(run, st(*key))
                    run = ""
                run, key = run + ch, k
            if run:
                out.append(run, st(*key))
        return out
