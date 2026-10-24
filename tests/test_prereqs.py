"""`comfyui-sandbox prereqs`: get WSL ready through UAC (ticket 03).

Tests drive the CLI with the fake Runner (what WSL answers) and the fake winapi
(what the elevated process does, or that UAC was declined).
"""

from pathlib import Path

INSTALL = ("wsl.exe", "--install", "--no-distribution")
UPDATE = ("wsl.exe", "--update")


def install_wsl(environ, runner, version="2.6.1.0", kernel="6.6.87.2-1"):
    """Make WSL count as installed, answering `wsl --version` with `version` and `kernel`."""
    exe = Path(environ["ProgramFiles"]) / "WSL" / "wsl.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"")
    runner.on("wsl.exe", "--version", stdout=f"WSL version: {version}\nKernel version: {kernel}\n")


def test_missing_wsl_is_installed_elevated_and_needs_a_reboot(cli, winapi, home):
    result = cli("prereqs", "--json", "--yes")

    assert result.exit_code == 0
    assert winapi.elevated == [INSTALL]
    doc = result.json
    assert doc["data"]["action"] == "install"
    assert doc["data"]["reboot_needed"] is True
    assert "reboot" in doc["message"].lower()
    log = Path(doc["log"])
    assert log.is_file()
    assert log.parent == home / ".comfyui-sandbox" / "logs"
    assert log.name.endswith("-prereqs.log")
    assert "wsl.exe --install --no-distribution" in log.read_text(encoding="utf-8")


def test_old_wsl_is_updated_elevated_without_a_reboot(cli, winapi, runner, environ):
    install_wsl(environ, runner, version="2.3.26.0")

    result = cli("prereqs", "--json", "--yes")

    assert result.exit_code == 0
    assert winapi.elevated == [UPDATE]
    doc = result.json
    assert doc["data"]["action"] == "update"
    assert doc["data"]["reboot_needed"] is False
    assert "wsl.exe --update" in Path(doc["log"]).read_text(encoding="utf-8")


def test_old_kernel_is_updated_even_when_the_wsl_version_is_new_enough(cli, winapi, runner, environ):
    install_wsl(environ, runner, version="2.4.10.0", kernel="5.15.167.4-1")

    result = cli("prereqs", "--json", "--yes")

    assert result.exit_code == 0
    assert winapi.elevated == [UPDATE]
    assert result.json["data"]["action"] == "update"
    assert "5.15.167.4" in result.stderr


def test_ready_wsl_does_nothing(cli, winapi, runner, environ):
    install_wsl(environ, runner, version="2.6.1.0")

    result = cli("prereqs", "--json", "--yes")

    assert result.exit_code == 0
    assert winapi.elevated == []
    doc = result.json
    assert doc["data"] == {"action": "none", "reboot_needed": False}
    assert "ready" in doc["message"].lower()


def test_declined_uac_fails_clearly_with_the_log(cli, winapi):
    winapi.elevation_declined = True

    result = cli("prereqs", "--json", "--yes")

    assert result.exit_code == 1
    doc = result.json
    assert doc["ok"] is False
    assert "declined" in doc["error"].lower()
    assert doc["data"]["action"] == "install"
    assert doc["data"]["reboot_needed"] is False
    log = Path(doc["log"])
    assert "exit=1" in log.read_text(encoding="utf-8")
    assert str(log) in result.stderr


def test_failed_wsl_command_fails_with_its_exit_code(cli, winapi, runner, environ):
    install_wsl(environ, runner, version="2.3.26.0")
    winapi.elevated_returncode = 1603

    result = cli("prereqs", "--json", "--yes")

    assert result.exit_code == 1
    doc = result.json
    assert "1603" in doc["error"]
    assert doc["data"] == {"action": "update", "reboot_needed": False}
    assert Path(doc["log"]).is_file()


def test_update_that_asks_for_a_reboot_reports_it(cli, winapi, runner, environ):
    install_wsl(environ, runner, version="2.3.26.0")
    winapi.elevated_returncode = 3010  # ERROR_SUCCESS_REBOOT_REQUIRED

    result = cli("prereqs", "--json", "--yes")

    assert result.exit_code == 0
    assert result.json["data"] == {"action": "update", "reboot_needed": True}


def test_virtual_machine_platform_off_runs_the_install(cli, winapi, runner, environ):
    install_wsl(environ, runner, version="2.6.1.0")
    runner.on("powershell.exe", stdout='{"vmp": 2, "hypervisor": false}')

    result = cli("prereqs", "--json", "--yes")

    assert result.exit_code == 0
    assert winapi.elevated == [INSTALL]
    assert result.json["data"] == {"action": "install", "reboot_needed": True}


def test_non_windows_host_exits_4_without_elevating(cli, winapi):
    result = cli("prereqs", "--json", "--yes", platform="linux")

    assert result.exit_code == 4
    assert winapi.elevated == []


def test_human_output_says_a_reboot_is_needed(cli):
    result = cli("prereqs", "--yes")

    assert result.exit_code == 0
    assert "reboot" in result.stdout.lower()


def test_asks_before_elevating_and_runs_on_yes(cli, winapi, prompter):
    prompter.answers = [True]

    result = cli("prereqs", "--json")

    assert result.exit_code == 0
    assert winapi.elevated == [INSTALL]
    assert [kind for kind, _ in prompter.questions] == ["confirm"]
    assert "wsl.exe --install --no-distribution" in prompter.questions[0][1]
    assert "needs administrator rights" in result.stderr


def test_declining_the_question_changes_nothing(cli, winapi, prompter):
    prompter.answers = [False]

    result = cli("prereqs", "--json")

    assert result.exit_code == 1
    assert winapi.elevated == []
    assert "not changed" in result.json["error"].lower()


def test_non_interactive_without_yes_exits_9_without_elevating(cli, winapi):
    result = cli("prereqs", "--json", "--non-interactive")

    assert result.exit_code == 9
    assert winapi.elevated == []
    assert "--yes" in result.json["error"]
