"""The logo and the wordmark beside it, and paint_logo, which draws the logo as a picture: wiping
in, catching a sheen, resolving out of latent noise, scanned, tinted or glitched."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .canvas import Canvas
from .noise import hash01, noise_pixel
from .theme import MINT, PINK, RED, SKY, WHITE, cycle, mix

VERSION = "v1.0.0"  # the CLI's version, once this is part of it
SUB = "S A N D B O X"  # the wordmark under or beside the logo

LOGO = [  # two rows, so the hero fits a default 120x30 PowerShell window
    "█▀▀ █▀█ █▀▄▀█ █▀▀ █▄█ █ █ █",
    "█▄▄ █▄█ █ ▀ █ █▀   █  █▄█ █",
]
LOGO_W = len(LOGO[0])

# The logo as a picture: its half blocks are two pixels a cell, so it can be sampled out of noise
# like an image, and scaled up for the splash. Four rows of LOGO_W pixels.
LOGO_PX = [[ch in ("█▀" if half == 0 else "█▄") for ch in line] for line in LOGO for half in (0, 1)]


@dataclass(frozen=True)
class LogoLook:
    """How paint_logo draws the logo beyond its position: see paint_logo for what each means."""

    noise: float = 0.0
    noise_t: float | None = None
    seed: int = 0
    scan: float | None = None
    tint: str | None = None
    tint_by: float = 1.0
    glitch: float = 0.0


def paint_logo(
    c: Canvas,
    x0: int,
    y0: int,
    t: float,
    *,
    scale: int = 1,
    reveal: float = math.inf,
    sheen: float | None = None,
    look: LogoLook = LogoLook(),
) -> None:
    """The logo, scale cells a pixel, its faces in a gradient that drifts slowly. reveal: how far
    along the diagonal it has wiped in. sheen: where a sheen crosses it, on that diagonal. noise:
    how much of it is still latent noise (each pixel resolves when noise drops below its own
    threshold, glowing as it does); noise_t freezes the noise's flicker at that time. scan: a beam
    at that fraction of the width, mint behind it. tint: mixed toward a colour by tint_by (a
    failure, a cancel). glitch (0..1): rows torn sideways and split into a hue, pixels corrupted."""
    noise, noise_t, seed, scan = look.noise, look.noise_t, look.seed, look.scan
    tint, tint_by, glitch = look.tint, look.tint_by, look.glitch
    pw, ph = LOGO_W * scale, len(LOGO_PX) * scale
    frame = int(t * 18)  # the glitch re-rolls 18 times a second, by the clock
    nt = t if noise_t is None else noise_t
    tear = [0] * ph
    if glitch >= 0.3:  # a burst: whole rows tear, a different few each frame
        for py in range(ph):
            band = py // scale
            if hash01(band, frame, seed) < 0.6 * glitch:
                tear[py] = round((hash01(band, frame, seed + 1) - 0.5) * (2 + 6 * glitch) * scale)

    def pixel(px: int, py: int) -> str | None:
        """The colour of one pixel of the picture, or None where the screen shows through."""
        lx, ly = (px - tear[py]) // scale, py // scale
        if not 0 <= lx < LOGO_W:
            return None
        d = lx + ly  # along the diagonal the wipe and the sheen travel
        if d > reveal:
            return None
        if glitch and hash01(px, py, frame + seed) < 0.05 * glitch:
            return (RED, PINK, WHITE)[int(hash01(py, px, frame) * 3)]  # a corrupted pixel
        lit = LOGO_PX[ly][lx]
        r = hash01(lx, ly, seed)
        if r < noise * (1.0 if lit else 0.8):  # the ground resolves a little ahead of the faces
            col = noise_pixel(px, py, nt)
            return mix(col, tint, 0.5 * tint_by) if tint else col
        if not lit:
            return None
        col = cycle(lx / LOGO_W * 0.5 - ly * 0.025 + t * 0.03)
        if sheen is not None:
            col = mix(col, WHITE, 0.6 * math.exp(-(((d - sheen) / 3.2) ** 2)))
        if reveal - d < 5:  # the wipe's leading edge burns bright
            col = mix(WHITE, col, (reveal - d) / 5)
        if scan is not None:
            beam = math.exp(-(((lx - scan * LOGO_W) / 2.0) ** 2))
            col = mix(col if lx > scan * LOGO_W else mix(col, MINT, 0.35), WHITE, 0.7 * beam)
        if tint:
            col = mix(col, tint, 0.55 * tint_by)
        if tear[py]:
            col = mix(col, PINK if tear[py] > 0 else SKY, 0.6)
        if noise > 0 and r < noise + 0.07:
            col = mix(col, WHITE, 0.6)  # about to resolve: it glows
        return col

    for cy in range(ph // 2):  # two pixel rows a cell: ▀ ▄ █ pick the glyph, fg and bg from which halves are lit
        for px in range(pw):
            top, bottom = pixel(px, 2 * cy), pixel(px, 2 * cy + 1)
            if top is None and bottom is None:
                continue
            if top == bottom:
                c.put(x0 + px, y0 + cy, "█", top)
            elif bottom is None:
                c.put(x0 + px, y0 + cy, "▀", top)
            elif top is None:
                c.put(x0 + px, y0 + cy, "▄", bottom)
            else:
                c.put(x0 + px, y0 + cy, "▀", top, bottom)
