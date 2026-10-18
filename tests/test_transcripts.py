"""Per-command transcripts (docs/scripting.md, "Transcripts").

`change` stands in for any command that changes something: it starts its transcript,
says something, runs an in-distro script and succeeds or fails.
"""

import re
from pathlib import Path

import typer

from comfyui_sandbox.cli import build_app
from comfyui_sandbox.errors import CliError
from comfyui_sandbox.output import Outcome
from comfyui_sandbox.wsl import ROOT

NAME = re.compile(r"^\d{8}T\d{6}Z-change(-\d+)?\.log$")
LINE = re.compile(r"^\[\d\d:\d\d:\d\d\] (step |info |warn |error|run  |out  |exit ) .*$")


def change_app() -> typer.Typer:
    app = build_app()

    @app.command("change")
    def change(ctx: typer.Context, fail: bool = False, crash: bool = False) -> Outcome:
        session = ctx.obj
        session.transcript()
        session.out.step("Do the thing")
        session.out.warn("careful")
        session.wsl().run_script("comfy-docker", ROOT, "create_user.sh", {"LINUX_USER": "comfy"})
        if fail:
            raise CliError("it broke")
        if crash:
            raise RuntimeError("unexpected")
        return Outcome("changed")

    @app.command("look")
    def look(ctx: typer.Context) -> Outcome:
        return Outcome("looked")

    return app


def logs(home) -> list[Path]:
    folder = home / ".comfyui-sandbox" / "logs"
    return sorted(folder.glob("*.log")) if folder.exists() else []


def wsl_installed(environ) -> None:
    exe = Path(environ["ProgramFiles"]) / "WSL" / "wsl.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")


def test_a_changing_command_writes_a_transcript_and_returns_log(cli, home, environ, runner):
    wsl_installed(environ)
    runner.on("wsl.exe", "-d", "comfy-docker", stdout="[ok] user comfy created\r\nuid=1000(comfy)\n")

    result = cli("change", "--json", app=change_app())

    assert result.exit_code == 0
    [path] = logs(home)
    assert result.json["log"] == str(path)
    assert NAME.match(path.name)
    raw = path.read_bytes()
    assert b"\r" not in raw and not raw.startswith(b"\xef\xbb\xbf")
    lines = raw.decode("utf-8").splitlines()
    assert lines[0].startswith("# comfyui-sandbox ") and lines[0].endswith(" transcript")
    assert lines[1] == "# command: change --json"
    assert lines[2].startswith("# started: ")
    assert lines[-1].startswith("# finished: ") and lines[-1].endswith(" exit=0")
    body = lines[3:-1]
    assert all(LINE.match(line) for line in body), body
    text = "\n".join(body)
    assert "step  Do the thing" in text
    assert "warn  careful" in text
    assert "run   wsl.exe -d comfy-docker -u root --cd / -- bash -s < create_user.sh LINUX_USER=comfy" in text
    assert "out   [ok] user comfy created" in text
    assert "exit  0" in text


def test_a_failing_command_prints_the_log_path_and_returns_it(cli, home, environ):
    wsl_installed(environ)

    result = cli("change", "--fail", "--json", app=change_app())

    assert result.exit_code == 1
    [path] = logs(home)
    assert result.json["log"] == str(path)
    assert f"Log: {path}" in result.stderr
    lines = path.read_text("utf-8").splitlines()
    assert any(line.endswith("error it broke") for line in lines)
    assert lines[-1].endswith(" exit=1")


def test_a_crash_writes_the_traceback_before_the_footer(cli, home, environ):
    wsl_installed(environ)

    result = cli("change", "--crash", app=change_app())

    assert result.exit_code == 1
    [path] = logs(home)
    text = path.read_text("utf-8")
    assert "error Traceback (most recent call last):" in text
    assert "RuntimeError: unexpected" in text
    assert text.rstrip("\n").splitlines()[-1].endswith(" exit=1")
    assert f"Log: {path}" in result.stderr


def test_read_only_commands_write_no_transcript(cli, home):
    result = cli("look", "--json", app=change_app())

    assert result.exit_code == 0
    assert "log" not in result.json
    assert logs(home) == []


def test_only_the_newest_20_transcripts_are_kept(cli, home, environ):
    wsl_installed(environ)
    folder = home / ".comfyui-sandbox" / "logs"
    folder.mkdir(parents=True)
    old = [folder / f"2025010{d}T0000{s:02d}Z-install.log" for d in range(1, 4) for s in range(10)]
    for path in old:
        path.write_text("# old\n", "utf-8")
    (folder / "notes.txt").write_text("keep me", "utf-8")

    result = cli("change", "--json", app=change_app())

    kept = logs(home)
    assert len(kept) == 20
    assert Path(result.json["log"]) in kept
    assert set(kept) - {Path(result.json["log"])} == set(sorted(old)[-19:])
    assert (folder / "notes.txt").exists()


def test_two_transcripts_in_the_same_second_get_distinct_names(cli, home, environ):
    wsl_installed(environ)

    first = cli("change", "--json", app=change_app())
    second = cli("change", "--json", app=change_app())

    assert first.json["log"] != second.json["log"]
    assert len(logs(home)) == 2
