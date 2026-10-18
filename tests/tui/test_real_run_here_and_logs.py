"""Run here and Show logs on the real gateway: `run` and `logs --follow` run in-process, each the
command line home shows, and leaving the page ends them.

The CLI's fake `Deps` play the machine (the spec's seam B): `installs.json` in a temporary home,
the fake runner for `wsl.exe` (a streamed call that lasts until it's terminated stands for
`docker compose logs --follow`, and for watch.sh while ComfyUI runs), the fake winapi for the
run-window marker, the fake Http for what answers on the port. What's asserted is what a user or
a script would see: the command line that ran, the lines that reached the screen's output, that
no process is left running, and what the next poll reads.
"""

from __future__ import annotations

import asyncio
import time

from test_real_actions import (
    UP,
    compose_stop_stops_comfyui,
    deps,
    home_on,
    ran_lines,
    row,
    state,
)
from test_real_gateway import installed

import scenarios
import wslcalls
from comfyui_sandbox.instance import instance_name
from comfyui_sandbox.tui.gateway import OperationHandle
from comfyui_sandbox.tui.gateway.real import RealGateway
from comfyui_sandbox.tui.states import SandboxState
from fakes import FakeLauncher

COMPOSE = ("env", "--chdir", "/home/comfy/comfyui-docker", "docker", "compose")
SHOW_LOGS = ["logs", "--follow", "--tail", "500", "--distro", "x"]
RUN_HERE = ["run", "--distro", "x"]


async def leave_once(gateway: RealGateway, argv: list[str], *, until) -> OperationHandle:
    """Runs argv on the gateway as home does, then, once until(handle) holds, leaves its page: the
    handle's cancel. Returns the handle, ended. Waits on until() rather than for a while: under
    load a fixed pause can end before the command got where the test means to leave it."""
    handle = gateway.run(argv)
    deadline = time.monotonic() + 30
    while not until(handle):
        assert time.monotonic() < deadline, f"never got there: {handle.progress.lines}"
        await asyncio.sleep(0.01)
    handle.cancel()
    await asyncio.wait_for(handle.wait(), 10)
    return handle


def following(runner) -> bool:
    """Whether `docker compose logs --follow` has started in the distro."""
    return any(call.argv[-1] == "--follow" for call in runner.calls)


def nothing_left_running(runner) -> bool:
    """Every process the command started has ended: the keepalive was released, and a call held
    until it's cancelled (FakeRunner.until_terminated) only returns once its wsl.exe was ended."""
    return all(process.terminated or process.exited is not None for process in runner.started)


# -- Show logs --------------------------------------------------------------------------------------


def test_show_logs_runs_exactly_its_command_line_and_its_lines_reach_the_output(deps, home, runner):
    installed(home, "x", 8188)
    runner.on("wsl.exe", "--list", "--running", "--quiet", stdout="x\n")  # its distro is up: logs never starts one
    runner.on_match(
        lambda argv: "logs" in argv and "--follow" in argv,
        lambda argv: (0, "comfyui-1  | Starting server\ncomfyui-1  | To see the GUI go to: http://0.0.0.0:8188\n"),
    )

    async def go():
        handle = RealGateway(deps).run(SHOW_LOGS)
        return handle, await handle.wait()

    handle, ended = asyncio.run(go())

    assert ended.ok, ended.message
    assert handle.argv == tuple(SHOW_LOGS)
    assert handle.progress.lines[:2] == [
        "comfyui-1  | Starting server",
        "comfyui-1  | To see the GUI go to: http://0.0.0.0:8188",
    ]
    [follow] = [c for c in wslcalls.commands(runner.calls) if "logs" in c.argv]
    assert (follow.distro, follow.argv) == ("x", (*COMPOSE, "logs", "--no-color", "--tail", "500", "--follow"))


def test_leaving_show_logs_ends_the_follow_and_leaves_no_child_process_running(deps, home, runner):
    installed(home, "x", 8188)
    runner.on("wsl.exe", "--list", "--running", "--quiet", stdout="x\n")  # its distro is up: logs never starts one
    runner.on_match(lambda argv: "logs" in argv and "--follow" in argv, lambda argv: (0, ""))
    runner.on("wsl.exe", "-d", "x", then=runner.until_terminated)  # the follow runs until it's ended
    runner.on("wsl.exe", "--list", "--quiet", stdout="Ubuntu\nx\n")

    handle = asyncio.run(leave_once(RealGateway(deps), SHOW_LOGS, until=lambda h: following(runner)))

    assert handle.result is not None
    assert handle.result.exit_code in (0, 130)  # `logs --follow` ends quietly when it's interrupted
    assert [c.argv[-1] for c in runner.terminated] == ["--follow"]  # the follow's wsl.exe was ended
    assert nothing_left_running(runner)


# -- Run here -------------------------------------------------------------------------------------


def watch_holds(runner) -> None:
    """watch.sh holds while ComfyUI runs, until it's terminated."""
    runner.on_script("watch.sh", then=runner.until_terminated)


def test_run_here_runs_exactly_the_run_command_line_it_shows(deps, home, runner, http):
    installed(home, "x", 8188)
    runner.on_script("watch.sh", stdout="[ok] ComfyUI stopped (container exit code 0)\n")

    async def go():
        handle = RealGateway(deps).run(RUN_HERE)
        return handle, await handle.wait()

    handle, ended = asyncio.run(go())

    assert ended.ok, ended.message
    assert handle.argv == tuple(RUN_HERE)
    assert ran_lines(deps) == ["run --distro x"]
    assert "ComfyUI is running at http://localhost:8188" in handle.progress.lines


def test_leaving_run_here_stops_the_container_releases_the_keepalive_and_the_next_poll_reads_stopped(
    deps, home, runner, http, winapi
):
    installed(home, "x", 8188)
    watch_holds(runner)
    compose_stop_stops_comfyui(runner, http, 8188)
    gateway = RealGateway(deps)

    handle = asyncio.run(
        leave_once(gateway, RUN_HERE, until=lambda h: "Close this window to stop ComfyUI." in h.progress.lines)
    )

    assert handle.result is not None and handle.result.interrupted  # ended by the user, through `run`'s own lifecycle
    stops = [c for c in wslcalls.commands(runner.calls) if c.argv == (*COMPOSE, "stop")]
    assert len(stops) == 1
    keepalive = runner.started[0]  # (the other: the reader of ComfyUI's log Run here shows)
    assert keepalive.terminated
    assert winapi.instances == set()  # the run-window marker is released
    assert nothing_left_running(runner)
    assert state(gateway, "x") == SandboxState.STOPPED


def test_leaving_run_here_while_comfyui_is_still_starting_stops_it_too(deps, home, runner, http, winapi):
    installed(home, "x", 8188)
    runner.on_script("wait_ready.sh", then=runner.until_terminated)  # ComfyUI takes its time
    compose_stop_stops_comfyui(runner, http, 8188)

    handle = asyncio.run(
        leave_once(
            RealGateway(deps), RUN_HERE, until=lambda h: "==> [2/3] Waiting for ComfyUI to answer" in h.progress.lines
        )
    )

    assert handle.result is not None and handle.result.interrupted
    assert [c for c in wslcalls.commands(runner.calls) if c.argv == (*COMPOSE, "stop")]
    assert "watch.sh" not in [run.script for run in wslcalls.scripts(runner.calls)]  # it never got that far
    assert nothing_left_running(runner) and winapi.instances == set()


def test_run_here_doesnt_open_the_run_window_a_terminal_would(deps, home, runner):
    installed(home, "x", 8188)
    runner.on_script("watch.sh", stdout="[ok] ComfyUI stopped (container exit code 0)\n")
    launcher = FakeLauncher()
    deps.launch, deps.stdin_is_terminal, deps.stdout_is_terminal = launcher, True, True

    async def go():
        return await RealGateway(deps).run(RUN_HERE).wait()

    assert asyncio.run(go()).ok
    assert launcher.opened == []  # no nested front end: home is the front end


def test_a_failing_run_here_doesnt_wait_for_a_key_as_a_window_of_its_own_would(deps, home, runner, winapi):
    installed(home, "x", 8188)
    winapi.own_console = True  # home was opened in a console of its own (double-clicked)
    runner.on_script("wait_ready.sh", returncode=1, stdout="[error] the container is exited, not running\n")

    async def go():
        return await RealGateway(deps).run(RUN_HERE).wait()

    ended = asyncio.run(go())

    assert not ended.ok and "stopped before it was ready" in ended.message
    assert winapi.key_waits == 0  # home stays open and shows the error itself
    assert instance_name("x") not in winapi.instances


# -- on screen: home on the real gateway ------------------------------------------------------------


def test_run_here_in_home_runs_run_and_leaving_it_stops_comfyui_and_the_row_reads_stopped(
    deps, home, runner, http, winapi
):
    installed(home, "x", 8188)
    http.refuse(8188)  # stopped: Run here is on its menu
    runner.on_match(
        lambda argv: argv[-3:] == ("compose", "up", "-d"), lambda argv: (http.answer(8188, UP), (0, ""))[1]
    )  # ComfyUI answers once it's up
    watch_holds(runner)
    compose_stop_stops_comfyui(runner, http, 8188)

    async def test(r: scenarios.Rec) -> None:
        await r.row("x")
        await r.choose("Run here")
        await r.wait("LIVE")
        assert "comfyui-sandbox run --distro x" in r.text
        await r.wait("ComfyUI is running at http://localhost:8188")  # run's own lines, in the box
        await r.press("escape", settle=0.2)
        await r.press("escape")
        await r.wait("x stopped")
        assert "stopped" in row(r, "x")

    home_on(deps, test)
    assert ran_lines(deps) == ["run --distro x"]  # no separate stop: run's own cleanup stopped it
    assert nothing_left_running(runner) and winapi.instances == set()
