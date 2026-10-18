"""The splash before home. It's off while deciding whether to keep it (SHOW_SPLASH); --intro
plays it anyway."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from rich.text import Text
from textual import events
from textual.widget import Widget

from ..canvas import Canvas
from ..logo import LOGO_W, SUB, VERSION, LogoLook, paint_logo
from ..parts import SPIN, bar_cells
from ..theme import FAINT, INK, LINE, MINT, MUTED, SKY, SOFT, TEXT, clamp, ease, mix

if TYPE_CHECKING:
    from .app import SandboxApp

SHOW_SPLASH = False  # the splash before home; off while deciding whether to keep it (--intro plays it)
SPLASH_LENGTH = 3.3  # seconds; any key skips it
SPLASH_SEED = 8188  # ComfyUI's port, as the sampler's seed
SAMPLER_STEPS = 20


class Splash(Widget, can_focus=True):
    """The entry screen, in the place of the hero and the screen's lines (the bottom bar stays):
    the logo, scaled up, is sampled out of latent noise as ComfyUI samples an image, with the
    sampler's readout under it; a sheen crosses it, the tagline types in, and it fades to home.
    The answer arrives through `future` when it's over or skipped."""

    DEFAULT_CSS = "Splash { height: 1fr; }"

    def __init__(self) -> None:
        super().__init__(id="splash")
        self.born = time.monotonic()
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()

    def on_mount(self) -> None:
        self.set_interval(1 / 30, self.refresh)
        self.set_timer(SPLASH_LENGTH, self.finish)

    def finish(self) -> None:
        """Ends the splash (the timer, or a key or click), once."""
        if not self.future.done():
            self.future.set_result(None)

    def on_key(self, event: events.Key) -> None:
        event.stop()
        self.finish()

    def on_click(self) -> None:
        self.finish()

    def render(self) -> Text:
        c = Canvas(self.size.width, self.size.height)
        t = time.monotonic()
        paint_splash(c, t, t - self.born)
        return c.text()


def readout(mark: str, word: str, color: str, what: str, seed: int) -> list[tuple[str, str, bool]]:
    """A sampler's one-line readout, as the splash shows it under its bar."""
    return [
        (f"{mark} ", color, True),
        (word, color, False),
        ("  ·  ", LINE, False),
        (what, SOFT, False),
        ("  ·  ", LINE, False),
        (f"seed {seed}", FAINT, False),
    ]


def paint_splash(c: Canvas, t: float, e: float) -> None:
    """e: seconds since the splash began."""
    scale = next((s for s in (3, 2) if c.width >= LOGO_W * s + 8 and c.height >= 2 * s + 12), 1)
    lw, lh = LOGO_W * scale, 2 * scale
    lx, ly = (c.width - lw) // 2, max(0, (c.height - lh - 7) // 2)  # 7: the lines under it
    level = 1 - ease((e - 0.25) / 1.45)  # 20 steps of denoising, front-loaded as a sampler's are
    sheen = (e - 1.75) / 0.6 * (LOGO_W + 40) - 20 if 1.75 < e < 2.35 else None
    paint_logo(c, lx, ly, t, scale=scale, sheen=sheen, look=LogoLook(noise=level, seed=SPLASH_SEED))

    # The wordmark beside the hero's logo goes under it here, once there's a logo to name.
    fade = clamp((e - 1.3) / 0.5)
    sub = f"{SUB}   {VERSION}"
    x = c.put((c.width - len(sub)) // 2, ly + lh + 1, SUB, mix(INK, SOFT, fade), bold=True)
    c.put(x + 3, ly + lh + 1, VERSION, mix(INK, FAINT, fade))

    # The sampler's readout: the hero's, with a bar.
    show = clamp((e - 0.2) / 0.3)
    if show > 0:
        steps = round((1 - level) * SAMPLER_STEPS)
        done = steps == SAMPLER_STEPS
        line = readout(
            "✓" if done else SPIN[int(t * 12) % len(SPIN)],
            "done" if done else "sampling",
            MINT if done else SKY,
            f"step {steps:2d}/{SAMPLER_STEPS}  ·  denoise {level:.2f}",
            SPLASH_SEED,
        )
        bw = min(24, c.width // 4)
        width = bw + 3 + sum(len(s) for s, _, _ in line)
        x = max(0, (c.width - width) // 2)
        for ch, color in bar_cells(1 - level, bw, sheen=False, edge=True):
            x = c.put(x, ly + lh + 3, ch, mix(INK, color, show))
        c.put_parts(x + 3, ly + lh + 3, [(s, mix(INK, color, show), b) for s, color, b in line])

    # The tagline types in under it.
    if e > 1.9:
        line1, line2 = "ComfyUI, sandboxed.", "Isolated in WSL 2  ·  served on 127.0.0.1  ·  one key away."
        n = int((e - 1.9) * 70)
        c.put((c.width - len(line1)) // 2, ly + lh + 5, line1[:n], TEXT, bold=True)
        c.put((c.width - len(line2)) // 2, ly + lh + 6, line2[: max(0, n - 10)], MUTED)

    c.fade(max(1 - e / 0.25, clamp((e - SPLASH_LENGTH + 0.35) / 0.35)))  # in from ink, out to it


async def play_splash(app: SandboxApp) -> None:
    """Plays the splash, then hands the screen to home with its intro still to play."""
    widget = Splash()
    await app.mount(widget, before=app.query_one("#body"))
    app.splashing = True
    app.keys = [("any key", "skip")]
    app.chrome()
    widget.focus()
    await widget.future  # not in a finally: quitting during it (Ctrl+C) tears the screen down itself
    await widget.remove()
    app.splashing = False
    app.keys = []
    app.started = time.monotonic()  # home's own intro (the hero's wipe, the table's cascade) starts now
    app.chrome()
