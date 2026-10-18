"""Exceptions map to exit codes in one place (docs/scripting.md, "Exit codes")."""

import re
from pathlib import Path

import pytest
import typer

from comfyui_sandbox.cli import build_app
from comfyui_sandbox.errors import AnswerMissing, CliError, ExitCode, NotReady


def app_raising(exc: BaseException) -> typer.Typer:
    """The real app plus a nested `probe fail` command that raises `exc`."""
    app = build_app()
    probe = typer.Typer()

    @probe.command("fail")
    def fail() -> None:
        raise exc

    app.add_typer(probe, name="probe")
    return app


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (CliError("WSL is too old.", ExitCode.WSL_NOT_READY), 7),
        (NotReady("ComfyUI did not answer within 120s."), 10),
        (RuntimeError("boom"), 1),
        (KeyboardInterrupt(), 130),
    ],
)
def test_errors_raised_by_a_command_become_its_exit_code(cli, exc, code):
    result = cli("probe", "fail", "--json", app=app_raising(exc))

    assert result.exit_code == code
    doc = result.json
    assert doc["command"] == "probe fail"
    assert doc["ok"] is False
    assert doc["exit_code"] == code
    assert doc["error"] == doc["message"] != ""
    assert "Error:" in result.stderr


def test_missing_answer_names_the_flag(cli):
    exc = AnswerMissing(
        "Port 8188 is used by python.exe (PID 4120). Re-run with --port <n>. Port 8189 is free.", flag="--port"
    )

    result = cli("--json", "probe", "fail", "--non-interactive", app=app_raising(exc))

    assert result.exit_code == 9
    assert result.json["flag"] == "--port"
    assert "--port" in result.json["message"]


def test_failure_without_json_prints_nothing_on_stdout(cli):
    result = cli("probe", "fail", app=app_raising(RuntimeError("boom")))

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "boom" in result.stderr


def test_unknown_option_is_a_usage_error_reported_on_stderr_only(cli):
    result = cli("--bogus")

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "--bogus" in result.stderr


def test_unknown_option_under_json_is_one_failure_document(cli):
    result = cli("--json", "--bogus")

    assert result.exit_code == 2
    doc = result.json
    assert set(doc) == {"command", "ok", "exit_code", "message", "data", "error"}
    assert doc["ok"] is False
    assert doc["exit_code"] == 2
    assert doc["data"] == {}
    assert "--bogus" in doc["error"]
    assert "--bogus" in result.stderr


def test_no_command_is_a_usage_error(cli):
    result = cli("--json")

    assert result.exit_code == 2
    assert result.json["exit_code"] == 2


SCRIPTING_DOC = Path(__file__).resolve().parent.parent / "docs" / "scripting.md"


def test_the_documented_exit_codes_are_exactly_the_enum():
    # Rows of the "Exit codes" table: | 9 | `ANSWER_MISSING` | ... |
    documented = {
        name: int(code) for code, name in re.findall(r"^\| (\d+) \| `(\w+)` \|", SCRIPTING_DOC.read_text("utf-8"), re.M)
    }

    assert documented == {code.name: code.value for code in ExitCode}
