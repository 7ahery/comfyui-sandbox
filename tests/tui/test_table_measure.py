"""The sandboxes table's measurements are cached: the table is not rendered again just to be measured."""

from dataclasses import replace

import pytest

from comfyui_sandbox.tui.gateway import Sandbox
from comfyui_sandbox.tui.home import table
from comfyui_sandbox.tui.measure import text_lines
from comfyui_sandbox.tui.states import SandboxState


@pytest.fixture
def renders(monkeypatch) -> list[int]:
    """Counts the tables built for measuring."""
    calls: list[int] = []
    real = table.sandboxes_table

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(table, "sandboxes_table", counting)
    table.forget_measurements()
    return calls


def sandboxes() -> list[Sandbox]:
    return [Sandbox("alpha", SandboxState.RUNNING, 8188), Sandbox("beta", SandboxState.STOPPED, 8189)]


def test_measuring_twice_renders_once(renders) -> None:
    a = table.measure_table(sandboxes(), 100)
    b = table.measure_table(sandboxes(), 100)
    assert a == b
    assert len(renders) == 1


def test_measurement_matches_the_rendered_table(renders) -> None:
    items = sandboxes()
    lines = text_lines(table.sandboxes_table(items, 100), 100)
    m = table.measure_table(items, 100)
    assert m.rows == len(lines)
    assert m.width == max(len(line.rstrip()) for line in lines)


@pytest.mark.parametrize(
    "change",
    [
        lambda i: replace(i, name="gamma-longer"),
        lambda i: replace(i, state=SandboxState.INCOMPLETE),
        lambda i: replace(i, port=9001),
        lambda i: replace(i, updates=("comfyui",)),
    ],
)
def test_a_change_to_a_keyed_field_invalidates(renders, change) -> None:
    items = sandboxes()
    table.measure_table(items, 100)
    table.measure_table([change(items[0]), items[1]], 100)
    assert len(renders) == 2


def test_a_change_of_width_invalidates(renders) -> None:
    table.measure_table(sandboxes(), 100)
    table.measure_table(sandboxes(), 60)
    assert len(renders) == 2


def test_the_measure_is_whole_table_width_with_no_sandboxes() -> None:
    assert table.table_width([], 80) == 80
