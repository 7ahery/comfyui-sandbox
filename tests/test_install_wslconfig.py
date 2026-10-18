""".wslconfig as an install step (ticket 08).

Same fake host as test_install (64 GB RAM, 24 CPUs: the suggested limits are 56 GB, 24 CPUs, 8 GB swap).
"""

import json

import pytest
from test_check import Machine
from test_intake_interactive import DEFAULTS

import wslcalls


@pytest.fixture
def machine():
    return Machine(distros=["Ubuntu"])


@pytest.fixture
def install(cli, runner, environ, machine):
    def run(*argv: str):
        machine.script(runner, environ)
        return cli("install", "--json", *argv)

    return run


@pytest.fixture
def config(home):
    return home / ".wslconfig"


def running(runner, *names):
    runner.on("wsl.exe", "--list", "--running", "--quiet", stdout="\n".join(names) + "\n")


def created(runner):
    return wslcalls.distro_ops(runner.calls, "--install")


def shutdowns(runner):
    return wslcalls.distro_ops(runner.calls, "--shutdown")


def test_install_merges_the_chosen_limits_before_it_creates_the_distro(install, config, runner):
    config.write_text("[wsl2]\nmemory=4GB\nlocalhostForwarding=true\n", "utf-8")
    seen = {}
    real = runner.stream

    def stream(argv, on_line, **kwargs):
        if "--install" in argv:
            seen["config_at_create"] = config.read_text("utf-8")
        return real(argv, on_line, **kwargs)

    runner.stream = stream

    result = install("--yes")

    assert result.exit_code == 0, result.stderr
    assert seen["config_at_create"] == "[wsl2]\nmemory=56GB\nlocalhostForwarding=true\nprocessors=24\nswap=8GB\n"
    assert len(list(config.parent.glob(".wslconfig.bak-*"))) == 1
    assert result.json["data"]["wsl_restart_pending"] is False


@pytest.mark.parametrize("memory", ["keep", "pinned", "neither"])
def test_install_leaves_the_wslconfig_alone_unless_tuning_was_chosen(install, config, memory):
    config.write_text("[wsl2]\nmemory=64GB\n", "utf-8")

    result = install("--yes", "--memory", memory)

    assert result.exit_code == 0, result.stderr
    assert config.read_text("utf-8") == "[wsl2]\nmemory=64GB\n"


def test_install_uses_the_limits_from_the_wsl_limits_flag(install, config):
    result = install("--yes", "--wsl-limits", "memory=32 processors=8 swap=4")

    assert result.exit_code == 0, result.stderr
    assert config.read_text("utf-8") == "[wsl2]\nmemory=32GB\nprocessors=8\nswap=4GB\n"


def test_yes_with_other_distros_running_writes_the_file_waits_and_says_so(install, runner, config):
    running(runner, "Ubuntu")

    result = install("--yes")

    assert result.exit_code == 0, result.stderr
    assert config.exists() and shutdowns(runner) == []
    assert result.json["data"]["wsl_restart_pending"] is True
    assert "not active until WSL restarts" in result.stderr


def test_shutting_down_other_distros_without_confirm_exits_8_before_anything_is_created(install, runner, config, home):
    running(runner, "Ubuntu")

    result = install("--yes", "--non-interactive", "--shutdown", "now")

    assert result.exit_code == 8
    assert "--confirm" in result.json["message"] and "Ubuntu" in result.json["message"]
    assert shutdowns(runner) == [] and created(runner) == []
    assert not config.exists()


def test_confirm_with_the_new_distro_name_allows_the_shutdown(install, runner, config):
    running(runner, "Ubuntu")

    result = install("--yes", "--non-interactive", "--shutdown", "now", "--confirm", "comfy-docker")

    assert result.exit_code == 0, result.stderr
    assert len(shutdowns(runner)) == 1 and len(created(runner)) == 1
    assert wslcalls.index(runner.calls, shutdowns(runner)[0]) < wslcalls.index(runner.calls, created(runner)[0])
    assert result.json["data"]["wsl_restart_pending"] is False


def test_non_interactive_with_other_distros_running_and_no_answer_exits_9_naming_shutdown(install, runner, home):
    running(runner, "Ubuntu")

    result = install(
        "--non-interactive",
        "--memory",
        "tune",
        "--models",
        "wsl",
        "--manager-ui",
        "legacy",
        "--security",
        "normal",
        "--distro",
        "comfy-docker",
        "--user",
        "comfy",
        "--models-path",
        "~/comfyui-models",
    )

    assert result.exit_code == 9 and result.json["flag"] == "--shutdown"
    assert created(runner) == []


def test_a_terminal_is_asked_now_or_later_and_the_name_to_confirm(install, runner, prompter, config):
    running(runner, "Ubuntu")
    prompter.answers += [*DEFAULTS, "Shut down now", "Ubuntu"]

    result = install()

    assert result.exit_code == 0, result.stderr
    assert len(shutdowns(runner)) == 1
    assert prompter.answers == []


def test_later_in_a_terminal_installs_and_reminds(install, runner, prompter, config):
    running(runner, "Ubuntu")
    prompter.answers += [*DEFAULTS, "Later"]

    result = install()

    assert result.exit_code == 0, result.stderr
    assert shutdowns(runner) == [] and len(created(runner)) == 1
    assert result.json["data"]["wsl_restart_pending"] is True


def test_the_install_json_data_is_has_the_pending_flag_and_the_verify_result(install):
    data = json.loads(install("--yes").stdout)["data"]

    assert set(data) == {
        "distro",
        "resumed",
        "install",
        "wsl_restart_pending",
        "verify",
        "launcher",
        "start",
        "summary",
    }
