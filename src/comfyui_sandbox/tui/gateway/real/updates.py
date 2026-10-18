"""Home's "update available" marks, from the real machine: the core's update check, run in the
background once per session per sandbox, and cached.

The check (`commands.update.available`) asks inside the sandbox's container and distro, so it
takes seconds, and asking a stopped sandbox would wake its distro to learn next to nothing (most
targets can only be checked while ComfyUI runs). So a sandbox is checked the first time a listing
finds it Running, which for one already running is home's first read. Until its check ends, and
for good if the check fails, it has no mark: the mark only ever says an update is there.

The checks run on daemon threads, not asyncio's worker threads: nothing waits for them, home
closing included (asyncio's executor would hold the exit until a minute-long check ended).
"""

from __future__ import annotations

import threading

from comfyui_sandbox import queries
from comfyui_sandbox.commands import update
from comfyui_sandbox.commands.update_support import Pending
from comfyui_sandbox.deps import Deps

from ...states import SandboxState
from ..records import Sandbox


class UpdateChecks:
    """The update checks of one home session, and what they found, by sandbox name."""

    def __init__(self, deps: Deps) -> None:
        self.deps = deps
        # Sandboxes whose check has started, each with a token for that check: never asked again,
        # until forget(). The token tells a check that ended after a forget() (whose finding is
        # stale) from the one asked since.
        self.asked: dict[str, object] = {}
        self.found: dict[str, dict[str, Pending]] = {}  # what each finished check found, by target
        self._lock = threading.Lock()

    def mark(self, rows: list[Sandbox]) -> None:
        """Puts on each row the updates its check found so far, and starts the check of each
        running sandbox not yet asked about. Returns at once."""
        for row in rows:
            # The finding is read under the lock too: a check's thread writes it, and forget()
            # drops it, so outside the lock a row could get a finding forget() had just dropped.
            with self._lock:
                start = row.state == SandboxState.RUNNING and row.name not in self.asked
                if start:
                    token = self.asked[row.name] = object()
                found = self.found.get(row.name, {})
                row.updates = tuple(found)
                row.update_versions = {
                    target: (check.current, check.available)
                    for target, check in found.items()
                    if check.current and check.available
                }
            if start:
                threading.Thread(
                    target=self.check, args=(row.name, token), daemon=True, name=f"update check: {row.name}"
                ).start()

    def forget(self, name: str | None = None) -> None:
        """Drops what's known about sandbox `name` (None: every sandbox), so it's checked again
        the next time a listing finds it running: after an update, whose targets the old finding
        may still name. Until then it has no mark (an update ends with ComfyUI stopped, and a
        stopped sandbox isn't checked)."""
        with self._lock:
            for key in [name] if name is not None else list(self.asked):
                self.asked.pop(key, None)
                self.found.pop(key, None)

    def check(self, name: str, token: object) -> None:
        """The check of sandbox `name`, on its own thread. The install is read again here, off
        home's thread, rather than carried on the row, which holds only what home shows."""

        def keep(target: str, check: Pending) -> None:
            # Each target's finding is kept as it comes: the targets after it can take minutes
            # (or fail), and the mark of one already found shouldn't wait for them.
            with self._lock:
                if self.asked.get(name) is token:  # not forgotten meanwhile
                    self.found.setdefault(name, {})[target] = check

        try:
            install = next(i for i in queries.sandboxes(self.deps) if i.distro == name)
            update.pending_checks(self.deps, install, on_found=keep)
        except Exception:
            return
