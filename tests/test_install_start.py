"""`install`, ticket 06: the first start, the Manager setup, the restart and completion."""

from pathlib import Path

from test_install import install, installs_json, machine

import wslcalls

PROJECT = "/home/comfy/comfyui-docker"
PROGRESS = "[07:30] [###---] 3/6 PyTorch (largest download) | data 3.1G | Collecting torch"
STOPPED = """\
[error] the container is exited, not running: the install or ComfyUI failed, or it was stopped. Last log lines:
container: exited (exit code 1), started 2026-10-06T16:50:00Z
----- last log lines -----
[setup] Installing ComfyUI via comfy-cli: install --nvidia --version latest
[error] Installing ComfyUI failed (3 of 3 tries with: --nvidia --version latest)
[error] The install failed 3 times with: --nvidia --version latest; not retrying.
--------------------------
"""


def waits(runner):
    return [run for run in wslcalls.scripts(runner.calls) if run.script == "wait_ready.sh"]


def test_the_first_start_wait_shows_phase_elapsed_time_and_data_growth(install, runner):
    runner.on_script("wait_ready.sh", stdout=PROGRESS + "\n[ok] ComfyUI answers (HTTP 200) after 12 min of waiting\n")

    result = install("--yes")

    assert result.exit_code == 0, result.stderr
    assert PROGRESS in result.stderr
    first, _second = waits(runner)
    assert (first.distro, first.user) == ("comfy-docker", "comfy")
    assert first.env == {"PROJECT_DIR": PROJECT, "TIMEOUT_MIN": "60", "INTERVAL": "30"}
    assert first.call.streamed
    log = Path(result.json["log"]).read_text(encoding="utf-8")
    assert f"out   {PROGRESS}" in log


def test_a_container_that_stops_during_the_first_start_reports_its_last_log_lines(install, runner, home):
    runner.on_script("wait_ready.sh", returncode=1, stdout=STOPPED)

    result = install("--yes")

    assert result.exit_code == 1
    message = result.json["message"]
    assert "stopped" in message
    assert "[error] The install failed 3 times with: --nvidia --version latest; not retrying." in message
    assert "[setup] Installing ComfyUI via comfy-cli" in message
    assert wslcalls.scripts(runner.calls)[-1].script == "wait_ready.sh"  # nothing ran after the failure
    assert installs_json(home)["installs"][0]["state"] == "in_progress"


def test_a_previous_install_that_did_not_finish_or_is_broken_is_reported_not_hidden(install, runner):
    runner.on_script(
        "wait_ready.sh",
        stdout=(
            "[setup] Previous install did not finish; removing it and starting over\n"
            "[setup] Previous install is broken; moving it to ComfyUI.broken-20261006-165000 (check it, then delete it)\n"
            + PROGRESS
            + "\n"
        ),
    )

    result = install("--yes")

    assert result.exit_code == 0, result.stderr
    assert "Warning: [setup] Previous install did not finish; removing it and starting over" in result.stderr
    assert "Warning: [setup] Previous install is broken; moving it to ComfyUI.broken-20261006-165000" in result.stderr
    log = Path(result.json["log"]).read_text(encoding="utf-8")
    assert "warn  [setup] Previous install did not finish" in log


def test_a_first_start_that_is_not_ready_in_time_exits_10_and_install_resumes(install, runner, home):
    runner.on_script(
        "wait_ready.sh",
        returncode=2,
        stdout="[timeout] not ready after 60 min (HTTP none, phase 3/6). If the log below moved on.\n",
    )

    result = install("--yes")

    assert result.exit_code == 10
    assert "install" in result.json["message"] and "again" in result.json["message"]
    assert [i["state"] for i in installs_json(home)["installs"]] == ["in_progress"]
    assert [r.script for r in wslcalls.scripts(runner.calls)][-1] == "wait_ready.sh"


def test_models_structure_manager_security_and_a_restart_follow_the_first_start(install, runner):
    result = install("--yes")

    assert result.exit_code == 0, result.stderr
    calls = runner.calls
    structure = wslcalls.script(calls, "models_structure.sh")
    assert (structure.user, structure.env) == (
        "comfy",
        {"MODELS_DIR": "/home/comfy/comfyui-models", "PROJECT_DIR": PROJECT, "CREATE": "1"},
    )  # WSL models
    manager = wslcalls.script(calls, "manager_settings.sh")
    assert (manager.user, manager.env) == ("comfy", {"SECURITY_LEVEL": "normal", "PROJECT_DIR": PROJECT})
    [restart] = [c for c in wslcalls.commands(calls) if c.argv[3:5] == ("docker", "compose") and "restart" in c.argv]
    assert (restart.distro, restart.user) == ("comfy-docker", "comfy")
    assert restart.argv == ("env", "--chdir", PROJECT, "docker", "compose", "restart")
    second = waits(runner)[1]
    assert second.env["TIMEOUT_MIN"] == "10"
    positions = [wslcalls.index(calls, x.call) for x in (waits(runner)[0], structure, manager, restart, second)]
    assert positions == sorted(positions)


def test_the_weak_security_level_is_written_too(install, runner):
    assert install("--yes", "--security", "weak").exit_code == 0

    assert wslcalls.script(runner.calls, "manager_settings.sh").env["SECURITY_LEVEL"] == "weak"


def test_a_windows_models_folder_is_checked_but_not_filled_in_without_consent(install, runner, tmp_path):
    assert install("--yes", "--models", "windows", "--models-path", str(tmp_path / "Models")).exit_code == 0

    structure = wslcalls.script(runner.calls, "models_structure.sh")
    assert structure.env == {"MODELS_DIR": "/mnt/models", "PROJECT_DIR": PROJECT}


def test_a_completed_install_is_marked_in_installs_json(install, runner, home):
    result = install("--yes")

    assert result.exit_code == 0, result.stderr
    [record] = installs_json(home)["installs"]
    assert record["state"] == "complete"
    assert result.json["data"]["install"]["state"] == "complete"
    assert "ComfyUI opens at" in result.json["message"] and "http://localhost:8188" in result.json["message"]
