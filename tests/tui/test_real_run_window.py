"""The Textual run window on the real gateway: `run --distro x` in-process, its lifecycle the core's.

The CLI's fake `Deps` play the machine (the spec's seam B): `installs.json` in a temporary home,
the fake runner for `wsl.exe` (ComfyUI's log, wait_ready.sh, watch.sh holding while ComfyUI runs,
`update comfyui`'s read-only check), the fake winapi for the run-window marker, the browser and
the console's close button, the fake Http for what answers on the port. The window is driven with
Textual's pilot (seam A). What's asserted is what someone at the window sees, and what's left
running on the machine once it has closed.
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from test_reach_versions import STATS
from test_real_actions import deps
from test_real_gateway import installed

import scenarios
import wslcalls
from comfyui_sandbox.commands.run import versions_line
from comfyui_sandbox.instance import instance_name
from comfyui_sandbox.tui.gateway.real import RealGateway
from comfyui_sandbox.tui.run_window.app import LaunchApp, run_window
from comfyui_sandbox.tui.run_window.launch import LOAD_STAGE, VERSIONS_SAID, Launch
from comfyui_sandbox.winapi import PortOwner

AS = ("wsl.exe", "-d", "x", "-u", "comfy", "--cd", "/", "--exec", "env")
PROJECT = "/home/comfy/comfyui-docker"
COMPOSE = (*AS, "env", "--chdir", PROJECT, "docker", "compose")
GIT = (*AS, "docker", "exec", "comfyui", "git", "-C", "/workspace/ComfyUI")
LOG = """\
** ComfyUI startup time: 2026-10-12 09:00:00
** Python version: 3.12.3 (main, Aug 14 2026, 17:47:21) [GCC 13.3.0]
Total VRAM 24564 MB, total RAM 65304 MB
pytorch version: 2.9.0+cu130
ComfyUI version: 0.3.9
ComfyUI frontend version: 1.28.7
Import times for custom nodes:
   0.1 seconds: /app/ComfyUI/custom_nodes/rgthree-comfy
   0.0 seconds (IMPORT FAILED): /app/ComfyUI/custom_nodes/ComfyUI_IPAdapter_plus
Starting server
"""


def comfyui_runs(runner) -> threading.Event:
    """x's ComfyUI: it logs LOG, is on release v0.3.9 with v0.3.10 out, and runs (watch.sh holds)
    until `docker compose stop`. Returns the event set once it has been stopped."""
    stopped = threading.Event()
    runner.on(*AS, "cat", f"{PROJECT}/.env", stdout='COMFY_PORT=8188\nCOMFY_INSTALL_ARGS="--nvidia --version latest"\n')
    runner.on(*COMPOSE, "ps", "--status", "running", "--quiet", stdout="3f2a9c1d7e44\n")
    runner.on(*GIT, "describe", "--tags", "--exact-match", stdout="v0.3.9\n")
    runner.on(*GIT, "ls-remote", "--tags", "--refs", "origin", stdout="1\trefs/tags/v0.3.9\n2\trefs/tags/v0.3.10\n")
    runner.on_match(lambda argv: "docker logs --follow" in argv[-1], lambda argv: (0, LOG))
    runner.on_match(lambda argv: argv[-3:] == ("docker", "compose", "stop"), lambda argv: (stopped.set(), (0, ""))[1])
    runner.on_script("wait_ready.sh", stdout="[ok] ComfyUI answers (HTTP 200) after 0 min of waiting\n")
    runner.on_script(
        "watch.sh", stdout="[ok] ComfyUI stopped (container exit code 143)\n", then=lambda: stopped.wait(30)
    )
    return stopped


def window_on(deps, test, *, size=(120, 30)) -> LaunchApp:
    """Opens x's run window on the real gateway over deps, as `comfyui-sandbox run` at a terminal
    does, and hands test a Rec of it. Returns the app, closed."""
    gateway = RealGateway(deps)
    app = LaunchApp(gateway, gateway.sandbox("x"))

    async def scenario() -> None:
        async with app.run_test(size=size) as pilot:
            await test(scenarios.Rec(pilot), app)

    asyncio.run(scenario())
    return app


def compose(runner, verb: str) -> list:
    return [
        c
        for c in wslcalls.commands(runner.calls)
        if c.argv[-len(verb.split()) :] == tuple(verb.split()) and "compose" in c.argv
    ]


def test_the_window_runs_run_for_its_sandbox_and_its_load_stage_is_one_of_runs_steps(deps, home, runner):
    installed(home, "x", 8188)
    stopped = comfyui_runs(runner)

    async def test(r: scenarios.Rec, app: LaunchApp) -> None:
        await r.wait("Running at")
        handle = app.handle
        assert handle is not None
        assert handle.argv == ("run", "--distro", "x")  # interactive: the window asks run's questions
        # The window finds ComfyUI's start-up among run's own stages by its title.
        assert LOAD_STAGE in [step.title for step in handle.progress.plan]
        stopped.set()
        await r.wait("ComfyUI has been stopped")

    window_on(deps, test)


def test_a_port_another_program_took_is_asked_about_in_the_window_and_comfyui_runs_on_the_answer(
    deps, home, runner, winapi
):
    installed(home, "x", 8188)
    stopped = comfyui_runs(runner)
    # `config --port 8190` was run while it was stopped: its next start asks for 8190, which a
    # program on Windows has taken since.
    runner.on(*AS, "cat", f"{PROJECT}/.env", stdout='COMFY_PORT=8190\nCOMFY_INSTALL_ARGS="--nvidia"\n')
    winapi.port_owners[8190] = PortOwner(4242, "python.exe")

    async def test(r: scenarios.Rec, app: LaunchApp) -> None:
        await r.wait("Port 8190 is used by python.exe (PID 4242)")
        assert "Use port 8191 instead (Recommended)" in r.text
        await r.press("enter")
        await r.wait("Running at")
        assert "127.0.0.1:8191" in r.text
        assert winapi.opened_urls == ["http://localhost:8191"]
        assert wslcalls.script(runner.calls, "write_env.sh")  # the answer is saved to .env, as at a terminal
        stopped.set()
        await r.wait("ComfyUI has been stopped")

    app = window_on(deps, test)

    assert app.ended is not None and app.ended.exit_code == 0


def test_a_pinned_sandboxs_window_says_its_pin_and_no_newer_release(deps, home, runner, winapi):
    installed(home, "x", 8188)
    stopped = comfyui_runs(runner)
    runner.on(*AS, "cat", f"{PROJECT}/.env", stdout='COMFY_PORT=8188\nCOMFY_INSTALL_ARGS="--nvidia --version 0.3.9"\n')

    async def test(r: scenarios.Rec, app: LaunchApp) -> None:
        await r.wait("Running at")
        await r.wait("Pinned to ComfyUI 0.3.9")
        assert "is available" not in r.text
        stopped.set()
        await r.wait("ComfyUI has been stopped")

    window_on(deps, test)


def test_the_window_shows_comfyuis_versions_its_failed_node_the_update_and_runs_stop_notice(deps, home, runner, winapi):
    installed(home, "x", 8188)
    stopped = comfyui_runs(runner)

    async def test(r: scenarios.Rec, app: LaunchApp) -> None:
        await r.wait("Running at")
        await r.wait("ComfyUI 0.3.10 is available")  # update comfyui's read-only check, once it's up
        text = r.text
        assert "0.3.9" in text and "1.28.7" in text and "2.9.0+cu130" in text  # ComfyUI, frontend, PyTorch
        assert "ComfyUI_IPAdapter_plus failed to import" in text
        assert "Closing this window stops ComfyUI." in text  # run's own stop notice
        assert winapi.opened_urls == ["http://localhost:8188"]
        stopped.set()
        await r.wait("ComfyUI has been stopped")
        assert "Closing this window stops ComfyUI." not in r.text

    window_on(deps, test)


def test_closing_the_console_while_the_window_is_up_stops_the_container_and_releases_the_keepalive(
    deps, home, runner, winapi
):
    installed(home, "x", 8188)
    comfyui_runs(runner)

    async def test(r: scenarios.Rec, app: LaunchApp) -> None:
        await r.wait("Running at")
        keepalive = runner.started[0]
        assert not keepalive.terminated and instance_name("x") in winapi.instances
        winapi.close_console()  # Windows calls the core's handler, with Textual owning the screen
        assert len(compose(runner, "stop")) == 1
        assert keepalive.terminated
        await r.wait("ComfyUI has been stopped")

    window_on(deps, test)
    assert len(compose(runner, "stop")) == 1  # once: run's own exit didn't stop it again
    assert all(p.terminated or p.exited is not None for p in runner.started)  # keepalive, log reader
    assert winapi.instances == set()  # the run-window marker is released


def test_a_window_that_crashes_stops_its_run_first_then_raises_for_the_cli_to_report(
    deps, home, runner, winapi, monkeypatch
):
    installed(home, "x", 8188)
    comfyui_runs(runner)
    runner.on_script("watch.sh", then=runner.until_terminated)  # ComfyUI runs until run is cancelled
    real_add = Launch.add

    def add(self, line: str) -> None:
        real_add(self, line)
        if line.startswith("ComfyUI is running at"):
            raise RuntimeError("the screen broke")

    monkeypatch.setattr(Launch, "add", add)
    gateway = RealGateway(deps)

    with pytest.raises(RuntimeError, match="the screen broke"):
        run_window(gateway, gateway.sandbox("x"), headless=True)

    assert len(compose(runner, "stop")) == 1  # run's own cleanup stopped ComfyUI before the window let go
    assert all(p.terminated or p.exited is not None for p in runner.started)
    assert winapi.instances == set()


def test_a_second_window_for_a_sandbox_whose_run_window_is_open_only_opens_the_browser(deps, home, runner, winapi):
    installed(home, "x", 8188)
    winapi.instances.add(instance_name("x"))  # the first window holds the marker

    async def test(r: scenarios.Rec, app: LaunchApp) -> None:
        await r.wait("Already running in another window")
        await r.press("enter")

    window_on(deps, test)
    assert winapi.opened_urls == ["http://localhost:8188"]
    assert compose(runner, "up -d") == [] and runner.started == []  # nothing started in the distro
    assert instance_name("x") in winapi.instances  # the first window's marker is untouched


def test_after_a_failure_the_window_stays_open_with_runs_message_and_its_log(deps, home, runner, winapi):
    installed(home, "x", 8188)
    winapi.own_console = True  # a shortcut's window: the plain one would wait for a key
    runner.on_script(
        "wait_ready.sh",
        returncode=1,
        stdout="[error] the container is exited, not running: the install or ComfyUI failed\n",
    )

    async def test(r: scenarios.Rec, app: LaunchApp) -> None:
        await r.wait("ComfyUI stopped before it was ready")
        assert "the container is exited, not running" in r.text  # run's own message
        assert "Log: " in r.text and "-run.log" in r.text
        await r.settle(0.5)
        assert app.is_running  # it stays open until it's closed
        await r.press("enter")

    window_on(deps, test)
    assert winapi.key_waits == 0  # the window waits itself; the core's key wait would fight Textual for keys
    assert len(compose(runner, "stop")) == 1 and winapi.instances == set()


def test_comfyuis_own_answer_settles_the_versions_whatever_its_log_said(deps, home, runner, winapi, http):
    installed(home, "x", 8188)
    stopped = comfyui_runs(runner)  # its log says 0.3.9 and frontend 1.28.7
    http.answer(8188, STATS)  # its /system_stats says what really runs

    async def test(r: scenarios.Rec, app: LaunchApp) -> None:
        await r.wait("2.14.1+cu130")
        text = r.text
        assert "0.40.0" in text and "1.55.17" in text and "3.12.15" in text
        assert "1.28.7" not in text  # the grid has /system_stats' frontend, not the log's
        stopped.set()
        await r.wait("ComfyUI has been stopped")

    window_on(deps, test)


def test_a_second_window_shows_the_running_comfyuis_versions(deps, home, runner, winapi, http):
    installed(home, "x", 8188)
    winapi.instances.add(instance_name("x"))  # the first window holds the marker
    http.answer(8188, STATS)

    async def test(r: scenarios.Rec, app: LaunchApp) -> None:
        await r.wait("Already running in another window")
        await r.wait("2.14.1+cu130")
        assert "0.40.0" in r.text and "1.55.17" in r.text
        await r.press("enter")

    window_on(deps, test)


def test_runs_versions_line_is_the_one_the_window_reads():
    assert VERSIONS_SAID.match(versions_line({"ComfyUI": "0.40.0", "Python": "3.12.15"}))
