"""A resumed install may change the answers its setup only writes into the .env and the Manager's
settings (intake.AMENDABLE), so an install that failed on one of them (a release number that
doesn't exist) carries on with a better one instead of failing the same way again.

Same fake host as test_install (a ready RTX 4090 machine); every wsl.exe call is recorded.
"""

import pytest
from test_install import install, installs_json, machine
from test_install_deploy import dotenv

from comfyui_sandbox.winapi import PortOwner

STOPPED = (
    "[error] the container is not running (exited 1)\n"
    "[error] Installing ComfyUI failed (1 of 3 tries with: --nvidia --version 0.3.999)\n"
)
READY = "[ok] ComfyUI answers (HTTP 200) after 12 min of waiting\n"


@pytest.fixture
def failed(install, runner, machine):
    """An install of a ComfyUI release that doesn't exist: its first start stops."""
    runner.on_script("wait_ready.sh", returncode=1, stdout=STOPPED)
    first = install("--yes", "--channel", "0.3.999")
    assert first.exit_code == 1, first.stderr
    machine.distros = ["Ubuntu", "comfy-docker"]
    runner.on_script("wait_ready.sh", stdout=READY)
    runner.calls.clear()
    return first


def test_the_failure_says_how_to_carry_on_and_how_to_change_the_answers(failed):
    message = failed.json["message"]

    assert "Installing ComfyUI failed" in message
    assert "'comfyui-sandbox install' carries on" in message
    assert "--change" in message and "--port" in message


def test_a_resume_with_another_version_deploys_it_and_saves_it(failed, install, runner, home):
    result = install("--non-interactive", "--channel", "0.3.40")

    assert result.exit_code == 0, result.stderr
    assert result.json["data"]["resumed"] is True
    assert dotenv(runner)["COMFY_INSTALL_ARGS"] == "--nvidia --version 0.3.40"
    [record] = installs_json(home)["installs"]
    assert record["answers"]["channel"] == "0.3.40"
    assert "Version:       0.3.40" in result.stderr


def test_a_resume_without_flags_keeps_the_saved_answers(failed, install, runner, home):
    saved = installs_json(home)["installs"][0]["answers"]

    result = install("--non-interactive")

    assert result.exit_code == 0, result.stderr
    assert dotenv(runner)["COMFY_INSTALL_ARGS"] == "--nvidia --version 0.3.999"
    assert installs_json(home)["installs"][0]["answers"] == saved
    assert "Changed the saved answers" not in result.stderr


def test_change_asks_the_version_manager_ui_and_security_again(failed, install, runner, home, prompter):
    prompter.answers += ["Latest stable", "Enable the legacy", "Weak"]

    result = install("--change")

    assert result.exit_code == 0, result.stderr
    assert [message for kind, message in prompter.questions] == [
        "ComfyUI version?",
        "Which Manager UI?",
        "ComfyUI Manager security level?",
    ]
    # The saved answers are preselected.
    assert prompter.defaults[0] == ("ComfyUI version?", "0.3.999")
    env = dotenv(runner)
    assert env["COMFY_INSTALL_ARGS"] == "--nvidia --version latest"
    assert "--enable-manager-legacy-ui" in env["COMFY_ARGS"]
    answers = installs_json(home)["installs"][0]["answers"]
    assert (answers["channel"], answers["manager_ui"], answers["security"]) == ("latest", "legacy", "weak")


def test_a_new_port_is_checked_and_saved(failed, install, home, winapi):
    winapi.port_owners[9000] = PortOwner(12, "node.exe")

    taken = install("--non-interactive", "--port", "9000")
    assert taken.exit_code == 9
    assert installs_json(home)["installs"][0]["port"] == 8188

    result = install("--non-interactive", "--port", "9001")

    assert result.exit_code == 0, result.stderr
    [record] = installs_json(home)["installs"]
    assert (record["port"], record["answers"]["port"]) == (9001, 9001)


def test_an_answer_that_built_the_distro_cant_change_on_a_resume(failed, install, home):
    saved = installs_json(home)["installs"][0]

    result = install("--non-interactive", "--user", "other", "--models", "windows")

    assert result.exit_code == 2
    assert "--models, --user can't change on a resume" in result.json["message"]
    assert installs_json(home)["installs"][0] == saved


def test_an_invalid_version_on_a_resume_changes_nothing(failed, install, home):
    saved = installs_json(home)["installs"][0]

    result = install("--non-interactive", "--channel", "newest")

    assert result.exit_code == 2
    assert result.json["message"].startswith("--channel:")
    assert installs_json(home)["installs"][0] == saved


def test_change_needs_a_terminal(failed, install):
    result = install("--non-interactive", "--change")

    assert result.exit_code == 9
    assert "--channel" in result.json["message"]


def test_change_without_an_unfinished_install_is_refused(install, home):
    result = install("--yes", "--change")

    assert result.exit_code == 2
    assert "none to resume" in result.json["message"]
    assert not (home / ".comfyui-sandbox" / "installs.json").exists()
