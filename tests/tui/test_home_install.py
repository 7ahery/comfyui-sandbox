"""Install a new sandbox and Resume install, on screen.

Home driven with Textual's pilot (the spec's seam A) on the demo gateway, whose `install` asks the
core's intake questions as the core asks them (gateway/demo/install.py), and once on the real gateway
over the CLI's fakes (seam B's `Deps`). Asserted: what's on screen (the questions and their hints, the
review, the live view, the double-Esc warning, the card, the Incomplete row) and the command lines
the gateway was asked to run.
"""

from __future__ import annotations

import asyncio
import io
import json

from test_check import Machine
from test_real_actions import ran_lines

import scenarios
from comfyui_sandbox.deps import Deps
from comfyui_sandbox.tui.gateway import Sandbox
from comfyui_sandbox.tui.gateway.demo import DemoGateway, Scenario
from comfyui_sandbox.tui.gateway.real import RealGateway
from comfyui_sandbox.tui.home.app import SandboxApp
from comfyui_sandbox.tui.states import SandboxState

SEEDED = [Sandbox("comfyui", SandboxState.RUNNING, 8188)]


def home(gateway, test, size=(120, 40)) -> None:
    async def scenario() -> None:
        app = SandboxApp(gateway)
        async with app.run_test(size=size) as pilot:
            r = scenarios.Rec(pilot)
            await r.wait("Pick a sandbox or an action")
            await test(r)

    asyncio.run(scenario())


def row(r: scenarios.Rec, name: str) -> str:
    return next(line for line in r.text.splitlines() if f" {name} " in line)


async def to_the_review(r: scenarios.Rec) -> None:
    await r.action("Install a new sandbox")
    await scenarios.through(r, "nothing has changed yet")


async def started(r: scenarios.Rec) -> None:
    """Through the questions, Start, and the port question (8188 is comfyui's): the live view."""
    await to_the_review(r)
    await r.press("enter")
    await r.wait("is the port of the sandbox comfyui")
    await r.press("enter")
    await r.wait("Installing comfy-docker")


def test_the_questions_are_the_cores_with_their_hints():
    async def test(r: scenarios.Rec) -> None:
        await r.action("Install a new sandbox")
        await r.wait("Where should your models live?")
        assert "comfyui-sandbox install" in r.text  # the command line it runs, as given
        assert "where ComfyUI finds checkpoints, LoRAs and VAEs" in r.text  # the question's hint
        await r.press("escape", settle=0.3)

    home(DemoGateway(list(SEEDED), scenario=Scenario(speed=20)), test)


def test_the_review_shows_the_answers_and_change_something_asks_again_with_them_as_defaults():
    async def test(r: scenarios.Rec) -> None:
        await to_the_review(r)
        assert "Install summary" in r.text and "Distro:        comfy-docker" in r.text
        assert "Ready to install?" in r.text
        await r.choose("Change something")
        await r.wait("Where should your models live?")
        await scenarios.through(r, "Name of the new sandbox")
        await r.choose("comfyui-2")
        await scenarios.through(r, "nothing has changed yet")
        assert "Distro:        comfyui-2" in r.text
        await r.press("escape", settle=0.3)

    home(DemoGateway(list(SEEDED), scenario=Scenario(speed=20)), test)


def test_esc_on_a_question_starts_nothing():
    gateway = DemoGateway(list(SEEDED), scenario=Scenario(speed=20))

    async def test(r: scenarios.Rec) -> None:
        await to_the_review(r)
        await r.press("escape")
        await r.wait("Install not started.")
        assert [s.name for s in await gateway.sandboxes()] == ["comfyui"]

    home(gateway, test)


def test_the_install_runs_under_the_live_view_with_its_whole_plan():
    async def test(r: scenarios.Rec) -> None:
        await started(r)
        assert "installing" in row(r, "comfy-docker")
        for title in ("Check the machine", "Questions", "Preflight", "Create the distro"):
            assert title in r.text
        # The bar and the keys draw at the live view's next frames: waited for, not assumed.
        await r.wait("%")
        await r.wait("esc ×2")
        await r.wait("Press any key to go back", timeout=60)
        await r.settle(0.3)  # the card draws at the live view's next frame
        assert "comfy-docker is installed" in r.text and "Log:" in r.text
        await r.press("enter", settle=0.4)
        await r.wait("Pick a sandbox or an action")
        assert "running" in row(r, "comfy-docker")

    home(DemoGateway(list(SEEDED), scenario=Scenario(speed=20)), test)


def test_esc_twice_cancels_it_and_it_stays_incomplete_to_resume():
    gateway = DemoGateway(list(SEEDED), scenario=Scenario(speed=2))

    async def test(r: scenarios.Rec) -> None:
        await started(r)
        await r.press("escape")
        await r.wait("Press Esc again to cancel")
        await r.press("escape")
        await r.wait("Install of comfy-docker cancelled")
        assert "incomplete" in row(r, "comfy-docker")
        await r.row("comfy-docker")
        await r.choose("Resume install")
        await r.wait("Installing comfy-docker")
        assert "comfyui-sandbox install --distro comfy-docker" in r.text
        await r.wait("Press any key to go back", timeout=60)
        await r.settle(0.3)  # the card draws at the live view's next frame
        assert gateway.ran[-1] == ("install", "--distro", "comfy-docker")

    home(gateway, test)


def test_a_failure_shows_the_cores_message_and_the_transcript():
    async def test(r: scenarios.Rec) -> None:
        await started(r)
        await r.wait("Press any key to go back", timeout=60)
        await r.settle(0.3)  # the card draws at the live view's next frame
        assert "Deploying ComfyUI failed (exit 1)" in r.text
        assert r"install-" in r.text and "Resume install" in r.text
        await r.press("enter", settle=0.4)
        await r.wait("install stopped at")
        assert "incomplete" in row(r, "comfy-docker")

    home(DemoGateway(list(SEEDED), scenario=Scenario(speed=20, fail=True)), test)


def test_change_answers_and_resume_asks_the_version_again_then_carries_on():
    gateway = DemoGateway(list(SEEDED), scenario=Scenario(speed=20, fail=True))

    async def test(r: scenarios.Rec) -> None:
        await started(r)
        await r.wait("Press any key to go back", timeout=60)
        await r.settle(0.3)  # the card draws at the live view's next frame
        assert "Change answers and resume" in r.text
        await r.press("enter", settle=0.4)
        await r.wait("install stopped at")
        await r.row("comfy-docker")
        await r.choose("Change answers and resume…")
        await r.wait("ComfyUI version?")
        assert "comfyui-sandbox install --distro comfy-docker --change" in r.text
        await r.choose("Nightly (newest, less tested)")
        await r.wait("Which Manager UI?")
        await r.press("enter")
        await r.wait("ComfyUI Manager security level?")
        await r.press("enter")
        await r.wait("Installing comfy-docker")
        await r.wait("Press any key to go back", timeout=60)
        await r.settle(0.3)
        assert "comfy-docker is installed" in r.text
        assert gateway.ran[-1] == ("install", "--distro", "comfy-docker", "--change")
        assert gateway._answers["comfy-docker"]["channel"] == "nightly"

    home(gateway, test)


def test_install_from_home_on_the_real_gateway_runs_install_and_leaves_a_sandbox(runner, winapi, home, environ, http):
    Machine(distros=["Ubuntu"]).script(runner, environ)
    deps = Deps(
        runner=runner,
        winapi=winapi,
        home=home,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        platform="win32",
        environ=environ,
        http=http,
        sleep=lambda seconds: None,
    )

    async def test(r: scenarios.Rec) -> None:
        await to_the_review(r)
        assert "Distro:        comfy-docker" in r.text
        await r.press("enter")
        await r.wait("Press any key to go back", timeout=60)
        await r.settle(0.3)  # the card draws at the live view's next frame
        assert "comfy-docker is installed" in r.text

    globals()["home"](RealGateway(deps), test, size=(160, 45))
    assert ran_lines(deps) == ["install --new"]
    saved = json.loads((home / ".comfyui-sandbox" / "installs.json").read_text("utf-8"))
    assert [(i["distro"], i["state"]) for i in saved["installs"]] == [("comfy-docker", "complete")]
