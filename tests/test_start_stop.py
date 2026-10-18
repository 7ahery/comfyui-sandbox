"""`comfyui-sandbox start` / `stop` (ticket 11): everything through the CLI entry point, with fakes."""

import pytest
from test_check import Machine
from test_verify import own

import wslcalls
from comfyui_sandbox.instance import instance_name

COMPOSE_STOP = wslcalls.COMPOSE_STOP


@pytest.fixture(autouse=True)
def wsl_present(runner, environ, home):
    Machine().script(runner, environ)
    own(home)


def test_start_without_a_run_window_opens_one_and_returns_when_windows_reaches_the_container(cli, winapi, http):
    result = cli("start", "--json")

    assert result.exit_code == 0, result.stderr
    assert winapi.consoles == [(r"C:\tools\bin\comfyui-sandbox.exe", "run", "--distro", "comfy-docker")]
    assert "http://127.0.0.1:8188/system_stats" in http.urls
    assert winapi.opened_urls == []  # the run window opens the browser, not start
    assert result.json["data"] == {"distro": "comfy-docker", "url": "http://localhost:8188", "already_running": False}


def test_start_keeps_waiting_while_windows_does_not_yet_reach_the_container(cli, winapi, http):
    answers = iter([None, None, '{"system": {"os": "linux"}}'])
    real_get = http.get

    def get(url, *, timeout=10):
        http.replies[8188] = next(answers)
        return real_get(url, timeout=timeout)

    http.get = get

    result = cli("start")

    assert result.exit_code == 0, result.stderr
    assert len(winapi.consoles) == 1


def test_start_with_a_run_window_already_open_only_opens_the_browser(cli, winapi, http):
    winapi.instances.add(instance_name("comfy-docker"))

    result = cli("start", "--json")

    assert result.exit_code == 0, result.stderr
    assert winapi.consoles == []
    assert winapi.opened_urls == ["http://localhost:8188"]
    assert http.urls == []
    assert result.json["data"]["already_running"] is True


def test_start_exits_10_when_windows_never_reaches_the_container(cli, winapi, http):
    http.refuse(8188)

    result = cli("start", "--json")

    assert result.exit_code == 10
    assert "'comfyui-sandbox start'" in result.json["message"]
    assert len(winapi.consoles) == 1
    assert len(http.urls) > 1


def test_start_fails_fast_when_the_run_window_died_and_points_at_the_last_transcript(cli, winapi, http):
    winapi.window_dies = True  # the window never holds the marker
    http.refuse(8188)

    result = cli("start", "--json")

    assert result.exit_code == 1
    assert "comfyui-sandbox logs --cli --last" in result.json["message"]
    assert len(http.urls) < 10  # a short grace period, not the ten minute wait


def test_start_waits_through_the_grace_period_for_a_slow_window_to_take_the_marker(cli, winapi, http):
    winapi.window_dies = True
    answers = iter([None, None, None, '{"system": {"os": "linux"}}'])
    real_get = http.get

    def get(url, *, timeout=10):
        http.replies[8188] = next(answers)
        return real_get(url, timeout=timeout)

    http.get = get

    assert cli("start").exit_code == 0  # reached before the grace period ended


def test_start_fails_at_once_when_a_windows_comfyui_holds_the_port(cli, winapi, http):
    http.answer(8188, '{"system": {"os": "win32"}}')

    result = cli("start")

    assert result.exit_code == 1
    assert len(http.urls) == 1


def test_stop_runs_compose_stop_and_leaves_the_run_window_and_marker_alone(cli, runner, winapi):
    winapi.instances.add(instance_name("comfy-docker"))

    result = cli("stop", "--json")

    assert result.exit_code == 0, result.stderr
    [stop] = [c for c in wslcalls.commands(runner.calls) if c.argv == COMPOSE_STOP]
    assert (stop.distro, stop.user) == ("comfy-docker", "comfy")
    assert winapi.instances == {instance_name("comfy-docker")}
    assert winapi.consoles == []
    assert result.json["data"] == {"distro": "comfy-docker"}


def test_stop_reports_a_failing_compose_stop(cli, runner):
    runner.on("wsl.exe", returncode=1, stderr="no configuration file provided")
    runner.on("wsl.exe", "--list", "--quiet", stdout="comfy-docker\n")

    result = cli("stop", "--json")

    assert result.exit_code == 1
    assert "no configuration file" in result.json["message"]
