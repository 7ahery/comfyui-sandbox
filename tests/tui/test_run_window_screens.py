"""The run window's screens on the demo gateway (the spec's seam A): what it reads from `run`'s
operation handle, and shows.

The stages are `run`'s plan, ComfyUI's facts and custom nodes come from its lines, the update from
a line `run` says, and the stop notice is `run`'s own, passed through the handle's progress: these
tests check each reaches the screen. The snapshot test (test_snapshots.py) has the looks.
"""

from __future__ import annotations

import asyncio

import scenarios
from comfyui_sandbox.tui.gateway import Sandbox
from comfyui_sandbox.tui.gateway.demo import DemoGateway, LaunchScenario
from comfyui_sandbox.tui.run_window.app import LaunchApp
from comfyui_sandbox.tui.states import SandboxState

SANDBOX = Sandbox("comfyui", SandboxState.STOPPED, 8188, "~/comfyui-models")


def window(test, *, gateway: DemoGateway | None = None, size=(120, 30), **scenario) -> None:
    app = LaunchApp(gateway or DemoGateway([SANDBOX], launch=LaunchScenario(speed=4, **scenario)), SANDBOX)

    async def scenario_() -> None:
        async with app.run_test(size=size) as pilot:
            await test(scenarios.Rec(pilot), app)

    asyncio.run(scenario_())


def test_the_stages_show_comfyuis_start_up_and_then_its_versions_and_custom_nodes():
    async def test(r: scenarios.Rec, app: LaunchApp) -> None:
        await r.wait("Loading custom nodes")  # a part of ComfyUI's own start-up stage
        assert [stage.name for stage in app.launch.stages][3] == "Waiting for ComfyUI to answer"
        await r.wait("Running at")
        text = r.text
        assert "0.3.66" in text and "1.28.7" in text and "2.9.0+cu130" in text and "3.12.3" in text
        assert app.launch.nodes == 6
        await r.press("ctrl+c", "ctrl+c")
        await r.wait("ComfyUI has been stopped")

    window(test)


def test_a_custom_node_that_failed_is_named():
    async def test(r: scenarios.Rec, app: LaunchApp) -> None:
        await r.wait("Running at")
        assert "ComfyUI_IPAdapter_plus failed to import" in r.text
        assert "1 failed" in r.text
        await r.press("ctrl+c", "ctrl+c")

    window(test, broken_node=True)


def test_a_newer_comfyui_is_shown_once_it_runs():
    async def test(r: scenarios.Rec, app: LaunchApp) -> None:
        await r.wait("ComfyUI 0.3.70 is available")
        await r.press("ctrl+c", "ctrl+c")

    window(test, update="release")


class SaysItsOwnNotice(DemoGateway):
    """A gateway whose `run` words its stop notice its own way: the window shows its words."""

    def run(self, argv):
        handle = super().run(argv)
        handle.progress.notice = "Shutting this console ends ComfyUI too."
        return handle


def test_the_stop_notice_is_runs_own_words_and_goes_once_comfyui_has_stopped():
    async def test(r: scenarios.Rec, app: LaunchApp) -> None:
        await r.wait("Shutting this console ends ComfyUI too.")
        assert "Closing this window stops ComfyUI." not in r.text
        await r.wait("Running at")
        await r.press("ctrl+c", "ctrl+c")
        await r.wait("ComfyUI has been stopped")
        assert "Shutting this console" not in r.text

    window(test, gateway=SaysItsOwnNotice([SANDBOX], launch=LaunchScenario(speed=4)))


# -- a question `run` asks: a port another program took ------------------------------------------


def test_a_taken_port_is_asked_about_in_the_window_and_the_answer_is_the_port_it_runs_on():
    async def test(r: scenarios.Rec, app: LaunchApp) -> None:
        await r.wait("Port 8188 is used by python.exe")
        assert "Use port 8189 instead (Recommended)" in r.text
        assert "Keep 8188, I'll close python.exe first" in r.text
        await r.press("enter")  # the recommended port
        await r.wait("Running at")
        assert "127.0.0.1:8189" in r.text and "Port 8188 is used" not in r.text
        await r.press("ctrl+c", "ctrl+c")
        await r.wait("ComfyUI has been stopped")

    window(test, port_taken=True)


def test_esc_on_the_port_question_backs_out_of_run_and_nothing_starts():
    async def test(r: scenarios.Rec, app: LaunchApp) -> None:
        await r.wait("Port 8188 is used by python.exe")
        await r.press("escape")
        await r.wait("ComfyUI has been stopped")
        handle = app.handle
        assert handle is not None and handle.result is not None and handle.result.interrupted
        assert not any("docker compose up" in line for line in handle.progress.lines)

    window(test, port_taken=True)
