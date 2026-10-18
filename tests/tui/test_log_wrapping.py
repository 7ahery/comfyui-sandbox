"""A log's long lines wrap into rows, and a wrapped log scrolls by log lines, not rows.

Each piece is checked at its edges: nothing to show, an exact fit, one over, and very narrow.
"""

import pytest
from rich.text import Text

from comfyui_sandbox.tui.logs import LogSession, WrappedLog, wrap_line, wrapped_tail


def plains(rows) -> list[str]:
    return [row.plain for row in rows]


# -- wrap_line ------------------------------------------------------------------------------------


def test_an_empty_line_is_one_empty_row() -> None:
    assert plains(wrap_line(Text(""), 20)) == [""]


def test_a_line_that_exactly_fits_stays_one_row() -> None:
    assert plains(wrap_line(Text("x" * 20), 20)) == ["x" * 20]


def test_one_cell_over_moves_the_last_character_to_an_indented_row() -> None:
    assert plains(wrap_line(Text("x" * 21), 20)) == ["x" * 20, "  x"]


def test_a_break_falls_after_a_space_in_the_rows_second_half() -> None:
    assert plains(wrap_line(Text("a" * 14 + " " + "b" * 10), 20)) == ["a" * 14, "  " + "b" * 10]


def test_a_space_in_the_rows_first_half_is_passed_over_for_a_mid_word_break() -> None:
    # Breaking at the space would leave most of the row empty.
    assert plains(wrap_line(Text("aaa " + "b" * 22), 20)) == ["aaa " + "b" * 16, "  " + "b" * 6]


def test_a_very_narrow_width_still_leaves_room_for_a_few_letters_a_row() -> None:
    # Below ten cells a row would be mostly indent; the rows stay ten wide and the screen crops.
    assert plains(wrap_line(Text("x" * 25), 1)) == ["x" * 10, "  " + "x" * 8, "  " + "x" * 7]


@pytest.mark.parametrize("width", [1, 10, 11, 19, 20, 21, 37, 80])
def test_wrapping_cuts_nothing_off(width: int) -> None:
    line = "Downloading https://example.com/models/sdxl_base_1.0.safetensors 6,938,078,334 B at 81.2 MB/s"
    rows = plains(wrap_line(Text(line), width))
    unwrapped = rows[0] + "".join(row[2:] for row in rows[1:])
    assert unwrapped.replace(" ", "") == line.replace(" ", "")  # only the spaces at breaks go


def test_a_wrapped_row_keeps_the_lines_highlighting() -> None:
    line = Text("x" * 18 + "ERROR")
    line.stylize("red", 18)
    rows = wrap_line(line, 20)
    assert plains(rows) == ["x" * 18 + "ER", "  ROR"]
    assert any(span.style == "red" for span in rows[1].spans)


# -- wrapped_tail ---------------------------------------------------------------------------------


def test_the_tail_of_no_lines_is_no_rows() -> None:
    assert wrapped_tail([], 20, 3) == []


def test_lines_that_exactly_fill_the_rows_all_show() -> None:
    assert plains(wrapped_tail([Text("a"), Text("b"), Text("c")], 20, 3)) == ["a", "b", "c"]


def test_one_line_over_drops_the_oldest() -> None:
    assert plains(wrapped_tail([Text("a"), Text("b"), Text("c"), Text("d")], 20, 3)) == ["b", "c", "d"]


def test_the_tail_shows_whole_lines_only() -> None:
    # The long line takes both rows; "a" doesn't get a row of its own, nor half of one.
    assert plains(wrapped_tail([Text("a"), Text("x" * 25)], 20, 2)) == ["x" * 20, "  " + "x" * 5]


def test_a_line_taller_than_the_rows_shows_its_start() -> None:
    assert plains(wrapped_tail([Text("x" * 50)], 20, 2)) == ["x" * 20, "  " + "x" * 18]


# -- WrappedLog: scrolling ------------------------------------------------------------------------


class Log(WrappedLog):
    """A WrappedLog with a fixed size, as a full-screen log draws it: the scrollbar takes two
    cells when the log is taller than its rows."""

    def __init__(self, lines: list[str], rows: int, width: int = 40) -> None:
        self.s, self.n, self.w = LogSession(), rows, width
        for line in lines:
            self.s.add(line)

    def session(self) -> LogSession:
        return self.s

    def rows(self) -> int:
        return self.n

    def text_width(self, overflow: bool) -> int:
        return self.w - (2 if overflow else 0)

    def shown(self) -> list[str]:
        rows, _, _ = self.view()
        return [row.plain for row in rows]


def numbered(n: int) -> list[str]:
    return [f"line {k}" for k in range(1, n + 1)]


def test_an_empty_log_fills_its_rows_with_blanks_and_says_so() -> None:
    log = Log([], rows=3)
    rows, first, last = log.view()
    assert [row.plain for row in rows] == ["", "", ""]
    assert log.where(first, last) == "no output yet"
    assert log.thumb(first, last) is None


def test_scrolling_an_empty_log_does_nothing() -> None:
    log = Log([], rows=3)
    log.view()
    log.scroll_lines(-1)
    log.page_lines(-1)
    log.to_top()
    assert log.session().following
    assert log.shown() == ["", "", ""]


def test_a_log_that_exactly_fits_has_no_scrollbar_and_does_not_scroll() -> None:
    log = Log(numbered(3), rows=3)
    rows, first, last = log.view()
    assert [row.plain for row in rows] == ["line 1", "line 2", "line 3"]
    assert log.thumb(first, last) is None
    log.scroll_lines(-1)
    assert log.shown() == ["line 1", "line 2", "line 3"]
    assert log.session().following


def test_one_line_over_shows_the_newest_and_scrolls_back_by_one() -> None:
    log = Log(numbered(4), rows=3)
    rows, first, last = log.view()
    assert [row.plain for row in rows] == ["line 2", "line 3", "line 4"]
    assert log.where(first, last) == "lines 2–4 of 4"
    assert log.thumb(first, last) is not None
    log.scroll_lines(-1)
    assert log.shown() == ["line 1", "line 2", "line 3"]
    assert not log.session().following
    log.scroll_lines(-1)  # already at the top
    assert log.shown() == ["line 1", "line 2", "line 3"]
    log.scroll_lines(1)  # back at the bottom: it follows new lines again
    assert log.shown() == ["line 2", "line 3", "line 4"]
    assert log.session().following


def test_a_paused_log_holds_its_place_as_new_lines_arrive() -> None:
    log = Log(numbered(4), rows=3)
    log.view()
    log.to_top()
    log.session().add("line 5")
    assert log.shown() == ["line 1", "line 2", "line 3"]
    log.follow()
    assert log.shown() == ["line 3", "line 4", "line 5"]


def test_a_wrapped_line_scrolls_as_a_whole() -> None:
    # width 20, less the scrollbar's 2: "x" * 30 takes two rows.
    log = Log(["first", "x" * 30, "third", "fourth"], rows=3, width=20)
    # At the bottom, only whole lines: the long one doesn't fit beside the two after it.
    assert log.shown() == ["", "third", "fourth"]
    log.to_top()
    assert log.shown() == ["first", "x" * 18, "  " + "x" * 12]
    log.scroll_lines(1)  # one log line down: both of the long line's rows move up together
    assert log.shown() == ["x" * 18, "  " + "x" * 12, "third"]


def test_paging_up_brings_in_the_lines_that_fill_the_screen_above() -> None:
    log = Log(numbered(10), rows=3)
    assert log.shown() == ["line 8", "line 9", "line 10"]
    log.page_lines(-1)
    assert log.shown() == ["line 5", "line 6", "line 7"]
    log.page_lines(1)  # the line at the bottom goes to the top
    assert log.shown() == ["line 7", "line 8", "line 9"]


def test_a_one_row_log_shows_the_start_of_a_line_too_tall_for_it() -> None:
    log = Log(["x" * 30], rows=1, width=12)
    assert log.shown() == ["x" * 12]


def test_the_place_is_counted_from_the_very_first_line_after_old_ones_drop() -> None:
    log = Log(numbered(3), rows=2)
    log.session().dropped = 100  # as if a hundred older lines had fallen off the top
    _, first, last = log.view()
    assert log.where(first, last) == "lines 102–103 of 103"
