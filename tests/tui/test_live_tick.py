"""A live widget re-lays out its container only when its height changes."""

import asyncio


class Spy:
    """Counts the refreshes a widget's ticks ask for, split by whether they asked for a layout.
    (Textual asks for layouts of its own, on resizes; those aren't the tick's.)"""

    def __init__(self, widget) -> None:
        self.layouts = self.plain = 0
        self.ticking = False
        real_refresh, real_tick = widget.refresh, type(widget).tick

        def refresh(*args, layout: bool = False, **kwargs):
            if self.ticking:
                if layout:
                    self.layouts += 1
                else:
                    self.plain += 1
            return real_refresh(*args, layout=layout, **kwargs)

        def tick() -> None:
            self.ticking = True
            try:
                real_tick(widget)
            finally:
                self.ticking = False

        widget.refresh = refresh
        widget.tick = tick


async def ticks(frames: list[list[str]]) -> tuple[Spy, list[int]]:
    """Mounts a Live showing frames[0], then ticks once per frame; returns the spy and the heights."""
    from textual.app import App
    from textual.containers import VerticalScroll

    from comfyui_sandbox.tui.home.widgets import Live

    shown = ["\n".join(frames[0])]
    live = Live(lambda: shown[0])
    spy = Spy(live)  # before mounting, so the interval ticks through it too

    class Host(App):
        hovered = None  # what HoverLinks asks the app for

        def compose(self):
            self.out = VerticalScroll()
            yield self.out

    app = Host()
    heights = []
    async with app.run_test() as pilot:
        await app.out.mount(live)
        await pilot.pause()
        live.tick()  # the first tick may lay out; the ones after are what's measured
        await pilot.pause()
        spy.layouts = spy.plain = 0
        for frame in frames:
            shown[0] = "\n".join(frame)
            live.tick()
            await pilot.pause(0.02)
            heights.append(live.size.height)
    return spy, heights


def run_ticks(frames: list[list[str]]) -> tuple[Spy, list[int]]:
    return asyncio.run(ticks(frames))


def test_a_tick_that_keeps_the_height_does_not_lay_out() -> None:
    spy, heights = run_ticks([["a"], ["b"], ["c"]])
    assert spy.layouts == 0
    assert spy.plain >= 3
    assert heights == [1, 1, 1]


def test_growing_and_shrinking_lays_out_and_resizes() -> None:
    spy, heights = run_ticks([["a"], ["a", "b", "c"], ["a", "b"], ["a"]])
    assert heights == [1, 3, 2, 1]
    assert spy.layouts == 3
