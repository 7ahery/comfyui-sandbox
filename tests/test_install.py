"""`comfyui-sandbox install`: the hardened-distro tracer (ticket 04).

The fake host is a ready RTX 4090 machine (see test_check.Machine). Every wsl.exe
call is recorded by the fake Runner; nothing touches a real distro.
"""

import json
import re
from importlib.metadata import version as package_version
from pathlib import Path

import pytest
from test_check import Machine

import wslcalls


@pytest.fixture
def machine():
    return Machine(distros=["Ubuntu"])


USER_EXEC = ("wsl.exe", "-d", "comfy-docker", "-u", "comfy", "--cd", "/", "--exec", "env")


@pytest.fixture
def install(cli, runner, environ, machine, home):
    def run(*argv: str):
        machine.script(runner, environ)
        # The Linux user's ids, which go into .env (different, so a swap shows).
        runner.on(*USER_EXEC, "id", "-u", stdout="1001\n")
        runner.on(*USER_EXEC, "id", "-g", stdout="1002\n")
        result = cli("install", "--json", *argv)
        registered_by_install(home, machine, runner, environ)
        return result

    return run


def registered_by_install(home, machine, runner, environ) -> None:
    """The fake WSL keeps a distro the install created (the entry says so: distro_created_at)."""
    path = home / ".comfyui-sandbox" / "installs.json"
    if not path.is_file():
        return
    for record in json.loads(path.read_text("utf-8"))["installs"]:
        if record.get("distro_created_at") and record["distro"] not in machine.distros:
            machine.distros = [*machine.distros, record["distro"]]
            machine.script(runner, environ)


def installs_json(home) -> dict:
    return json.loads((home / ".comfyui-sandbox" / "installs.json").read_text("utf-8"))


def test_install_yes_leaves_a_hardened_distro_and_a_complete_install(install, runner, home):
    result = install("--yes")

    assert result.exit_code == 0, result.stderr
    doc = result.json
    assert doc["command"] == "install"
    assert doc["data"]["distro"] == "comfy-docker"

    saved = installs_json(home)
    assert saved["schema"] == 1
    [record] = saved["installs"]
    assert record["distro"] == "comfy-docker"
    assert record["linux_user"] == "comfy"
    assert record["state"] == "complete"
    assert record["port"] == 8188
    assert record["models"] == {"mode": "wsl", "path": "/home/comfy/comfyui-models"}
    assert record["gpu"] == {"mode": "nvidia", "name": "NVIDIA GeForce RTX 4090"}
    assert record["cuda_version"] is None
    assert record["location"] is None
    assert record["deployed_by"] == package_version("comfyui-sandbox")
    assert record["answers"]["distro"] == "comfy-docker"

    calls = runner.calls
    [create] = wslcalls.distro_ops(calls, "--install")
    assert "Ubuntu-26.04" in create.argv
    assert create.argv[create.argv.index("--name") + 1] == "comfy-docker"
    assert "--no-launch" in create.argv
    assert "--location" not in create.argv

    user = wslcalls.script(calls, "create_user.sh")
    assert (user.distro, user.user, user.env) == ("comfy-docker", "root", {"LINUX_USER": "comfy"})

    harden = wslcalls.script(calls, "harden.sh")
    assert (harden.distro, harden.user) == ("comfy-docker", "root")
    assert harden.env == {"LINUX_USER": "comfy", "MODELS_MODE": "wsl", "MODELS_DIR": "/home/comfy/comfyui-models"}
    assert "systemd-binfmt.service" in harden.body
    assert "WSLInterop" in harden.body

    terminates = wslcalls.distro_ops(calls, "--terminate")
    assert [t.argv[t.argv.index("--terminate") + 1] for t in terminates] == ["comfy-docker"] * 2

    order = [wslcalls.index(calls, c) for c in (create, user.call, harden.call, terminates[0])]
    assert order == sorted(order)


# What install does once Docker is ready: the first start, the Manager setup, a restart, then verify.
DEPLOY = [
    "prepare_data.sh",
    "deploy_comfyui.sh",
    "wait_ready.sh",
    "models_structure.sh",
    "manager_settings.sh",
    "wait_ready.sh",
    "verify.sh",
]
USER_SCRIPTS = {"deploy_comfyui.sh", "wait_ready.sh", "models_structure.sh", "manager_settings.sh"}


def sequence(calls):
    """What the install did after creating the distro, in order: scripts by name and
    each termination as "terminate"."""
    seen = []
    for call in calls:
        if not wslcalls.is_wsl(call):
            continue
        if "--terminate" in call.argv:
            seen.append("terminate")
        else:
            seen += [run.script for run in wslcalls.scripts([call])]
    return seen


def test_nvidia_install_continues_with_docker_then_the_toolkit_then_stops_the_distro(install, runner):
    result = install("--yes")

    assert result.exit_code == 0, result.stderr
    assert sequence(runner.calls) == [
        "create_user.sh",
        "harden.sh",
        "terminate",
        "install_docker.sh",
        "install_nvidia_toolkit.sh",
        "terminate",
        *DEPLOY,
    ]
    docker = wslcalls.script(runner.calls, "install_docker.sh")
    assert (docker.distro, docker.user, docker.env) == ("comfy-docker", "root", {"LINUX_USER": "comfy"})
    toolkit = wslcalls.script(runner.calls, "install_nvidia_toolkit.sh")
    assert (toolkit.distro, toolkit.user, toolkit.env) == ("comfy-docker", "root", {})


def test_cpu_install_has_no_toolkit_step_and_no_gpu_calls(install, runner, machine):
    machine.nvidia_smi = None
    machine.video_controllers = ["AMD Radeon RX 7900"]

    result = install("--yes")

    assert result.exit_code == 0, result.stderr
    assert sequence(runner.calls) == [
        "create_user.sh",
        "harden.sh",
        "terminate",
        "install_docker.sh",
        "terminate",
        *DEPLOY,
    ]
    text = " ".join(" ".join(c.argv) + (c.stdin or b"").decode("utf-8") for c in runner.calls)
    assert "--gpus" not in text and "nvidia-ctk" not in text and "/dev/dxg" not in text
    assert "toolkit" not in result.stderr.lower()


def test_no_linux_nvidia_driver_is_ever_installed(install, runner):
    assert install("--yes").exit_code == 0

    drivers = re.compile(r"nvidia-(driver|utils|dkms|kernel)|cuda-drivers|libnvidia-(compute|gl)")
    for run in wslcalls.scripts(runner.calls):
        for line in run.body.splitlines():
            if "install" in line and not line.lstrip().startswith("#"):
                assert not drivers.search(line), line
    assert "nvidia-container-toolkit" in wslcalls.script(runner.calls, "install_nvidia_toolkit.sh").body


ROOT_EXEC = ("wsl.exe", "-d", "comfy-docker", "-u", "root", "--cd", "/", "--exec", "env")
HARDENED = json.dumps(
    {"no-new-privileges": True, "log-driver": "local", "log-opts": {"max-size": "20m", "max-file": "3"}}
)


def distro_has(runner, *, docker: bool = False, toolkit: bool = False, daemon_json: str = HARDENED):
    """What the distro answers when install asks whether Docker and the toolkit are done.
    Unscripted, the distro answers nothing: neither is done."""
    if docker:
        runner.on(*ROOT_EXEC, "cat", "/etc/docker/daemon.json", stdout=daemon_json)
    if toolkit:
        runner.on(
            *ROOT_EXEC,
            "docker",
            "info",
            "--format",
            "{{json .Runtimes}}",
            stdout='{"nvidia":{"path":"nvidia-container-runtime"},"runc":{"path":"runc"}}',
        )


def test_steps_already_done_do_nothing_on_resume(install, runner):
    distro_has(runner, docker=True, toolkit=True)

    result = install("--yes")

    assert result.exit_code == 0, result.stderr
    assert sequence(runner.calls) == ["create_user.sh", "harden.sh", "terminate", "terminate", *DEPLOY]


def test_docker_without_the_hardened_defaults_is_installed_again(install, runner):
    distro_has(runner, docker=True, toolkit=True, daemon_json='{"log-driver": "json-file"}')

    assert install("--yes").exit_code == 0
    assert sequence(runner.calls)[3:] == ["install_docker.sh", "terminate", *DEPLOY]


def test_only_the_toolkit_is_installed_when_docker_is_done(install, runner):
    distro_has(runner, docker=True)

    assert install("--yes").exit_code == 0
    assert sequence(runner.calls)[3:] == ["install_nvidia_toolkit.sh", "terminate", *DEPLOY]


def test_long_steps_stream_progress_to_stderr_and_the_transcript(install, runner):
    runner.on(
        "wsl.exe",
        "-d",
        "comfy-docker",
        "-u",
        "root",
        "--cd",
        "/",
        "--",
        "bash",
        "-s",
        stdout="Get:1 https://download.docker.com/linux/ubuntu noble InRelease\n",
    )

    result = install("--yes")

    assert result.exit_code == 0, result.stderr
    assert "Get:1 https://download.docker.com" in result.stderr
    assert wslcalls.script(runner.calls, "install_docker.sh").call.streamed
    log = Path(result.json["log"]).read_text(encoding="utf-8")
    assert "out   Get:1 https://download.docker.com" in log


def test_answers_are_saved_before_any_distro_call(install, runner, home):
    runner.on("wsl.exe", "--install", returncode=1, stdout="Error: network unreachable\n")

    result = install("--yes")

    assert result.exit_code == 1
    doc = result.json
    assert "network unreachable" in doc["message"]
    [record] = installs_json(home)["installs"]
    assert record["state"] == "in_progress"
    assert record["answers"]["distro"] == "comfy-docker"
    assert wslcalls.scripts(runner.calls) == []
    log = doc["log"]
    assert f"Log: {log}" in result.stderr
    text = Path(log).read_text(encoding="utf-8")
    assert "wsl.exe --install Ubuntu-26.04 --name comfy-docker --no-launch" in text
    assert text.rstrip("\n").splitlines()[-1].endswith("exit=1")


def test_install_again_resumes_with_the_saved_answers_and_reruns_every_step(install, runner, machine, home):
    runner.on(
        "wsl.exe",
        "-d",
        "comfy-docker",
        "-u",
        "root",
        "--cd",
        "/",
        "--",
        "bash",
        "-s",
        returncode=1,
        stderr="[error] mounting failed\n",
    )
    first = install("--yes")
    assert first.exit_code == 1
    saved = installs_json(home)["installs"][0]

    # The distro now exists; the next run has no --yes and no questions to ask.
    machine.distros = ["Ubuntu", "comfy-docker"]
    runner.on("wsl.exe", "-d", "comfy-docker", "-u", "root", "--cd", "/", "--", "bash", "-s")
    runner.calls.clear()
    second = install("--non-interactive")

    assert second.exit_code == 0, second.stderr
    assert second.json["data"]["resumed"] is True
    assert wslcalls.distro_ops(runner.calls, "--install") == []  # already done: nothing to do
    assert wslcalls.script(runner.calls, "create_user.sh").env == {"LINUX_USER": "comfy"}
    assert wslcalls.script(runner.calls, "harden.sh").env["MODELS_DIR"] == "/home/comfy/comfyui-models"
    assert len(wslcalls.distro_ops(runner.calls, "--terminate")) == 2
    [record] = installs_json(home)["installs"]
    assert record["answers"] == saved["answers"]
    assert record["created_at"] == saved["created_at"]


def test_new_installs_another_sandbox_while_one_is_unfinished(install, runner, machine, home):
    runner.on("wsl.exe", "--install", returncode=1, stdout="Error: network unreachable\n")
    assert install("--yes").exit_code == 1
    runner.on("wsl.exe", "--install")

    result = install("--new", "--yes", "--port", "8189")

    assert result.exit_code == 0, result.stderr
    assert result.json["data"]["resumed"] is False
    states = {i["distro"]: i["state"] for i in installs_json(home)["installs"]}
    assert states == {"comfy-docker": "in_progress", "comfy-docker-2": "complete"}


def test_new_refuses_to_name_the_unfinished_install(install, runner, home):
    runner.on("wsl.exe", "--install", returncode=1, stdout="Error: network unreachable\n")
    assert install("--yes").exit_code == 1

    result = install("--new", "--yes", "--distro", "comfy-docker")

    assert result.exit_code == 1
    assert "without --new" in result.json["message"]


def test_resume_by_distro_name(install, runner, machine, home):
    runner.on("wsl.exe", "--terminate", returncode=1)
    assert install("--yes").exit_code == 1
    runner.on("wsl.exe", "--terminate")
    machine.distros = ["Ubuntu", "comfy-docker"]

    result = install("--distro", "COMFY-docker")

    assert result.exit_code == 0, result.stderr
    assert result.json["data"]["distro"] == "comfy-docker"
    assert len(installs_json(home)["installs"]) == 1


def test_the_default_distro_name_skips_names_that_exist(install, runner, machine):
    machine.distros = ["Ubuntu", "comfy-docker", "COMFY-DOCKER-2"]

    result = install("--yes")

    assert result.exit_code == 0, result.stderr
    assert result.json["data"]["distro"] == "comfy-docker-3"
    [create] = wslcalls.distro_ops(runner.calls, "--install")
    assert create.argv[create.argv.index("--name") + 1] == "comfy-docker-3"


def test_a_named_distro_that_exists_but_is_not_ours_is_never_touched(install, runner, machine, home):
    machine.distros = ["Ubuntu", "ComfyDocker"]

    result = install("--yes", "--distro", "comfydocker")

    assert result.exit_code == 9
    assert result.json["flag"] == "--distro"
    assert not (home / ".comfyui-sandbox" / "installs.json").exists()
    touched = [c for c in runner.calls if wslcalls.is_wsl(c) and "comfydocker" in [a.lower() for a in c.argv]]
    assert touched == []


def test_a_machine_that_is_not_ready_stops_before_anything_is_saved(install, machine, home, runner):
    machine.wsl_version = "WSL version: 2.3.0.0\n"

    result = install("--yes")

    assert result.exit_code == 7
    assert not (home / ".comfyui-sandbox" / "installs.json").exists()
    assert wslcalls.distro_ops(runner.calls, "--install") == []


def test_cpu_only_machine_installs_in_cpu_mode(install, machine, home):
    machine.nvidia_smi = None
    machine.video_controllers = ["AMD Radeon RX 7900"]

    result = install("--yes")

    assert result.exit_code == 0, result.stderr
    assert installs_json(home)["installs"][0]["gpu"] == {"mode": "cpu", "name": None}
    assert "CPU-only" in result.stderr


def test_blackwell_card_records_the_suggested_cuda_version(install, machine, home):
    machine.nvidia_smi = [("NVIDIA GeForce RTX 5090", "580.10", "32607 MiB", "12.0")]

    assert install("--yes").exit_code == 0
    assert installs_json(home)["installs"][0]["cuda_version"] == "12.8"


def test_no_password_is_ever_set_and_root_tasks_use_wsl_u_root(install, runner):
    assert install("--yes").exit_code == 0

    for call in runner.calls:
        text = " ".join(call.argv) + (call.stdin or b"").decode("utf-8")
        assert "chpasswd" not in text and "--password" not in text
    for run in wslcalls.scripts(runner.calls):
        assert run.user == ("comfy" if run.script in USER_SCRIPTS else "root")


# -- ticket 06: deploy and first start ---------------------------------------------

KEEPALIVE = ("wsl.exe", "-d", "comfy-docker", "-u", "root", "--cd", "/", "--exec", "env", "sleep", "infinity")


def test_keepalive_is_held_from_the_data_folder_step_until_install_ends(install, runner, winapi):
    result = install("--yes")

    assert result.exit_code == 0, result.stderr
    [keepalive] = runner.started
    assert keepalive.call.argv == KEEPALIVE
    assert winapi.job_bound == [keepalive.pid]  # a child of the CLI's kill-on-close Job Object
    calls = runner.calls
    first = wslcalls.index(calls, keepalive.call)
    prepare = wslcalls.index(calls, wslcalls.script(calls, "prepare_data.sh").call)
    stop_distro = max(wslcalls.index(calls, c) for c in wslcalls.distro_ops(calls, "--terminate"))
    assert stop_distro < first < prepare  # started after the last terminate, before the data folder
    assert keepalive.stopped_after == len(calls)  # held through every later step


def test_keepalive_ends_when_a_later_step_fails(install, runner):
    runner.on(
        "wsl.exe",
        "-d",
        "comfy-docker",
        "-u",
        "comfy",
        "--cd",
        "/",
        "--",
        "bash",
        "-s",
        returncode=1,
        stderr="[error] cannot reach Docker as comfy\n",
    )

    result = install("--yes")

    assert result.exit_code == 1
    [keepalive] = runner.started
    assert keepalive.stopped_after is not None


def test_keepalive_never_starts_or_stops_the_container(install, runner):
    assert install("--yes").exit_code == 0

    [keepalive] = runner.started
    assert keepalive.call.argv == KEEPALIVE  # exactly `sleep infinity` as root: no docker in it
    assert keepalive.terminated  # the wsl.exe child is ended with the install
    container_stops = [
        c
        for c in wslcalls.commands(runner.calls)
        if "docker" in c.argv and any(verb in c.argv for verb in ("stop", "kill", "down"))
    ]
    # The one stop in the whole install is the explicit "Stop ComfyUI" step, after verify.
    assert [c.argv for c in container_stops] == [wslcalls.COMPOSE_STOP]
    verify = wslcalls.script(runner.calls, "verify.sh").call
    assert wslcalls.index(runner.calls, verify) < wslcalls.index(runner.calls, container_stops[0].call)


def test_a_step_that_times_out_ends_in_an_error_naming_the_step(install, runner):
    from comfyui_sandbox.runner import CommandTimeout

    def hang():
        raise CommandTimeout("Timed out after 300s: wsl.exe")

    runner.on_script("harden.sh", then=hang)
    result = install("--yes")

    assert result.exit_code != 0
    message = result.json["error"]
    assert "Harden the distro" in message
    assert "Timed out after 300s" in message
