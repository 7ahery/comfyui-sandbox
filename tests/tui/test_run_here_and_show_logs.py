"""Run here and Show logs, on screen: home on the demo gateway, driven with Textual's pilot (the
spec's seam A).

What's asserted is what the user sees and what home asked the gateway to run: the command line
shown is the one run, L shows the log full screen, leaving the page ends the command, and Run here
takes a second press to stop ComfyUI, whose own `run` then stops it (no separate `stop`).
"""

from __future__ import annotations

import asyncio

from test_home_states import home, row, until

import scenarios
from comfyui_sandbox.tui.gateway import Result, Sandbox
from comfyui_sandbox.tui.gateway.demo import DemoGateway, DemoOperation, Scenario, seed_sandboxes
from comfyui_sandbox.tui.states import SandboxState

FAST = Scenario(speed=20)


class Kept(DemoGateway):
    """A demo gateway that keeps each operation handle it gave out, to see how it ended."""

    def __init__(self, sandboxes: list[Sandbox], **kwargs) -> None:
        super().__init__(sandboxes, scenario=kwargs.pop("scenario", FAST), **kwargs)
        self.handles = []

    def run(self, argv):
        handle = super().run(argv)
        self.handles.append(handle)
        return handle


def one(state: SandboxState) -> list[Sandbox]:
    return [Sandbox("alpha", state, 8188)]


# -- Show logs ------------------------------------------------------------------------------------


def test_show_logs_shows_and_runs_exactly_logs_follow_tail_n_distro_x_and_l_shows_it_full_screen() -> None:
    gateway = Kept(one(SandboxState.RUNNING))

    async def test(r: scenarios.Rec) -> None:
        await r.row("alpha")
        await r.choose("Show logs")
        await r.wait("ComfyUI output")
        assert "comfyui-sandbox logs --follow --tail 500 --distro alpha" in r.text
        await r.wait("got prompt")  # the command's lines, on the page
        await r.press("l", settle=0.2)
        await r.wait("alpha · logs")  # full screen, titled with the command it runs
        assert "comfyui-sandbox logs --follow --tail 500 --distro alpha" in r.text
        assert "ComfyUI output" not in r.text
        await r.press("l", settle=0.2)
        await r.wait("ComfyUI output")  # back on the page, still following
        assert gateway.handles[0].result is None

    home(gateway, test)
    assert gateway.ran == [("logs", "--follow", "--tail", "500", "--distro", "alpha")]


def test_leaving_show_logs_ends_the_follow() -> None:
    gateway = Kept(one(SandboxState.RUNNING))

    async def test(r: scenarios.Rec) -> None:
        await r.row("alpha")
        await r.choose("Show logs")
        await r.wait("ComfyUI output")
        await r.press("escape", settle=0.3)
        await r.wait("What do you want to do with alpha?")
        await until(r, lambda: gateway.handles[0].result is not None)

    home(gateway, test)
    assert gateway.handles[0].result.interrupted


def test_show_logs_is_greyed_out_when_the_sandboxs_distro_isnt_running() -> None:
    gateway = Kept(one(SandboxState.STOPPED), distros_down=["alpha"])

    async def test(r: scenarios.Rec) -> None:
        await r.row("alpha")
        await r.wait("What do you want to do with alpha?")
        assert "• Show logs" in r.text and "≡ Show logs" not in r.text  # greyed out, with no reason beside it
        assert "WSL isn't running" not in r.text
        for _ in range(8):  # the pointer goes round the menu without once stopping on it
            assert "❯ ≡ Show logs" not in r.text
            await r.press("down")
        await r.press("enter", settle=0.3)  # whatever it's on, it isn't Show logs
        assert "ComfyUI output" not in r.text

    home(gateway, test)
    assert ("logs", "--follow", "--tail", "500", "--distro", "alpha") not in gateway.ran


def test_show_logs_of_a_stopped_sandbox_whose_distro_is_up_is_offered() -> None:
    gateway = Kept(one(SandboxState.STOPPED))

    async def test(r: scenarios.Rec) -> None:
        await r.row("alpha")
        await r.wait("What do you want to do with alpha?")
        assert "≡ Show logs" in r.text
        assert gateway.distro_checks == ["alpha"]  # asked when the menu opened, not on every poll

    home(gateway, test)


def test_a_running_sandboxs_menu_doesnt_ask_whether_its_distro_is_up() -> None:
    gateway = Kept(one(SandboxState.RUNNING))

    async def test(r: scenarios.Rec) -> None:
        await r.row("alpha")
        await r.wait("What do you want to do with alpha?")
        assert "≡ Show logs" in r.text

    home(gateway, test)
    assert gateway.distro_checks == []  # ComfyUI answers from inside it: it is up


def test_show_logs_of_a_stopped_sandbox_says_no_new_output_is_coming() -> None:
    gateway = Kept(one(SandboxState.STOPPED))

    async def test(r: scenarios.Rec) -> None:
        await r.row("alpha")
        await r.choose("Show logs")
        await r.wait("ComfyUI output")
        await r.wait("no new output")

    home(gateway, test)


# -- Run here -------------------------------------------------------------------------------------


def test_run_here_shows_and_runs_exactly_run_distro_x_live_over_comfyuis_output() -> None:
    gateway = Kept(one(SandboxState.STOPPED))

    async def test(r: scenarios.Rec) -> None:
        await r.row("alpha")
        await r.choose("Run here")
        await r.wait("LIVE")
        assert "comfyui-sandbox run --distro alpha" in r.text
        await r.wait("To see the GUI go to")
        assert "ComfyUI output" in r.text  # the same box as Show logs
        await r.press("l", settle=0.2)
        await r.wait("alpha · running here")  # L: full screen
        assert "ComfyUI output" not in r.text
        await r.press("l", settle=0.2)
        await r.wait("ComfyUI output")
        assert gateway.handles[0].result is None  # going full screen and back doesn't stop it

    home(gateway, test)
    assert gateway.ran == [("run", "--distro", "alpha")]


class RunHeld(Kept):
    """`run` that starts its plan and holds on its last step until `up` is set: ComfyUI still
    booting for as long as a test likes."""

    def __init__(self, sandboxes: list[Sandbox], **kwargs) -> None:
        super().__init__(sandboxes, **kwargs)
        self.up = asyncio.Event()

    def run(self, argv):
        if argv[0] != "run":
            return super().run(argv)
        self.ran.append(tuple(argv))
        op = DemoOperation(argv, speed=1)
        op.plan([("Checking the port", 0.1), ("Waiting for ComfyUI to answer", 5.0)])

        async def boot() -> Result:
            op.progress.begin(0)
            op.progress.end(0)
            op.progress.begin(1)
            op.say("Starting server")
            await self.up.wait()
            op.progress.end(1)
            op.say("ComfyUI is running at http://127.0.0.1:8188")
            while True:
                await op.nap(1)

        self.handles.append(op)
        return op.start(boot())


def test_run_here_says_comfyui_is_running_only_once_its_plan_is_through() -> None:
    gateway = RunHeld(one(SandboxState.STOPPED))

    async def test(r: scenarios.Rec) -> None:
        await r.row("alpha")
        await r.choose("Run here")
        await r.wait("Starting server")
        await r.wait("Starting ComfyUI: Waiting for ComfyUI to answer")
        assert "LIVE" not in r.text and "is running at" not in r.text
        gateway.up.set()
        await r.wait("LIVE")
        assert "Starting ComfyUI" not in r.text
        await r.wait("ComfyUI is running at")

    home(gateway, test)


def test_one_press_of_esc_only_arms_the_stop_and_the_second_stops_comfyui_through_run_itself() -> None:
    gateway = Kept(one(SandboxState.STOPPED))

    async def test(r: scenarios.Rec) -> None:
        await r.row("alpha")
        await r.choose("Run here")
        await r.wait("To see the GUI go to")
        await r.press("escape", settle=0.2)
        await r.wait("Press Esc again to stop ComfyUI.")
        assert gateway.handles[0].result is None  # one press stops nothing
        assert "running" in row(r, "alpha")
        await r.press("escape")
        await r.wait("alpha stopped")
        assert "stopped" in row(r, "alpha")

    home(gateway, test)
    # Leaving ended `run` itself, and its own cleanup stopped the container: no `stop` ran.
    assert gateway.ran == [("run", "--distro", "alpha")]
    assert gateway.handles[0].result.interrupted


def test_ctrl_c_twice_from_full_screen_stops_it_too() -> None:
    gateway = Kept(one(SandboxState.STOPPED))

    async def test(r: scenarios.Rec) -> None:
        await r.row("alpha")
        await r.choose("Run here")
        await r.wait("To see the GUI go to")
        await r.press("l", settle=0.2)
        await r.wait("alpha · running here")
        await r.press("ctrl+c", settle=0.2)
        await r.wait("press Ctrl+C again to stop")
        await r.press("ctrl+c")
        await r.wait("alpha stopped")

    home(gateway, test)
    assert gateway.handles[0].result.interrupted


class RunFails(Kept):
    """`run` ends on its own, failing, as a real one whose ComfyUI crashes does."""

    def run(self, argv):
        if argv[0] != "run":
            return super().run(argv)
        self.ran.append(tuple(argv))
        op = DemoOperation(argv, speed=1)

        async def crash() -> Result:
            await op.nap(0.5)
            op.say("Killed")
            return Result(1, "ComfyUI stopped on its own: Killed")

        self.handles.append(op)
        return op.start(crash())


def test_run_here_that_ends_by_itself_leaves_the_page_and_says_why() -> None:
    gateway = RunFails(one(SandboxState.STOPPED))

    async def test(r: scenarios.Rec) -> None:
        await r.row("alpha")
        await r.choose("Run here")
        await r.wait("Starting ComfyUI")  # it never got as far as answering
        await r.wait("ComfyUI stopped on its own: Killed")
        await r.wait("What do you want to do with alpha?")
        assert "Starting ComfyUI" not in r.text and "LIVE" not in r.text

    home(gateway, test)
    assert gateway.ran == [("run", "--distro", "alpha")]  # nothing else ran: run stopped it itself


def test_the_seeded_demo_has_a_stopped_sandbox_whose_distro_is_down() -> None:
    gateway = Kept(seed_sandboxes())

    assert asyncio.run(gateway.distro_running("sandbox-scratch")) is False
    assert asyncio.run(gateway.distro_running("comfyui-flux")) is True
