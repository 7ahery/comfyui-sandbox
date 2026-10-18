"""Which front end a command line opens (docs/scripting.md, "The TUI"; ADR 0017).

`main()` is the one place that picks: a bare `comfyui-sandbox`, or `home`, at a terminal opens home
through `Deps.launch`; everything else is the plain CLI. These tests drive `main(argv, deps)` with
the fake launcher and play the terminal facts, so no TUI ever opens here.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from comfyui_sandbox.front_end import Home


def test_a_bare_command_at_a_terminal_opens_home(cli, launcher):
    result = cli(terminal=True)

    assert launcher.opened == [Home()]
    assert result.exit_code == 0


def test_home_at_a_terminal_opens_home(cli, launcher):
    result = cli("home", terminal=True)

    assert launcher.opened == [Home()]
    assert result.exit_code == 0


@pytest.mark.parametrize(
    "options, terminal",
    [
        pytest.param((), {"terminal": False}, id="no terminal"),
        pytest.param((), {"stdin_terminal": False, "stdout_terminal": True}, id="stdin piped"),
        pytest.param((), {"stdin_terminal": True, "stdout_terminal": False}, id="stdout piped"),
        pytest.param(("--non-interactive",), {"terminal": True}, id="--non-interactive"),
    ],
)
@pytest.mark.parametrize("command", [(), ("home",)], ids=["bare", "home"])
def test_where_home_cant_open_nothing_opens_and_the_message_says_a_terminal_would_open_it(
    cli, launcher, command, options, terminal
):
    result = cli(*command, *options, **terminal)

    assert launcher.opened == []
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Error:" in result.stderr
    assert "a terminal" in result.stderr and "opens home" in result.stderr


@pytest.mark.parametrize("command", [(), ("home",)], ids=["bare", "home"])
def test_under_json_nothing_opens_and_the_document_says_a_terminal_would_open_home(cli, launcher, command):
    result = cli(*command, "--json", terminal=True)

    assert launcher.opened == []
    assert result.exit_code == 2
    doc = result.json
    assert doc["ok"] is False and doc["exit_code"] == 2
    assert doc["command"] == " ".join(command)
    assert "a terminal" in doc["error"] and "opens home" in doc["error"]


@pytest.mark.parametrize("options", [("--yes",), ("--distro", "comfyui"), ("--distro=comfyui",)])
@pytest.mark.parametrize("command", [(), ("home",)], ids=["bare", "home"])
def test_yes_or_distro_with_no_command_is_a_usage_error_even_at_a_terminal(cli, launcher, command, options):
    """Home doesn't open straight on a sandbox (yet), and has no defaults to accept."""
    result = cli(*command, *options, terminal=True)

    assert launcher.opened == []
    assert result.exit_code == 2
    assert "--help" in result.stderr


def test_help_lists_home(cli, launcher):
    result = cli("--help", terminal=True)

    assert result.exit_code == 0
    assert launcher.opened == []
    assert "home" in [line.strip("│ ").split(" ", 1)[0] for line in result.stdout.splitlines()]


def test_homes_own_help_says_it_needs_a_terminal(cli, launcher):
    result = cli("home", "--help", terminal=True)

    assert result.exit_code == 0
    assert launcher.opened == []
    assert "terminal" in result.stdout


def test_home_is_handed_the_same_deps_the_commands_get(cli, launcher, runner, winapi, home, http):
    """The real gateway reads the machine through these, never through Deps of its own: so home
    sees the same installs.json, run-window markers and ports a command line would."""
    cli(terminal=True)

    [deps] = launcher.deps
    assert (deps.runner, deps.winapi, deps.home, deps.http) == (runner, winapi, home, http)


def test_the_screens_exit_code_is_the_commands(cli, launcher):
    """Quitting home is 0; Ctrl+C in it is 130, as for any command. The screen has already shown
    what happened, so nothing is printed after it."""
    launcher.exit_code = 130

    result = cli(terminal=True)

    assert result.exit_code == 130
    assert result.stdout == "" and result.stderr == ""


def test_a_crash_in_the_tui_exits_1_with_its_traceback_in_a_transcript(cli, launcher, home):
    def deep_inside_the_tui():
        raise RuntimeError("the screen fell over")

    try:
        deep_inside_the_tui()
    except RuntimeError as exc:
        launcher.crash = exc

    result = cli(terminal=True)

    assert result.exit_code == 1
    assert "RuntimeError: the screen fell over" in result.stderr
    [transcript] = (home / ".comfyui-sandbox" / "logs").glob("*-home.log")
    assert str(transcript) in result.stderr
    text = transcript.read_text(encoding="utf-8")
    assert "deep_inside_the_tui" in text  # the traceback, not just the message
    assert "exit=1" in text


def test_a_piped_bare_command_still_exits_2_for_real(tmp_path):
    """The whole way, with the real terminal facts: a script or a pipe never gets a full screen."""
    env = dict(os.environ, USERPROFILE=str(tmp_path), PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "comfyui_sandbox"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=60,
        check=False,
    )

    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "a terminal" in proc.stderr and "opens home" in proc.stderr
