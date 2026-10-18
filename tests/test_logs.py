"""`comfyui-sandbox logs` (ticket 16): the container log, and the CLI's own transcripts.

Read-only: it never writes a transcript of its own. The fake Runner plays `docker compose logs`.
"""

import pytest
from test_check import Machine
from test_install import install, installs_json, machine
from test_verify import own

import wslcalls
from comfyui_sandbox.errors import ExitCode

COMPOSE = ["env", "--chdir", "/home/comfy/comfyui-docker", "docker", "compose"]


@pytest.fixture(autouse=True)
def wsl_present(runner, environ):
    Machine().script(runner, environ)


@pytest.fixture(autouse=True)
def distro_up(runner):
    runner.on("wsl.exe", "--list", "--running", "--quiet", stdout="comfy-docker\n")


def logs_folder(home):
    folder = home / ".comfyui-sandbox" / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def test_logs_prints_the_container_log(cli, runner, home):
    own(home)
    runner.on("wsl.exe", "-d", "comfy-docker", stdout="comfyui  | Starting\ncomfyui  | Ready\n")

    result = cli("logs")

    assert result.exit_code == 0, result.stderr
    assert result.stdout == "comfyui  | Starting\ncomfyui  | Ready\n"
    run = wslcalls.commands(runner.calls)[-1]
    assert (run.distro, run.user) == ("comfy-docker", "comfy")
    assert run.argv == (*COMPOSE, "logs", "--no-color")
    assert not (home / ".comfyui-sandbox" / "logs").exists()  # read-only: no transcript


def test_tail_limits_the_lines_asked_for(cli, runner, home):
    own(home)
    runner.on("wsl.exe", "-d", "comfy-docker", stdout="last\n")

    result = cli("logs", "--tail", "50", "--json")

    assert result.exit_code == 0, result.stderr
    assert wslcalls.commands(runner.calls)[-1].argv == (*COMPOSE, "logs", "--no-color", "--tail", "50")
    assert result.json["data"]["log"] == "last\n"
    assert "log" not in result.json  # the envelope's "log" is a transcript path; there is none


def test_follow_streams_and_ctrl_c_ends_it_cleanly(cli, runner, home):
    own(home)

    def ctrl_c():
        raise KeyboardInterrupt

    runner.on("wsl.exe", "-d", "comfy-docker", then=ctrl_c)  # how a person ends a follow

    result = cli("logs", "--follow")

    assert result.exit_code == 0, result.stderr
    call = wslcalls.commands(runner.calls)[-1]
    assert call.argv == (*COMPOSE, "logs", "--no-color", "--follow")
    assert call.call.streamed


def test_follow_prints_each_line_as_it_arrives(cli, runner, home):
    own(home)
    runner.on("wsl.exe", "-d", "comfy-docker", stdout="one\ntwo\n")

    result = cli("logs", "--follow", "--tail", "2")

    assert result.exit_code == 0, result.stderr
    assert result.stdout == "one\ntwo\n"


@pytest.mark.parametrize("args", [("logs",), ("logs", "--follow")])
def test_a_distro_that_is_not_running_is_refused_not_started(cli, runner, home, args):
    own(home)
    runner.on("wsl.exe", "--list", "--running", "--quiet", stdout="")  # the sandbox's distro is down

    result = cli(*args, "--json")

    assert result.exit_code == ExitCode.NOT_READY, result.stderr
    assert "isn't running" in result.json["error"]
    assert "comfy-docker" in result.json["error"]
    assert wslcalls.commands(runner.calls) == []  # nothing ran in the distro: `wsl -d` would have booted it


def test_cli_lists_transcripts_newest_first(cli, home):
    folder = logs_folder(home)
    for name in ("20261006T100000Z-install.log", "20261006T120000Z-run.log", "20261005T090000Z-verify.log"):
        (folder / name).write_text("x\n", "utf-8")

    result = cli("logs", "--cli", "--json")

    assert result.exit_code == 0, result.stderr
    paths = result.json["data"]["transcripts"]
    assert [p.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for p in paths] == [
        "20261006T120000Z-run.log",
        "20261006T100000Z-install.log",
        "20261005T090000Z-verify.log",
    ]
    human = cli("logs", "--cli")
    assert human.stdout.splitlines() == paths


def test_cli_last_prints_the_newest_transcript(cli, home):
    folder = logs_folder(home)
    (folder / "20261005T090000Z-verify.log").write_text("old\n", "utf-8")
    (folder / "20261006T120000Z-run.log").write_text("# transcript\n[12:00:00] error boom\n", "utf-8")

    human = cli("logs", "--cli", "--last")
    result = cli("logs", "--cli", "--last", "--json")

    assert human.stdout == "# transcript\n[12:00:00] error boom\n"
    data = result.json["data"]
    assert data["path"].endswith("20261006T120000Z-run.log")
    assert data["content"] == "# transcript\n[12:00:00] error boom\n"
    assert data["transcripts"][0] == data["path"]


def test_cli_with_no_transcripts_lists_nothing(cli, home):
    result = cli("logs", "--cli", "--json")
    assert result.exit_code == 0
    assert result.json["data"]["transcripts"] == []
    last = cli("logs", "--cli", "--last", "--json")
    assert last.exit_code == 0
    assert last.json["data"]["path"] is None


def test_the_cli_log_needs_no_install(cli, home):
    logs_folder(home)
    assert cli("logs", "--cli").exit_code == 0
