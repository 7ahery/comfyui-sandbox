"""Home's sandboxes table gives up columns as the window narrows: Name, State and URL stay whole;
Notes and Models get what's left, Models is cut in the middle first and dropped below its minimum
width, then Notes goes.

Checked at the edges: no sandboxes, an exact fit, one over, and very narrow.
"""

import pytest
from rich.cells import cell_len

from comfyui_sandbox.tui.gateway import Sandbox
from comfyui_sandbox.tui.home.table import MODELS_MIN, note_cell, sandboxes_table
from comfyui_sandbox.tui.measure import text_lines
from comfyui_sandbox.tui.states import SandboxState

MODELS = r"D:\AI\models\checkpoints"  # the longest models folder, so it sets the column's width
URL = "http://127.0.0.1:8188"
NOTE = "↑ update ready"


def sandboxes() -> list[Sandbox]:
    return [
        Sandbox("comfyui", SandboxState.RUNNING, 8188, models=MODELS, updates=("comfyui",)),
        Sandbox("sdxl", SandboxState.STOPPED, 8189),
    ]


def drawn(width: int) -> list[str]:
    return text_lines(sandboxes_table(sandboxes(), width, expand=False), width)


def header(width: int) -> list[str]:
    return drawn(width)[0].split()


def first_row(width: int) -> str:
    return drawn(width)[2]  # under the header and its rule


@pytest.fixture
def natural() -> int:
    """The table's width with every column whole."""
    return max(cell_len(line.rstrip()) for line in drawn(200))


def test_a_pinned_sandbox_says_its_pin_in_place_of_an_update_mark() -> None:
    pinned = Sandbox("old", SandboxState.STOPPED, 8188, channel="0.3.40")
    assert note_cell(pinned).plain == "◆ pinned to 0.3.40"
    pinned.updates = ("nodes",)  # another target's update still says so first
    assert note_cell(pinned).plain == NOTE
    assert note_cell(Sandbox("new", SandboxState.STOPPED, 8188, channel="nightly")).plain == ""


def test_no_sandboxes_draw_an_invitation_to_install_one() -> None:
    text = "\n".join(text_lines(sandboxes_table([], 80), 80))
    assert "No sandboxes yet" in text
    assert "NAME" not in text


def test_at_exactly_its_natural_width_every_column_is_whole(natural: int) -> None:
    assert header(natural) == ["NAME", "STATE", "URL", "MODELS", "NOTES"]
    row = first_row(natural)
    assert MODELS in row and URL in row and NOTE in row


def test_one_cell_narrower_cuts_the_models_folder_in_the_middle(natural: int) -> None:
    row = first_row(natural - 1)
    assert MODELS not in row
    assert "D:\\AI\\" in row and "checkpoints" in row and "…" in row
    assert NOTE in row and URL in row


def test_models_stays_down_to_its_minimum_width_then_is_dropped(natural: int) -> None:
    at_minimum = natural - (len(MODELS) - MODELS_MIN)
    assert "MODELS" in header(at_minimum)
    assert header(at_minimum - 1) == ["NAME", "STATE", "URL", "NOTES"]
    assert NOTE in first_row(at_minimum - 1)


def test_notes_stay_while_they_fit_whole_then_are_dropped(natural: int) -> None:
    width = natural - (len(MODELS) - MODELS_MIN) - 1
    without_models = max(cell_len(line.rstrip()) for line in drawn(width))
    assert NOTE in first_row(without_models)
    assert header(without_models - 1) == ["NAME", "STATE", "URL"]
    assert URL in first_row(without_models - 1)  # the URL is never what gives way first


def test_a_very_narrow_window_cuts_the_row_at_its_end_but_keeps_the_name() -> None:
    assert first_row(20).startswith("  comfyui")


@pytest.mark.parametrize("width", [10, 20, 30, 47, 61, 62, 80, 88, 89, 120])
def test_no_line_is_ever_wider_than_the_window(width: int) -> None:
    assert all(len(line.rstrip()) <= width for line in drawn(width))
