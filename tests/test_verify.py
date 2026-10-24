"""`comfyui-sandbox verify` (ticket 09): the in-distro checklist plus the Windows reach check.

The fake Runner plays verify.sh's output; the fake Http plays what answers on the Windows port.
"""

import json

import pytest
from test_check import Machine
from test_install import install, installs_json, machine

import wslcalls
from comfyui_sandbox.winapi import PortOwner

ISOLATION_OUTPUT = """\
== WSL isolation ==
[PASS] no Windows drive letters mounted
[PASS] automount disabled in /etc/wsl.conf
[PASS] WSL interop disabled in /etc/wsl.conf
[PASS] WSL Windows-binary handler inactive
[INFO] WSL isolation check complete
"""

PASS_OUTPUT = """\
== WSL isolation ==
[PASS] no Windows drive letters mounted
[PASS] WSL interop disabled
== Docker ==
[PASS] docker service active
[WARN] daemon.json lacks no-new-privileges
[HINT] Re-run 'comfyui-sandbox install' to rewrite /etc/docker/daemon.json.
== ComfyUI container ==
[PASS] container running
[INFO] COMFY_ARGS: --enable-manager
"""

FAIL_OUTPUT = (
    PASS_OUTPUT
    + """\
[FAIL] container runs as root
[HINT] Redeploy the container: 'comfyui-sandbox rebuild'.
[FAIL] port published beyond localhost: 0.0.0.0:8188
"""
)


@pytest.fixture(autouse=True)
def wsl_present(runner, environ):
    Machine().script(runner, environ)


def own(home, distro="comfy-docker", *, gpu="nvidia", models=("wsl", "/home/comfy/models"), port=8188):
    folder = home / ".comfyui-sandbox"
    folder.mkdir(exist_ok=True)
    record = {
        "distro": distro,
        "linux_user": "comfy",
        "state": "complete",
        "port": port,
        "models": {"mode": models[0], "path": models[1]},
        "gpu": {"mode": gpu},
        "deployed_by": "1.0.0",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "answers": {},
    }
    (folder / "installs.json").write_text(json.dumps({"schema": 1, "installs": [record]}), "utf-8")


def test_a_pass_reports_every_check_in_human_and_json_output(cli, runner, home):
    own(home)
    runner.on_script("verify.sh", stdout=PASS_OUTPUT)

    result = cli("verify", "--json")

    assert result.exit_code == 0, result.stderr
    run = wslcalls.script(runner.calls, "verify.sh")
    assert (run.distro, run.user) == ("comfy-docker", "root")
    assert run.env == {"GPU_MODE": "nvidia", "MODELS_DIR": "/home/comfy/models", "ISOLATION_ONLY": "0"}
    data = result.json["data"]
    assert data["distro"] == "comfy-docker"
    assert data["counts"] == {"pass": 5, "warn": 1, "fail": 0}
    assert {
        "section": "Docker",
        "status": "warn",
        "message": "daemon.json lacks no-new-privileges",
        "hint": "Re-run 'comfyui-sandbox install' to rewrite /etc/docker/daemon.json.",
    } in data["checks"]
    assert data["windows_reach"]["ok"] is True
    assert data["windows_reach"]["os"] == "linux"
    for line in (
        "[PASS] docker service active",
        "[WARN] daemon.json lacks no-new-privileges",
        "[ OK ] http://127.0.0.1:8188 reaches the ComfyUI container",
    ):
        assert line in result.stderr
    assert not (home / ".comfyui-sandbox" / "logs").exists()  # read-only: no transcript


def test_a_windows_models_folder_is_checked_at_its_mount(cli, runner, home):
    own(home, models=("windows", r"D:\Models"))

    assert cli("verify").exit_code == 0

    assert wslcalls.script(runner.calls, "verify.sh").env["MODELS_DIR"] == "/mnt/models"


def test_a_cpu_only_install_skips_the_gpu_checks(cli, runner, home):
    own(home, gpu="cpu")

    assert cli("verify").exit_code == 0

    assert wslcalls.script(runner.calls, "verify.sh").env["GPU_MODE"] == "cpu"


def test_an_in_distro_failure_prints_its_hint_and_exits_1(cli, runner, home):
    own(home)
    runner.on_script("verify.sh", returncode=1, stdout=FAIL_OUTPUT)

    result = cli("verify", "--json")

    assert result.exit_code == 1
    doc = result.json
    assert doc["data"]["counts"]["fail"] == 2
    assert "2 checks failed" in doc["message"]
    assert "[FAIL] container runs as root" in result.stderr
    assert "Hint: Redeploy the container: 'comfyui-sandbox rebuild'." in result.stderr
    assert doc["data"]["windows_reach"]["ok"] is True  # still checked


def test_a_script_that_fails_without_a_fail_line_still_fails(cli, runner, home):
    own(home)
    runner.on_script("verify.sh", returncode=1, stdout="[PASS] fine\n")

    assert cli("verify").exit_code == 1


def test_a_comfyui_on_windows_holding_the_port_is_not_the_container(cli, runner, home, http, winapi):
    own(home)
    runner.on_script("verify.sh", stdout=PASS_OUTPUT)
    http.answer(8188, '{"system": {"os": "win32"}}')
    winapi.port_owners[8188] = PortOwner(4242, "python")

    result = cli("verify", "--json")

    assert result.exit_code == 1
    reach = result.json["data"]["windows_reach"]
    assert reach["ok"] is False and reach["os"] == "win32"
    assert "ComfyUI running on Windows" in reach["message"]
    assert "python (PID 4242)" in reach["message"]
    assert reach["hint"]
    assert "[FAIL]" in result.stderr and "Hint:" in result.stderr


@pytest.mark.parametrize("os_name", ["linux", "posix"])
def test_linux_and_posix_both_count_as_the_container(cli, runner, home, http, os_name):
    own(home)
    http.answer(8188, json.dumps({"system": {"os": os_name}}))

    assert cli("verify").exit_code == 0


def test_nothing_answering_on_the_port_fails_with_a_hint(cli, runner, home, http):
    own(home, port=8190)
    http.refuse(8190)

    result = cli("verify", "--json")

    assert result.exit_code == 1
    reach = result.json["data"]["windows_reach"]
    assert reach["ok"] is False and reach["os"] is None
    assert "nothing answers" in reach["message"]
    assert http.urls == ["http://127.0.0.1:8190/system_stats"]


def test_another_program_answering_without_system_stats_fails(cli, home, http, winapi):
    own(home)
    http.answer(8188, "<html>Not ComfyUI</html>")
    winapi.port_owners[8188] = PortOwner(77, "nginx")

    result = cli("verify", "--json")

    assert result.exit_code == 1
    assert "nginx (PID 77)" in result.json["data"]["windows_reach"]["message"]


# -- install runs verify after the Manager security level -------------------------


def test_install_runs_verify_after_the_manager_security_level(install, runner):
    runner.on_script("verify.sh", stdout=PASS_OUTPUT)

    result = install("--yes")

    assert result.exit_code == 0, result.stderr
    calls = runner.calls
    run = wslcalls.script(calls, "verify.sh")
    assert run.user == "root" and run.env["GPU_MODE"] == "nvidia"
    manager = wslcalls.script(calls, "manager_settings.sh")
    assert wslcalls.index(calls, manager.call) < wslcalls.index(calls, run.call)
    assert result.json["data"]["verify"]["windows_reach"]["ok"] is True


def test_a_failed_verify_stops_install_with_the_transcript_path(install, runner, home):
    runner.on_script("verify.sh", returncode=1, stdout=FAIL_OUTPUT)

    result = install("--yes")

    assert result.exit_code == 1
    assert "verify" in result.json["message"].lower()
    assert result.json["log"].endswith("-install.log")
    assert installs_json(home)["installs"][0]["state"] == "in_progress"


def test_a_windows_comfyui_on_the_port_stops_install(install, http):
    http.answer(8188, '{"system": {"os": "win32"}}')

    result = install("--yes")

    assert result.exit_code == 1
    assert "Windows" in result.json["message"]
