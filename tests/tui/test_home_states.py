"""Home keeps up with the sandboxes on its own, shows their notes, and offers only what its gateway
can really do (the spec's seam A: the screens on a gateway, driven with Textual's pilot).

"Another terminal" is played by changing what the gateway lists while home waits on its picker:
nothing is pressed, so whatever changes on screen came from home's own polling.
"""

from __future__ import annotations

import asyncio
import copy
import re

import scenarios
from comfyui_sandbox.tui.gateway import Sandbox
from comfyui_sandbox.tui.gateway.demo import DemoGateway, Scenario
from comfyui_sandbox.tui.home.app import SandboxApp
from comfyui_sandbox.tui.states import SandboxState

FAST = Scenario(speed=20)
POLL_WAIT = 8  # seconds: "every few seconds", with room for a slow machine


class Elsewhere(DemoGateway):
    """A demo gateway whose sandboxes the test changes behind home's back, as commands run in
    another terminal would: `world` is what it lists."""

    def __init__(self, world: list[Sandbox], **kwargs) -> None:
        super().__init__(world, scenario=FAST, **kwargs)
        self.world = world
        self.reads = 0

    async def sandboxes(self) -> list[Sandbox]:
        self.reads += 1
        return [copy.copy(s) for s in self.world]


def home(gateway, test, size=(160, 40)) -> None:
    """Runs home on gateway and hands test a Rec once its picker is up."""

    async def scenario() -> None:
        app = SandboxApp(gateway)
        async with app.run_test(size=size) as pilot:
            r = scenarios.Rec(pilot)
            await r.wait("Pick a sandbox or an action")
            await r.settle(0.6)  # the rows cascade in
            await test(r)

    asyncio.run(scenario())


def row(r: scenarios.Rec, name: str) -> str:
    """The table line of sandbox name."""
    lines = [line for line in r.text.splitlines() if re.search(rf"(?:▌ |  ){re.escape(name)}\s", line)]
    assert lines, f"no row {name!r}:\n{r.text}"
    return lines[0]


async def until(r: scenarios.Rec, check, timeout: float = POLL_WAIT) -> None:
    """Waits, pressing nothing, until check() holds (a check that fails an assert hasn't held yet)."""
    for _ in range(int(timeout / 0.1)):
        try:
            if check():
                return
        except AssertionError:
            pass
        await r.settle(0.1)
    raise AssertionError(f"never happened:\n{r.text}")


# -- polling ------------------------------------------------------------------------------------


def test_a_sandbox_started_and_stopped_elsewhere_changes_state_on_home_without_a_key() -> None:
    world = [Sandbox("alpha", SandboxState.STOPPED, 8188), Sandbox("beta", SandboxState.STOPPED, 8189)]
    gateway = Elsewhere(world)

    async def test(r: scenarios.Rec) -> None:
        assert "stopped" in row(r, "beta")
        world[1] = Sandbox("beta", SandboxState.STARTING, 8189)  # `start --distro beta` in another terminal
        await until(r, lambda: "starting" in row(r, "beta"))
        world[1] = Sandbox("beta", SandboxState.RUNNING, 8189)
        await until(r, lambda: "running" in row(r, "beta"))
        world[1] = Sandbox("beta", SandboxState.STOPPED, 8189)  # `stop --distro beta`
        await until(r, lambda: "stopped" in row(r, "beta"))
        assert "stopped" in row(r, "alpha")

    home(gateway, test)


def test_a_sandbox_installed_or_removed_elsewhere_comes_and_goes_and_can_be_opened() -> None:
    world = [Sandbox("alpha", SandboxState.STOPPED, 8188)]
    gateway = Elsewhere(world)

    async def test(r: scenarios.Rec) -> None:
        world.append(Sandbox("gamma", SandboxState.INCOMPLETE, 8190))
        await until(r, lambda: "gamma" in r.text)
        await r.row("gamma")  # the picker has it as a row, not only the table
        await r.wait("What do you want to do with gamma?")
        await r.choose("Back")
        await r.wait("Pick a sandbox or an action")
        del world[0]
        await until(r, lambda: "alpha" not in r.text and "gamma" in r.text)  # gone, not just not drawn yet
        await r.row("gamma")  # the pointer still finds its way among what's left
        await r.wait("What do you want to do with gamma?")

    home(gateway, test)


def test_home_keeps_polling_while_idle_and_reads_again_after_an_action() -> None:
    gateway = Elsewhere([Sandbox("alpha", SandboxState.RUNNING, 8188)])

    async def test(r: scenarios.Rec) -> None:
        first = gateway.reads
        await until(r, lambda: gateway.reads >= first + 2)  # a couple of polls, nothing pressed
        await r.row("alpha")
        before = gateway.reads
        await r.choose("Stop")
        await r.wait("alpha stopped")
        assert gateway.reads > before

    home(gateway, test)


def test_several_homes_poll_on_their_own() -> None:
    world = [Sandbox("alpha", SandboxState.STOPPED, 8188)]
    one, two = Elsewhere(world), Elsewhere(world)

    async def both() -> None:
        a, b = SandboxApp(one), SandboxApp(two)
        async with a.run_test(size=(120, 30)) as pa, b.run_test(size=(120, 30)) as pb:
            ra, rb = scenarios.Rec(pa), scenarios.Rec(pb)
            await ra.wait("Pick a sandbox or an action")
            await rb.wait("Pick a sandbox or an action")
            world[0] = Sandbox("alpha", SandboxState.RUNNING, 8188)
            await until(ra, lambda: "running" in row(ra, "alpha"))
            await until(rb, lambda: "running" in row(rb, "alpha"))

    asyncio.run(both())


# -- notes --------------------------------------------------------------------------------------


def test_a_running_sandbox_with_no_run_window_has_a_note_and_its_menu_explains_stop() -> None:
    gateway = DemoGateway(
        [
            Sandbox("alpha", SandboxState.RUNNING, 8188, run_window=False),
            Sandbox("beta", SandboxState.RUNNING, 8189, run_window=True),
        ],
        scenario=FAST,
    )

    async def test(r: scenarios.Rec) -> None:
        assert "no run window" in row(r, "alpha")
        assert "no run window" not in row(r, "beta")
        await r.row("alpha")
        await r.wait("What do you want to do with alpha?")
        assert "Closing a window won't stop it" in r.text
        await r.choose("Stop")  # offered, and it stops it
        await r.wait("alpha stopped")

    home(gateway, test)


def test_a_stopped_sandbox_whose_port_is_held_names_who_holds_it() -> None:
    gateway = DemoGateway(
        [
            Sandbox("alpha", SandboxState.STOPPED, 8188, port_held_by="python.exe (PID 4242)"),
            Sandbox("beta", SandboxState.STOPPED, 8189),
        ],
        scenario=FAST,
    )

    async def test(r: scenarios.Rec) -> None:
        assert "port held by python.exe (PID 4242)" in row(r, "alpha")
        assert "port held by" not in row(r, "beta")

    home(gateway, test)


def test_the_notes_go_with_the_state_they_belong_to() -> None:
    # A stale note on a record in the wrong state isn't drawn: no-run-window is about a running
    # sandbox, port-held-by about a stopped one.
    gateway = DemoGateway(
        [
            Sandbox("alpha", SandboxState.STOPPED, 8188, run_window=False),
            Sandbox("beta", SandboxState.RUNNING, 8189, run_window=True, port_held_by="x.exe (PID 1)"),
        ],
        scenario=FAST,
    )

    async def test(r: scenarios.Rec) -> None:
        assert "no run window" not in row(r, "alpha")
        assert "port held by" not in row(r, "beta")

    home(gateway, test)
