"""`comfyui-sandbox config` (ticket 18): change the port, launch flags, models folder, Manager UI,
pinned memory and security level in the distro's `.env`. ComfyUI ends stopped (review-fix 08), so
nothing is applied: the values take effect at the next start.

The fake Runner plays the distro (the `.env` it holds, the scripts, docker compose); nothing
touches a real distro.
"""

import base64
import json
import re

import pytest
from test_check import Machine
from test_verify import own

import wslcalls
from comfyui_sandbox import envfile
from comfyui_sandbox.winapi import PortOwner
from fakes import CANCEL

PROJECT = "/home/comfy/comfyui-docker"
COMPOSE = ("env", "--chdir", PROJECT, "docker", "compose")
ENV = """\
# Written by comfyui-sandbox
PUID=1001
PGID=1002
MODELS_DIR="/home/comfy/models"
COMFY_PORT=8188
COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml
COMFY_ARGS="--enable-manager --enable-manager-legacy-ui --lowvram"
COMFY_INSTALL_ARGS="--nvidia --version latest"
MY_OWN=keep me
"""


@pytest.fixture(autouse=True)
def distro(runner, environ, home):
    Machine().script(runner, environ)
    own(home)
    set_env(runner, ENV)


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


def written(runner) -> dict[str, str]:
    """The `.env` that config wrote, parsed."""
    write = wslcalls.script(runner.calls, "write_env.sh")
    assert (write.distro, write.user, write.env["PROJECT_DIR"]) == ("comfy-docker", "comfy", PROJECT)
    assert re.fullmatch(r"\d{8}-\d{6}", write.env["STAMP"])
    return envfile.parse(base64.b64decode(write.env["ENV_B64"]).decode())


def compose(runner, *verb):
    return [c for c in wslcalls.commands(runner.calls) if c.argv == (*COMPOSE, *verb)]


def changed_nothing(runner):
    assert wslcalls.scripts(runner.calls) == []
    assert compose(runner, "up", "-d") == []
    assert not [c for c in wslcalls.commands(runner.calls) if c.argv[:1] == ("mkdir",)]


def record(home):
    data = json.loads((home / ".comfyui-sandbox" / "installs.json").read_text("utf-8"))
    return data["installs"][0]


# -- each setting, by flag ---------------------------------------------------------


def test_a_port_change_edits_env_keeps_the_rest_and_leaves_applying_it_to_the_next_start(cli, runner, home):
    result = cli("config", "--port", "9000", "--json")

    assert result.exit_code == 0, result.stderr
    assert written(runner) == {**envfile.parse(ENV), "COMFY_PORT": "9000"}
    assert compose(runner, "up", "-d") == [] and compose(runner, "restart") == []  # ComfyUI stays stopped
    data = result.json["data"]
    assert data["changes"] == {"COMFY_PORT": {"from": "8188", "to": "9000"}}
    assert data["backup"].startswith(".env.bak-")
    assert result.json["log"]  # a transcript was written
    assert (home / ".comfyui-sandbox" / "logs").is_dir()


def test_the_saved_record_follows_the_port_and_nothing_else_is_duplicated_from_env(cli, runner, home):
    before = record(home)

    assert cli("config", "--port", "9000", "--launch-flags", "--enable-manager --fp16-vae").exit_code == 0

    after = record(home)
    assert after["port"] == 9000
    assert after["answers"] == before["answers"]
    for key in ("distro", "linux_user", "state", "models", "deployed_by", "created_at"):
        assert after[key] == before[key], key
    assert after["gpu"]["mode"] == before["gpu"]["mode"]


def test_launch_flags_replace_comfy_args_and_keep_enable_manager(cli, runner):
    result = cli("config", "--launch-flags", "--lowvram --preview-method auto")

    assert result.exit_code == 0, result.stderr
    assert written(runner)["COMFY_ARGS"] == "--enable-manager --lowvram --preview-method auto"
    assert compose(runner, "up", "-d") == []


def test_manager_ui_standard_drops_the_legacy_flag_and_keeps_the_users_own_flags(cli, runner):
    assert cli("config", "--manager-ui", "standard").exit_code == 0

    assert written(runner)["COMFY_ARGS"] == "--enable-manager --lowvram"


def test_manager_ui_legacy_adds_the_flag_once(cli, runner):
    set_env(runner, ENV.replace(" --enable-manager-legacy-ui", ""))

    assert cli("config", "--manager-ui", "legacy").exit_code == 0

    assert written(runner)["COMFY_ARGS"] == "--enable-manager --lowvram --enable-manager-legacy-ui"


def test_pinned_memory_off_adds_the_disable_flag_and_on_removes_it(cli, runner):
    assert cli("config", "--pinned-memory", "off").exit_code == 0
    assert written(runner)["COMFY_ARGS"].endswith("--lowvram --disable-pinned-memory")

    runner.calls.clear()
    set_env(runner, ENV.replace("--lowvram", "--lowvram --disable-pinned-memory"))
    assert cli("config", "--pinned-memory", "on").exit_code == 0
    assert "--disable-pinned-memory" not in written(runner)["COMFY_ARGS"]


def test_cuda_version_is_added_to_the_install_flags_and_the_record_and_says_reinstall_applies_it(cli, runner, home):
    result = cli("config", "--cuda-version", "12.8", "--json")

    assert result.exit_code == 0, result.stderr
    assert written(runner)["COMFY_INSTALL_ARGS"] == "--nvidia --cuda-version 12.8 --version latest"
    assert written(runner)["MY_OWN"] == "keep me"
    assert record(home)["cuda_version"] == "12.8"
    assert "reinstall" in result.stderr


def test_cuda_version_replaces_an_existing_one_and_keeps_the_channel(cli, runner):
    set_env(runner, ENV.replace('"--nvidia --version latest"', '"--nvidia --cuda-version 12.6 --version nightly"'))

    assert cli("config", "--cuda-version", "12.8").exit_code == 0

    assert written(runner)["COMFY_INSTALL_ARGS"] == "--nvidia --cuda-version 12.8 --version nightly"


@pytest.mark.parametrize("value", ["12", "cu128", "12.8 --privileged", "12.8;", "-v"])
def test_a_cuda_version_that_is_not_a_plain_version_is_refused(cli, runner, value):
    result = cli("config", "--cuda-version", value)

    assert result.exit_code == 2
    changed_nothing(runner)


def test_a_cpu_install_has_no_cuda_version_to_set(cli, runner):
    set_env(runner, ENV.replace('"--nvidia --version latest"', '"--cpu --version latest"'))

    result = cli("config", "--cuda-version", "12.8", "--json")

    assert result.exit_code == 2
    assert "CPU-only sandbox" in result.json["message"]
    changed_nothing(runner)


def test_the_models_folder_is_created_structured_and_set_in_env_and_the_record(cli, runner, home):
    result = cli("config", "--models-path", "~/ai-models")

    assert result.exit_code == 0, result.stderr
    [mkdir] = [c for c in wslcalls.commands(runner.calls) if c.argv[:1] == ("mkdir",)]
    assert (mkdir.user, mkdir.argv) == ("comfy", ("mkdir", "-p", "/home/comfy/ai-models"))
    structure = wslcalls.script(runner.calls, "models_structure.sh")
    assert (structure.user, structure.env) == (
        "comfy",
        {"MODELS_DIR": "/home/comfy/ai-models", "PROJECT_DIR": PROJECT, "CREATE": "1"},
    )
    assert written(runner)["MODELS_DIR"] == "/home/comfy/ai-models"
    assert record(home)["models"] == {"mode": "wsl", "path": "/home/comfy/ai-models"}
    assert compose(runner, "up", "-d") == []


@pytest.mark.parametrize("level", ["weak", "normal-", "normal", "strict"])
def test_the_security_level_goes_to_the_manager_config_and_ends_without_a_restart(cli, runner, level):
    result = cli("config", "--security", level)

    assert result.exit_code == 0, result.stderr
    run = wslcalls.script(runner.calls, "manager_settings.sh")
    assert (run.user, run.env) == ("comfy", {"SECURITY_LEVEL": level, "PROJECT_DIR": PROJECT})
    assert compose(runner, "restart") == []  # ComfyUI ends stopped: the level applies at the next start
    assert [s.script for s in wslcalls.scripts(runner.calls)] == ["manager_settings.sh"]  # no .env write


def test_a_bad_security_level_is_a_usage_error(cli, runner):
    result = cli("config", "--security", "off")

    assert result.exit_code == 2
    assert "weak" in result.stderr
    changed_nothing(runner)


def test_several_settings_make_one_backup_and_no_compose_up(cli, runner):
    result = cli("config", "--port", "9100", "--pinned-memory", "off", "--manager-ui", "standard")

    assert result.exit_code == 0, result.stderr
    env = written(runner)
    assert (env["COMFY_PORT"], env["COMFY_ARGS"]) == ("9100", "--enable-manager --lowvram --disable-pinned-memory")
    assert len(wslcalls.scripts(runner.calls)) == 1 and compose(runner, "up", "-d") == []


def test_a_setting_that_is_already_right_changes_nothing(cli, runner):
    result = cli("config", "--port", "8188", "--json")

    assert result.exit_code == 0, result.stderr
    changed_nothing(runner)
    assert result.json["data"]["changes"] == {}
    assert result.json["data"]["backup"] is None


# -- the port check, same as preflight ----------------------------------------------


def test_a_port_in_use_stops_with_exit_9_naming_the_flag_and_changes_nothing(cli, runner, winapi):
    winapi.port_owners[9000] = PortOwner(4120, "python.exe")

    result = cli("config", "--port", "9000", "--non-interactive", "--json")

    assert result.exit_code == 9
    assert result.json["flag"] == "--port"
    assert "python.exe" in result.json["message"] and "9001 is free" in result.json["message"]
    changed_nothing(runner)


def test_the_port_of_another_sandbox_is_named_as_that_sandbox(cli, runner, home):
    path = home / ".comfyui-sandbox" / "installs.json"
    doc = json.loads(path.read_text("utf-8"))
    doc["installs"].append({**doc["installs"][0], "distro": "comfy-flux", "port": 9000})
    path.write_text(json.dumps(doc), "utf-8")

    result = cli("config", "--port", "9000", "--distro", "comfy-docker", "--non-interactive", "--json")

    assert result.exit_code == 9
    assert "the port of the sandbox comfy-flux" in result.json["message"]
    changed_nothing(runner)


def test_a_port_in_use_is_asked_about_in_a_terminal(cli, runner, winapi, prompter):
    winapi.port_owners[9000] = PortOwner(4120, "python.exe")
    prompter.answers = ["Use port 9001"]

    assert cli("config", "--port", "9000").exit_code == 0

    assert written(runner)["COMFY_PORT"] == "9001"


def test_the_current_port_is_not_checked_against_ourselves(cli, runner, winapi):
    winapi.port_owners[8188] = PortOwner(900, "wslrelay.exe")  # our own container
    assert cli("config", "--manager-ui", "standard", "--port", "8188").exit_code == 0


def test_a_privileged_port_is_refused(cli, runner):
    result = cli("config", "--port", "80")

    assert result.exit_code == 2
    changed_nothing(runner)


# -- isolation-weakening values are refused, with nothing changed ---------------------

REFUSED_FLAGS = [
    "--listen 0.0.0.0",
    "--listen=0.0.0.0",
    "--privileged",
    "--network host",
    "--network=host",
    "--net=host",
    "--pid=host",
    "--cap-add SYS_ADMIN",
    "--cap-add=ALL",
    "--security-opt seccomp=unconfined",
    "-v /var/run/docker.sock:/var/run/docker.sock",
    "--volume /run/docker.sock:/x",
    "--mount type=bind,src=/tmp,dst=/x",
    "--extra-model-paths-config C:\\Users\\me\\models.yaml",
    "--output-directory D:/outputs",
    "--input-directory /mnt/c/Users/me",
    "--temp-directory \\\\server\\share",
]


@pytest.mark.parametrize("flags", REFUSED_FLAGS)
def test_isolation_weakening_launch_flags_are_refused(cli, runner, home, flags):
    before = (home / ".comfyui-sandbox" / "installs.json").read_text("utf-8")

    result = cli("config", "--launch-flags", flags, "--json")

    assert result.exit_code == 2, result.stderr
    message = result.json["message"]
    assert "isolation" in message and "Nothing was changed" in message
    assert result.json["flag"] == "--launch-flags"
    changed_nothing(runner)
    assert (home / ".comfyui-sandbox" / "installs.json").read_text("utf-8") == before


@pytest.mark.parametrize(
    "path",
    ["C:\\AI\\Models", "D:/models", "/mnt/c/Users/me/models", "/mnt/d", "\\\\server\\share", "/var/run/docker.sock"],
)
def test_a_models_path_that_mounts_windows_or_the_docker_socket_is_refused(cli, runner, path):
    result = cli("config", "--models-path", path, "--json")

    assert result.exit_code == 2, result.stderr
    assert "isolation" in result.json["message"] or "absolute Linux path" in result.json["message"]
    changed_nothing(runner)


@pytest.mark.parametrize("root", ["D:\\", "D:", "D:/"])
def test_a_drive_root_as_the_models_path_is_refused_with_a_folder_suggestion(cli, runner, root):
    result = cli("config", "--models-path", root, "--json")

    assert result.exit_code == 2, result.stderr
    assert result.json["flag"] == "--models-path"
    assert "whole drive" in result.json["message"] and "~/comfyui-models" in result.json["message"]
    assert "Nothing was changed" in result.json["message"]
    changed_nothing(runner)


@pytest.mark.parametrize("root", ["D:\\", "D:", "D:/"])
def test_a_drive_root_typed_in_the_models_question_is_rejected_and_asked_again(cli, runner, prompter, root):
    prompter.answers += ["Models folder", root, "~/ai-models", "Apply changes"]

    result = cli("config")

    assert result.exit_code == 0, result.stderr
    assert "whole drive" in prompter.rejected[0][1]


def test_a_windows_models_install_refuses_a_wsl_models_path_and_changes_nothing(cli, runner, home):
    own(home, models=("windows", r"D:\models"))
    set_env(runner, ENV.replace('MODELS_DIR="/home/comfy/models"', 'MODELS_DIR="/mnt/models"'))

    result = cli("config", "--models-path", "~/ai-models", "--json")

    assert result.exit_code == 2
    assert "install it again" in result.json["message"] and "models location" in result.json["message"]
    changed_nothing(runner)
    assert record(home)["models"] == {"mode": "windows", "path": r"D:\models"}


def test_a_models_path_inside_the_project_folder_is_refused(cli, runner):
    result = cli("config", "--models-path", f"{PROJECT}/data/models")

    assert result.exit_code == 2
    changed_nothing(runner)


def test_a_refused_value_among_valid_ones_changes_nothing_at_all(cli, runner):
    result = cli("config", "--port", "9000", "--pinned-memory", "off", "--launch-flags", "--privileged")

    assert result.exit_code == 2
    changed_nothing(runner)
    assert not list(wslcalls.scripts(runner.calls))


# -- interactive ---------------------------------------------------------------------


def test_without_flags_a_terminal_asks_what_to_change_one_question_at_a_time(cli, runner, prompter):
    prompter.answers = ["Port", "9000", "Pinned memory", "off", "Apply"]

    result = cli("config")

    assert result.exit_code == 0, result.stderr
    env = written(runner)
    assert env["COMFY_PORT"] == "9000" and env["COMFY_ARGS"].endswith("--disable-pinned-memory")
    assert compose(runner, "up", "-d") == []
    assert all(kind != "confirm" for kind, _ in prompter.questions)


def test_an_interactive_refused_value_is_rejected_and_asked_again(cli, runner, prompter):
    prompter.answers = ["Launch flags", "--listen 0.0.0.0", "--lowvram --fp16-vae", "Apply"]

    result = cli("config")

    assert result.exit_code == 0, result.stderr
    assert "isolation" in prompter.rejected[0][1]
    assert written(runner)["COMFY_ARGS"] == "--enable-manager --lowvram --fp16-vae"


def test_without_flags_and_without_a_terminal_it_says_what_can_be_given(cli, runner):
    result = cli("config", "--non-interactive")

    assert result.exit_code == 2
    assert "--port" in result.stderr and "--security" in result.stderr
    changed_nothing(runner)


def test_yes_does_not_turn_a_missing_answer_into_a_change(cli, runner):
    assert cli("config", "--yes").exit_code == 2
    changed_nothing(runner)


# -- failures -------------------------------------------------------------------------


def test_a_missing_env_asks_for_an_install_first(cli, runner):
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
        returncode=1,
    )

    result = cli("config", "--port", "9000")

    assert result.exit_code == 1
    assert "install" in result.stderr
    assert wslcalls.scripts(runner.calls) == []


def test_it_only_touches_installs_we_own(cli, runner):
    result = cli("config", "--distro", "Ubuntu", "--port", "9000")

    assert result.exit_code == 1
    assert wslcalls.scripts(runner.calls) == []


def test_the_menu_explains_each_setting_with_a_hint_for_a_front_end_that_shows_one(cli, runner, prompter):
    prompter.answers = ["Apply"]

    cli("config")

    [(message, hint, hints)] = prompter.hints
    assert message == "What do you want to change?" and hint
    # Every choice is explained, Apply changes included (what applying does is the surprise).
    assert set(hints) == set(prompter.asked[0][1])
    assert all(hints.values())


def test_the_port_question_says_what_the_port_is(cli, runner, prompter):
    prompter.answers = ["Port", "9000", "Apply"]

    cli("config")

    assert [m for m, hint, _ in prompter.hints if hint] == [
        "What do you want to change?",
        "Port (1024-65535):",
        "What do you want to change?",
    ]


def test_cancelling_a_question_ends_as_interrupted_and_changes_nothing(cli, runner, prompter, home):
    prompter.answers = ["Port", "9000", CANCEL]

    result = cli("config", "--json")

    assert result.exit_code == 130 and result.json["message"] == "Interrupted."
    changed_nothing(runner)
    assert record(home)["port"] == 8188
