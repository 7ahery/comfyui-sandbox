"""`comfyui-sandbox run` (ticket 10): the foreground run window.

The fake Runner plays wait_ready.sh and watch.sh; the fake Http plays the Windows port; the fake
winapi holds the run-window marker and the browser. Nothing touches a real distro.
"""

import pytest
from test_check import Machine
from test_verify import ISOLATION_OUTPUT, own

import wslcalls
from comfyui_sandbox.instance import instance_name, stopping_name
from comfyui_sandbox.runner import CommandNotFound, CommandTimeout

PROJECT = "/home/comfy/comfyui-docker"
COMPOSE = ("env", "--chdir", PROJECT, "docker", "compose")
CRASHED = """\
[error] ComfyUI stopped (container exit code 137). Last log lines:
loading model flux1-dev
Killed
"""


@pytest.fixture(autouse=True)
def wsl_present(runner, environ, home):
    Machine().script(runner, environ)
    own(home)
    runner.on_script("verify.sh", stdout=ISOLATION_OUTPUT)


def compose_calls(runner, verb):
    return [c for c in wslcalls.commands(runner.calls) if c.argv == (*COMPOSE, *verb)]


def test_run_starts_waits_opens_the_browser_and_holds_until_the_container_stops(cli, runner, winapi, http):
    seen = {}

    def while_running():
        seen["marker"] = instance_name("comfy-docker") in winapi.instances
        seen["browser"] = list(winapi.opened_urls)
        seen["keepalive"] = [p.terminated for p in runner.started]
        seen["stops"] = len(compose_calls(runner, ("stop",)))

    runner.on_script("watch.sh", stdout="[ok] ComfyUI stopped (container exit code 0)\n", then=while_running)

    result = cli("run", "--json")

    assert result.exit_code == 0, result.stderr
    calls = runner.calls
    [keepalive] = runner.started
    [up] = compose_calls(runner, ("up", "-d"))
    wait = wslcalls.script(calls, "wait_ready.sh")
    watch = wslcalls.script(calls, "watch.sh")
    [stop] = compose_calls(runner, ("stop",))
    assert (up.distro, up.user) == ("comfy-docker", "comfy")
    assert (wait.user, wait.env["PROJECT_DIR"]) == ("comfy", PROJECT)
    assert (watch.user, watch.env) == ("comfy", {"PROJECT_DIR": PROJECT})
    order = [wslcalls.index(calls, x) for x in (keepalive.call, up.call, wait.call, watch.call, stop.call)]
    assert order == sorted(order)
    assert "http://127.0.0.1:8188/system_stats" in http.urls  # the Windows reach check
    # While the window holds: the marker is taken, the browser is open, the keepalive runs, nothing stopped yet.
    assert seen == {"marker": True, "browser": ["http://localhost:8188"], "keepalive": [False], "stops": 0}
    # After: stop ran, the keepalive was released, the marker is free.
    assert keepalive.terminated and keepalive.stopped_after > order[-1]
    assert winapi.instances == set()
    assert result.json["data"] == {"distro": "comfy-docker", "url": "http://localhost:8188", "ended": "stopped"}


def test_run_checks_isolation_in_the_owned_distro_before_starting_comfyui(cli, runner):
    result = cli("run", "--json")

    assert result.exit_code == 0, result.stderr
    check = wslcalls.script(runner.calls, "verify.sh")
    [up] = compose_calls(runner, ("up", "-d"))
    assert (check.distro, check.user, check.env) == ("comfy-docker", "root", {"ISOLATION_ONLY": "1"})
    assert check.call.timeout == 10
    assert wslcalls.index(runner.calls, check.call) < wslcalls.index(runner.calls, up.call)
    assert "isolation failed or could not be verified" not in result.stderr


@pytest.mark.parametrize(
    "returncode, output",
    [
        (1, "== WSL isolation ==\n[FAIL] WSL interop settings are not disabled\n"),
        (1, "== WSL isolation ==\n[FAIL] WSL interop runtime isolation could not be verified\n"),
        (1, ""),
        (0, ""),
        (0, "== WSL isolation ==\n[PASS] no Windows drive letters mounted\n"),
        (0, "not a checklist"),
        (0, ISOLATION_OUTPUT + "[WARN] unexpected Windows-backed mount\n"),
    ],
)
def test_failed_or_inconclusive_isolation_warns_before_launch_and_still_runs(cli, runner, returncode, output):
    runner.on_script("verify.sh", returncode=returncode, stdout=output)
    runner.on(*wslcalls_prefix(), *COMPOSE, "up", stdout="container started\n")

    result = cli("run", "--json")

    assert result.exit_code == 0, result.stderr
    assert result.stderr.index("Warning: Sandbox isolation") < result.stderr.index("container started")
    assert "ComfyUI will still start" in result.stderr
    assert "Run 'comfyui-sandbox verify' for details." in result.stderr
    assert compose_calls(runner, ("up", "-d"))
    assert result.json["data"]["ended"] == "stopped"


@pytest.mark.parametrize("error", [CommandTimeout, CommandNotFound])
def test_an_isolation_check_that_cannot_run_warns_and_still_launches(cli, runner, error):
    def unavailable():
        raise error("isolation probe unavailable")

    runner.on_script("verify.sh", then=unavailable)

    result = cli("run", "--json")

    assert result.exit_code == 0, result.stderr
    assert "Warning: Sandbox isolation" in result.stderr
    assert "isolation probe unavailable" in result.stderr
    assert compose_calls(runner, ("up", "-d"))


def test_cancelling_the_startup_isolation_check_does_not_launch(cli, runner):
    def cancel():
        raise KeyboardInterrupt

    runner.on_script("verify.sh", then=cancel)

    result = cli("run")

    assert result.exit_code == 130
    assert compose_calls(runner, ("up", "-d")) == []
    assert "ComfyUI will still start" not in result.stderr


def test_a_second_run_only_opens_the_browser_and_exits_0(cli, runner, winapi):
    winapi.instances.add(instance_name("comfy-docker"))

    result = cli("run", "--json")

    assert result.exit_code == 0, result.stderr
    assert winapi.opened_urls == ["http://localhost:8188"]
    assert [c for c in runner.calls if wslcalls.is_wsl(c) and "-d" in c.argv] == []  # nothing run in the distro
    assert runner.started == []
    assert result.json["data"]["already_running"] is True
    assert instance_name("comfy-docker") in winapi.instances  # the first window's marker is untouched


def test_the_marker_is_per_distro(cli, runner, winapi):
    winapi.instances.add(instance_name("other"))  # another distro's window is open

    assert cli("run").exit_code == 0

    assert wslcalls.script(runner.calls, "watch.sh")  # this distro ran: it was not "already running"
    assert winapi.instances == {instance_name("other")}  # and its own marker was taken and released


def test_ctrl_c_stops_the_container_and_releases_the_keepalive(cli, runner, winapi):
    def ctrl_c():
        raise KeyboardInterrupt

    runner.on_script("watch.sh", then=ctrl_c)

    result = cli("run")

    assert result.exit_code == 130
    assert len(compose_calls(runner, ("stop",))) == 1
    assert runner.started[0].terminated
    assert winapi.instances == set()


def test_the_stopping_marker_is_held_while_compose_stop_runs_and_released_after(cli, runner, winapi):
    seen = []

    def stopping(argv):
        seen.append(
            (stopping_name("comfy-docker") in winapi.instances, instance_name("comfy-docker") in winapi.instances)
        )
        return 0, ""

    runner.on_match(lambda argv: argv[-3:] == ("docker", "compose", "stop"), stopping)

    assert cli("run").exit_code == 0

    assert seen == [(True, True)]  # stopping while the run window is still open
    assert winapi.instances == set()


def test_closing_the_console_window_stops_the_container_once(cli, runner, winapi):
    runner.on_script("watch.sh", then=winapi.close_console)

    result = cli("run")

    assert result.exit_code == 0, result.stderr
    assert len(compose_calls(runner, ("stop",))) == 1  # the close handler stopped it; the normal exit did not repeat it
    assert runner.started[0].terminated
    assert winapi.instances == set()


def test_a_container_that_stops_on_its_own_is_reported_with_its_last_log_lines(cli, runner, winapi):
    runner.on_script("watch.sh", returncode=3, stdout=CRASHED)

    result = cli("run", "--json")

    assert result.exit_code == 1
    message = result.json["message"]
    assert "stopped on its own" in message
    assert "exit code 137" in message and "loading model flux1-dev" in message and "Killed" in message
    assert "Killed" in result.stderr  # shown in the window too
    assert len(compose_calls(runner, ("stop",))) == 1
    assert runner.started[0].terminated
    assert winapi.instances == set()


def test_a_crash_in_a_run_window_that_owns_its_console_waits_for_a_key_after_showing_the_log(cli, runner, winapi):
    winapi.own_console = True
    runner.on_script("watch.sh", returncode=3, stdout=CRASHED)

    result = cli("run")

    assert result.exit_code == 1
    assert winapi.key_waits == 1
    assert result.stderr.index("Killed") < result.stderr.index("Press any key to close this window.")
    assert len(compose_calls(runner, ("stop",))) == 1  # ComfyUI was stopped before the wait, not after


def test_a_crash_in_the_users_own_terminal_never_waits_for_a_key(cli, runner, winapi):
    winapi.own_console = False
    runner.on_script("watch.sh", returncode=3, stdout=CRASHED)

    result = cli("run")

    assert result.exit_code == 1
    assert winapi.key_waits == 0
    assert "Press any key" not in result.stderr


def test_a_normal_stop_closes_the_run_window_without_waiting_for_a_key(cli, runner, winapi):
    winapi.own_console = True
    runner.on_script("watch.sh", stdout="[ok] ComfyUI stopped (container exit code 0)\n")

    result = cli("run")

    assert result.exit_code == 0
    assert winapi.key_waits == 0


def test_ctrl_c_and_closing_the_window_never_wait_for_a_key(cli, runner, winapi):
    winapi.own_console = True

    def ctrl_c():
        raise KeyboardInterrupt

    runner.on_script("watch.sh", then=ctrl_c)
    assert cli("run").exit_code == 130
    runner.on_script("watch.sh", then=winapi.close_console)
    assert cli("run").exit_code == 0

    assert winapi.key_waits == 0


def test_an_unexpected_exception_in_a_run_window_shows_its_message_logs_it_and_waits_for_a_key(
    cli, runner, winapi, home
):
    winapi.own_console = True

    def boom():
        raise RuntimeError("disk on fire")

    runner.on_script("watch.sh", then=boom)

    result = cli("run")

    assert result.exit_code == 1
    assert winapi.key_waits == 1
    assert result.stderr.index("RuntimeError: disk on fire") < result.stderr.index(
        "Press any key to close this window."
    )
    assert len(compose_calls(runner, ("stop",))) == 1
    [path] = sorted((home / ".comfyui-sandbox" / "logs").glob("*.log"))
    assert "RuntimeError: disk on fire" in path.read_text("utf-8")


def test_a_run_that_fails_before_it_is_ready_also_waits_in_its_own_window(cli, runner, winapi):
    winapi.own_console = True
    runner.on_script(
        "wait_ready.sh", returncode=1, stdout="[error] the container is exited, not running: x. Last log lines:\nboom\n"
    )

    assert cli("run").exit_code == 1
    assert winapi.key_waits == 1


def test_a_wsl_session_that_ends_without_a_report_is_not_called_a_crash(cli, runner):
    runner.on_script("watch.sh", returncode=1)

    result = cli("run", "--json")

    assert result.exit_code == 1
    assert "WSL session ended" in result.json["message"]


def test_a_container_that_stops_before_it_is_ready_is_reported(cli, runner, winapi):
    runner.on_script(
        "wait_ready.sh", returncode=1, stdout="[error] the container is exited, not running: x. Last log lines:\nboom\n"
    )

    result = cli("run", "--json")

    assert result.exit_code == 1
    assert "boom" in result.json["message"]
    assert winapi.opened_urls == []
    assert [s.script for s in wslcalls.scripts(runner.calls)] == ["verify.sh", "wait_ready.sh"]  # never got to watch
    assert len(compose_calls(runner, ("stop",))) == 1


def test_a_container_that_is_not_ready_in_time_exits_10(cli, runner, winapi):
    runner.on_script("wait_ready.sh", returncode=2, stdout="[timeout] not ready after 10 min\n")

    result = cli("run")

    assert result.exit_code == 10
    assert winapi.opened_urls == []
    assert len(compose_calls(runner, ("stop",))) == 1


def add_second_install(home):
    import json

    path = home / ".comfyui-sandbox" / "installs.json"
    doc = json.loads(path.read_text("utf-8"))
    doc["installs"].append({**doc["installs"][0], "distro": "comfy-two", "port": 8189})
    path.write_text(json.dumps(doc), "utf-8")


def test_the_ready_timeout_hint_does_not_mention_install(cli, runner, winapi):
    runner.on_script("wait_ready.sh", returncode=2, stdout="[timeout] not ready after 10 min\n")

    result = cli("run", "--json")

    message = result.json["message"]
    assert "comfyui-sandbox install" not in message
    assert "'comfyui-sandbox run'" in message and "--distro" not in message


def test_the_ready_timeout_hint_names_the_distro_when_there_are_several_installs(cli, runner, winapi, home):
    add_second_install(home)
    runner.on_script("wait_ready.sh", returncode=2, stdout="[timeout] not ready after 10 min\n")

    result = cli("run", "--distro", "comfy-docker", "--json")

    assert "comfyui-sandbox install" not in result.json["message"]
    assert "'comfyui-sandbox run --distro comfy-docker'" in result.json["message"]


def test_a_comfyui_on_windows_holding_the_port_is_refused_before_the_browser_opens(cli, runner, http, winapi):
    http.answer(8188, '{"system": {"os": "win32"}}')

    result = cli("run", "--json")

    assert result.exit_code == 1
    assert "ComfyUI running on Windows" in result.json["message"]
    assert winapi.opened_urls == []
    assert len(compose_calls(runner, ("stop",))) == 1
    assert runner.started[0].terminated


def test_a_container_windows_cannot_reach_fails_after_retries(cli, http, winapi):
    http.refuse(8188)

    result = cli("run", "--json")

    assert result.exit_code == 1
    assert "Nothing answers" in result.json["message"]
    assert len(http.urls) > 1  # forwarding can lag behind the container: it looked more than once
    assert winapi.opened_urls == []


def test_a_failed_compose_up_stops_and_reports(cli, runner, winapi):
    runner.on(*wslcalls_prefix(), *COMPOSE, "up", returncode=1)

    result = cli("run", "--json")

    assert result.exit_code == 1
    assert "compose up" in result.json["message"]
    assert [script.script for script in wslcalls.scripts(runner.calls)] == ["verify.sh"]
    assert winapi.instances == set()


def wslcalls_prefix():
    return ("wsl.exe", "-d", "comfy-docker", "-u", "comfy", "--cd", "/", "--exec", "env")


def test_the_port_comes_from_the_install(cli, home, http, winapi):
    own(home, port=8190)

    assert cli("run").exit_code == 0

    assert winapi.opened_urls == ["http://localhost:8190"]
    assert http.urls[0] == "http://127.0.0.1:8190/system_stats"


def test_run_writes_a_transcript(cli, home):
    result = cli("run", "--json")

    assert result.json["log"].endswith("-run.log")


# -- the real run-window marker ----------------------------------------------


@pytest.mark.windows_only
def test_the_real_marker_allows_one_holder_per_name_until_released():
    from comfyui_sandbox.winapi import WindowsApi

    api = WindowsApi()
    name = instance_name("pytest-marker-test")
    assert api.instance_exists(name) is False
    first = api.acquire_instance(name)
    try:
        assert first is not None
        assert api.instance_exists(name) is True
        assert api.acquire_instance(name) is None
    finally:
        first.release()
    assert api.instance_exists(name) is False
    again = api.acquire_instance(name)
    assert again is not None
    again.release()
