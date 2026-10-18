"""FAKE: GPU load for the demo's running sandboxes, now and then a burst as if a prompt ran, summed
into the one machine-wide reading DemoGateway.gpu returns. The real gateway reads nvidia-smi."""

from __future__ import annotations

import random
import time
from collections import deque

from ...states import SandboxState
from ...theme import clamp
from ..records import Sandbox

GPU_GB = 24


class Telemetry:
    """Sampled five times a second while an operation runs, once a second when idle (home's
    pulse); the chances and the smoothing are per SAMPLE seconds, so either rate looks alike.
    Each running sandbox idles low and now and then runs a prompt (a burst)."""

    SAMPLE = 0.2

    HISTORY = 48

    def __init__(self) -> None:
        self.load: dict[int, deque[float]] = {}
        self.busy_until: dict[int, float] = {}
        self.up: set[int] = set()  # the sandboxes that are running
        self.rest: dict[int, float] = {}  # VRAM it holds while idle, in GB
        self.first = True
        self.sampled = time.monotonic()

    def tick(self, sandboxes: list[Sandbox]) -> None:
        """Takes one reading of every sandbox. DemoGateway.gpu() calls it each time home asks for
        a reading: every SAMPLE seconds while an operation runs and every second otherwise."""
        t = time.monotonic()
        # The steps below are sized for one SAMPLE. Scaled by the time since the last reading,
        # a slow tick moves things as far as the fast ticks it replaces (compounding, as they
        # would), so the meter moves at the same speed whichever rate the pulse runs at.
        samples = max(1.0, (t - self.sampled) / self.SAMPLE)  # of the usual length, since the last
        self.sampled = t
        follow = 1 - (1 - 0.55) ** samples  # how far a sample moves toward its target
        for i in sandboxes:
            key = id(i)
            hist = self.load.setdefault(key, deque([0.0] * self.HISTORY, maxlen=self.HISTORY))
            if i.state != SandboxState.RUNNING:
                self.up.discard(key)
                hist.append(hist[-1] * 0.45**samples)  # spins down rather than dropping to nothing
                continue
            if key not in self.up:  # the ones up at launch have been up a while, with a history
                self.up.add(key)
                self.rest.setdefault(key, random.uniform(1.6, 3.4))
                if self.first:
                    burst = random.randrange(self.HISTORY)
                    hist.extend(
                        random.uniform(0.7, 0.95) if 0 <= k - burst < 12 else random.uniform(0.03, 0.15)
                        for k in range(self.HISTORY)
                    )
            if self.busy_until.get(key, 0) < t and random.random() < 1 - (1 - 0.015) ** samples:
                self.busy_until[key] = t + random.uniform(2, 7)
            target = random.uniform(0.7, 1.0) if self.busy_until.get(key, 0) > t else random.uniform(0.02, 0.14)
            hist.append(hist[-1] + (target - hist[-1]) * follow)
        self.first = False

    def level(self, sandbox: Sandbox) -> float:
        """sandbox's latest GPU load, 0..1 (0 before it has been read)."""
        hist = self.load.get(id(sandbox))
        return hist[-1] if hist else 0.0

    def vram(self, sandbox: Sandbox) -> float:
        """GB of VRAM sandbox holds: what it keeps at rest, plus what its load adds; 0 if it isn't running."""
        if sandbox.state != SandboxState.RUNNING:
            return 0.0
        return self.rest.get(id(sandbox), 2.5) + 4.5 * self.level(sandbox)

    def gpu(self, sandboxes: list[Sandbox]) -> float:
        # One GPU shared by every sandbox: a single busy one takes most of it.
        return clamp(sum(self.level(i) for i in sandboxes if i.state == SandboxState.RUNNING) * 0.6)

    def vram_total(self, sandboxes: list[Sandbox]) -> float:
        """The VRAM all sandboxes hold, kept under the card's size (GPU_GB)."""
        return min(23.6, sum(self.vram(i) for i in sandboxes))
