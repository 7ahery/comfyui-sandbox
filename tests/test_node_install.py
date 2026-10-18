"""`comfyui-sandbox node install` (ticket 21): a registry id goes through comfy-cli, a git URL through
cm-cli; ComfyUI restarts and an import failure of the node is reported. Fakes only."""

import pytest
from test_check import Machine
from test_verify import own

import wslcalls

PROJECT = "/home/comfy/comfyui-docker"
COMPOSE = ("env", "--chdir", PROJECT, "docker", "compose")
LOGS = (*COMPOSE, "logs", "--no-color", "--tail", "500")
FAILED_LOG = """\
Cannot import /workspace/ComfyUI/custom_nodes/ComfyUI-Broken module for custom nodes: No module named 'cv2'
   0.0 seconds (IMPORT FAILED): /workspace/ComfyUI/custom_nodes/ComfyUI-Broken
   0.1 seconds: /workspace/ComfyUI/custom_nodes/ComfyUI-Fine
"""


@pytest.fixture(autouse=True)
def ready(runner, environ, home):
    Machine().script(runner, environ)
    own(home)


def steps(runner) -> list[str]:
    """What ran, in order: install, restart, wait, logs."""
    labelled = []
    for run in wslcalls.scripts(runner.calls):
        if run.script == "wait_ready.sh":
            labelled.append((run.call, "wait"))
    for run in wslcalls.commands(runner.calls):
        if run.argv[:2] == ("docker", "exec"):
            labelled.append((run.call, "install"))
        elif run.argv == (*COMPOSE, "restart"):
            labelled.append((run.call, "restart"))
        elif run.argv == LOGS:
            labelled.append((run.call, "logs"))
    return [name for _, name in sorted(labelled, key=lambda item: wslcalls.index(runner.calls, item[0]))]


def test_a_registry_id_goes_through_comfy_cli_then_restarts_and_waits(cli, runner):
    result = cli("--json", "node", "install", "comfyui-impact-pack")

    assert result.exit_code == 0, result.stderr
    [run] = wslcalls.docker_execs(runner.calls)
    assert (run.user, run.command) == ("comfy", ("comfy", "--skip-prompt", "node", "install", "comfyui-impact-pack"))
    assert steps(runner) == ["install", "restart", "wait", "logs"]
    assert result.json["data"]["node"] == "comfyui-impact-pack"
    assert result.json["data"]["imported"] is True
    assert result.json["log"]


AS_USER = ("wsl.exe", "-d", "comfy-docker", "-u", "comfy", "--cd", "/", "--exec", "env")


def test_a_git_url_goes_through_cm_cli_without_touching_the_security_level(cli, runner):
    result = cli("node", "install", "https://github.com/example/ComfyUI-Fine.git")

    assert result.exit_code == 0, result.stderr
    [run] = wslcalls.docker_execs(runner.calls)
    assert run.command == ("cm-cli", "install", "--exit-on-fail", "https://github.com/example/ComfyUI-Fine.git")
    assert run.env == {"COMFYUI_PATH": "/workspace/ComfyUI"} and run.workdir == "/workspace/ComfyUI"
    assert steps(runner) == ["install", "restart", "wait", "logs"]
    assert not [s for s in wslcalls.scripts(runner.calls) if s.script == "manager_settings.sh"]
    assert not [c for c in runner.calls if b"SECURITY_LEVEL" in (c.stdin or b"")]


def test_an_import_failure_of_the_node_exits_non_zero_and_is_reported(cli, runner):
    runner.on(*AS_USER, *LOGS, stdout=FAILED_LOG)

    result = cli("--json", "node", "install", "https://github.com/example/ComfyUI-Broken")

    assert result.exit_code == 1
    doc = result.json
    assert doc["ok"] is False and doc["data"]["imported"] is False
    assert "No module named 'cv2'" in doc["message"]
    assert doc["log"]


def test_an_import_failure_of_another_node_is_not_this_nodes_failure(cli, runner):
    runner.on(*AS_USER, *LOGS, stdout=FAILED_LOG)

    result = cli("--json", "node", "install", "comfyui-fine")

    assert result.exit_code == 0, result.stderr
    assert result.json["data"]["imported"] is True


NODES = f"{PROJECT}/data/ComfyUI/custom_nodes"
ODD_LOG = """\
Cannot import /workspace/ComfyUI/custom_nodes/odd-folder module for custom nodes: No module named 'cv2'
   0.0 seconds (IMPORT FAILED): /workspace/ComfyUI/custom_nodes/odd-folder
"""


def folders(runner, before: str, after: str) -> None:
    """The custom_nodes listing before the install runs and after it ran."""
    runner.on(*AS_USER, "ls", "-1", NODES, stdout=before)
    runner.on(*AS_USER, "docker", "exec", then=lambda: runner.on(*AS_USER, "ls", "-1", NODES, stdout=after))


def test_a_failure_in_a_folder_the_install_added_counts_even_when_its_name_differs(cli, runner):
    folders(runner, "ComfyUI-Manager\n", "ComfyUI-Manager\nodd-folder\n")
    runner.on(*AS_USER, *LOGS, stdout=ODD_LOG)

    result = cli("--json", "node", "install", "some-registry-id")

    assert result.exit_code == 1
    assert "No module named 'cv2'" in result.json["message"]
    assert result.json["data"]["imported"] is False
    assert result.json["data"]["new_folders"] == ["odd-folder"]


def test_a_failure_in_a_folder_that_was_already_there_is_not_this_nodes_failure(cli, runner):
    folders(runner, "ComfyUI-Manager\nodd-folder\n", "ComfyUI-Manager\nodd-folder\nsome-registry-id\n")
    runner.on(*AS_USER, *LOGS, stdout=ODD_LOG)

    result = cli("--json", "node", "install", "some-registry-id")

    assert result.exit_code == 0, result.stderr
    assert result.json["data"]["new_folders"] == ["some-registry-id"]


def test_a_failed_install_stops_before_the_restart(cli, runner):
    runner.on(*AS_USER, "docker", "exec", returncode=1, stderr="no such node\n")

    result = cli("node", "install", "no-such-node")

    assert result.exit_code == 1
    assert steps(runner) == ["install"]


@pytest.mark.parametrize("target", ["--force", "bad id", "a;b"])
def test_a_target_that_is_neither_an_id_nor_a_url_is_a_usage_error(cli, runner, target):
    result = cli("node", "install", "--", target)

    assert result.exit_code == 2
    assert steps(runner) == []


def test_a_failed_restart_is_reported_by_the_shared_restart_step_and_skips_the_wait(cli, runner):
    runner.on(*AS_USER, *COMPOSE, "restart", returncode=1, stderr="daemon down\n")

    result = cli("--json", "node", "install", "comfyui-impact-pack")

    assert result.exit_code == 1
    assert "Restarting ComfyUI failed (exit 1): daemon down" in result.json["error"]
    assert steps(runner) == ["install", "restart"]
