"""`comfyui-sandbox status` (ticket 14): the state at a glance, read-only.

The fake Runner plays status.sh; the fake Http plays the Windows port; the fake winapi holds the
run-window marker.
"""

import json
from importlib.metadata import version

import pytest
from test_check import Machine
from test_verify import own

import wslcalls
from comfyui_sandbox.instance import instance_name
from comfyui_sandbox.runner import CommandTimeout

RUNNING = "container=running\n----- last log lines -----\nStarting server\nTo see the GUI go to: http://0.0.0.0:8188\n"
EXITED = "container=exited\n----- last log lines -----\nKilled\n"


@pytest.fixture(autouse=True)
def wsl_present(runner, environ, home):
    Machine().script(runner, environ)
    runner.on("wsl.exe", "--list", "--running", "--quiet", stdout="comfy-docker\n")
    own(home)


def deployed_by(home, text):
    path = home / ".comfyui-sandbox" / "installs.json"
    doc = json.loads(path.read_text("utf-8"))
    doc["installs"][0]["deployed_by"] = text
    path.write_text(json.dumps(doc), "utf-8")


def test_a_running_ready_container_with_an_open_window(cli, runner, winapi):
    runner.on_script("status.sh", stdout=RUNNING)
    winapi.instances.add(instance_name("comfy-docker"))

    result = cli("status", "--json")

    assert result.exit_code == 0, result.stderr
    data = result.json["data"]
    assert data["distro"] == "comfy-docker"
    assert data["container"] == "running"
    assert data["ready"] is True
    assert data["run_window"] is True
    assert data["url"] == "http://localhost:8188"
    assert data["drift"] is False
    assert data["log_tail"] == ["Starting server", "To see the GUI go to: http://0.0.0.0:8188"]
    for text in ("Container: running", "ComfyUI: ready at http://localhost:8188", "Run window: open"):
        assert text in result.stderr
    run = wslcalls.script(runner.calls, "status.sh")
    assert (run.distro, run.user) == ("comfy-docker", "comfy")
    assert run.env["PROJECT_DIR"] == "/home/comfy/comfyui-docker"


def test_a_stopped_container_is_not_ready_and_has_no_window(cli, runner, http):
    runner.on_script("status.sh", stdout=EXITED)
    http.refuse(8188)

    result = cli("status", "--json")

    assert result.exit_code == 0, result.stderr
    data = result.json["data"]
    assert (data["container"], data["ready"], data["run_window"]) == ("exited", False, False)
    assert "Killed" in data["log_tail"]


def test_a_distro_that_is_not_running_is_reported_without_being_started(cli, runner, http):
    runner.on("wsl.exe", "--list", "--running", "--quiet", stdout="")
    http.refuse(8188)

    result = cli("status", "--json")

    assert result.exit_code == 0, result.stderr
    assert result.json["data"]["container"] == "stopped"
    assert wslcalls.scripts(runner.calls) == []
    assert list(wslcalls.commands(runner.calls)) == []


def test_a_container_that_is_gone_reports_missing(cli, runner):
    runner.on_script("status.sh", stdout="container=missing\n")

    assert cli("status", "--json").json["data"]["container"] == "missing"


def test_an_older_deploying_version_warns_and_suggests_update_setup(cli, runner, home):
    runner.on_script("status.sh", stdout=RUNNING)
    deployed_by(home, "0.0.1")

    result = cli("status", "--json")

    assert result.exit_code == 0, result.stderr
    data = result.json["data"]
    assert data["drift"] is True
    assert (data["deployed_by"], data["cli_version"]) == ("0.0.1", version("comfyui-sandbox"))
    assert "comfyui-sandbox update setup" in result.stderr


def test_the_same_or_a_newer_deploying_version_is_no_drift(cli, runner, home):
    runner.on_script("status.sh", stdout=RUNNING)
    deployed_by(home, "99.0.0")

    result = cli("status", "--json")

    assert result.json["data"]["drift"] is False
    assert "update setup" not in result.stderr


def test_status_changes_nothing(cli, runner, home, winapi):
    runner.on_script("status.sh", stdout=RUNNING)
    before = (home / ".comfyui-sandbox" / "installs.json").read_text("utf-8")

    assert cli("status").exit_code == 0

    assert (home / ".comfyui-sandbox" / "installs.json").read_text("utf-8") == before
    assert not (home / ".comfyui-sandbox" / "logs").exists()
    assert runner.started == [] and winapi.opened_urls == [] and winapi.instances == set()


def test_status_reports_the_part_that_hung_when_the_distro_does_not_answer(cli, runner, http):
    def hang():
        raise CommandTimeout("Timed out after 60s: wsl.exe")

    runner.on_script("status.sh", then=hang)

    result = cli("status", "--json")

    assert result.exit_code == 0, result.stderr
    data = result.json["data"]
    assert data["container"] == "unknown"
    assert data["ready"] is True  # the Windows side still answered
    assert "Container: unknown" in result.stderr
    assert "timed out" in result.stderr.lower()
