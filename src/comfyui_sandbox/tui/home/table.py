"""The sandboxes table: one line per sandbox, its columns fitted to the window."""

from __future__ import annotations

from functools import lru_cache
from typing import NamedTuple

from rich import box
from rich.cells import cell_len
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..gateway import Sandbox
from ..links import url_link
from ..measure import text_lines
from ..parts import DASHED, elide, spinner
from ..states import SandboxState
from ..theme import AMBER, FAINT, GREEN, INK, LINE, MINT, MUTED, RAISED, SKY, STRIPE, TEXT, VIOLET, breathe, mix, st

ROW_SELECTED = f"on {RAISED}"  # the sandbox the home pointer or an open sandbox menu is on
ROW_STRIPE = f"on {STRIPE}"

# state: (glyph, label, colour). Glyphs that move (the dot, the spinner) are drawn by state_cell.
STATE_LOOK = {
    SandboxState.RUNNING: ("●", "running", GREEN),
    SandboxState.STOPPED: ("○", "stopped", FAINT),
    SandboxState.INCOMPLETE: ("▲", "incomplete", AMBER),
    SandboxState.INSTALLING: ("◐", "installing", SKY),
    SandboxState.STARTING: ("◐", "starting", SKY),
    SandboxState.STOPPING: ("◐", "stopping", SKY),
    SandboxState.RESTARTING: ("◐", "restarting", SKY),
}

NAME_MAX = 24  # longer names are cut in the middle; the sandbox menu shows them whole
MODELS_MIN = 16  # narrower than this, the Models column is dropped
SCROLLBAR_COLS = 4  # the scrollbar column: one cell, its padding and the gap before it


def state_cell(i: Sandbox, k: int) -> Text:
    """The State column. A running dot breathes, each a little after the one above it, so a wave
    runs down the table; work in progress spins."""
    glyph, label, color = STATE_LOOK[i.state]
    if i.state == SandboxState.RUNNING:
        return Text.assemble(("●", st(mix(GREEN, INK, 0.6 * breathe(2.6, -k * 0.09)))), (f" {label}", st(GREEN)))
    if glyph == "◐":
        return Text.assemble(spinner(SKY), (f" {label}", st(SKY)))
    return Text(f"{glyph} {label}", st(color if i.state != SandboxState.STOPPED else MUTED))


def note_cell(i: Sandbox) -> Text:
    """The Notes column. One note at most, so a row stays one line (home's picker maps clicks to
    lines): the one that says what to do first. An unfinished install has nothing to update yet.

    Each note goes with the state it's about, and only that one, so a record still carrying a
    note from before the TUI marked it (stopping, restarting...) doesn't show it: "no run window"
    is about a running sandbox (closing a window won't stop it), and "port held by" about a
    stopped one (starting it would find the port taken)."""
    if i.state == SandboxState.INCOMPLETE:
        return Text("↻ pick it to resume", st(AMBER))
    if i.state == SandboxState.RUNNING and i.run_window is False:
        return Text("◌ no run window", st(MUTED))
    if i.state == SandboxState.STOPPED and i.port_held_by:
        return Text(f"▲ port held by {i.port_held_by}", st(AMBER))
    if i.update_ready:
        return Text("↑ update ready", st(VIOLET))
    if i.pinned:  # its ComfyUI is never "update ready": it says where it's held instead
        return Text(f"◆ pinned to {i.pinned}", st(SKY))
    return Text("")


# The widest the table gets: it fills the window up to this.
TABLE_MAX_WIDTH = 120


def spins(sandboxes: list[Sandbox]) -> bool:
    """Whether a row of the table shows a spinner, the one thing in it that moves fast."""
    return any(STATE_LOOK[i.state][0] == "◐" for i in sandboxes)


def sandboxes_table(
    sandboxes: list[Sandbox],
    width: int,
    *,
    highlight: Sandbox | None = None,
    only: Sandbox | None = None,
    window: tuple[int, int] | None = None,
    reveal: float | None = None,
    expand: bool = True,
):
    """One line per sandbox, at most width wide. Name, State and URL are always whole. Notes and
    Models get what's left, in that order: Models is cut in the middle, and a column with no room
    is dropped. highlight marks home's pointer row. only shows just that sandbox (it's been
    opened), in the columns all of them would get, so the row doesn't shift sideways from where it
    was on home. window=(first, count) shows that many rows from first, with a scrollbar and a
    caption; the columns are still sized for every row, so they don't change while scrolling.
    expand: fill the width (up to TABLE_MAX_WIDTH) with the Name column; False draws the columns at
    their fitted widths, as the tests probe where each is given up.
    reveal: how many rows have cascaded in so far (the rest draw empty, so the layout doesn't move)."""
    if not sandboxes:
        return Panel(
            Group(
                Text.assemble(("◇ ", st(SKY, bold=True)), ("No sandboxes yet", st(TEXT, bold=True))),
                Text("Install one to get started: it takes about ten minutes.", st(MUTED)),
            ),
            box=DASHED,
            border_style=st(LINE),
            expand=False,
            padding=(1, 3),
        )
    width = min(width, TABLE_MAX_WIDTH)
    first, count = window or (0, len(sandboxes))
    scrolling = count < len(sandboxes)
    if scrolling:
        width -= SCROLLBAR_COLS
    rows_ = []
    for k, i in enumerate(sandboxes):
        note = note_cell(i)
        # Every cell is a Text: a plain string would be read as markup, and a path's [ or \ would break.
        rows_.append(
            {
                "Name": Text(i.name),
                "State": state_cell(i, k),
                "URL": Text.from_markup(url_link(i.port), style=st(SKY))
                if i.state == SandboxState.RUNNING
                else Text(f"— port {i.port}", st(FAINT)),
                "Models": Text(i.models, st(MUTED)),
                "Notes": note,
            }
        )

    def natural_width(col: str) -> int:
        return max(cell_len(col), *(r[col].cell_len for r in rows_))

    # Long names give way before State and URL do (a narrow window can't fit 24 + 14 + 22).
    name_max = min(NAME_MAX, max(8, width - 10 - natural_width("State") - natural_width("URL")))
    for i, r in zip(sandboxes, rows_, strict=False):
        mark = Text("▌ ", st(mix(SKY, MINT, breathe(2.0)), bold=True)) if i is highlight else Text("  ")
        r["Name"] = Text.assemble(mark, (elide(r["Name"].plain, name_max), st(TEXT, bold=i is highlight)))
    natural = {col: natural_width(col) for col in rows_[0]}

    # Each column costs its width, 2 of padding and 1 separator; pad_edge drops the outer 2.
    left = width - (natural["Name"] + natural["State"] + natural["URL"] + 3 * 3 - 3)
    widths: dict[str, int] = {}
    for col in ("Notes", "Models"):
        if col == "Models":
            fit = min(natural["Models"], left - 3)
            if fit >= MODELS_MIN:
                widths["Models"] = fit
                left -= fit + 3
        elif left >= natural[col] + 3:
            widths[col] = natural[col]
            left -= natural[col] + 3
    shown = [c for c in ("Models", "Notes") if c in widths]

    t = Table(
        box=box.SIMPLE_HEAD,
        show_edge=False,
        expand=expand,
        pad_edge=False,
        border_style=st(LINE),
        header_style=st(FAINT, bold=True),
        caption=Text(f"  {first + 1}–{first + count} of {len(sandboxes)} sandboxes", st(FAINT)) if scrolling else None,
        caption_justify="left",
    )
    for col in ("Name", "State", "URL"):
        # If even these don't fit (a very narrow window), Rich cuts them at the end.
        t.add_column(
            ("  " if col == "Name" else "") + col.upper(),
            no_wrap=True,
            overflow="ellipsis",
            min_width=natural[col],
            ratio=1 if col == "Name" else None,
        )
    for col in shown:
        t.add_column(col.upper(), width=widths[col], no_wrap=True)
    if "Models" in widths:
        for r in rows_:
            r["Models"] = Text(elide(r["Models"].plain, widths["Models"]), st(MUTED))
    if scrolling:
        t.add_column("", width=1, no_wrap=True)
        bar_cells = scrollbar(first, count, len(sandboxes))
    for k, (i, r) in enumerate(zip(sandboxes, rows_, strict=False)):
        if (only is not None and i is not only) or not first <= k < first + count:
            continue
        cells = [r[col] for col in ("Name", "State", "URL", *shown)]
        if reveal is not None and k - first >= reveal:
            cells = [Text("") for _ in cells]
        if scrolling:
            cells.append(bar_cells[k - first])
        # Stripes follow the sandbox, not the screen line, so they don't flicker while scrolling.
        t.add_row(*cells, style=ROW_SELECTED if i is highlight else ROW_STRIPE if k % 2 else None)
    return t


class TableSize(NamedTuple):
    """What the sandboxes table takes: its rows, and how wide each of its lines draws."""

    rows: int
    line_widths: tuple[int, ...]

    @property
    def width(self) -> int:
        """The widest line."""
        return max(self.line_widths, default=0)


def measure_table(sandboxes: list[Sandbox], width: int, window: tuple[int, int] | None = None) -> TableSize:
    """The size of the table at width. Only the measurement is cached (keyed by what the table draws
    from each sandbox, the width and the window): the table itself is not, because its running dots
    and pointer mark breathe every frame.

    Building and rendering the table is what costs, and a frame asks for its size several times
    (the picker's render and its visible-rows count, the live view's width and room). The key has
    each field a column's width comes from (name: Name; state: State; port: URL; updates, run_window
    and port_held_by: Notes; models: Models, which is also dropped when too narrow), the width the columns are fitted to,
    and the window (it sets which rows show, and the scrollbar column). The pointer, `only` and
    `reveal` change what a row looks like, not how much room the table takes."""
    return _measure(
        (
            tuple((i.name, i.state, i.port, i.updates, i.models, i.run_window, i.port_held_by) for i in sandboxes),
            width,
            window,
        )
    )


@lru_cache(maxsize=32)
def _measure(key: tuple) -> TableSize:
    # A Sandbox is mutable and compares by identity, so it can't be the key: the key holds the
    # fields it was made from, and the table is rebuilt from them on a miss.
    shown, width, window = key
    sandboxes = [
        Sandbox(name, state, port, models, updates, run_window, port_held_by)
        for name, state, port, updates, models, run_window, port_held_by in shown
    ]
    lines = text_lines(sandboxes_table(sandboxes, width, window=window), width)
    return TableSize(len(lines), tuple(cell_len(line.rstrip()) for line in lines))


def forget_measurements() -> None:
    """Empties the measurement cache (the tests start from an empty one)."""
    _measure.cache_clear()


def table_width(sandboxes: list[Sandbox], width: int) -> int:
    """How wide the sandboxes table draws in width (its columns are sized for every sandbox,
    even when it shows one), or all of width with no sandboxes."""
    if not sandboxes:
        return width
    return min(width, measure_table(sandboxes, width).width)


def scrollbar(first: int, count: int, total: int) -> list[Text]:
    """One cell per visible row: a thumb sized and placed by how much of the list shows."""
    size = max(1, round(count * count / total))
    top = round(first * (count - size) / (total - count))
    return [Text("┃", st(SKY, bold=True)) if top <= k < top + size else Text("│", st(LINE)) for k in range(count)]
