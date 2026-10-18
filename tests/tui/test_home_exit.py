"""How home ends, as `run_home` reports it to the CLI (docs/scripting.md, "The TUI").

Quitting is exit 0 (the snapshot scenarios record each app's return code on Quit). A crash anywhere in
home must not end as Textual's own exit code 1 with nothing for the CLI to log: `run_home` raises
it once Textual has closed and given the terminal back, so `main()`'s crash path can write the
transcript and print its path (tests/test_entry.py covers that half).
"""

from __future__ import annotations

import pytest

from comfyui_sandbox.tui.gateway.demo import DemoGateway
from comfyui_sandbox.tui.home.app import run_home


class BrokenGateway(DemoGateway):
    """A gateway whose first read fails, as a bug in a real one would."""

    async def sandboxes(self):
        raise RuntimeError("no sandboxes today")


def test_a_crash_in_home_is_raised_after_the_screen_has_closed() -> None:
    with pytest.raises(RuntimeError, match="no sandboxes today"):
        run_home(BrokenGateway(), headless=True)
