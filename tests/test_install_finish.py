"""`install`, ticket 13: launcher, stop, keepalive release, hand-off to `start`, and the summary."""

from test_install import USER_EXEC, install, installs_json, machine, sequence
from test_install_wslconfig import running

import wslcalls

PROJECT = "/home/comfy/comfyui-docker"
COMPOSE_STOP = wslcalls.COMPOSE_STOP
VERIFY_OUT = "== System ==\n[INFO] WSL sees 56 GB RAM, 8 GB swap, 24 CPUs\n[PASS] everything\n"


def stops(runner):
    return [c for c in wslcalls.commands(runner.calls) if c.argv == COMPOSE_STOP]


def test_install_ends_with_the_launcher_a_stopped_container_a_released_keepalive_and_start(install, runner, winapi):
    runner.on_script("verify.sh", stdout=VERIFY_OUT)

    result = install("--yes")

    assert result.exit_code == 0, result.stderr
    calls = runner.calls
    assert {p.name for p in winapi.shortcuts} == {"ComfyUI (comfy-docker).lnk"}
    assert len(winapi.shortcuts) == 2
    [stop] = stops(runner)
    assert (stop.distro, stop.user) == ("comfy-docker", "comfy")
    [keepalive] = runner.started
    verify = wslcalls.index(calls, wslcalls.script(calls, "verify.sh").call)
    assert verify < wslcalls.index(calls, stop.call) < keepalive.stopped_after
    assert winapi.consoles == [
        (r"C:\tools\bin\comfyui-sandbox.exe", "run", "--distro", "comfy-docker")
    ]  # start's run window
    data = result.json["data"]
    assert sorted(data["launcher"]["shortcuts"]) == sorted(map(str, winapi.shortcuts))
    assert data["launcher"]["refused"] is None
    assert data["start"] == {"distro": "comfy-docker", "url": "http://localhost:8188", "already_running": False}


def test_the_cpu_path_ends_the_same_way(install, runner, winapi, machine):
    machine.nvidia_smi = None

    result = install("--yes")

    assert result.exit_code == 0, result.stderr
    assert len(winapi.shortcuts) == 2 and len(stops(runner)) == 1 and len(winapi.consoles) == 1
    assert result.json["data"]["summary"]["gpu"] == {"mode": "cpu", "name": None}
    assert "install_nvidia_toolkit.sh" not in sequence(runner.calls)


def test_a_packaged_app_refuses_the_launcher_and_install_says_so_but_finishes(install, runner, winapi):
    winapi.packaged_app = True

    result = install("--yes")

    assert result.exit_code == 0, result.stderr
    assert winapi.shortcuts == {}
    launcher = result.json["data"]["launcher"]
    assert launcher["shortcuts"] == [] and "reinstall" in launcher["refused"].lower()
    assert "reinstall" in result.stderr.lower()
    assert len(winapi.consoles) == 1 and len(stops(runner)) == 1


def test_the_summary_has_every_item_in_json_and_human_output(install, runner):
    runner.on_script("verify.sh", stdout=VERIFY_OUT)

    result = install("--yes")

    assert result.json["data"]["summary"] == {
        "url": "http://localhost:8188",
        "gpu": {"mode": "nvidia", "name": "NVIDIA GeForce RTX 4090"},
        "wsl_ram_gb": 56,
        "pinned_memory": "off",
        "project": PROJECT,
        "models": {
            "mode": "wsl",
            "path": "/home/comfy/comfyui-models",
            "windows_path": r"\\wsl.localhost\comfy-docker\home\comfy\comfyui-models",
        },
        "runs_only_while_window_open": True,
    }
    for text in (
        "http://localhost:8188",
        "NVIDIA GeForce RTX 4090",
        "56 GB",
        PROJECT,
        r"\\wsl.localhost\comfy-docker\home\comfy\comfyui-models",
        "only while its window is open",
    ):
        assert text in result.stderr, text
    assert "Pinned memory is off" in result.stderr


def test_pinned_memory_off_and_a_windows_models_folder_show_in_the_summary(install, tmp_path):
    models = tmp_path / "Models"

    result = install("--yes", "--memory", "pinned", "--models", "windows", "--models-path", str(models))

    summary = result.json["data"]["summary"]
    assert summary["pinned_memory"] == "off"
    assert summary["models"] == {"mode": "windows", "path": str(models), "windows_path": None}
    assert "Pinned memory is off" in result.stderr


def test_the_later_reminder_appears_only_when_the_wslconfig_limits_are_not_active(install, runner):
    running(runner, "Ubuntu")

    result = install("--yes")

    assert result.json["data"]["wsl_restart_pending"] is True
    assert "run 'wsl --shutdown' when you can" in result.stderr


def test_no_reminder_when_the_limits_are_active(install):
    result = install("--yes", "--memory", "neither")

    assert result.json["data"]["wsl_restart_pending"] is False
    assert "wsl --shutdown" not in result.stderr


def test_a_failure_while_stopping_the_container_writes_a_transcript_and_resumes(install, runner, home, winapi):
    runner.on(*USER_EXEC, *COMPOSE_STOP, returncode=1, stderr="boom")

    failed = install("--yes")

    assert failed.exit_code == 1 and failed.json["log"]
    assert installs_json(home)["installs"][0]["state"] == "in_progress"
    assert winapi.consoles == []
    runner.on(*USER_EXEC, *COMPOSE_STOP)  # it stops fine now
    runner.on_script("verify.sh", stdout=VERIFY_OUT)

    resumed = install("--yes")

    assert resumed.exit_code == 0, resumed.stderr
    assert resumed.json["data"]["resumed"] is True
    assert len(winapi.consoles) == 1
    assert installs_json(home)["installs"][0]["state"] == "complete"


def refuse_once_started(http, winapi):
    """Windows reaches ComfyUI during verify, but not once start has opened the run window."""
    real_get = http.get

    def get(url, *, timeout=10):
        if winapi.consoles:
            http.refuse(8188)
        return real_get(url, timeout=timeout)

    http.get = get


def test_a_failing_start_still_shows_the_summary_and_reminder_keeps_the_exit_code_and_points_at_start(
    install, runner, home, http, winapi
):
    running(runner, "Ubuntu")
    refuse_once_started(http, winapi)  # setup is done but Windows never reaches ComfyUI

    result = install("--yes")

    assert result.exit_code == 10
    assert "comfyui-sandbox start" in result.json["message"]
    assert "http://localhost:8188" in result.stderr  # the summary
    assert "run 'wsl --shutdown' when you can" in result.stderr  # the .wslconfig reminder
    assert result.json["data"]["summary"]["url"] == "http://localhost:8188"
    assert result.json["data"]["wsl_restart_pending"] is True
    assert installs_json(home)["installs"][0]["state"] == "complete"  # setup is done: retry with start


def test_an_unexpected_error_in_the_start_hand_off_still_shows_the_summary_and_the_next_step(install, winapi):
    def broken(argv):
        raise NotImplementedError("the window could not be opened")

    winapi.open_console = broken

    result = install("--yes")

    assert result.exit_code == 1
    assert "Summary" in result.stderr and "http://localhost:8188" in result.stderr
    message = result.json["message"]
    assert "the window could not be opened" in message
    assert "comfyui-sandbox start" in message
    assert result.json["data"]["summary"]["url"] == "http://localhost:8188"
