"""Everything a command needs from outside the process, injected (docs/adr/0013)."""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TextIO

from comfyui_sandbox.cancel import CancelSwitch
from comfyui_sandbox.front_end import Launcher, open_tui
from comfyui_sandbox.net import Http, UrllibHttp
from comfyui_sandbox.prompts import Prompter, QuestionaryPrompter
from comfyui_sandbox.runner import Runner, SubprocessRunner
from comfyui_sandbox.winapi import WinApi, WindowsApi


@dataclass
class Deps:
    runner: Runner
    winapi: WinApi
    home: Path
    stdout: TextIO
    stderr: TextIO
    # sys.platform and the environment (ProgramFiles, USERNAME, ...), injected so tests
    # can play any host.
    platform: str = sys.platform
    environ: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    # Terminal questions (ticket 04); tests pass a scripted fake.
    prompter: Prompter = field(default_factory=QuestionaryPrompter)
    # HTTP from Windows (ticket 09); tests pass a scripted fake.
    http: Http = field(default_factory=UrllibHttp)
    # Pauses between retries (ticket 10); tests pass a no-op.
    sleep: Callable[[float], None] = time.sleep
    # Whether stdin and stdout are a terminal: a full-screen TUI opens only when both are, so a
    # script or a pipe never hangs on one (docs/scripting.md, "The TUI"). False unless real()
    # finds one, so a Deps built by hand gets the plain CLI.
    stdin_is_terminal: bool = False
    stdout_is_terminal: bool = False
    # Opens a TUI screen and returns its exit code; tests pass a fake that opens nothing.
    launch: Launcher = open_tui

    @property
    def app_dir(self) -> Path:
        """The CLI's own folder: installs.json and logs/ live here."""
        return self.home / ".comfyui-sandbox"

    def cancellable(self, switch: CancelSwitch) -> Deps:
        """These Deps, for a command that `switch` stops (the TUI's cancel): its runner's calls
        (Runner.cancellable) and its pauses between tries end with the interruption Ctrl+C raises,
        once, so the command's cleanup then runs normally."""

        def sleep(seconds: float) -> None:
            switch.check()
            if self.sleep is time.sleep:
                # The real clock: wait on the switch instead, so a cancel ends the pause the moment
                # it's flipped rather than when the pause is up (start pauses seconds between tries).
                switch.wait(seconds)
            else:
                self.sleep(seconds)  # a test's own clock (a no-op, a short wait): its timing is the test's
            switch.check()

        return replace(self, runner=self.runner.cancellable(switch), sleep=sleep)

    @classmethod
    def real(cls) -> Deps:
        home = Path(os.environ.get("USERPROFILE") or Path.home())
        return cls(
            SubprocessRunner(),
            WindowsApi(),
            home,
            sys.stdout,
            sys.stderr,
            stdin_is_terminal=_is_terminal(sys.stdin),
            stdout_is_terminal=_is_terminal(sys.stdout),
        )


def _is_terminal(stream: TextIO | None) -> bool:
    """Whether stream is a terminal. A GUI-launched Python (pythonw) has no stdin or stdout at all."""
    return stream is not None and stream.isatty()
