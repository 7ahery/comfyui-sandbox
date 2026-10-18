"""`comfyui-sandbox reinstall` (ticket 23; `recreate` is its hidden alias), through the CLI entry point.

The fake Runner plays wsl.exe and the commands inside the distro; no real distro is touched.
"""

import json
import re

import pytest
from test_check import Machine
from test_verify import own

import wslcalls
from fakes import CANCEL

PROJECT = "/home/comfy/comfyui-docker"
COMPOSE = ("env", "--chdir", PROJECT, "docker", "compose")
DELETE = "r" + "m"  # the wipe is the one delete command; it must target data/ and nothing else
READY = "[ok] ComfyUI answers (HTTP 200) after 12 min of waiting\n"


@pytest.fixture(autouse=True)
def distro(runner, environ, home):
    Machine().script(runner, environ)
    own(home)
    runner.on_script("wait_ready.sh", stdout=READY)


def argvs(runner):
    return [run.argv for run in wslcalls.commands(runner.calls)]


def destructive(runner):
    """Every call that stops, deletes, rebuilds or starts anything."""
    return [
        argv
        for argv in argvs(runner)
        if argv[:1] == (DELETE,) or (argv[:4] == COMPOSE and argv[4] in ("down", "build", "up"))
    ]


def test_a_confirmed_recreate_wipes_data_rebuilds_without_cache_and_waits_for_the_first_start(cli, runner):
    result = cli("reinstall", "--confirm", "comfy-docker", "--json")

    assert result.exit_code == 0, result.stderr
    calls = argvs(runner)
    order = [
        (*COMPOSE, "down"),
        (DELETE, "-rf", f"{PROJECT}/data"),
        ("mkdir", f"{PROJECT}/data"),
        ("chmod", "750", f"{PROJECT}/data"),
        (*COMPOSE, "build", "--pull", "--no-cache"),
        (*COMPOSE, "up", "-d"),
    ]
    positions = [calls.index(step) for step in order]
    assert positions == sorted(positions)
    wait = next(r for r in wslcalls.scripts(runner.calls) if r.script == "wait_ready.sh")
    assert wait.env["PROJECT_DIR"] == PROJECT
    assert wait.env["TIMEOUT_MIN"] == "60"  # the first start installs PyTorch: as long as install waits
    assert wslcalls.index(runner.calls, wait.call) > wslcalls.index(
        runner.calls, next(run.call for run in wslcalls.commands(runner.calls) if run.argv == (*COMPOSE, "up", "-d"))
    )
    assert result.json["data"]["distro"] == "comfy-docker"
    assert result.json["log"]


def test_recreate_its_first_name_still_reinstalls_but_help_no_longer_lists_it(cli, runner):
    result = cli("recreate", "--confirm", "comfy-docker", "--json")

    assert result.exit_code == 0, result.stderr
    assert result.json["message"].startswith("Reinstalled ComfyUI in comfy-docker")
    assert (DELETE, "-rf", f"{PROJECT}/data") in argvs(runner)
    listing = cli("--help").stdout
    assert "reinstall" in listing and "recreate" not in listing


def test_only_data_is_deleted_so_models_and_backups_are_untouched(cli, runner):
    assert cli("reinstall", "--confirm", "comfy-docker").exit_code == 0

    deletes = [argv for argv in argvs(runner) if argv[:1] == (DELETE,)]
    assert deletes == [(DELETE, "-rf", f"{PROJECT}/data")]
    for argv in argvs(runner):
        assert "/home/comfy/models" not in " ".join(argv)
        assert "comfyui-backups" not in " ".join(argv)


def test_the_distro_is_held_up_during_the_recreate_and_released_after(cli, runner):
    assert cli("reinstall", "--confirm", "comfy-docker").exit_code == 0

    [keepalive] = runner.started
    assert keepalive.stopped_after is not None


def test_without_confirmation_it_exits_8_and_makes_no_destructive_call(cli, runner):
    result = cli("reinstall", "--non-interactive", "--json")

    assert result.exit_code == 8
    assert "--confirm comfy-docker" in result.json["message"]
    assert destructive(runner) == []
    assert wslcalls.scripts(runner.calls) == []


def test_yes_alone_never_confirms(cli, runner):
    result = cli("reinstall", "--yes", "--non-interactive")

    assert result.exit_code == 8
    assert destructive(runner) == []


def test_a_wrong_confirmation_exits_8_and_changes_nothing(cli, runner):
    result = cli("reinstall", "--confirm", "Ubuntu")

    assert result.exit_code == 8
    assert destructive(runner) == []


@pytest.mark.parametrize("models", [f"{PROJECT}/data/models", f"{PROJECT}/data", f"{PROJECT}/data/../data/x"])
def test_it_refuses_when_the_models_are_inside_data_before_the_gate_and_any_destructive_call(cli, runner, home, models):
    own(home, models=("wsl", models))

    result = cli("reinstall", "--non-interactive", "--json")  # no confirmation: the refusal still comes first

    assert result.exit_code == 1
    assert "inside data" in result.json["message"]
    assert destructive(runner) == []
    assert argvs(runner) == [("cat", f"{PROJECT}/.env")]  # only the read of .env


def test_models_beside_data_or_on_windows_are_fine(cli, runner, home):
    own(home, models=("windows", r"D:\models"))

    assert cli("reinstall", "--confirm", "comfy-docker").exit_code == 0


def test_a_failed_build_exits_1_with_the_reason_and_does_not_wait(cli, runner):
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
        *COMPOSE,
        "build",
        returncode=1,
        stderr="failed to solve: pull access denied",
    )

    result = cli("reinstall", "--confirm", "comfy-docker", "--json")

    assert result.exit_code == 1
    assert "pull access denied" in result.json["message"]
    assert wslcalls.scripts(runner.calls) == []


def saved_answers(home, **answers):
    path = home / ".comfyui-sandbox" / "installs.json"
    doc = json.loads(path.read_text("utf-8"))
    doc["installs"][0]["answers"] = answers
    path.write_text(json.dumps(doc), "utf-8")


def test_the_manager_security_level_and_the_models_structure_come_back_after_the_first_start(cli, runner, home):
    saved_answers(home, security="weak")

    result = cli("reinstall", "--confirm", "comfy-docker")

    assert result.exit_code == 0, result.stderr
    security = wslcalls.script(runner.calls, "manager_settings.sh")
    assert (security.distro, security.user) == ("comfy-docker", "comfy")
    assert security.env == {"SECURITY_LEVEL": "weak", "PROJECT_DIR": PROJECT}
    structure = wslcalls.script(runner.calls, "models_structure.sh")
    assert structure.env == {"MODELS_DIR": "/home/comfy/models", "PROJECT_DIR": PROJECT, "CREATE": "1"}
    first, second = [r for r in wslcalls.scripts(runner.calls) if r.script == "wait_ready.sh"]
    restart = next(r for r in wslcalls.commands(runner.calls) if r.argv == (*COMPOSE, "restart"))
    order = [
        wslcalls.index(runner.calls, c) for c in (first.call, structure.call, security.call, restart.call, second.call)
    ]
    assert order == sorted(order)
    assert second.env["TIMEOUT_MIN"] == "10"


def test_a_windows_models_folder_is_only_checked_and_security_defaults_to_normal(cli, runner, home):
    own(home, models=("windows", r"D:\models"))

    assert cli("reinstall", "--confirm", "comfy-docker").exit_code == 0

    assert wslcalls.script(runner.calls, "models_structure.sh").env == {
        "MODELS_DIR": "/mnt/models",
        "PROJECT_DIR": PROJECT,
    }  # no CREATE: the person's folder is not changed
    assert wslcalls.script(runner.calls, "manager_settings.sh").env["SECURITY_LEVEL"] == "normal"


def test_a_first_start_timeout_during_recreate_says_to_run_start_not_install(cli, runner):
    runner.on_script("wait_ready.sh", returncode=2, stdout="[timeout] not ready after 60 min\n")

    result = cli("reinstall", "--confirm", "comfy-docker", "--json")

    assert result.exit_code == 10
    message = result.json["message"]
    assert "comfyui-sandbox install" not in message
    assert "'comfyui-sandbox start'" in message and "--distro" not in message


def test_the_recreate_timeout_hint_names_the_distro_when_there_are_several_installs(cli, runner, home):
    path = home / ".comfyui-sandbox" / "installs.json"
    doc = json.loads(path.read_text("utf-8"))
    doc["installs"].append({**doc["installs"][0], "distro": "comfy-two", "port": 8189})
    path.write_text(json.dumps(doc), "utf-8")
    runner.on_script("wait_ready.sh", returncode=2, stdout="[timeout] not ready after 60 min\n")

    result = cli("reinstall", "--distro", "comfy-docker", "--confirm", "comfy-docker", "--json")

    assert "'comfyui-sandbox start --distro comfy-docker'" in result.json["message"]


def test_a_recreate_that_times_out_is_continued_by_the_hinted_start(cli, runner):
    runner.on_script("wait_ready.sh", returncode=2, stdout="[timeout] not ready after 60 min\n")
    first = cli("reinstall", "--confirm", "comfy-docker", "--json")
    assert first.exit_code == 10
    [command] = re.findall(r"'comfyui-sandbox ([^']+)'", first.json["message"])

    runner.on_script("wait_ready.sh", stdout=READY)
    retry = cli(*command.split(), "--json")

    assert retry.exit_code == 0, retry.stderr


# -- recreate uses the current settings (review-fix 10) ------------------------------
MANAGER_CONFIG = f"{PROJECT}/data/ComfyUI/user/__manager/config.ini"


def script_cat(runner, path, text, returncode=0):
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
        path,
        stdout=text,
        returncode=returncode,
    )


def set_env(runner, models):
    lines = ["PUID=1001", f'MODELS_DIR="{models}"', "COMFY_PORT=8188", ""]
    script_cat(runner, f"{PROJECT}/.env", chr(10).join(lines))


@pytest.mark.parametrize("level", ["weak", "strict"])
def test_a_security_level_set_with_config_survives_the_recreate(cli, runner, home, level):
    saved_answers(home, security="normal")  # the install-time answer is older than the setting
    script_cat(runner, MANAGER_CONFIG, chr(10).join(["[default]", f"security_level = {level}", ""]))

    result = cli("reinstall", "--confirm", "comfy-docker")

    assert result.exit_code == 0, result.stderr
    assert wslcalls.script(runner.calls, "manager_settings.sh").env["SECURITY_LEVEL"] == level
    calls = argvs(runner)
    assert calls.index(("cat", MANAGER_CONFIG)) < calls.index((*COMPOSE, "down"))  # read before the wipe


def test_an_unreadable_security_level_falls_back_to_the_install_answer_and_says_so(cli, runner, home):
    saved_answers(home, security="weak")
    script_cat(runner, MANAGER_CONFIG, "", returncode=1)

    result = cli("reinstall", "--confirm", "comfy-docker")

    assert result.exit_code == 0, result.stderr
    assert wslcalls.script(runner.calls, "manager_settings.sh").env["SECURITY_LEVEL"] == "weak"
    assert "install answer" in result.stderr


def test_models_inside_data_set_only_in_env_make_it_refuse_and_delete_nothing(cli, runner):
    set_env(runner, f"{PROJECT}/data/models")

    result = cli("reinstall", "--confirm", "comfy-docker", "--json")

    assert result.exit_code == 1
    assert "inside data" in result.json["message"]
    assert destructive(runner) == []


def test_moving_the_models_out_of_data_in_env_lets_it_go_ahead(cli, runner, home):
    own(home, models=("wsl", f"{PROJECT}/data/models"))  # the record is stale
    set_env(runner, "/home/comfy/models")

    result = cli("reinstall", "--confirm", "comfy-docker")

    assert result.exit_code == 0, result.stderr
    assert (DELETE, "-rf", f"{PROJECT}/data") in argvs(runner)


def test_the_models_structure_step_uses_the_folder_from_env(cli, runner):
    set_env(runner, "/home/comfy/other-models")

    assert cli("reinstall", "--confirm", "comfy-docker").exit_code == 0

    assert wslcalls.script(runner.calls, "models_structure.sh").env == {
        "MODELS_DIR": "/home/comfy/other-models",
        "PROJECT_DIR": PROJECT,
        "CREATE": "1",
    }


def test_cancelling_the_typed_confirmation_ends_as_interrupted_and_deletes_nothing(cli, runner, prompter):
    prompter.answers = [CANCEL]

    result = cli("reinstall")

    assert result.exit_code == 130
    assert destructive(runner) == []


WIPE = (DELETE, "-rf", f"{PROJECT}/data")


@pytest.mark.parametrize(
    "failing, later",
    [
        ((*COMPOSE, "down"), [WIPE, (*COMPOSE, "build"), (*COMPOSE, "up")]),
        (WIPE, [(*COMPOSE, "build"), (*COMPOSE, "up")]),
        ((*COMPOSE, "up"), []),
    ],
    ids=["down", "wipe", "up"],
)
def test_a_step_that_fails_stops_the_recreate_before_anything_after_it(cli, runner, failing, later):
    # A container that will not stop is never followed by deleting its data/, and a failed wipe is
    # never followed by a build on what is left.
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
        *failing,
        returncode=1,
        stderr="it broke",
    )

    result = cli("reinstall", "--confirm", "comfy-docker", "--json")

    assert result.exit_code == 1
    assert "it broke" in result.json["message"]
    calls = argvs(runner)
    for argv in later:
        assert not any(call[: len(argv)] == argv for call in calls), argv
    assert wslcalls.scripts(runner.calls) == []


@pytest.mark.parametrize("name", ["reinstall", "recreate"])
def test_an_unfinished_install_is_refused_and_nothing_is_changed(cli, runner, home, name):
    path = home / ".comfyui-sandbox" / "installs.json"
    data = json.loads(path.read_text("utf-8"))
    data["installs"][0]["state"] = "in_progress"
    path.write_text(json.dumps(data), "utf-8")

    result = cli(name, "--confirm", "comfy-docker", "--json")

    assert result.exit_code != 0
    assert "unfinished" in result.json["message"]
    assert "install --distro comfy-docker" in result.json["message"]
    assert destructive(runner) == []
    assert wslcalls.scripts(runner.calls) == []
