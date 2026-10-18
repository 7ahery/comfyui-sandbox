"""`start`, `run` and `status` and a hand-edited `COMFY_PORT` (review-fixes-2 ticket 04).

`.env` is what the next start uses; `installs.json` holds "the port as of the last start". The commands that
start ComfyUI read `.env`, and write the record once ComfyUI answers. `status` reports the record, and says
when `.env` now differs.
"""

import json

import pytest
from test_check import Machine
from test_config import ENV, set_env
from test_verify import own

from comfyui_sandbox.instance import instance_name
from comfyui_sandbox.winapi import PortOwner

HAND_EDITED = ENV.replace("COMFY_PORT=8188", "COMFY_PORT=9000")
RUNNING = "container=running\n----- last log lines -----\nStarting server\n"


@pytest.fixture(autouse=True)
def distro(runner, environ, home):
    Machine().script(runner, environ)
    runner.on("wsl.exe", "--list", "--running", "--quiet", stdout="comfy-docker\n")
    own(home)


def record(home) -> dict:
    return json.loads((home / ".comfyui-sandbox" / "installs.json").read_text("utf-8"))["installs"][0]


def add_install(home, distro, port):
    path = home / ".comfyui-sandbox" / "installs.json"
    doc = json.loads(path.read_text("utf-8"))
    doc["installs"].append({**doc["installs"][0], "distro": distro, "port": port})
    path.write_text(json.dumps(doc), "utf-8")


def test_start_uses_the_env_port_for_the_reach_check_and_updates_the_record(cli, runner, winapi, http, home):
    set_env(runner, HAND_EDITED)

    result = cli("start", "--json")

    assert result.exit_code == 0, result.stderr
    assert result.json["data"]["url"] == "http://localhost:9000"
    assert "http://127.0.0.1:9000/system_stats" in http.urls
    assert "http://127.0.0.1:8188/system_stats" not in http.urls
    assert record(home)["port"] == 9000


def test_a_failed_start_leaves_the_records_port(cli, runner, winapi, http, home):
    set_env(runner, HAND_EDITED)
    http.refuse(9000)

    result = cli("start")

    assert result.exit_code == 10
    assert record(home)["port"] == 8188


def test_start_without_a_port_in_env_keeps_the_records_port(cli, runner, http, home):
    set_env(runner, "PUID=1001\n")

    assert cli("start").exit_code == 0

    assert "http://127.0.0.1:8188/system_stats" in http.urls
    assert record(home)["port"] == 8188


def test_start_with_a_window_already_open_opens_the_port_it_started_with(cli, runner, winapi, home):
    set_env(runner, HAND_EDITED)
    winapi.instances.add(instance_name("comfy-docker"))

    assert cli("start").exit_code == 0

    assert winapi.opened_urls == ["http://localhost:8188"]
    assert record(home)["port"] == 8188


def test_start_does_not_silently_use_a_port_another_install_holds(cli, runner, winapi, home):
    set_env(runner, HAND_EDITED)
    add_install(home, "second", 9000)

    result = cli("start", "--distro", "comfy-docker", "--non-interactive", "--json")

    assert result.exit_code == 9
    assert result.json["flag"] == "--port"
    assert "second" in result.json["message"] and "9000" in result.json["message"]
    assert winapi.consoles == []
    assert record(home)["port"] == 8188


def test_start_offers_the_next_free_port_when_another_install_holds_the_env_port(
    cli, runner, winapi, prompter, http, home
):
    set_env(runner, HAND_EDITED)
    add_install(home, "second", 9000)
    prompter.answers.append("Use port 9001 instead (Recommended)")

    result = cli("start", "--distro", "comfy-docker", "--json")

    assert result.exit_code == 0, result.stderr
    assert result.json["data"]["url"] == "http://localhost:9001"
    assert record(home)["port"] == 9001


def test_start_also_checks_that_windows_has_the_env_port_free(cli, runner, winapi, home):
    set_env(runner, HAND_EDITED)
    winapi.port_owners[9000] = PortOwner(4120, "python.exe")

    result = cli("start", "--distro", "comfy-docker", "--non-interactive", "--json")

    assert result.exit_code == 9
    assert "python.exe" in result.json["message"]


def test_run_uses_the_env_port_for_the_reach_check_and_the_browser_and_updates_the_record(
    cli, runner, winapi, http, home
):
    set_env(runner, HAND_EDITED)
    runner.on_script("watch.sh", stdout="[ok] ComfyUI stopped (container exit code 0)\n")

    result = cli("run", "--json")

    assert result.exit_code == 0, result.stderr
    assert "http://127.0.0.1:9000/system_stats" in http.urls
    assert winapi.opened_urls == ["http://localhost:9000"]
    assert result.json["data"]["url"] == "http://localhost:9000"
    assert record(home)["port"] == 9000


def test_a_run_that_never_gets_ready_leaves_the_records_port(cli, runner, winapi, http, home):
    set_env(runner, HAND_EDITED)
    runner.on_script("wait_ready.sh", returncode=2, stdout="")

    result = cli("run")

    assert result.exit_code == 10
    assert record(home)["port"] == 8188


def test_run_does_not_silently_use_a_port_another_install_holds(cli, runner, winapi, home):
    set_env(runner, HAND_EDITED)
    add_install(home, "second", 9000)

    result = cli("run", "--distro", "comfy-docker", "--non-interactive", "--json")

    assert result.exit_code == 9
    assert record(home)["port"] == 8188
    assert winapi.opened_urls == []


def test_status_says_when_env_now_holds_another_port(cli, runner, home):
    set_env(runner, HAND_EDITED)
    runner.on_script("status.sh", stdout=RUNNING)

    result = cli("status", "--json")

    assert result.exit_code == 0, result.stderr
    assert (
        "Port 8188 (in use since the last start). .env now says 9000, which takes effect the next time ComfyUI starts."
    ) in result.stderr
    data = result.json["data"]
    assert data["url"] == "http://localhost:8188"
    assert data["env_port"] == 9000


def test_status_with_matching_env_has_no_env_port_field(cli, runner):
    set_env(runner, ENV)
    runner.on_script("status.sh", stdout=RUNNING)

    result = cli("status", "--json")

    assert "env_port" not in result.json["data"]
    assert ".env now says" not in result.stderr


def test_status_with_an_unreadable_env_shows_the_record_without_a_warning(cli, runner):
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
        "/home/comfy/comfyui-docker/.env",
        returncode=1,
    )
    runner.on_script("status.sh", stdout=RUNNING)

    result = cli("status", "--json")

    assert "Port 8188 (as of the last start)" in result.stderr
    assert ".env now says" not in result.stderr
    assert "env_port" not in result.json["data"]


def test_start_then_run_after_keeping_a_busy_port_does_not_ask_again(cli, runner, winapi, prompter, http, home):
    # `start` resolves the port and opens `run` in a new window; `run` resolves it again, because the
    # record only changes once ComfyUI answers. The user closed the program, so the second pass is silent.
    set_env(runner, HAND_EDITED)
    runner.on_script("watch.sh", stdout="[ok] ComfyUI stopped (container exit code 0)\n")
    winapi.port_owners[9000] = PortOwner(4120, "python.exe")
    prompter.answers += ["Keep 9000", True]
    confirm = prompter.confirm
    prompter.confirm = lambda message, default=True: (winapi.port_owners.clear(), confirm(message, default))[1]

    started = cli("start", "--distro", "comfy-docker", "--json")
    assert started.exit_code == 0, started.stderr
    assert started.json["data"]["url"] == "http://localhost:9000"
    assert prompter.answers == []
    assert record(home)["port"] == 9000

    ran = cli("run", "--distro", "comfy-docker", "--json")

    assert ran.exit_code == 0, ran.stderr
    assert ran.json["data"]["url"] == "http://localhost:9000"
    assert len(prompter.asked) == 1


def test_run_exits_9_without_asking_when_the_port_is_busy_and_it_cannot_ask(cli, runner, winapi, prompter):
    set_env(runner, HAND_EDITED)
    winapi.port_owners[9000] = PortOwner(4120, "python.exe")

    result = cli("run", "--distro", "comfy-docker", "--yes", "--json")

    assert result.exit_code == 9
    assert prompter.asked == []
    assert winapi.opened_urls == []


def test_status_does_not_start_a_stopped_distro_to_read_env(cli, runner):
    runner.on("wsl.exe", "--list", "--running", "--quiet", stdout="")
    set_env(runner, HAND_EDITED)

    result = cli("status", "--json")

    assert "env_port" not in result.json["data"]
    assert "Port 8188 (as of the last start)" in result.stderr


# -- two installs never share a port (review-fixes-2 ticket 05) ---------------------------------------


def set_env_of(runner, distro, text):
    runner.on(
        "wsl.exe",
        "-d",
        distro,
        "-u",
        "comfy",
        "--cd",
        "/",
        "--exec",
        "env",
        "cat",
        "/home/comfy/comfyui-docker/.env",
        stdout=text,
    )


def test_config_port_refuses_the_port_of_another_install_naming_it(cli, runner, winapi, home):
    set_env(runner, ENV)
    add_install(home, "second", 9000)

    result = cli("config", "--distro", "comfy-docker", "--port", "9000", "--yes", "--non-interactive", "--json")

    assert result.exit_code == 9
    assert result.json["flag"] == "--port"
    assert "second" in result.json["message"] and "9000" in result.json["message"]
    assert record(home)["port"] == 8188


def test_another_installs_env_port_wins_over_its_record(cli, runner, winapi, home):
    set_env(runner, ENV)
    add_install(home, "second", 9000)
    set_env_of(runner, "second", ENV.replace("COMFY_PORT=8188", "COMFY_PORT=9500"))
    runner.on("wsl.exe", "--list", "--running", "--quiet", stdout="comfy-docker\nsecond\n")

    taken = cli("config", "--distro", "comfy-docker", "--port", "9500", "--yes", "--non-interactive", "--json")
    assert taken.exit_code == 9 and "second" in taken.json["message"]

    stale = cli("config", "--distro", "comfy-docker", "--port", "9000", "--yes", "--non-interactive", "--json")
    assert stale.exit_code == 0, stale.stderr


def test_install_is_offered_another_port_while_a_stopped_install_holds_the_default(cli, runner, environ, winapi, home):
    Machine(distros=["Ubuntu"]).script(runner, environ)

    result = cli("install", "--json", "--yes", "--non-interactive")

    assert result.exit_code == 9
    assert result.json["flag"] == "--port"
    assert "comfy-docker" in result.json["message"] and "8188" in result.json["message"]
    assert "8189 is free" in result.json["message"]


def test_a_stopped_install_is_never_asked_for_its_env_so_it_is_not_booted(cli, runner, winapi, home):
    set_env(runner, ENV)
    add_install(home, "second", 9000)
    set_env_of(runner, "second", ENV.replace("COMFY_PORT=8188", "COMFY_PORT=9500"))  # "second" is not running

    result = cli("config", "--distro", "comfy-docker", "--port", "9000", "--yes", "--non-interactive", "--json")

    assert result.exit_code == 9 and "second" in result.json["message"]  # its record still holds 9000
    assert not [c for c in runner.calls if "second" in c.argv]


def test_a_running_install_with_no_readable_env_holds_the_port_of_its_record(cli, runner, winapi, home):
    set_env(runner, ENV)
    add_install(home, "second", 9000)
    runner.on("wsl.exe", "--list", "--running", "--quiet", stdout="comfy-docker\nsecond\n")
    runner.on(
        "wsl.exe",
        "-d",
        "second",
        "-u",
        "comfy",
        "--cd",
        "/",
        "--exec",
        "env",
        "cat",
        "/home/comfy/comfyui-docker/.env",
        returncode=1,
        stderr="No such file",
    )

    result = cli("config", "--distro", "comfy-docker", "--port", "9000", "--yes", "--non-interactive", "--json")

    assert result.exit_code == 9
    assert "second" in result.json["message"] and "9000" in result.json["message"]


def test_install_port_flag_naming_another_installs_port_is_refused(cli, runner, environ, winapi, home):
    Machine(distros=["Ubuntu"]).script(runner, environ)

    result = cli("install", "--json", "--yes", "--non-interactive", "--port", "8188")

    assert result.exit_code == 9
    assert "comfy-docker" in result.json["message"]
