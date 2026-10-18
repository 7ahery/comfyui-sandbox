"""This machine's GPU, read live: how busy it is and how much of its memory is used.

Home's bottom bar shows it about once a second (the feat-tui-integration spec, "Home"). Starting
`nvidia-smi` once a second would cost a process start on every reading, so one `nvidia-smi --loop`
runs for as long as the reading is wanted, and its lines are read in the background as they come;
asking for a reading only looks at the latest one, and never waits.

The readings are for the whole machine: per-sandbox numbers would mean asking each distro, with a
`wsl.exe` call on a timer. With no `nvidia-smi` (no NVIDIA driver), or one that finds no GPU, there
are no readings at all, which a front end shows by showing nothing.

Nothing here changes anything; `nvidia-smi` only reports.
"""

from __future__ import annotations

import contextlib
import threading
from dataclasses import dataclass

from comfyui_sandbox.deps import Deps
from comfyui_sandbox.runner import CommandNotFound, Process

NVIDIA_SMI = "nvidia-smi.exe"
# One line per GPU per second: "0, 37, 8123, 24564" (index, utilisation %, memory used and total in
# MiB). The index tells the GPUs of a multi-GPU machine apart, since their lines come one after another.
QUERY = ("--query-gpu=index,utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits", "--loop=1")


@dataclass(frozen=True)
class GpuSample:
    """One reading of the whole machine's GPUs: how busy they are (0..1, the mean across them) and
    their memory, used and in all, in MiB (added up across them)."""

    utilisation: float
    memory_used_mib: int
    memory_total_mib: int


class GpuWatch:
    """The machine's GPU, watched until `stop()`: `reading()` is the latest sample, or None.

    Made by `watch(deps)`, which has already started it. Safe to read from any thread: the lines
    arrive on the runner's reader thread.
    """

    def __init__(self) -> None:
        self._process: Process | None = None  # None: nothing to watch (no nvidia-smi, or stopped)
        self._lock = threading.Lock()
        self._gpus: dict[str, tuple[int, int, int]] = {}  # each GPU's latest line, by its index

    def follow(self, process: Process) -> None:
        """Watches process, the nvidia-smi whose lines come to `heard` (watch() starts it). Its lines
        can arrive before this is called, so `heard` is handed to the runner first."""
        self._process = process

    def heard(self, line: str) -> None:
        """One line of nvidia-smi's output (the runner calls it). A line that isn't a reading (an
        error, or a GPU that can't report its load: "[N/A]") is passed over: it mustn't raise."""
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 4:
            return
        index, *numbers = parts
        try:
            utilisation, used, total = (int(number) for number in numbers)
        except ValueError:
            return
        with self._lock:
            self._gpus[index] = (utilisation, used, total)

    def reading(self) -> GpuSample | None:
        """The latest sample, or None when there is none: nothing started, it has ended (an
        nvidia-smi that found no GPU exits), it was stopped, or no line has come yet."""
        if self._process is None or self._process.poll() is not None:
            return None
        with self._lock:
            gpus = list(self._gpus.values())
        if not gpus:
            return None
        return GpuSample(
            sum(u for u, _, _ in gpus) / len(gpus) / 100,
            sum(used for _, used, _ in gpus),
            sum(total for _, _, total in gpus),
        )

    def stop(self) -> None:
        """Ends nvidia-smi; there are no readings after this. Stopping again does nothing."""
        process, self._process = self._process, None
        if process is not None:
            process.terminate()


def watch(deps: Deps) -> GpuWatch:
    """Starts watching this machine's GPU, and returns the watch; `stop()` it when done.

    The core is for Windows, so on another platform there is nothing to watch (no readings).
    """
    found = GpuWatch()
    if deps.platform == "win32":
        with contextlib.suppress(CommandNotFound):  # no NVIDIA driver: no readings
            found.follow(deps.runner.follow([NVIDIA_SMI, *QUERY], found.heard))
    return found
