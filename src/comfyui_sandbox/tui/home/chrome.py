"""What frames home's screen: the hero (the logo, the wordmark, and the graph beside them) at the
top, and the bottom bar. While a long operation runs, the hero's logo is its preview (Sampler)."""

from __future__ import annotations

import random
import time
from collections.abc import Callable

from rich.text import Text

from ..canvas import Canvas
from ..logo import LOGO_W, SUB, VERSION, LogoLook, paint_logo
from ..memo import Memo
from ..noise import glitch_level
from ..pacing import Pacer
from ..parts import bar, keys_line
from ..states import Outcome, SamplerKind, SandboxState
from ..theme import (
    AMBER,
    FAINT,
    GREEN,
    HEAT,
    INK,
    LINE,
    MINT,
    PINK,
    RED,
    SKY,
    SOFT,
    VIOLET,
    WHITE,
    clamp,
    ease,
    mix,
    ramp,
    st,
)
from .prompts import Prompt
from .widgets import HomeWidget


class BottomBar(HomeWidget):
    """The keys that work right now; on the right, the machine's GPU and its memory, as the
    gateway last read them (none when it has no readings)."""

    def on_mount(self) -> None:
        # Busy while an operation runs (its keys change, and so does the GPU meter); idle otherwise.
        Pacer(self, self.refresh, lambda: self.app.op is not None, busy_hz=8).start()

    def render(self) -> Text:
        app, width = self.app, self.size.width
        prompt = app.prompt
        left = keys_line(prompt.keys() if prompt is not None else app.keys)
        status = prompt.status() if isinstance(prompt, Prompt) else None
        if status is not None:
            left.append("   ")
            left.append_text(status)
        gpu = app.gpu
        parts = (
            []
            if gpu is None
            else [  # dropped from the front when the window is narrow
                Text.assemble(
                    ("GPU ", st(FAINT)),
                    bar(gpu.utilisation, 10, stops=HEAT, sheen=False),
                    (f" {round(gpu.utilisation * 100):3d}%", st(SOFT)),
                ),
                Text.assemble(
                    ("VRAM ", st(FAINT)),
                    (f"{gpu.memory_used:4.1f}", st(SOFT)),
                    (f"/{gpu.memory_total:g} GB", st(FAINT)),
                ),
            ]
        )
        while parts:
            right = Text("   ").join(parts) + Text(" ")
            if left.cell_len + right.cell_len + 2 <= width:
                break
            parts.pop(0)
        else:
            right = Text("")
        return Text.assemble(left, " " * max(0, width - left.cell_len - right.cell_len), right, no_wrap=True)


# -- the logo as an operation's preview ------------------------------------------------------


def op_noise(kind: SamplerKind, p: float) -> float:
    """How much of the logo is noise at progress p: an install resolves it, an uninstall dissolves
    it, a recreate does one then the other, an update or a restore clears what's left. Scans have none."""
    return {
        SamplerKind.INSTALL: 1 - p,
        SamplerKind.UNINSTALL: p,
        SamplerKind.RECREATE: 1 - abs(1 - 2 * p),
        SamplerKind.UPDATE: 0.5 * (1 - p),
        SamplerKind.RESTORE: 0.5 * (1 - p),
    }.get(kind, 0.0)


REFORM = 0.9  # seconds the logo takes to come back once an operation's outcome is dismissed


class Sampler:
    """The hero's logo as a long operation's preview, the way ComfyUI previews an image while it
    samples: how much of it is noise says how far the operation is (op_noise). kind: install,
    uninstall, recreate, update, restore, or backup and check (which scan). seed: the sandbox's port, so
    each one resolves in its own order. Once ended it holds how it ended while the outcome is on
    screen; the next Console.draw() releases it and the logo re-forms."""

    def __init__(self, kind: SamplerKind, seed: int, progress: Callable[[], float]) -> None:
        self.kind, self.seed, self.progress = kind, seed, progress
        self.started = time.monotonic()
        self.outcome = Outcome.RUNNING
        self.ended: float | None = None
        self.final = 0.0  # progress when it ended
        self.released: float | None = None

    def end(self, outcome: Outcome) -> None:
        """The operation is over: hold how it ended, and how far it got, for the logo to show."""
        self.outcome, self.ended = outcome, time.monotonic()
        self.final = 1.0 if outcome == Outcome.OK else self.progress()
        self.progress = self.held  # the operation's view is no longer needed: this holds how far it got

    def held(self) -> float:
        return self.final

    def release(self) -> None:
        """The outcome has been read (the next screen is being drawn): the logo starts to re-form."""
        if self.ended is not None and self.released is None:
            self.released = time.monotonic()

    def over(self, t: float) -> bool:
        """Whether the logo has finished re-forming by t, so the operation can be dropped."""
        return self.released is not None and t - self.released >= REFORM

    def look(self, t: float) -> LogoLook:
        """paint_logo's arguments now."""
        p = self.final if self.ended else self.progress()
        scanning = self.kind in (SamplerKind.BACKUP, SamplerKind.CHECK)
        # It goes to noise quickly first, rather than jump: the logo is re-sampled.
        noise = op_noise(self.kind, p) * ease((t - self.started) / 0.5)
        tint = {Outcome.FAILED: RED, Outcome.CANCELLED: AMBER}.get(self.outcome)
        glitch = glitch_level(t, self.ended) if self.outcome == Outcome.FAILED and self.ended is not None else 0.0
        scan = p if scanning and self.outcome == Outcome.RUNNING else None
        kept = 1.0
        if self.released is not None:
            kept = 1 - ease((t - self.released) / REFORM)
            noise, glitch, scan = noise * kept, 0.0, None
        # A failure or cancel freezes the noise's flicker at the moment it ended; after an OK it goes on.
        return LogoLook(
            noise=noise,
            noise_t=self.ended if self.outcome != Outcome.OK else None,
            seed=self.seed,
            scan=scan,
            tint=tint,
            tint_by=kept,
            glitch=glitch,
        )


# -- the hero --------------------------------------------------------------------------------

HERO_H = 3  # a blank row, then the logo's two
INTRO_LENGTH = 2.0  # seconds the logo takes to wipe in and the wordmark to fade in, with room to spare
# Between sheens and operations only the logo's colours move, by 0.03 of their cycle a second. So a
# still logo is painted at this step, as of the step's start, and reused (Hero.still) in between: the
# idle hero costs a cache hit per tick. A step moves the colours by 0.015 of the cycle.
DRIFT_STEP = 0.5  # seconds between the repaints of a still logo: its colours drift 0.03 a second
SHEEN_PERIOD = 7.0  # seconds between the sheens that cross the logo
SHEEN_LENGTH = 1.3  # seconds one takes to cross
SHEEN_LEAD = 0.4  # seconds before one starts that the hero is expected to move
SHEEN_TRAIL = 0.2  # seconds after one ends that it still is
SHOW_GRAPH = False  # the animated graph beside the logo; off while deciding whether to keep it
WIRE_W = 7  # socket, five cells of wire, socket
GRAPH = [("Checkpoint", VIOLET), ("KSampler", SKY), ("VAE Decode", MINT)]
GRAPH_W = sum(len(title) + 4 for title, _ in GRAPH) + 2 * WIRE_W + 2 + 10  # nodes, wires, preview
MODEL_C, LATENT_C = "#b9a2ff", PINK  # the sockets' colours, by type, as ComfyUI colours them


def sheening(intro: float) -> bool:
    """Whether a sheen is crossing the logo, intro seconds after the app started."""
    return intro % SHEEN_PERIOD < SHEEN_LENGTH


def sheen_near(intro: float) -> bool:
    """Whether a sheen is crossing the logo, or about to, or just was."""
    since = intro % SHEEN_PERIOD
    return since <= SHEEN_LENGTH + SHEEN_TRAIL or since >= SHEEN_PERIOD - SHEEN_LEAD


class Hero(HomeWidget):
    """The logo, and on the right (when the window is wide enough) a one-line ComfyUI graph
    that runs while sandboxes do."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.still: Memo[Text] = Memo()  # the still hero (below), for what it was painted for

    def on_mount(self) -> None:
        # Draws at 15 Hz while moving() and for the grace after; at the idle rate otherwise.
        self.pacer = Pacer(self, self.tick, self.moving, busy_hz=15)
        self.pacer.start()

    def moving(self) -> bool:
        """Whether the hero is animating: an operation's preview, the logo wiping in, or the sheen
        (due every seven seconds, so it is expected a moment before it starts)."""
        intro = time.monotonic() - self.app.started
        return self.app.op is not None or intro < INTRO_LENGTH or sheen_near(intro)

    def tick(self) -> None:
        """Drops the operation once its sampler's logo has re-formed, then repaints. It is here,
        on the timer, because render() may not change the app's state."""
        sampler = self.app.sampler
        if sampler is not None and sampler.over(time.monotonic()):
            self.app.op = None
        self.refresh()

    def render(self) -> Text:
        # A pure read: it assigns nothing, so how the logo moves depends on the clock and not on how
        # often Textual redraws (the Pacer's rate, a resize, the mouse). Changes happen in tick().
        t, intro = time.monotonic(), time.monotonic() - self.app.started
        sampler = self.app.sampler
        if not SHOW_GRAPH and intro >= INTRO_LENGTH and not sheening(intro) and (sampler is None or sampler.over(t)):
            # Nothing but the colours' slow drift moves the logo: paint it once per drift step.
            # The step's start is the time it is painted at, so the frame depends only on the key.
            step = int(t / DRIFT_STEP)

            def still() -> Text:
                canvas = Canvas(self.size.width, HERO_H)
                self.paint_logo(canvas, 2, 1, step * DRIFT_STEP, intro)
                return canvas.text()

            return self.still((self.size.width, step), still)
        canvas = Canvas(self.size.width, HERO_H)
        self.paint_logo(canvas, 2, 1, t, intro)
        gx = self.size.width - 2 - GRAPH_W
        if SHOW_GRAPH and gx >= 2 + LOGO_W + 3 + max(len(SUB), len(VERSION)) + 4:
            running = sum(s.state == SandboxState.RUNNING for s in self.app.sandboxes)
            paint_graph(canvas, gx, 1, t, intro, running)
        return canvas.text()

    def paint_logo(self, c: Canvas, x0: int, y0: int, t: float, intro: float) -> None:
        """The logo wipes in from the left on start, and a sheen crosses it every SHEEN_PERIOD seconds.
        Beside it, S A N D B O X over the version. While a long operation runs (the app's
        sampler) the logo is its preview."""
        sampler = self.app.sampler
        if sampler is not None and sampler.over(t):
            sampler = None  # tick() drops the operation
        look = sampler.look(t) if sampler is not None else LogoLook()
        sheen = None
        if intro > SHEEN_PERIOD and sheening(intro) and sampler is None:
            sheen = (intro % SHEEN_PERIOD) / SHEEN_LENGTH * (LOGO_W + 30) - 15
        paint_logo(c, x0, y0, t, reveal=intro * 60 - 8, sheen=sheen, look=look)
        fade = clamp((intro - 0.55) / 0.5)
        sx = x0 + LOGO_W + 3
        c.put(sx, y0, SUB, mix(INK, SOFT, fade), bold=True)
        c.put(sx, y0 + 1, VERSION, mix(INK, FAINT, fade))


def paint_graph(c: Canvas, gx: int, gy: int, t: float, intro: float, running: int) -> None:
    """Checkpoint → KSampler → VAE Decode, on one line. While sandboxes run, a pulse leaves the
    checkpoint, lights each node it reaches, and the decoder renders a new little image (two
    pixels per cell); more running sandboxes, faster pulses. With none running it sits dark."""
    alive = running > 0
    appear = clamp((intro - 0.5) / 0.8)
    period = 3.6 / (1 + 0.12 * (running - 1)) if alive else 1.0
    u = (t / period) % 1 if alive else -1.0
    n_cycle = int(t / period)

    def window(a: float, b: float) -> float:
        return 1 - (u - a) / (b - a) if a <= u <= b else 0.0

    glows = [window(0.0, 0.16), window(0.30, 0.48), window(0.66, 0.9)]
    pulses = [(u - 0.04) / 0.26, (u - 0.40) / 0.26]  # 0..1 while a pulse is on that wire

    def ink(color: str) -> str:
        return mix(INK, color, appear)

    x = gx
    for k, (title, hue) in enumerate(GRAPH):
        # The node: a pill, its half-block caps in the fill's colour.
        g = glows[k] if alive else 0.0
        fill = ink(mix(INK, hue, (0.16 + 0.22 * g) if alive else 0.07))
        c.put(x, gy, "▐", fill)
        c.put(x + 1, gy, f" {title} ", ink(mix(hue, WHITE, 0.5 * g) if alive else mix(hue, INK, 0.45)), fill, True)
        c.put(x + len(title) + 3, gy, "▌", fill)
        x += len(title) + 4
        if k == len(GRAPH) - 1:
            break
        # The wire to the next node, carrying its pulse with a short tail.
        color, p = (MODEL_C, pulses[0]) if k == 0 else (LATENT_C, pulses[1])
        sock = ink(color if alive else FAINT)
        c.put(x, gy, "●" if alive else "○", sock, bold=True)
        head = p * (WIRE_W + 1) - 1.5
        for j in range(WIRE_W - 2):
            b = clamp(1 - abs(j - head) / 1.8) if alive and 0 <= p <= 1 else 0.0
            if alive and 0 <= p <= 1 and j < head:
                b = max(b, clamp(0.5 - (head - j) / 6))  # the tail
            base = mix(LINE, color, 0.35) if alive else LINE
            c.put(x + 1 + j, gy, "━" if b > 0.4 else "─", ink(mix(base, color, b)))
        c.put(x + WIRE_W - 1, gy, "●" if alive else "○", sock, bold=True)
        x += WIRE_W

    # The decoder's preview: ten cells, two pixels each (▀: the top in fg, the bottom in bg).
    px = x + 2
    if not alive:
        c.put(px, gy, "· · · · · ", ink(LINE))
    else:

        def picture(n: int) -> list[tuple[str, str]]:
            rnd = random.Random(n)
            pal = tuple(rnd.sample([VIOLET, SKY, MINT, PINK, AMBER, GREEN], 3))
            sun = rnd.randint(2, 7)
            cells = []
            for j in range(10):
                sky = ramp(j / 9, pal)
                top = mix(sky, WHITE, 0.55) if abs(j - sun) < 1 else sky
                cells.append((top, mix(ramp(1 - j / 9, pal), INK, 0.45)))
            return cells

        wipe = clamp((u - 0.66) / 0.2) * 10
        old, new = picture(n_cycle - 1), picture(n_cycle)
        for j in range(10):
            top, bottom = new[j] if j < wipe else old[j]
            if 0 <= wipe - j < 1.5:  # the scanline
                top, bottom = mix(top, WHITE, 0.6), mix(bottom, WHITE, 0.4)
            c.put(px + j, gy, "▀", ink(top), ink(bottom))
