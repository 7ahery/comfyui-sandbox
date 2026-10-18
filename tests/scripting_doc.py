"""The keys docs/scripting.md documents for each command's `data` ("Per-command `data`").

`conftest.cli` checks every successful `--json` document the suite produces against them, so a key added to a
command's `data` without a row in the table fails the test that produced it. The check is one-sided: a documented
key the suite never sees is not caught, and nested keys aren't checked.

How a row is read:
- The first cell names the commands: each code span is one (`--version` is `version`, options are dropped), and a
  one-word span after a two-word one shares its first word (`launcher install` / `remove`). Commands split by
  ` / ` take the second cell's parts split the same way, in order.
- The second cell's keys are the quoted names at the top level of its `{...}` spans, plus any code span that is a
  bare or quoted lowercase name other than a JSON literal (`env_port`, `"path"`). `check`'s row points at its own table, whose first cells give its keys.
- Commands under "Commands that change the sandbox end stopped" may also carry `container` and `run_window_closed`.
- An old name (`reinstall` (and its old name `recreate`)) returns what the command it names returns.
"""

import re
from functools import cache
from pathlib import Path

SCRIPTING_DOC = Path(__file__).resolve().parent.parent / "docs" / "scripting.md"

STOP_RULE_KEYS = {"container", "run_window_closed"}

SPAN = re.compile(r"`([^`]+)`")
NAME = re.compile(r'^"?([a-z_][a-z0-9_]*)"?$')
LITERALS = {"null", "true", "false"}
ALIAS = re.compile(r"`([\w-]+)` \(and its old name `([\w-]+)`\)")


def _section(text: str, title: str) -> str:
    start = text.index(f"\n## {title}\n")
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else len(text)]


def _rows(section: str) -> list[list[str]]:
    """Table body rows, split into cells (`\\|` inside a cell is not a separator)."""
    rows = []
    for line in section.splitlines():
        if not line.startswith("| ") or line.startswith("|---"):
            continue
        cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", line)[1:-1]]
        rows.append(cells)
    return rows[1:]  # the header row


def _commands(cell: str) -> list[str]:
    names: list[str] = []
    for span in SPAN.findall(cell):
        words = [word for word in span.split() if not word.startswith("-") and not word.startswith("[")]
        if span == "--version":
            words = ["version"]
        if len(words) == 1 and names and " " in names[-1]:
            words = [names[-1].split()[0], words[0]]
        names.append(" ".join(words))
    return names


def _top_level_keys(span: str) -> set[str]:
    """Quoted names right after `{` or `,` at the outermost brace level of `span`."""
    keys, depth, previous = set(), 0, ""
    for match in re.finditer(r'"[^"]*"|[{}\[\],:|]', span):
        token = match.group()
        if token in "{[":
            depth += 1
        elif token in "}]":
            depth -= 1
        elif token.startswith('"') and depth == 1 and previous in ("{", ","):
            keys.add(token.strip('"'))
        previous = token
    return keys


def _keys(cell: str) -> set[str]:
    keys: set[str] = set()
    for span in SPAN.findall(cell.replace("\\|", "|")):
        if span.startswith("{"):
            keys |= _top_level_keys(span)
        elif (name := NAME.match(span)) and name.group(1) not in LITERALS:
            keys.add(name.group(1))
    return keys


def _ends_stopped(text: str) -> tuple[set[str], bool]:
    """The commands the stop rule names, and whether it covers every `update` target."""
    paragraph = _section(text, "Commands that change the sandbox end stopped").split("\n\n")[1]
    named = paragraph.split(" stop ComfyUI")[0]  # the rest names `start` and `backup`, which it doesn't cover
    every_update = "every `update` target" in named
    return set(SPAN.findall(named)) - ({"update"} if every_update else set()), every_update


@cache
def documented_data_keys() -> dict[str, frozenset[str]]:
    """Command words (as in the document's `command`) to the top-level `data` keys documented for them."""
    text = SCRIPTING_DOC.read_text("utf-8")
    section = _section(text, "Per-command `data`")
    tables = section.split("\n\n")
    command_table = next(t for t in tables if t.startswith("| Command |"))
    check_table = next(t for t in tables if t.startswith("| Key |"))
    check_keys = {span.split(".")[0] for row in _rows(check_table) for span in SPAN.findall(row[0])}
    stopped, every_update = _ends_stopped(text)

    found: dict[str, set[str]] = {}
    for command_cell, data_cell in _rows(command_table):
        commands = _commands(command_cell)
        if "See below" in data_cell:
            cells = [data_cell] * len(commands)
        elif " / " in command_cell:  # `launcher install` / `remove`: one data cell each, in order
            cells = data_cell.split(" / ")
        else:
            cells = [data_cell] * len(commands)
        for command, cell in zip(commands, cells, strict=True):
            keys = check_keys if "See below" in cell else _keys(cell)
            found.setdefault(command, set()).update(keys)
    for command, keys in found.items():
        if command in stopped or (every_update and command.startswith("update ")):
            keys |= STOP_RULE_KEYS
    for command, old_name in ALIAS.findall(command_table):
        found[old_name] = found[command]
    return {command: frozenset(keys) for command, keys in found.items()}


def undocumented_data_keys(doc: dict) -> set[str]:
    """The keys of a successful document's `data` that its command's row doesn't list."""
    documented = documented_data_keys()
    assert doc["command"] in documented, f'`{doc["command"]}` has no row in docs/scripting.md, "Per-command data"'
    return set(doc["data"]) - documented[doc["command"]]
