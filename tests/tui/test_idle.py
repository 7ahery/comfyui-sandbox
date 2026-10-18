"""Nothing moving means few redraws: an idle window slows its timers, and input speeds them again."""

import asyncio

import scenarios
from comfyui_sandbox.tui import pacing
from comfyui_sandbox.tui.home.chrome import Hero
from comfyui_sandbox.tui.run_window.view import Banner


class Count:
    """Counts a widget class's renders."""

    def __init__(self, monkeypatch, cls) -> None:
        self.n = 0
        real = cls.render

        def render(widget):
            self.n += 1
            return real(widget)

        monkeypatch.setattr(cls, "render", render)

    async def over(self, pilot, seconds: float) -> int:
        before = self.n
        await pilot.pause(seconds)
        return self.n - before


def test_an_idle_run_window_redraws_far_below_20_hz(monkeypatch) -> None:
    monkeypatch.setattr(pacing, "GRACE", 0.3)
    banner = Count(monkeypatch, Banner)

    async def scenario() -> None:
        app = scenarios.run_app(speed=20)
        async with app.run_test(size=(120, 30)) as pilot:
            await scenarios.Rec(pilot).wait("Running at")
            assert app.follow_task is not None
            app.follow_task.cancel()  # the demo keeps printing; a real idle ComfyUI does not
            await pilot.pause(1.0)  # the glow of what just arrived, and the grace, are over
            assert await banner.over(pilot, 2.0) <= 8  # 2 s: 40 at 20 Hz

    asyncio.run(scenario())


def test_a_key_press_wakes_an_idle_run_window(monkeypatch) -> None:
    monkeypatch.setattr(pacing, "GRACE", 0.5)
    banner = Count(monkeypatch, Banner)

    async def scenario() -> None:
        app = scenarios.run_app(speed=20)
        async with app.run_test(size=(120, 30)) as pilot:
            r = scenarios.Rec(pilot)
            await r.wait("Running at")
            assert app.follow_task is not None
            app.follow_task.cancel()
            await pilot.pause(1.5)
            await pilot.press("up")
            assert await banner.over(pilot, 0.4) >= 4  # back at the busy rate for the grace

    asyncio.run(scenario())


def test_an_idle_home_redraws_its_hero_far_below_15_hz(monkeypatch) -> None:
    monkeypatch.setattr(pacing, "GRACE", 0.3)
    hero = Count(monkeypatch, Hero)

    async def scenario() -> None:
        app = scenarios.home_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await scenarios.Rec(pilot).wait("Pick a sandbox or an action")
            await pilot.pause(2.5)  # the logo's wipe in is over
            assert await hero.over(pilot, 2.0) <= 2 * 5

    asyncio.run(scenario())


def test_an_idle_home_pulses_and_ticks_its_live_lines_slowly(monkeypatch) -> None:
    from comfyui_sandbox.tui.home.app import SandboxApp
    from comfyui_sandbox.tui.home.widgets import Live

    monkeypatch.setattr(pacing, "GRACE", 0.3)
    pulses, ticks = [], []
    real_pulse, real_tick = SandboxApp.pulse, Live.tick
    monkeypatch.setattr(SandboxApp, "pulse", lambda self: (pulses.append(1), real_pulse(self))[1])
    monkeypatch.setattr(Live, "tick", lambda self: (ticks.append(1), real_tick(self))[1])

    async def scenario() -> None:
        app = scenarios.home_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await scenarios.Rec(pilot).wait("Pick a sandbox or an action")
            await pilot.pause(2.5)  # past the grace
            pulses.clear()
            ticks.clear()
            await pilot.pause(3.0)
            assert len(pulses) <= 5  # 3 s: 15 at 5 Hz
            assert len(ticks) <= 3 * 4 * 2  # the screen's table, at 4 Hz at most (12 Hz would be 36)

    asyncio.run(scenario())


def test_a_still_logo_is_painted_once_until_something_changes_it(monkeypatch) -> None:
    from comfyui_sandbox.tui import logo

    painted = []
    real = logo.paint_logo
    monkeypatch.setattr(
        "comfyui_sandbox.tui.home.chrome.paint_logo", lambda *a, **k: (painted.append(1), real(*a, **k))[1]
    )

    async def scenario() -> None:
        app = scenarios.home_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await scenarios.Rec(pilot).wait("Pick a sandbox or an action")
            await pilot.pause(2.5)
            hero = app.query_one(Hero)
            hero.render()
            before = len(painted)
            hero.render()
            hero.render()
            assert len(painted) == before

    asyncio.run(scenario())


def test_the_run_windows_dot_breathes_smoothly_while_the_banner_stays_slow(monkeypatch) -> None:
    from comfyui_sandbox.tui.run_window.view import Summary

    monkeypatch.setattr(pacing, "GRACE", 0.3)
    banner, summary = Count(monkeypatch, Banner), Count(monkeypatch, Summary)

    async def scenario() -> None:
        app = scenarios.run_app(speed=20)
        async with app.run_test(size=(120, 30)) as pilot:
            await scenarios.Rec(pilot).wait("Running at")
            assert app.follow_task is not None
            app.follow_task.cancel()
            await pilot.pause(1.0)
            b, s = banner.n, summary.n
            await pilot.pause(2.0)
            assert summary.n - s >= 14  # 2 s: 20 at 10 Hz (2 Hz would be 4)
            assert banner.n - b <= 8

    asyncio.run(scenario())


def test_the_run_windows_dot_is_smooth_whether_or_not_log_lines_arrive(monkeypatch) -> None:
    from comfyui_sandbox.tui.run_window.view import Summary

    summary = Count(monkeypatch, Summary)

    async def scenario() -> None:
        app = scenarios.run_app(speed=1)  # the fake keeps printing after ready
        async with app.run_test(size=(120, 30)) as pilot:
            await scenarios.Rec(pilot).wait("Running at")
            assert await summary.over(pilot, 2.0) >= 14

    asyncio.run(scenario())


def test_home_redraws_its_prompt_fast_only_while_a_dot_breathes(monkeypatch) -> None:
    from comfyui_sandbox.tui.gateway.demo import DemoGateway, seed_sandboxes
    from comfyui_sandbox.tui.home.app import SandboxApp
    from comfyui_sandbox.tui.home.picker import HomePrompt
    from comfyui_sandbox.tui.states import SandboxState

    monkeypatch.setattr(pacing, "GRACE", 0.3)
    renders = Count(monkeypatch, HomePrompt)

    async def scenario(running: bool) -> int:
        sandboxes = seed_sandboxes()
        if not running:
            for s in sandboxes:
                if s.state == SandboxState.RUNNING:
                    s.state = SandboxState.STOPPED
        app = SandboxApp(DemoGateway(sandboxes))
        async with app.run_test(size=(120, 40)) as pilot:
            await scenarios.Rec(pilot).wait("Pick a sandbox or an action")
            await pilot.pause(2.5)
            return await renders.over(pilot, 2.0)

    assert asyncio.run(scenario(True)) >= 16  # 2 s: 24 at 12 Hz (4 Hz would be 8)
    assert asyncio.run(scenario(False)) <= 12
