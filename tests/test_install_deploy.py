"""`install`, ticket 06: prepare the data folder, deploy the docker assets, write `.env`.

Same fake host as test_install (a ready RTX 4090 machine); every wsl.exe call is recorded.
"""

import base64
import io
import tarfile

from test_install import install, machine

import wslcalls


def payload_of(runner) -> dict[str, tuple[int, bytes]]:
    """The files the deploy script is sent: name -> (mode, content). The script gets them as
    a base64 tar in $PAYLOAD."""
    deploy = wslcalls.script(runner.calls, "deploy_comfyui.sh")
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(deploy.env["PAYLOAD"]))) as tar:
        files = {}
        for m in tar.getmembers():
            if m.isfile():
                member = tar.extractfile(m)
                assert member is not None  # a regular file has contents
                files[m.name.removeprefix("./")] = (m.mode, member.read())
        return files


def dotenv(runner) -> dict[str, str]:
    text = payload_of(runner)[".env"][1].decode("utf-8")
    assert "\r" not in text
    pairs = [line.split("=", 1) for line in text.splitlines() if line and not line.startswith("#")]
    return {key: value.strip('"') for key, value in pairs}


def test_the_data_folder_is_prepared_as_root_then_the_docker_assets_are_deployed_as_the_user(install, runner):
    result = install("--yes", "--memory", "neither")

    assert result.exit_code == 0, result.stderr
    prepare = wslcalls.script(runner.calls, "prepare_data.sh")
    assert (prepare.distro, prepare.user, prepare.env) == ("comfy-docker", "root", {"LINUX_USER": "comfy"})
    deploy = wslcalls.script(runner.calls, "deploy_comfyui.sh")
    assert (deploy.distro, deploy.user) == ("comfy-docker", "comfy")
    assert deploy.env["MODELS_DIR"] == "/home/comfy/comfyui-models"
    assert deploy.call.streamed
    order = [wslcalls.index(runner.calls, c) for c in (prepare.call, deploy.call)]
    assert order == sorted(order)

    files = payload_of(runner)
    assert set(files) == {"Dockerfile", "docker-compose.yml", "docker-compose.gpu.yml", "entrypoint.sh", ".env"}
    assert files["entrypoint.sh"][0] & 0o111  # executable
    assert b"\r" not in files["docker-compose.yml"][1]
    assert b"container_name: comfyui" in files["docker-compose.yml"][1]


def test_env_holds_the_intake_choices(install, runner):
    assert install("--yes", "--memory", "neither").exit_code == 0

    assert dotenv(runner) == {
        "PUID": "1001",
        "PGID": "1002",
        "MODELS_DIR": "/home/comfy/comfyui-models",
        "COMFY_PORT": "8188",
        "COMPOSE_FILE": "docker-compose.yml:docker-compose.gpu.yml",
        "COMFY_ARGS": "--enable-manager",
        "COMFY_INSTALL_ARGS": "--nvidia --version latest",
    }


def test_env_for_a_cpu_install_with_the_other_choices(install, runner, machine):
    machine.nvidia_smi = None
    machine.video_controllers = ["AMD Radeon RX 7900"]

    assert (
        install(
            "--yes", "--memory", "pinned", "--manager-ui", "standard", "--channel", "nightly", "--port", "9000"
        ).exit_code
        == 0
    )

    assert dotenv(runner) == {
        "PUID": "1001",
        "PGID": "1002",
        "MODELS_DIR": "/home/comfy/comfyui-models",
        "COMFY_PORT": "9000",
        "COMPOSE_FILE": "docker-compose.yml",
        "COMFY_ARGS": "--enable-manager --disable-pinned-memory --cpu",
        "COMFY_INSTALL_ARGS": "--cpu --version nightly",
    }


def test_env_pins_a_specific_comfyui_release(install, runner):
    assert install("--yes", "--channel", "v0.3.40").exit_code == 0

    assert dotenv(runner)["COMFY_INSTALL_ARGS"] == "--nvidia --version 0.3.40"


def test_env_for_a_blackwell_card_pins_the_cuda_version(install, runner, machine):
    machine.nvidia_smi = [("NVIDIA GeForce RTX 5090", "580.10", "32607 MiB", "12.0")]

    assert install("--yes").exit_code == 0

    assert dotenv(runner)["COMFY_INSTALL_ARGS"] == "--nvidia --cuda-version 12.8 --version latest"


def test_a_windows_models_folder_is_mounted_at_mnt_models(install, runner, tmp_path):
    models = tmp_path / "Models"

    assert install("--yes", "--models", "windows", "--models-path", str(models)).exit_code == 0

    assert dotenv(runner)["MODELS_DIR"] == "/mnt/models"
    assert wslcalls.script(runner.calls, "deploy_comfyui.sh").env["MODELS_DIR"] == "/mnt/models"
