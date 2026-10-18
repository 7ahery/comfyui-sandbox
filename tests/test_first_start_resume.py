"""A first start that times out is continued by the command the message names (review-fixes-2, ticket 07).

The setup's downloads persist in ./data (bind-mounted as /workspace), so a restart picks them up
instead of downloading PyTorch again. The container is stopped after a timeout; the hint says what continues.
"""

import json
import re
from pathlib import Path

from test_check import Machine
from test_install import install, installs_json, machine
from test_verify import own

import wslcalls

ASSETS = Path(__file__).resolve().parent.parent / "src" / "comfyui_sandbox" / "docker"
TIMEOUT = "[timeout] not ready after 60 min (HTTP none, phase 3/6).\n"
READY = "[ok] ComfyUI answers (HTTP 200) after 12 min of waiting\n"


def hinted(message: str) -> list[str]:
    """The arguments of the command the message tells the person to run."""
    [command] = re.findall(r"'comfyui-sandbox ([^']+)'", message)
    return command.split()


def add_second_install(home, state="complete"):
    path = home / ".comfyui-sandbox" / "installs.json"
    doc = json.loads(path.read_text("utf-8"))
    doc["installs"].append({**doc["installs"][0], "distro": "comfy-two", "port": 8189, "state": state})
    path.write_text(json.dumps(doc), "utf-8")


def test_an_install_that_times_out_names_the_command_that_continues_and_it_does(install, cli, runner, home):
    runner.on_script("wait_ready.sh", returncode=2, stdout=TIMEOUT)
    first = install("--yes")
    assert first.exit_code == 10
    assert hinted(first.json["message"]) == ["install"]
    assert [i["state"] for i in installs_json(home)["installs"]] == ["in_progress"]

    runner.on_script("wait_ready.sh", stdout=READY)
    retry = cli(*hinted(first.json["message"]), "--json")

    assert retry.exit_code == 0, retry.stderr
    assert retry.json["data"]["resumed"] is True
    assert [i["state"] for i in installs_json(home)["installs"]] == ["complete"]


def test_the_install_hint_names_the_distro_when_there_are_several_installs(install, cli, runner, home):
    runner.on_script("wait_ready.sh", returncode=2, stdout=TIMEOUT)
    assert install("--yes").exit_code == 10
    add_second_install(home)

    second = cli("install", "--distro", "comfy-docker", "--json")

    assert second.exit_code == 10
    assert hinted(second.json["message"]) == ["install", "--distro", "comfy-docker"]


def test_a_run_that_times_out_is_continued_by_the_hinted_run(cli, runner, winapi, home, environ):
    Machine().script(runner, environ)
    own(home)
    runner.on_script("wait_ready.sh", returncode=2, stdout="[timeout] not ready after 10 min\n")
    first = cli("run", "--json")
    assert first.exit_code == 10
    assert hinted(first.json["message"]) == ["run"]

    runner.on_script("wait_ready.sh", stdout=READY)
    retry = cli(*hinted(first.json["message"]), "--json")

    assert retry.exit_code == 0, retry.stderr
    assert len([s for s in wslcalls.scripts(runner.calls) if s.script == "wait_ready.sh"]) == 2


COMPOSE_STOP = wslcalls.COMPOSE_STOP


def compose_stops(runner) -> list:
    return [c for c in wslcalls.commands(runner.calls) if c.argv == COMPOSE_STOP]


def test_after_an_install_times_out_stop_leaves_nothing_running(install, cli, runner):
    runner.on_script("wait_ready.sh", returncode=2, stdout=TIMEOUT)
    assert install("--yes").exit_code == 10
    assert runner.started and all(p.terminated for p in runner.started)  # no keepalive holds the distro
    calls_before = len(runner.calls)

    result = cli("stop", "--json")

    assert result.exit_code == 0, result.stderr
    [stop] = [c for c in compose_stops(runner) if wslcalls.index(runner.calls, c.call) >= calls_before]
    assert (stop.distro, stop.user) == ("comfy-docker", "comfy")  # the container the first start left running
    assert all(p.terminated for p in runner.started)
    assert len(runner.started) == 1  # stop starts no keepalive of its own


def test_after_a_run_times_out_stop_leaves_nothing_running(cli, runner, winapi, home, environ):
    Machine().script(runner, environ)
    own(home)
    runner.on_script("wait_ready.sh", returncode=2, stdout="[timeout] not ready after 10 min\n")
    assert cli("run", "--json").exit_code == 10
    assert runner.started and all(p.terminated for p in runner.started)
    calls_before = len(runner.calls)

    result = cli("stop", "--json")

    assert result.exit_code == 0, result.stderr
    assert [c for c in compose_stops(runner) if wslcalls.index(runner.calls, c.call) >= calls_before]
    assert all(p.terminated for p in runner.started)
    assert len(runner.started) == 1


def test_a_start_that_is_not_reachable_names_the_distro_when_there_are_several_installs(
    cli, runner, winapi, http, home, environ
):
    Machine().script(runner, environ)
    own(home)
    http.refuse(8188)
    add_second_install(home)

    result = cli("start", "--distro", "comfy-docker", "--json")

    assert result.exit_code == 10
    assert hinted(result.json["message"]) == ["start", "--distro", "comfy-docker"]


def test_the_hint_says_the_downloads_are_kept(install, runner):
    runner.on_script("wait_ready.sh", returncode=2, stdout=TIMEOUT)

    message = install("--yes").json["message"]

    assert "downloaded packages are kept" in message
    assert "carries on" not in message  # the setup itself runs again from its start


# -- the downloads persist (src/comfyui_sandbox/docker) --------------------------------


def dockerfile_env() -> dict[str, str]:
    text = (ASSETS / "Dockerfile").read_text("utf-8")
    found = re.search(r"^ENV (.*?)\n\n", text, re.S | re.M)
    assert found is not None
    return dict(re.findall(r"(\w+)=(\S+)", found.group(1).replace("\\\n", " ")))


def test_the_pip_and_uv_caches_live_under_the_bind_mounted_workspace():
    env = dockerfile_env()

    assert env["PIP_CACHE_DIR"].startswith("/workspace/")
    assert env["UV_CACHE_DIR"].startswith("/workspace/")
    assert "./data:/workspace" in (ASSETS / "docker-compose.yml").read_text("utf-8")


def test_nothing_turns_the_download_caches_off():
    text = (ASSETS / "Dockerfile").read_text("utf-8") + (ASSETS / "entrypoint.sh").read_text("utf-8")

    assert "NO_CACHE" not in text and "--no-cache-dir" not in text and "pip cache purge" not in text


def test_the_install_marker_is_still_written_only_after_the_install_succeeds():
    entry = (ASSETS / "entrypoint.sh").read_text("utf-8")

    assert entry.index("install $INSTALL_ARGS") < entry.index('touch "$DONE"')
