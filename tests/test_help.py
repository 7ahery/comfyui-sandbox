"""--help is the one exception to the result contract (docs/scripting.md, "Streams")."""

import pytest
import typer
import typer.core

from comfyui_sandbox.cli import build_app

# They hand everything after their name to the tool they wrap, `--help` included (docs/scripting.md).
PASSTHROUGHS = {"comfy", "cm-cli", "exec"}


@pytest.mark.parametrize("argv", [("--help",), ("--json", "--help")])
def test_help_lists_the_global_options_and_prints_no_result_document(cli, argv):
    result = cli(*argv)

    assert result.exit_code == 0
    for option in ("--json", "--yes", "--non-interactive", "--distro", "--version"):
        assert option in result.stdout
    assert '"exit_code"' not in result.stdout


def _command_paths(command, path=()):
    """Every command and group under the CLI that prints its own help, as the words that name it."""
    yield path
    for name, sub in sorted(getattr(command, "commands", {}).items()):
        if name not in PASSTHROUGHS:
            yield from _command_paths(sub, (*path, name))


def test_no_help_text_calls_a_sandbox_an_instance(cli):
    """ "Sandbox" is the one user-facing noun (the feat-tui-integration spec, "Naming"); "instance" is gone."""
    paths = list(_command_paths(typer.main.get_command(build_app())))
    assert ("update", "setup") in paths
    for path in paths:
        result = cli(*path, "--help")
        assert result.exit_code == 0, path
        assert "instance" not in result.stdout.lower(), path


def test_the_distro_option_is_described_as_the_sandbox_to_act_on(cli):
    # Read off the option, not the rendered page: Rich wraps the help to the terminal's width.
    [option] = [
        p
        for p in typer.main.get_command(build_app()).params
        if p.name == "distro" and isinstance(p, typer.core.TyperOption)
    ]
    assert option.help == "The sandbox to act on."
