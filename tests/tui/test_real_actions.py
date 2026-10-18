"""Home's first real actions: Open in browser, Start and Stop, each the command line it shows, run
in-process through the CLI's `main()` on a worker thread.

The gateway half runs on the CLI's fake `Deps` (the spec's seam B): `installs.json` in a temporary
home, the fake winapi for run windows and the browser, the fake runner for `wsl.exe`, the fake Http
for what answers on each port. The screen half drives home on that gateway with Textual's pilot
(seam A). What's asserted is what a user or a script would see: the command line shown, the one
the transcript says ran, the next poll's state, and what the screen says when it fails.
"""

from __future__ import annotations

import asyncio
import io
import re
import threading
from pathlib import Path

import pytest
from test_check import Machine
from test_real_gateway import installed

import scenarios
from comfyui_sandbox.deps import Deps
from comfyui_sandbox.instance import instance_name
from comfyui_sandbox.logs import transcripts
from comfyui_sandbox.tui.gateway.real import RealGateway
from comfyui_sandbox.tui.gateway.real.operation import RealOperation
from comfyui_sandbox.tui.home.app import SandboxApp
from comfyui_sandbox.tui.states import SandboxState

CLI = r"C:\tools\bin\comfyui-sandbox.exe"  # conftest's cli_on_path
UP = '{"system": {"os": "linux"}}'


@pytest.fixture
def deps(runner, winapi, home, environ, prompter, http) -> Deps:
    """The Deps the command line gave home: all fakes, on a machine with WSL and the distro `x`."""
    Machine(distros=["Ubuntu", "x"]).script(runner, environ)
    return Deps(
        runner=runner,
        winapi=winapi,
        home=home,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        platform="win32",
        environ=environ,
        prompter=prompter,
        http=http,
        sleep=lambda seconds: None,
    )


def compose_stop_stops_comfyui(runner, http, port: int, *, until: threading.Event | None = None) -> None:
    """`docker compose stop` in the distro takes ComfyUI down: its port stops answering. until: it
    takes until that's set (a screen test looks at the screen meanwhile)."""

    def stop(argv) -> tuple[int, str]:
        if until is not None:
            until.wait(30)
        http.refuse(port)
        return 0, ""

    runner.on_match(lambda argv: argv[-3:] == ("docker", "compose", "stop"), stop)


def run_window_serves_comfyui(http, winapi, distro: str, port: int, *, until: threading.Event | None = None) -> None:
    """ComfyUI answers on port once a run window for distro is open (it holds the marker). until:
    asking it takes until that's set, once the window is open."""
    real_get = http.get

    def get(url, *, timeout=10):
        if until is not None and instance_name(distro) in winapi.instances:
            until.wait(30)
        http.replies[port] = UP if instance_name(distro) in winapi.instances else None
        return real_get(url, timeout=timeout)

    http.get = get


def ran(deps: Deps, argv: list[str]):
    """Runs argv on the real gateway, as home does (from its event loop), and returns the gateway
    and how it ended."""
    gateway = RealGateway(deps)

    async def go():
        handle = gateway.run(argv)
        assert handle.argv == tuple(argv)
        return await handle.wait()

    return gateway, asyncio.run(go())


def ran_lines(deps: Deps) -> list[str]:
    """The command line each transcript says ran, newest first."""
    return [
        next(line for line in path.read_text("utf-8").splitlines() if line.startswith("# command: ")).removeprefix(
            "# command: "
        )
        for path in transcripts(deps.app_dir)
    ]


def state(gateway: RealGateway, name: str) -> SandboxState:
    return {s.name: s for s in asyncio.run(gateway.sandboxes())}[name].state


# -- the gateway: each action is exactly its command line ------------------------------------------


def test_stop_runs_exactly_stop_distro_x_and_the_next_poll_reads_stopped(deps, home, runner, http):
    installed(home, "x", 8188)
    compose_stop_stops_comfyui(runner, http, 8188)
    assert state(RealGateway(deps), "x") == SandboxState.RUNNING

    gateway, result = ran(deps, ["stop", "--distro", "x"])

    assert result.ok, result.message
    assert ran_lines(deps) == ["stop --distro x"]
    assert state(gateway, "x") == SandboxState.STOPPED


def test_a_failure_in_the_gateways_own_bookkeeping_after_a_command_goes_to_its_transcript(deps, home, runner, http):
    installed(home, "x", 8188)
    compose_stop_stops_comfyui(runner, http, 8188)

    def bookkeeping(result) -> None:
        raise RuntimeError("the update marks could not be refreshed")

    async def go():
        return await RealOperation(deps, ["stop", "--distro", "x"], on_end=bookkeeping).start().wait()

    result = asyncio.run(go())

    assert result.ok, result.message  # the command went as it went: the screen still learns its end
    transcript = Path(result.log).read_text("utf-8")
    assert "RuntimeError: the update marks could not be refreshed" in transcript


def test_start_runs_exactly_start_distro_x_opens_its_run_window_and_the_next_poll_reads_running(
    deps, home, winapi, http
):
    installed(home, "x", 8188)
    run_window_serves_comfyui(http, winapi, "x", 8188)
    assert state(RealGateway(deps), "x") == SandboxState.STOPPED

    gateway, result = ran(deps, ["start", "--distro", "x"])

    assert result.ok, result.message
    assert ran_lines(deps) == ["start --distro x"]
    assert winapi.consoles == [(CLI, "run", "--distro", "x")]
    assert state(gateway, "x") == SandboxState.RUNNING


def test_open_runs_exactly_open_distro_x_which_only_opens_the_browser(deps, home, winapi):
    installed(home, "x", 8188)  # running, with no run window: open mustn't give it one

    _, result = ran(deps, ["open", "--distro", "x"])

    assert result.ok, result.message
    assert winapi.opened_urls == ["http://localhost:8188"] and winapi.consoles == []


def test_every_action_writes_its_transcript_as_the_cli_would_and_reports_its_path(deps, home, runner, http):
    installed(home, "x", 8188)

    _, result = ran(deps, ["stop", "--distro", "x"])

    [log] = transcripts(deps.app_dir)
    assert log.name.endswith("-stop.log")
    assert result.log == str(log)
    assert "Stopping ComfyUI" in log.read_text("utf-8")


def test_what_the_command_says_reaches_the_operations_output_as_lines(deps, home):
    installed(home, "x", 8188)
    gateway = RealGateway(deps)

    async def go():
        handle = gateway.run(["stop", "--distro", "x"])
        await handle.wait()
        return handle.progress

    progress = asyncio.run(go())

    assert "==> Stopping ComfyUI" in progress.lines
    assert progress.lines[-1] == "ComfyUI has been stopped."  # the result message, as stdout shows it
    assert progress.plan == []  # a short action declares no plan
    assert progress.log is not None


def test_a_failing_action_ends_with_the_cores_message_its_exit_code_and_its_transcript(deps, home, runner):
    installed(home, "x", 8188)
    runner.on("wsl.exe", returncode=1, stderr="no configuration file provided")
    runner.on("wsl.exe", "--list", "--quiet", stdout="x\n")

    _, result = ran(deps, ["stop", "--distro", "x"])

    assert (result.ok, result.exit_code) == (False, 1)
    assert "docker compose stop failed" in result.message and "no configuration file" in result.message
    assert result.log == str(transcripts(deps.app_dir)[0])


def test_the_command_runs_off_the_event_loop_so_it_keeps_running_meanwhile(deps, home, runner):
    installed(home, "x", 8188)
    release = threading.Event()
    runner.on_match(
        lambda argv: argv[-3:] == ("docker", "compose", "stop"), lambda argv: (release.wait(10), (0, ""))[1]
    )
    gateway = RealGateway(deps)

    async def go() -> int:
        handle = gateway.run(["stop", "--distro", "x"])
        ticks = 0
        while handle.result is None and ticks < 20:  # the loop turns while the command is stuck
            await asyncio.sleep(0.01)
            ticks += 1
        assert handle.result is None
        release.set()
        result = await handle.wait()
        assert result.ok
        return ticks

    assert asyncio.run(go()) == 20


def test_a_cancel_interrupts_a_waiting_start_which_ends_as_interrupted(deps, home, winapi, http):
    installed(home, "x", 8188)
    http.refuse(8188)  # it never answers: start waits, a pause between each try
    gateway = RealGateway(deps)
    deps.sleep = lambda seconds: threading.Event().wait(0.01)

    async def go():
        handle = gateway.run(["start", "--distro", "x"])
        while not http.urls:  # until start is waiting: it has asked ComfyUI at least once
            await asyncio.sleep(0.01)
        handle.cancel()
        return await handle.wait()

    result = asyncio.run(go())

    assert result.interrupted and result.message == "Interrupted."


# -- the screens: home on the real gateway --------------------------------------------------------


def home_on(deps: Deps, test, size=(160, 40)) -> None:
    """Runs home on the real gateway over deps, and hands test a Rec once its picker is up."""

    async def scenario() -> None:
        app = SandboxApp(RealGateway(deps))
        async with app.run_test(size=size) as pilot:
            r = scenarios.Rec(pilot)
            await r.wait("Pick a sandbox or an action")
            await r.settle(0.6)  # the rows cascade in
            await test(r)

    asyncio.run(scenario())


def row(r: scenarios.Rec, name: str) -> str:
    """The table line of sandbox name."""
    return next(line for line in r.text.splitlines() if re.search(rf"(?:▌ |  ){re.escape(name)}\s", line))


def test_stop_shows_its_command_line_runs_it_and_the_row_reads_stopped(deps, home, runner, http):
    installed(home, "x", 8188)
    release = threading.Event()
    compose_stop_stops_comfyui(runner, http, 8188, until=release)

    async def test(r: scenarios.Rec) -> None:
        await r.row("x")
        await r.choose("Stop")
        await r.wait("Stopping the container")
        assert "comfyui-sandbox stop --distro x" in r.text
        assert "stopping" in row(r, "x")
        release.set()
        await r.wait("x stopped")
        assert "stopped" in row(r, "x")

    try:
        home_on(deps, test)
    finally:
        release.set()
    assert ran_lines(deps) == ["stop --distro x"]


def test_start_shows_its_command_line_opens_the_run_window_and_the_row_reads_running(deps, home, winapi, http):
    installed(home, "x", 8188)
    release = threading.Event()
    run_window_serves_comfyui(http, winapi, "x", 8188, until=release)

    async def test(r: scenarios.Rec) -> None:
        assert "stopped" in row(r, "x")
        await r.row("x")
        await r.choose("Start (in its own window)")
        await r.wait("Opening the run window")
        assert "comfyui-sandbox start --distro x" in r.text
        assert "starting" in row(r, "x")
        release.set()
        await r.wait("x is up at")
        assert "running" in row(r, "x")

    try:
        home_on(deps, test)
    finally:
        release.set()
    assert ran_lines(deps) == ["start --distro x"]
    assert winapi.consoles == [(CLI, "run", "--distro", "x")]


def test_open_in_browser_shows_the_command_line_it_runs_and_opens_no_run_window(deps, home, winapi):
    installed(home, "x", 8188)  # running, with no run window
    release = threading.Event()
    opened = winapi.open_url
    winapi.open_url = lambda url: (release.wait(30), opened(url))  # the browser takes a moment

    async def test(r: scenarios.Rec) -> None:
        await r.row("x")
        await r.choose("Open in browser")
        await r.wait("Opening ComfyUI in your browser")
        assert "comfyui-sandbox open --distro x" in r.text
        release.set()
        await r.wait("Opened http://localhost:8188")

    try:
        home_on(deps, test)
    finally:
        release.set()
    assert winapi.opened_urls == ["http://localhost:8188"]
    assert winapi.consoles == []


def test_open_in_browser_on_a_sandbox_that_stopped_meanwhile_says_how_to_start_it(deps, home, http):
    installed(home, "x", 8188)

    async def test(r: scenarios.Rec) -> None:
        await r.row("x")
        await r.wait("Open in browser")
        http.refuse(8188)  # it stops before the action runs (another terminal)
        await r.choose("Open in browser")
        await r.wait("ComfyUI isn't running in x")

    home_on(deps, test, size=(220, 40))


def test_a_running_sandbox_with_no_run_window_offers_stop(deps, home):
    installed(home, "x", 8188)  # its own ComfyUI answers, and no window is open

    async def test(r: scenarios.Rec) -> None:
        await r.row("x")
        await r.wait("What do you want to do with x?")
        assert "Stop, below, stops it." in r.text
        await r.choose("Stop")
        await r.wait("x stopped")

    home_on(deps, test)


def test_a_failing_action_shows_the_cores_message_and_the_transcripts_path(deps, home, runner):
    installed(home, "x", 8188)
    runner.on("wsl.exe", returncode=1, stderr="no configuration file provided")
    runner.on("wsl.exe", "--list", "--quiet", stdout="x\n")

    async def test(r: scenarios.Rec) -> None:
        await r.row("x")
        await r.choose("Stop")
        await r.wait("docker compose stop failed")
        await r.wait(transcripts(deps.app_dir)[0].name)

    home_on(deps, test, size=(220, 40))  # wide enough for the transcript's whole path


def test_home_stays_responsive_while_a_command_runs_on_its_worker_thread(deps, home, runner):
    installed(home, "x", 8188)
    release = threading.Event()
    runner.on_match(
        lambda argv: argv[-3:] == ("docker", "compose", "stop"), lambda argv: (release.wait(30), (0, ""))[1]
    )

    async def test(r: scenarios.Rec) -> None:
        await r.row("x")
        await r.choose("Stop")
        await r.wait("Stopping the container")
        before = r.text
        await r.settle(0.3)
        assert r.text != before  # the spinner and its clock keep moving while the command is stuck
        await r.press("l", settle=0.3)  # the full log opens, with what the command has said so far
        await r.wait("==> Stopping ComfyUI")
        release.set()
        await r.press("escape", settle=0.2)
        await r.wait("x stopped")

    try:
        home_on(deps, test)
    finally:
        release.set()
