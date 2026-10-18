"""Home's GPU meters, from the real machine: the core's GPU watch, as `GpuReading`s.

The watch (`comfyui_sandbox.gpu`) runs a process for as long as it's wanted, so this starts it on
the first reading home asks for, not when the gateway is made, and stops it when the gateway is
closed (home closing): a gateway that's never asked starts nothing, and a closed one never starts
it again.
"""

from __future__ import annotations

from comfyui_sandbox import gpu
from comfyui_sandbox.deps import Deps

from ..records import GpuReading

MIB_PER_GB = 1024  # nvidia-smi's MiB, shown as GB: a "24 GB" card reports 24564 MiB


class Meters:
    """The machine's GPU readings for one home session."""

    def __init__(self, deps: Deps) -> None:
        self.deps = deps
        self.watch: gpu.GpuWatch | None = None
        self.closed = False

    def reading(self) -> GpuReading | None:
        """The latest reading, or None (no meters). Starts the watch on the first call."""
        if self.closed:
            return None
        if self.watch is None:
            self.watch = gpu.watch(self.deps)
        sample = self.watch.reading()
        if sample is None:
            return None
        # The total is rounded so the bar reads "/24 GB", not "/23.98828125 GB"; what's used moves
        # every second, and the meter formats it itself.
        return GpuReading(
            sample.utilisation, sample.memory_used_mib / MIB_PER_GB, round(sample.memory_total_mib / MIB_PER_GB, 1)
        )

    def close(self) -> None:
        """Stops the watch for good."""
        self.closed = True
        if self.watch is not None:
            self.watch.stop()
