"""`update` and `node install` start ComfyUI just for the step that needs it and stop it again after
(review-fix 07, 08; the end-to-end rule is tested in test_commands_end_stopped.py). The fake Runner
plays the distro; nothing touches a real one."""

import json

import pytest
from test_check import Machine
from test_update_sandbox import ANSWERS, ENV, PROJECT, READY, set_env
from test_verify import own

import wslcalls
from fakes import FakeContainer

COMPOSE = ("env", "--chdir", PROJECT, "docker", "compose")
AS = ("wsl.exe", "-d", "comfy-docker", "-u", "comfy", "--cd", "/", "--exec", "env")
PS = (*AS, *COMPOSE, "ps", "--status", "running", "--quiet")
RUNNING = "3f2a9c1d7e44\n"


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


def stopped(runner):
    runner.on(*PS, stdout="")


def running(runner):
    runner.on(*PS, stdout=RUNNING)


def compose(runner, verb):
    return [c for c in wslcalls.commands(runner.calls) if wslcalls.compose_verb(c.argv) == verb]


def position(runner, run):
    return wslcalls.index(runner.calls, run.call)


@pytest.mark.parametrize(
    "args, executed",
    [
        (("update", "comfyui"), ("comfy", "--skip-prompt", "update", "comfy", "--version", "latest")),
        (("update", "nodes"), ("comfy", "--skip-prompt", "node", "update", "all")),
        (("update", "comfy-cli"), ("comfy", "--skip-prompt", "update", "cli")),
        (
            ("node", "install", "comfyui-impact-pack"),
            ("comfy", "--skip-prompt", "node", "install", "comfyui-impact-pack"),
        ),
        (
            ("node", "install", "https://github.com/example/ComfyUI-Fine.git"),
            ("cm-cli", "install", "--exit-on-fail", "https://github.com/example/ComfyUI-Fine.git"),
        ),
    ],
)
def test_a_stopped_container_is_started_before_the_command_and_stopped_after(cli, runner, args, executed):
    stopped(runner)

    result = cli(*args, "--json")

    assert result.exit_code == 0, result.stderr
    [up] = compose(runner, "up")
    assert up.argv == (*COMPOSE, "up", "-d")
    [run] = wslcalls.docker_execs(runner.calls)
    assert run.command == executed
    [stop] = compose(runner, "stop")
    assert position(runner, up) < wslcalls.index(runner.calls, run.call) < position(runner, stop)
    last_change = max(
        position(runner, c)
        for c in wslcalls.commands(runner.calls)
        if wslcalls.compose_verb(c.argv) not in (None, "ps")
    )
    assert position(runner, stop) == last_change
    assert result.json["data"]["container"] == "stopped"


@pytest.mark.parametrize(
    "args",
    [("update", "comfyui"), ("update", "nodes"), ("update", "comfy-cli"), ("node", "install", "comfyui-impact-pack")],
)
def test_a_running_container_is_stopped_first_started_for_the_step_and_stopped_after(cli, runner, args):
    container = FakeContainer(runner, running=True)

    result = cli(*args, "--json")

    assert result.exit_code == 0, result.stderr
    [first, second, *_, last] = container.history
    assert (first, second, last) == ("stop", "up", "stop")  # nothing is left running
    assert container.history.count("up") == 1 and container.running is False


def test_update_all_updates_every_target_when_comfyui_is_stopped(cli, runner):
    stopped(runner)

    result = cli("update", "all", "--json")

    assert result.exit_code == 0, result.stderr
    assert len(wslcalls.docker_execs(runner.calls)) == 3
    assert result.json["data"]["container"] == "stopped"


def test_a_failed_update_still_stops_a_container_it_had_to_start(cli, runner):
    stopped(runner)
    runner.on(*AS, "docker", "exec", returncode=1, stderr="network down")

    result = cli("update", "nodes", "--json")

    assert result.exit_code == 1
    assert len(compose(runner, "up")) == 1
    assert len(compose(runner, "stop")) == 1


def test_a_container_that_cannot_be_started_fails_with_a_message_and_runs_nothing(cli, runner):
    stopped(runner)
    runner.on(*AS, *COMPOSE, "up", returncode=1, stderr="no such image")

    result = cli("update", "nodes", "--json")

    assert result.exit_code == 1
    assert "docker compose up" in result.json["message"]
    assert wslcalls.docker_execs(runner.calls) == []


def test_the_human_output_says_comfyui_was_started_for_the_update(cli, runner):
    stopped(runner)

    result = cli("update", "nodes")

    text = result.stdout + result.stderr
    assert "Start ComfyUI for this step" in text and "stopped again" in text
