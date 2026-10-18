"""The TUI's states are enums, not strings: a typo is an error, not a silent mismatch."""

import ast
import enum
from pathlib import Path

from comfyui_sandbox import tui
from comfyui_sandbox.tui import states
from comfyui_sandbox.tui.home import table

TUI = Path(tui.__file__).parent

ENUMS = [
    states.SandboxState,
    states.LaunchPhase,
    states.StageState,
    states.StepStatus,
    states.Outcome,
    states.SamplerKind,
]
VALUES = {m.value for e in ENUMS for m in e}


def test_every_state_enum_is_a_strenum() -> None:
    assert all(issubclass(e, enum.StrEnum) for e in ENUMS)


def test_the_state_look_table_covers_every_sandbox_state() -> None:
    assert set(table.STATE_LOOK) == set(states.SandboxState)
    assert all(isinstance(k, states.SandboxState) for k in table.STATE_LOOK)


def _strings(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Tuple | ast.List | ast.Set):
        return [s for e in node.elts for s in _strings(e)]
    return []


# Menu choices and plain text share a few words with the states ("install", "cancelled"); they are
# not states, so a comparison with one of these names is left alone, and so is a dict with other keys.
NOT_STATES = {"picked", "plain"}


def test_no_state_is_compared_or_keyed_by_a_string_literal() -> None:
    hits = []
    for path in sorted(TUI.rglob("*.py")):
        if path.name == "states.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Compare):
                operands = [node.left, *node.comparators]
                if any(isinstance(o, ast.Name) and o.id in NOT_STATES for o in operands):
                    continue
                literals = [s for o in operands for s in _strings(o)]
            elif isinstance(node, ast.Dict):
                literals = [s for k in node.keys if k is not None for s in _strings(k)]
                if not all(s in VALUES for s in literals):
                    continue
            else:
                continue
            hits += [f"{path.relative_to(TUI.parent)}:{node.lineno} {s!r}" for s in literals if s in VALUES]
    assert not hits, "state strings still used as literals:\n" + "\n".join(hits)
