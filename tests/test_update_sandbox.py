"""`comfyui-sandbox update setup` (ticket 19, renamed from `update sandbox`): redeploy the docker assets, merge `.env` keeping the
user's edits (with a backup), rebuild, and record the CLI version that deployed them.
"""

import base64
import io
import json
import tarfile
from importlib import resources
from importlib.metadata import version

import pytest
from test_check import Machine
from test_verify import own

import wslcalls
from comfyui_sandbox import envfile
from comfyui_sandbox.intake import Answers

PROJECT = "/home/comfy/comfyui-docker"
READY = "[ok] ComfyUI answers (HTTP 200) after 1 min of waiting\n"
# An older deploy: no COMFY_INSTALL_ARGS, a port the user changed, and a key of their own.
ENV = """\
# Written by comfyui-sandbox
PUID=1001
PGID=1002
MODELS_DIR="/home/comfy/models"
COMFY_PORT=9000
COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml
COMFY_ARGS="--enable-manager --lowvram"
MY_OWN=keep me
"""
ANSWERS = Answers(
    distro="comfy-docker",
    linux_user="comfy",
    models="wsl",
    models_dir="/home/comfy/models",
    port=8188,
    gpu="nvidia",
    gpu_name="NVIDIA GeForce RTX 4090",
)


def set_env(runner, text):
    runner.on(
        "wsl.exe",
        "-d",
        "comfy-docker",
        "-u",
        "comfy",
        "--cd",
        "/",
        "--exec",
        "env",
        "cat",
        f"{PROJECT}/.env",
        stdout=text,
    )


@pytest.fixture(autouse=True)
def distro(runner, environ, home):
    Machine().script(runner, environ)
    own(home)
    path = home / ".comfyui-sandbox" / "installs.json"
    doc = json.loads(path.read_text("utf-8"))
    doc["installs"][0].update(deployed_by="0.0.1", answers=ANSWERS.to_dict())
    path.write_text(json.dumps(doc), "utf-8")
    set_env(runner, ENV)
    runner.on_script("wait_ready.sh", stdout=READY)


def deployed(runner) -> dict[str, tuple[int, bytes]]:
    deploy = wslcalls.script(runner.calls, "deploy_comfyui.sh")
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(deploy.env["PAYLOAD"]))) as tar:
        files = {}
        for m in tar.getmembers():
            if m.isfile():
                member = tar.extractfile(m)
                assert member is not None  # a regular file has contents
                files[m.name.removeprefix("./")] = (m.mode, member.read())
        return files


def record(home):
    return json.loads((home / ".comfyui-sandbox" / "installs.json").read_text("utf-8"))["installs"][0]


def test_the_assets_are_redeployed_as_the_user_with_the_models_folder_of_the_current_env(cli, runner):
    result = cli("update", "setup", "--json")

    assert result.exit_code == 0, result.stderr
    deploy = wslcalls.script(runner.calls, "deploy_comfyui.sh")
    assert (deploy.distro, deploy.user) == ("comfy-docker", "comfy")
    assert deploy.env["MODELS_DIR"] == "/home/comfy/models"
    files = deployed(runner)
    assert set(files) == {"Dockerfile", "docker-compose.yml", "docker-compose.gpu.yml", "entrypoint.sh", ".env"}
    assert files["entrypoint.sh"][0] & 0o111
    assert b"container_name: comfyui" in files["docker-compose.yml"][1]
    wait = wslcalls.script(runner.calls, "wait_ready.sh")
    assert wslcalls.index(runner.calls, deploy.call) < wslcalls.index(runner.calls, wait.call)
    assert result.json["log"]


def test_env_keeps_the_users_edits_and_gains_the_keys_it_lacked(cli, runner):
    result = cli("update", "setup", "--json")

    assert result.exit_code == 0, result.stderr
    sent = envfile.parse(deployed(runner)[".env"][1].decode())
    assert sent == {**envfile.parse(ENV), "COMFY_INSTALL_ARGS": "--nvidia --version latest"}
    assert sent["COMFY_PORT"] == "9000"  # the user's port, not the answers' 8188
    assert sent["COMFY_ARGS"] == "--enable-manager --lowvram"
    assert result.json["data"]["env_added"] == ["COMFY_INSTALL_ARGS"]


def test_the_env_is_written_once_by_the_deploy_script_with_one_backup(cli, runner):
    result = cli("update", "setup", "--json")

    assert [r.script for r in wslcalls.scripts(runner.calls)] == ["deploy_comfyui.sh", "wait_ready.sh"]
    deploy = wslcalls.script(runner.calls, "deploy_comfyui.sh")
    assert result.json["data"]["backup"] == f".env.bak-{deploy.env['ENV_BACKUP_STAMP']}"
    assert "BACKUP_ENV" not in deploy.env


def test_the_deploy_script_makes_the_one_env_backup_with_the_stamp_it_is_given():
    script = (resources.files("comfyui_sandbox") / "distro" / "deploy_comfyui.sh").read_text("utf-8")

    assert "ENV_BACKUP_STAMP" in script and "BACKUP_ENV" in script


def test_an_env_that_already_has_every_key_is_not_rewritten(cli, runner):
    set_env(runner, ENV + 'COMFY_INSTALL_ARGS="--nvidia --version nightly"\n')

    result = cli("update", "setup", "--json")

    assert result.exit_code == 0, result.stderr
    assert [r.script for r in wslcalls.scripts(runner.calls)] == ["deploy_comfyui.sh", "wait_ready.sh"]
    assert wslcalls.script(runner.calls, "deploy_comfyui.sh").env["BACKUP_ENV"] == "no"
    assert result.json["data"]["backup"] is None
    assert envfile.parse(deployed(runner)[".env"][1].decode())["COMFY_INSTALL_ARGS"] == "--nvidia --version nightly"


def test_the_record_gets_the_current_cli_version_and_status_stops_warning(cli, runner, home):
    runner.on("wsl.exe", "--list", "--running", "--quiet", stdout="comfy-docker\n")
    runner.on_script("status.sh", stdout="container=running\n")
    assert cli("status", "--json").json["data"]["drift"] is True

    assert cli("update", "setup").exit_code == 0

    assert record(home)["deployed_by"] == version("comfyui-sandbox")
    after = cli("status", "--json")
    assert after.json["data"]["drift"] is False
    assert "update setup" not in after.stderr


def test_the_record_keeps_everything_else(cli, home):
    before = record(home)

    assert cli("update", "setup").exit_code == 0

    after = record(home)
    for key in ("distro", "linux_user", "state", "port", "models", "answers", "created_at"):
        assert after[key] == before[key], key


def test_a_failed_deploy_exits_1_and_leaves_the_recorded_version(cli, runner, home):
    runner.on_script("deploy_comfyui.sh", returncode=1, stdout="[error] compose config is invalid")

    result = cli("update", "setup", "--json")

    assert result.exit_code == 1
    assert "Deploying" in result.json["message"]
    assert record(home)["deployed_by"] == "0.0.1"
    assert not [r for r in wslcalls.scripts(runner.calls) if r.script == "wait_ready.sh"]
    assert result.json["log"]


def test_a_container_that_stops_after_the_update_still_records_the_deployed_version(cli, runner, home):
    runner.on_script(
        "wait_ready.sh", returncode=1, stdout="[error] the container is exited\n----- last log lines -----\nboom\n"
    )

    result = cli("update", "setup", "--json")

    assert result.exit_code == 1
    assert record(home)["deployed_by"] == version("comfyui-sandbox")  # the files are the new ones


def test_an_install_without_saved_answers_asks_for_a_reinstall(cli, runner, home):
    path = home / ".comfyui-sandbox" / "installs.json"
    doc = json.loads(path.read_text("utf-8"))
    doc["installs"][0]["answers"] = {}
    path.write_text(json.dumps(doc), "utf-8")

    result = cli("update", "setup", "--json")

    assert result.exit_code == 1
    assert "install" in result.json["message"]
    assert wslcalls.scripts(runner.calls) == []


def test_the_update_group_lists_setup(cli):
    assert "setup" in cli("update", "--help").stdout


def test_update_sandbox_is_an_unknown_command_with_no_alias(cli, runner):
    """The target was renamed before any release, so the old name is simply gone."""
    result = cli("update", "sandbox", "--json")

    assert result.exit_code == 2
    assert "No such command 'sandbox'" in result.json["message"]
    assert wslcalls.scripts(runner.calls) == []
