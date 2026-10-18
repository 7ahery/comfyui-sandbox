"""`comfy`, `cm-cli` and `exec` (ticket 17): escape hatches that pass their arguments through unchanged.

The fake Runner plays wsl.exe and the inner command; no real distro is touched.
"""

import pytest
from test_check import Machine
from test_verify import own

import wslcalls

AS_USER = ("wsl.exe", "-d", "comfy-docker", "-u", "comfy", "--cd", "/", "--exec", "env")


@pytest.fixture(autouse=True)
def ready(runner, environ, home):
    Machine().script(runner, environ)
    own(home)


def test_comfy_runs_in_the_container_as_the_linux_user_with_skip_prompt(cli, runner):
    result = cli("comfy", "node", "install", "--fast-deps", "some-node")

    assert result.exit_code == 0, result.stderr
    [run] = wslcalls.docker_execs(runner.calls)
    assert (run.distro, run.user, run.container) == ("comfy-docker", "comfy", "comfyui")
    assert run.command == ("comfy", "--skip-prompt", "node", "install", "--fast-deps", "some-node")


def test_comfy_passes_flag_like_arguments_through_unchanged(cli, runner):
    result = cli("comfy", "--help", "--json", "--yes", "--distro", "x")

    assert result.exit_code == 0, result.stderr
    [run] = wslcalls.docker_execs(runner.calls)
    assert run.command == ("comfy", "--skip-prompt", "--help", "--json", "--yes", "--distro", "x")


def test_cm_cli_sets_comfyui_path_and_the_working_directory(cli, runner):
    result = cli("cm-cli", "install", "--exit-on-fail", "https://example.com/node.git")

    assert result.exit_code == 0, result.stderr
    [run] = wslcalls.docker_execs(runner.calls)
    assert (run.user, run.container) == ("comfy", "comfyui")
    assert run.env == {"COMFYUI_PATH": "/workspace/ComfyUI"}
    assert run.workdir == "/workspace/ComfyUI"
    assert run.command == ("cm-cli", "install", "--exit-on-fail", "https://example.com/node.git")


def test_exec_runs_as_the_linux_user_by_default(cli, runner):
    result = cli("exec", "--", "ls", "-la", "--root")

    assert result.exit_code == 0, result.stderr
    [run] = wslcalls.commands(runner.calls)
    assert (run.distro, run.user, run.argv) == ("comfy-docker", "comfy", ("ls", "-la", "--root"))


def test_exec_root_runs_as_root(cli, runner):
    result = cli("exec", "--root", "--", "id", "-u")

    assert result.exit_code == 0, result.stderr
    [run] = wslcalls.commands(runner.calls)
    assert (run.user, run.argv) == ("root", ("id", "-u"))


def test_exec_without_a_command_is_a_usage_error(cli, runner):
    result = cli("exec", "--root")

    assert result.exit_code == 2
    assert wslcalls.commands(runner.calls) == []


def test_the_inner_exit_code_is_the_exit_code(cli, runner):
    runner.on(*AS_USER, "docker", "exec", returncode=42, stdout="partial\n", stderr="boom\n")

    result = cli("comfy", "node", "install", "x")

    assert result.exit_code == 42
    assert "partial" in result.stdout
    assert "boom" in result.stderr


def test_the_inner_output_is_stdout_in_human_mode(cli, runner):
    runner.on(*AS_USER, stdout="hello\n", stderr="warn line\n")

    result = cli("exec", "--", "echo", "hello")

    assert result.exit_code == 0
    assert result.stdout == "hello\n"
    assert "warn line" in result.stderr


def test_json_carries_the_inner_exit_code_and_output_in_data(cli, runner):
    runner.on(*AS_USER, returncode=3, stdout="out\n", stderr="err\n")

    result = cli("--json", "exec", "--", "false")

    assert result.exit_code == 3
    doc = result.json
    assert doc["ok"] is False and doc["exit_code"] == 3
    assert doc["data"] == {"exit_code": 3, "stdout": "out\n", "stderr": "err\n"}


def test_json_success_has_the_output_in_data(cli, runner):
    runner.on(*AS_USER, stdout="out\n")

    result = cli("--json", "exec", "--", "echo", "out")

    assert result.exit_code == 0
    assert result.json["data"] == {"exit_code": 0, "stdout": "out\n", "stderr": ""}


@pytest.mark.parametrize("argv", [("comfy", "env"), ("cm-cli", "show"), ("exec", "--", "id")])
def test_a_distro_not_in_installs_json_is_refused(cli, runner, argv):
    result = cli("--distro", "Ubuntu", *argv)

    assert result.exit_code == 1
    assert "never touched" in result.stderr
    assert wslcalls.commands(runner.calls) == []
