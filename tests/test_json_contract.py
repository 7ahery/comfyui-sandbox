"""The --json result contract (docs/scripting.md, "The --json result document")."""

import pytest
import typer
from scripting_doc import documented_data_keys
from test_version import PROJECT_VERSION
from typer.core import TyperGroup

from comfyui_sandbox.cli import build_app

REQUIRED_KEYS = {"command", "ok", "exit_code", "message", "data"}


@pytest.mark.parametrize("argv", [("--version", "--json"), ("--json", "--version")])
def test_version_json_is_exactly_one_document_with_the_documented_keys(cli, argv):
    result = cli(*argv)

    assert result.exit_code == 0
    assert result.json == {
        "command": "version",
        "ok": True,
        "exit_code": 0,
        "message": f"comfyui-sandbox {PROJECT_VERSION}",
        "data": {"version": PROJECT_VERSION},
    }
    assert result.stdout.endswith("}\n")


def test_every_command_has_a_row_in_the_data_table():
    # Each document the suite produces is checked against its row (conftest.cli); a command with no row would
    # only fail once a test ran it with --json. home never succeeds under --json.
    group = typer.main.get_command(build_app())
    assert isinstance(group, TyperGroup)
    commands = {name for name, command in group.commands.items() if not command.hidden} - {"home"}
    documented = {command.split()[0] for command in documented_data_keys()}

    assert commands - documented == set()
