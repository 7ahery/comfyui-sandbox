"""Rendering is a pure read: animation doesn't depend on how often Textual redraws."""

import asyncio
import time

import pytest

import scenarios
from comfyui_sandbox.tui.gateway.demo import BUILD_STEP, Scenario
from comfyui_sandbox.tui.home.chrome import Hero, Sampler
from comfyui_sandbox.tui.run_window.view import Banner, ease_toward
from comfyui_sandbox.tui.states import Outcome, SamplerKind


def test_banner_reaches_the_same_point_at_any_refresh_rate() -> None:
    coarse = ease_toward(0.0, 1.0, 1.0)
    fine = 0.0
    for _ in range(100):
        fine = ease_toward(fine, 1.0, 0.01)
    assert fine == pytest.approx(coarse)
    assert 0.0 < coarse < 1.0


def test_banner_render_leaves_its_progress_alone() -> None:
    async def scenario() -> None:
        app = scenarios.run_app(speed=1)
        async with app.run_test(size=(120, 30)) as pilot:
            banner = app.query_one(Banner)
            await pilot.pause(0.3)
            shown = banner.shown
            banner.render()
            banner.render()
            assert banner.shown == shown

    asyncio.run(scenario())


def test_hero_render_leaves_the_operation_alone_and_a_tick_drops_it() -> None:
    async def scenario() -> None:
        app = scenarios.home_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.3)
            op = app.begin_operation()
            op.sampler = Sampler(SamplerKind.BACKUP, 1, lambda: 0.5)
            op.sampler.end(Outcome.OK)
            op.sampler.release()
            op.sampler.released = time.monotonic() - 60  # long ago: its logo has re-formed
            hero = app.query_one(Hero)
            hero.render()
            assert app.op is op
            hero.tick()
            assert app.op is None

    asyncio.run(scenario())


def test_taking_the_fail_at_happens_once() -> None:
    scenario = Scenario(fail=True)
    assert scenario.take_fail_at() == BUILD_STEP
    assert scenario.take_fail_at() is None


def test_run_here_remembers_a_cancel_key_pressed_once_across_the_redraw() -> None:
    async def scenario() -> None:
        app = scenarios.home_app()
        async with app.run_test(size=(120, 40)) as pilot:
            r = scenarios.Rec(pilot)
            await r.wait("Pick a sandbox or an action")
            await r.row("comfyui-flux")
            await r.choose("Run here")
            await r.wait("LIVE")
            await r.press("escape")
            assert app.op is not None and app.op.armed.key() == "Esc"
            await r.press("l", settle=0.2)
            await r.press("escape", settle=0.3)  # back from full screen: the page is drawn again
            await r.wait("LIVE")
            # (still the same press: the 3s window may run out while the page is drawn again)
            assert app.op is not None and app.op.armed.armed is not None and app.op.armed.armed[0] == "Esc"

    asyncio.run(scenario())


def widget_state(widget) -> dict:
    return dict(vars(widget))


def test_the_hero_the_picker_and_a_confirm_leave_their_state_alone_when_rendered() -> None:
    from comfyui_sandbox.tui.home.picker import HomePrompt
    from comfyui_sandbox.tui.home.prompts import ConfirmPrompt

    async def scenario() -> None:
        app = scenarios.home_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await scenarios.Rec(pilot).wait("Pick a sandbox or an action")
            await pilot.pause(2.5)  # the wipe in is over: the hero paints its still
            picker = app.query_one(HomePrompt)
            for widget in (app.query_one(Hero), picker):
                widget.render()
                before = widget_state(widget)
                widget.render()
                assert widget_state(widget) == before
            assert picker.geometry().first_row > 0  # what the mouse reads, without a render
            confirm = ConfirmPrompt("Sure?", True)
            await app.out.mount(confirm)
            confirm.render()
            before = widget_state(confirm)
            confirm.render()
            assert widget_state(confirm) == before
            assert confirm.geometry()[1][True][0] == 0

    asyncio.run(scenario())


def test_an_ended_operation_keeps_only_what_is_read_after_it() -> None:
    from comfyui_sandbox.tui.home.model import Operation

    op = Operation()
    op.live_view, op.cancel_keys = object(), {"Esc"}  # ty: ignore[invalid-assignment]  # any marker: the test checks it is dropped
    op.sampler = Sampler(SamplerKind.BACKUP, 1, lambda: 0.4)
    op.sampler.end(Outcome.CANCELLED)
    op.finish()
    assert op.live_view is None and not op.cancel_keys
    assert op.sampler.progress() == 0.4  # it holds how far it got, not the view it asked
