"""Latent noise. In ComfyUI an image arrives out of noise, step by step. Here the logo does (the
splash, a long operation's preview, the run window's banner), and so does the splash's sampler
bar's head. A failure glitches the logo."""

from __future__ import annotations

from .theme import AMBER, INK, MINT, PINK, SKY, VIOLET, mix

NOISE = (VIOLET, SKY, MINT, PINK, AMBER, "#3b4675")
NOISE_DASHES = "╍┅┉━╺"  # noise on a thin line: a progress bar's track


def hash01(x: int, y: int, k: int = 0) -> float:
    """A stable pseudo-random number in [0, 1): what decides when a pixel leaves the noise."""
    h = (x * 374761393 + y * 668265263 + k * 1274126177) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1103515245) & 0xFFFFFFFF
    return (h ^ (h >> 16)) / 4294967296.0


def noise_pixel(x: int, y: int, t: float) -> str:
    """Latent noise for one pixel: a colour out of the palette, near ink, flickering 12 times a second."""
    frame = int(t * 12)
    return mix(INK, NOISE[int(hash01(x, y, frame + 3) * len(NOISE))], 0.12 + 0.6 * hash01(y, x, frame))


def glitch_level(t: float, since: float) -> float:
    """How hard a failure glitches (0..1), since it failed: a hard break that settles, then an
    aftershock every few seconds over a faint fault that never quite goes."""
    age = t - since
    if age < 0.8:
        return 1.0 - 0.5 * age / 0.8
    return 0.65 if (age - 0.8) % 2.6 < 0.2 else 0.12
