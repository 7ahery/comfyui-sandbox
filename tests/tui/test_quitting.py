"""Quitting home while an operation runs (feat-tui-integration 10).

The operation handle's cancel flips the `Runner` cancel switch, which stops the `wsl.exe` call
running. Ctrl+C twice quits home during any operation, after a warning that it'll be left
unfinished; quitting cancels what's running and waits for it to unwind (its cleanup calls run)
before home exits, so nothing it started is left running.

The gateway runs on the CLI's fake `Deps` (seam B); the screens drive home on it with Textual's
pilot (seam A). The fake runner records which calls a cancel terminated and which processes were
released.
"""

from __future__ import annotations

import asyncio

from test_real_actions import deps
from test_real_gateway import installed
from test_run import compose_calls

import scenarios
import wslcalls
from comfyui_sandbox.tui.gateway.demo import DemoGateway, Scenario, seed_sandboxes
from comfyui_sandbox.tui.gateway.real import RealGateway
from comfyui_sandbox.tui.home.app import SandboxApp


def stop_holds_until_cancelled(runner) -> None:
    """`docker compose stop` runs until a cancel terminates it."""
    runner.on_match(
        lambda argv: argv[-3:] == ("docker", "compose", "stop"), lambda argv: (runner.until_terminated(), (0, ""))[1]
    )


def stop_calls(runner) -> list:
    """The `stop` command's own `docker compose stop` calls (steps.stop_argv)."""
    return [c for c in wslcalls.commands(runner.calls) if c.argv == wslcalls.COMPOSE_STOP]


def test_cancelling_an_operation_terminates_its_running_wsl_call(deps, home, runner):
    installed(home, "x", 8188)
    stop_holds_until_cancelled(runner)
    gateway = RealGateway(deps)

    async def go():
        handle = gateway.run(["stop", "--distro", "x"])
        while not stop_calls(runner):
            await asyncio.sleep(0.01)
        handle.cancel()
        return await handle.wait()

    result = asyncio.run(asyncio.wait_for(go(), 30))

    assert result.interrupted and result.message == "Interrupted."
    [stop] = stop_calls(runner)
    assert runner.terminated == [stop.call]


# -- the screens: Ctrl+C twice quits, once the operation has unwound ------------------------------


def home_on(gateway, test, size=(160, 40)) -> SandboxApp:
    """Runs home on gateway, hands test a Rec once its picker is up, and returns the app once home
    has ended (its return_code is home's exit code)."""
    app = SandboxApp(gateway)

    async def scenario() -> None:
        async with app.run_test(size=size) as pilot:
            r = scenarios.Rec(pilot)
            await r.wait("Pick a sandbox or an action")
            await r.settle(0.6)  # the rows cascade in
            await test(r)
            for _ in range(300):  # home exits on its own once the operation has unwound
                if app.return_code is not None:
                    break
                await pilot.pause(0.05)

    asyncio.run(scenario())
    return app


def test_ctrl_c_once_during_a_short_action_warns_and_a_second_press_quits_once_it_has_stopped(deps, home, runner):
    installed(home, "x", 8188)
    stop_holds_until_cancelled(runner)

    async def test(r: scenarios.Rec) -> None:
        await r.row("x")
        await r.choose("Stop")
        await r.wait("Stopping the container")
        await r.press("ctrl+c", settle=0.2)
        assert "Press Ctrl+C again to quit" in r.text and "left unfinished" in r.text
        assert runner.terminated == []  # one press only warns: the command runs on
        await r.press("ctrl+c")

    app = home_on(RealGateway(deps), test)

    assert app.return_code == 130
    [stop] = stop_calls(runner)
    assert runner.terminated == [stop.call]  # its running wsl.exe call was stopped before home ended


def test_however_home_quits_what_it_started_is_stopped_and_cleaned_up_first(deps, home, runner, http, winapi):
    """`run` starts the most: the keepalive, the container, the run-window marker. Home quits (Ctrl+C
    on its picker) while one it started holds: the run's cleanup stops the container and releases
    them all before home ends."""
    installed(home, "x", 8188)
    http.refuse(8188)
    runner.on_script("wait_ready.sh", then=runner.until_terminated)

    async def test(r: scenarios.Rec) -> None:
        r.pilot.app.gateway.run(["run", "--distro", "x"])  # started by home, with no screen showing it
        while not [s for s in wslcalls.scripts(runner.calls) if s.script == "wait_ready.sh"]:
            await r.settle(0.02)
        await r.press("ctrl+c")

    app = home_on(RealGateway(deps), test)

    assert app.return_code == 130
    assert [c.argv for c in runner.terminated] == [wslcalls.script(runner.calls, "wait_ready.sh").call.argv]
    assert len(compose_calls(runner, ("stop",))) == 1
    assert runner.started and all(p.terminated for p in runner.started)
    assert winapi.instances == set()


def test_ctrl_c_twice_during_steps_that_cant_be_cancelled_quits_once_they_have_stopped():
    """The live view's operations (an update here, on the demo gateway) offer no cancel: Ctrl+C
    warns, and its second press quits home, with the update stopped where it was."""
    gateway = DemoGateway(seed_sandboxes(False), scenario=Scenario(speed=0.2))
    started = []
    run = gateway.run
    gateway.run = lambda argv: started.append(run(argv)) or started[-1]  # ty: ignore[invalid-assignment]  # wraps run() to keep what it starts

    async def test(r: scenarios.Rec) -> None:
        await r.row("sdxl")
        await r.choose("Update… (update available)")
        await r.choose("ComfyUI (v0.3.8 → v0.3.10 available)")
        await r.choose("Newest release (now)")
        await r.wait("Update ComfyUI now?")
        await r.press("enter")
        await r.wait("Updating sdxl")
        await r.press("ctrl+c", settle=0.2)
        assert "Press Ctrl+C again to quit" in r.text
        assert started[0].result is None  # one press only warns
        await r.press("ctrl+c")

    app = home_on(gateway, test)

    assert app.return_code == 130
    assert started[0].result is not None and started[0].result.interrupted  # stopped, not left running


# -- Ctrl+C with nothing running quits as Quit does -----------------------------------------------


def test_ctrl_c_on_an_idle_home_exits_0_as_quit_does():
    app = home_on(DemoGateway(seed_sandboxes(False), scenario=Scenario(speed=4)), lambda r: r.press("ctrl+c"))

    assert app.return_code == 0


def test_ctrl_c_on_a_sandbox_menu_with_nothing_running_exits_0():
    async def test(r: scenarios.Rec) -> None:
        await r.row("comfyui")
        await r.wait("What do you want to do with comfyui?")
        await r.press("ctrl+c")

    app = home_on(DemoGateway(seed_sandboxes(False), scenario=Scenario(speed=4)), test)

    assert app.return_code == 0


# -- an install: Esc twice cancels it, Ctrl+C twice quits -----------------------------------------


def test_ctrl_c_twice_during_an_install_quits_after_the_warning_and_stops_the_install():
    gateway = DemoGateway([], scenario=Scenario(speed=0.5))
    started = []
    run = gateway.run
    gateway.run = lambda argv: started.append(run(argv)) or started[-1]  # ty: ignore[invalid-assignment]  # wraps run() to keep what it starts

    async def test(r: scenarios.Rec) -> None:
        await r.action("Install a new sandbox")
        await scenarios.through(r, "nothing has changed yet")
        await r.press("enter")  # Start
        await r.wait("Installing comfy-docker")
        await r.press("ctrl+c")
        await r.wait("Press Ctrl+C again to quit")
        assert started[0].result is None  # one press only warns: the install runs on
        await r.press("ctrl+c")

    app = home_on(gateway, test)

    assert app.return_code == 130
    assert started[0].result is not None and started[0].result.interrupted  # stopped, not left running
