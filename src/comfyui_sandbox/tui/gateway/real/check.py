"""Check this PC, from the real machine: the core's `check`, finding by finding, as `CheckItem`s.

Nothing here knows what's checked: the rows are whatever the core's check finds, in its order
(on Windows: the host, the GPU, virtualization, WSL and the install's defaults), so a core for
another platform brings its own rows and the screen stays as it is.
"""

from __future__ import annotations

import asyncio

from comfyui_sandbox.commands import check
from comfyui_sandbox.deps import Deps

from ..records import CheckItem


async def check_this_pc(deps: Deps) -> list[CheckItem]:
    """The core check's findings. It asks Windows and wsl.exe, which takes seconds, so it runs on
    a worker thread: the screen keeps drawing meanwhile."""
    found = await asyncio.to_thread(check.findings, deps)
    return [CheckItem(item.label, item.value, item.ok, item.fixed_by_prereqs) for item in found]
