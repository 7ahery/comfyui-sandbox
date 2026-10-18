"""Commands that change the sandbox stop ComfyUI, do their work and end with it stopped (docs/adr/0015).
Every command under the rule is run end to end against a fake distro whose
container has a state, from a running and from a stopped start.
"""

import json

import pytest
from test_check import Machine
from test_update_sandbox import ANSWERS, ENV, READY, set_env
from test_verify import own

import wslcalls
from comfyui_sandbox.instance import instance_name
from fakes import FakeContainer

AS = ("wsl.exe", "-d", "comfy-docker", "-u", "comfy", "--cd", "/", "--exec", "env")
BACKUP = "comfyui-20260101-000000.tar"

UNDER_THE_RULE = {
    "config": ("config", "--pinned-memory", "off"),
    "config --security": ("config", "--security", "strict"),
    "update comfyui": ("update", "comfyui"),
    "update nodes": ("update", "nodes"),
    "update comfy-cli": ("update", "comfy-cli"),
    "update system": ("update", "system"),
    "update setup": ("update", "setup"),
    "update all": ("update", "all"),
    "node install": ("node", "install", "comfyui-impact-pack"),
    "node install url": ("node", "install", "https://github.com/example/ComfyUI-Fine.git"),
    "restore": ("restore", "--backup", BACKUP, "--confirm", "comfy-docker"),
    "rebuild": ("rebuild",),
    "reinstall": ("reinstall", "--confirm", "comfy-docker"),
}
EVERY_COMMAND = pytest.mark.parametrize("argv", UNDER_THE_RULE.values(), ids=UNDER_THE_RULE.keys())
BOTH_STARTS = pytest.mark.parametrize("was_running", [True, False], ids=["running", "stopped"])


@pytest.fixture(autouse=True)
def distro(runner, environ, home):
    Machine().script(runner, environ)
    own(home)
    path = home / ".comfyui-sandbox" / "installs.json"
    doc = json.loads(path.read_text("utf-8"))
    doc["installs"][0].update(deployed_by="0.0.1", answers=ANSWERS.to_dict())
    path.write_text(json.dumps(doc), "utf-8")
    set_env(runner, ENV + 'COMFY_INSTALL_ARGS="--nvidia --version latest"\n')
    runner.on_script("wait_ready.sh", stdout=READY)
    runner.on(*AS, "ls", "-1", "/home/comfy/comfyui-backups", stdout=BACKUP + "\n")


@EVERY_COMMAND
@BOTH_STARTS
def test_the_command_ends_with_comfyui_stopped_and_says_how_to_start_it(cli, runner, argv, was_running):
    container = FakeContainer(runner, running=was_running)

    result = cli(*argv, "--json")

    assert result.exit_code == 0, result.stderr
    assert container.running is False
    assert result.json["data"]["container"] == "stopped"
    assert result.json["data"]["run_window_closed"] is False
    assert "comfyui-sandbox start" in result.json["message"]
    assert "comfyui-sandbox start" in cli(*argv).stdout  # the plain text says it too


@EVERY_COMMAND
def test_an_open_run_window_is_said_to_close_and_nothing_is_asked(cli, runner, winapi, prompter, argv):
    container = FakeContainer(runner, running=True)
    winapi.instances.add(instance_name("comfy-docker"))

    result = cli(*argv, "--json")

    assert result.exit_code == 0, result.stderr
    assert container.running is False
    assert result.json["data"]["run_window_closed"] is True
    assert "run window closed" in result.stderr
    assert "comfyui-sandbox start" in result.stderr
    assert prompter.questions == []


def test_update_all_ends_stopped_whichever_target_ran_last_and_started_nothing_after_it(cli, runner):
    container = FakeContainer(runner, running=True)

    result = cli("update", "all", "--json")

    assert result.exit_code == 0, result.stderr
    assert container.running is False
    assert container.history[-1] == "stop"
    assert set(result.json["data"]["targets"]) == {"comfyui", "nodes", "comfy-cli", "system", "setup"}


@pytest.mark.parametrize(
    "argv, fails",
    [
        (("update", "nodes"), lambda call: "docker" in call and "exec" in call),
        (("update", "system"), lambda call: "apt-get" in call and "update" in call),
        (("rebuild",), lambda call: "--build" in call),
    ],
    ids=["update nodes", "update system", "rebuild"],
)
def test_a_failed_command_still_leaves_comfyui_stopped(cli, runner, argv, fails):
    container = FakeContainer(runner, running=True)
    runner.on_match(fails, lambda call: (1, ""))

    result = cli(*argv, "--json")

    assert result.exit_code == 1
    assert container.running is False
    assert result.json["data"]["container"] == "stopped"


def test_config_no_longer_applies_with_up_or_restarts_for_the_security_level(cli, runner):
    container = FakeContainer(runner, running=True)

    assert cli("config", "--security", "strict", "--pinned-memory", "off").exit_code == 0

    assert container.history == ["stop"]
    wslcalls.script(runner.calls, "manager_settings.sh")


@BOTH_STARTS
def test_backup_still_leaves_comfyui_as_it_found_it(cli, runner, was_running):
    container = FakeContainer(runner, running=was_running)

    result = cli("backup", "--json")

    assert result.exit_code == 0, result.stderr
    assert container.running is was_running
