"""`comfyui-sandbox rebuild` (ticket 19): `docker compose up -d --build`, then wait until ComfyUI
answers. The fake Runner plays the distro; nothing touches a real one.
"""

import pytest
from test_check import Machine
from test_verify import own

import wslcalls

PROJECT = "/home/comfy/comfyui-docker"
BUILD = ("env", "--chdir", PROJECT, "docker", "compose", "up", "-d", "--build")
READY = "[ok] ComfyUI answers (HTTP 200) after 1 min of waiting\n"
STOPPED = (
    "[error] the container is exited, not running: the install or ComfyUI failed, or it was stopped. "
    "Last log lines:\n----- last log lines -----\nTraceback: boom\n"
)


@pytest.fixture(autouse=True)
def distro(runner, environ, home):
    Machine().script(runner, environ)
    own(home)
    runner.on_script("wait_ready.sh", stdout=READY)


def test_rebuild_builds_with_compose_up_then_waits_for_ready(cli, runner, home):
    result = cli("rebuild", "--json")

    assert result.exit_code == 0, result.stderr
    [build] = [c for c in wslcalls.commands(runner.calls) if c.argv == BUILD]
    assert (build.distro, build.user) == ("comfy-docker", "comfy")
    wait = wslcalls.script(runner.calls, "wait_ready.sh")
    assert wait.env["PROJECT_DIR"] == PROJECT
    assert wslcalls.index(runner.calls, build.call) < wslcalls.index(runner.calls, wait.call)
    assert result.json["data"]["distro"] == "comfy-docker"
    assert result.json["log"]
    assert (home / ".comfyui-sandbox" / "logs").is_dir()


def test_rebuild_changes_no_files_and_the_record_stays_as_it_was(cli, runner, home):
    path = home / ".comfyui-sandbox" / "installs.json"
    before = path.read_text("utf-8")

    assert cli("rebuild").exit_code == 0

    assert path.read_text("utf-8") == before
    assert wslcalls.scripts(runner.calls) == [wslcalls.script(runner.calls, "wait_ready.sh")]


def test_the_distro_is_held_up_during_the_rebuild_and_released_after(cli, runner):
    assert cli("rebuild").exit_code == 0

    [keepalive] = runner.started
    assert keepalive.stopped_after is not None


def test_a_failed_build_exits_1_with_the_reason_and_does_not_wait(cli, runner):
    runner.on(
        "wsl.exe",
        "-d",
        "comfy-docker",
        "-u",
        "comfy",
        "--cd",
        "/",
        "--exec",
        "env",
        *BUILD,
        returncode=1,
        stderr="failed to solve: pull access denied",
    )

    result = cli("rebuild", "--json")

    assert result.exit_code == 1
    assert "pull access denied" in result.json["message"]
    assert wslcalls.scripts(runner.calls) == []
    assert result.json["log"]


def test_a_container_that_stops_after_the_rebuild_reports_its_last_log_lines(cli, runner):
    runner.on_script("wait_ready.sh", returncode=1, stdout=STOPPED)

    result = cli("rebuild", "--json")

    assert result.exit_code == 1
    assert "Traceback: boom" in result.json["message"]


def test_a_rebuild_that_is_not_ready_in_time_exits_10(cli, runner):
    runner.on_script("wait_ready.sh", returncode=2)

    assert cli("rebuild").exit_code == 10
