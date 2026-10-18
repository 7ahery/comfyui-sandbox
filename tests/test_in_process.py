"""A front end calling the CLI in-process: `cli.call(argv, deps, out)`, as its docstring describes it.

The TUI runs every action as a real command line through this, with its own `Output`. What's checked is
what a front end relies on: its `Output` is the one the command talks through, the result handed back
is what `--json` would have printed, and nothing else about the run changes (the transcript, the exit
codes, `after_failure`). Through the spec's seam B: real command lines on the CLI's fakes.
"""

from __future__ import annotations

import io
import json
from dataclasses import replace

import pytest
import typer
from test_check import Machine
from test_config import ENV, set_env
from test_verify import own

from comfyui_sandbox import cli as cli_module
from comfyui_sandbox.deps import Deps
from comfyui_sandbox.errors import CliError
from comfyui_sandbox.logs import logs_dir
from comfyui_sandbox.output import Output
from comfyui_sandbox.winapi import PortOwner


class Recording(Output):
    """An Output that remembers what the command said through it, as a front end's would show it."""

    stdout: io.StringIO  # the one it is built with, which the tests read back

    def __init__(self) -> None:
        super().__init__(io.StringIO(), io.StringIO(), json_mode=False)
        self.said: list[tuple[str, str]] = []

    def step(self, text: str) -> None:
        self.said.append(("step", text))
        super().step(text)

    def info(self, text: str) -> None:
        self.said.append(("info", text))
        super().info(text)

    def error(self, text: str) -> None:
        self.said.append(("error", text))
        super().error(text)


@pytest.fixture
def deps(runner, winapi, home, environ, prompter, http, launcher) -> Deps:
    """A terminal on both ends, so a call that dispatched to a front end would (and must not) open one."""
    Machine().script(runner, environ)
    own(home)
    return Deps(
        runner=runner,
        winapi=winapi,
        home=home,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        platform="win32",
        environ=environ,
        prompter=prompter,
        http=http,
        sleep=lambda seconds: None,
        stdin_is_terminal=True,
        stdout_is_terminal=True,
        launch=launcher,
    )


def json_document(deps: Deps, *argv: str) -> dict:
    """What the plain CLI prints for argv under --json."""
    stdout = io.StringIO()
    cli_module.main([*argv, "--json"], replace(deps, stdout=stdout, stderr=io.StringIO()))
    return json.loads(stdout.getvalue())


def test_the_command_talks_through_the_injected_output(deps):
    out = Recording()

    cli_module.call(["stop", "--distro", "comfy-docker"], deps, out)

    assert ("step", "Stopping ComfyUI") in out.said


def test_the_result_handed_back_is_what_json_prints(deps):
    printed = json_document(deps, "stop", "--distro", "comfy-docker")

    result = cli_module.call(["stop", "--distro", "comfy-docker"], deps, Recording())

    # Each run writes its own transcript, so only the path differs.
    assert {**result.document(), "log": None} == {**printed, "log": None}
    assert (result.ok, result.exit_code, result.message) == (True, 0, "ComfyUI has been stopped.")
    assert result.data == {"distro": "comfy-docker"}
    assert result.log is not None and result.log.exists()


def test_a_failure_hands_back_the_same_document_as_json_with_its_error_and_transcript(deps, runner):
    runner.on("wsl.exe", returncode=1, stderr="no configuration file provided")
    runner.on("wsl.exe", "--list", "--quiet", stdout="comfy-docker\n")
    printed = json_document(deps, "stop", "--distro", "comfy-docker")

    out = Recording()
    result = cli_module.call(["stop", "--distro", "comfy-docker"], deps, out)

    assert {**result.document(), "log": None} == {**printed, "log": None}
    assert (result.ok, result.exit_code) == (False, 1)
    assert result.error == result.message and "docker compose stop failed" in result.message
    assert ("error", result.message) in out.said  # shown through the front end's Output, as the CLI shows it
    assert result.log is not None and result.log.parent == logs_dir(deps.app_dir)


def test_a_usage_error_maps_to_its_exit_code_as_in_the_cli(deps):
    result = cli_module.call(["stop", "--no-such-option"], deps, Recording())

    assert (result.exit_code, result.ok) == (2, False)


def test_the_result_carries_the_flag_that_answers_a_missing_answer(deps, winapi, runner):
    set_env(runner, ENV)
    winapi.port_owners[9000] = PortOwner(4120, "python.exe")
    argv = ["config", "--distro", "comfy-docker", "--port", "9000", "--non-interactive"]
    printed = json_document(deps, *argv)

    result = cli_module.call(argv, deps, Recording())

    assert (result.exit_code, result.flag) == (9, "--port")
    assert {**result.document(), "log": None} == {**printed, "log": None}


def test_the_injected_output_takes_no_json_mode_from_the_front_end_but_from_the_command_line(deps):
    out = Recording()

    result = cli_module.call(["stop", "--distro", "comfy-docker", "--json"], deps, out)

    assert result.ok
    assert json.loads(out.stdout.getvalue()) == result.document()  # --json still prints its document


def test_an_in_process_call_never_opens_a_front_end_again(deps, launcher):
    for argv in ([], ["home"]):
        result = cli_module.call(argv, deps, Recording())

        assert result.exit_code == 2
    assert launcher.opened == []


def test_after_failure_still_runs_once_the_failure_is_shown(deps):
    shown: list[str] = []
    app = cli_module.build_app()

    @app.command("boom")
    def boom(ctx: typer.Context) -> None:
        ctx.obj.after_failure = lambda: shown.append("after")
        raise CliError("It broke.")

    out = Recording()
    result = cli_module.call(["boom"], deps, out, app=app)

    assert result.message == "It broke."
    assert out.said[-1] == ("error", "It broke.") and shown == ["after"]


def test_an_interruption_inside_a_command_ends_as_interrupted_with_its_transcript_closed_so(deps, runner):
    # Typer turns a KeyboardInterrupt raised inside a command into exit 130 itself, rather than
    # letting it reach main(): it must still read as the CLI's interruption, and close the transcript.
    def ctrl_c():
        raise KeyboardInterrupt

    runner.on("wsl.exe", "-d", "comfy-docker", "-u", "comfy", then=ctrl_c)
    printed = json_document(deps, "stop", "--distro", "comfy-docker")

    result = cli_module.call(["stop", "--distro", "comfy-docker"], deps, Recording())

    assert {**result.document(), "log": None} == {**printed, "log": None}
    assert (result.exit_code, result.message) == (130, "Interrupted.")
    assert result.log is not None and "exit=130" in result.log.read_text("utf-8")
