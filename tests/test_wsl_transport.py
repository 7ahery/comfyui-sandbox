"""The in-distro wsl transport (the `comfyui_sandbox.wsl` docstring), seen from the CLI.

`indistro` stands in for any command that runs a package script or a command in the
distro through `session.wsl()`.
"""

from pathlib import Path

import pytest
import typer

import wslcalls
from comfyui_sandbox.cli import build_app
from comfyui_sandbox.output import Outcome
from comfyui_sandbox.runner import CommandTimeout
from comfyui_sandbox.wsl import CALL_TIMEOUT, ROOT, script_text

HOSTILE = 'it\'s a "path" with $HOME, `ticks` and spaces'


def indistro_app() -> typer.Typer:
    app = build_app()

    @app.command("script")
    def script(ctx: typer.Context, user: str = ROOT) -> Outcome:
        done = ctx.obj.wsl().run_script("comfy-docker", user, "create_user.sh", {"LINUX_USER": "comfy", "ODD": HOSTILE})
        return Outcome("ran", {"exit": done.returncode, "out": done.out()})

    @app.command("streamed")
    def streamed(ctx: typer.Context) -> Outcome:
        lines: list[str] = []
        done = ctx.obj.wsl().run_script("comfy-docker", "comfy", "status.sh", on_line=lines.append)
        return Outcome("ran", {"exit": done.returncode, "lines": lines})

    @app.command("command")
    def command(ctx: typer.Context) -> Outcome:
        done = ctx.obj.wsl().run_command(
            "comfy-docker", "comfy", ["docker", "compose", "ps"], {"COMPOSE_FILE": "a.yml:b.yml"}
        )
        return Outcome("ran", {"exit": done.returncode, "out": done.out()})

    return app


@pytest.fixture
def wsl_installed(environ):
    exe = Path(environ["ProgramFiles"]) / "WSL" / "wsl.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")


def test_scripts_are_streamed_over_stdin_with_list_args(cli, runner, wsl_installed):
    result = cli("script", "--json", app=indistro_app())

    assert result.exit_code == 0
    [call] = runner.calls
    assert isinstance(call.argv, tuple) and all(isinstance(a, str) for a in call.argv)
    assert call.argv == ("wsl.exe", "-d", "comfy-docker", "-u", "root", "--cd", "/", "--", "bash", "-s")
    assert call.env["WSL_UTF8"] == "1"
    run = wslcalls.script(runner.calls, "create_user.sh")
    assert run.env == {"LINUX_USER": "comfy", "ODD": HOSTILE}  # quoted for bash, read back intact
    assert run.body == script_text("create_user.sh").rstrip("\n")
    text = call.stdin.decode("utf-8")
    assert "\r" not in text
    assert "set -euo pipefail\n" in text
    assert text.endswith("\n} </dev/null\n")


def test_scripts_run_as_the_linux_user_when_asked(cli, runner, wsl_installed):
    cli("script", "--user", "comfy", app=indistro_app())

    assert wslcalls.script(runner.calls, "create_user.sh").user == "comfy"


def test_the_exit_code_is_the_scripts_and_output_is_decoded(cli, runner, wsl_installed):
    runner.on("wsl.exe", "-d", returncode=3, stdout="caf\xe9 ✓\r\n\0".encode())

    result = cli("script", "--json", app=indistro_app())

    assert result.json["data"] == {"exit": 3, "out": "caf\xe9 ✓\n"}


def test_streamed_scripts_feed_lines_as_they_come(cli, runner, wsl_installed):
    runner.on("wsl.exe", "-d", stdout="one\r\ntwo\n")

    result = cli("streamed", "--json", app=indistro_app())

    assert result.json["data"] == {"exit": 0, "lines": ["one", "two"]}
    assert runner.calls[0].streamed


def test_commands_run_without_a_shell(cli, runner, wsl_installed):
    result = cli("command", "--json", app=indistro_app())

    assert result.exit_code == 0
    [call] = runner.calls
    assert call.argv == (
        "wsl.exe",
        "-d",
        "comfy-docker",
        "-u",
        "comfy",
        "--cd",
        "/",
        "--exec",
        "env",
        "COMPOSE_FILE=a.yml:b.yml",
        "docker",
        "compose",
        "ps",
    )
    assert call.stdin is None
    assert call.env["WSL_UTF8"] == "1"
    [run] = wslcalls.commands(runner.calls)
    assert (run.user, run.env, run.argv) == ("comfy", {"COMPOSE_FILE": "a.yml:b.yml"}, ("docker", "compose", "ps"))


def test_nothing_runs_when_wsl_is_not_installed(cli, runner):
    result = cli("script", "--json", app=indistro_app())

    assert result.exit_code == 7
    assert runner.calls == []


def test_an_in_distro_call_without_a_timeout_gets_the_default_one(cli, runner, wsl_installed):
    cli("script", "--json", app=indistro_app())
    cli("command", "--json", app=indistro_app())

    assert [call.timeout for call in runner.calls] == [CALL_TIMEOUT, CALL_TIMEOUT]
    assert 0 < CALL_TIMEOUT < 3600  # shorter than the long steps


def test_a_call_that_never_returns_ends_in_an_error_naming_the_program(cli, runner, wsl_installed):
    def hang():
        raise CommandTimeout(f"Timed out after {CALL_TIMEOUT:g}s: wsl.exe")

    runner.on("wsl.exe", "-d", then=hang)

    result = cli("command", "--json", app=indistro_app())

    assert result.exit_code != 0
    assert "Timed out" in result.stderr + result.stdout
