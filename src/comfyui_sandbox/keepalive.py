"""The keepalive: a distro with no session idles out and stops, so a child `wsl.exe ... sleep
infinity` holds it up while `install` works (docs/adr/0007).

The child is bound to a kill-on-close Job Object (`winapi.kill_with_parent`), so it ends when
the CLI exits, even on a crash. It never starts or stops the container: it only keeps the
distro up.
"""

from __future__ import annotations

from comfyui_sandbox.errors import CliError
from comfyui_sandbox.runner import Process
from comfyui_sandbox.winapi import WinApi
from comfyui_sandbox.wsl import Wsl


class Keepalive:
    def __init__(self, wsl: Wsl, winapi: WinApi) -> None:
        self._wsl, self._winapi = wsl, winapi
        self._process: Process | None = None

    @property
    def held(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self, distro: str) -> None:
        """Hold `distro` up. Does nothing when already held."""
        if self.held:
            return
        process = self._wsl.start_sleeper(distro)
        try:
            self._winapi.kill_with_parent(process.pid)
        except BaseException:
            process.terminate()
            raise
        if process.poll() is not None:
            raise CliError(f"The keepalive for {distro} ended at once (exit {process.poll()}).")
        self._process = process

    def stop(self) -> None:
        """Let the distro idle out again (the Job Object does the same if the CLI dies)."""
        process, self._process = self._process, None
        if process is not None:
            process.terminate()
