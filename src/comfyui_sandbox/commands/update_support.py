"""What the `update` targets share: how a target's update is made ready and run as a step table, and the
container-side targets' steps (ticket 20): run comfy-cli in the container, then restart the stack (this
also re-syncs the Manager) and wait until ComfyUI answers again. The container is started just for those
steps; the command as a whole ends with ComfyUI stopped (`steps.ending_stopped`).

A target's update runs in two halves (comfyui_sandbox.plans): `prepare` reads what its plan depends on
(the channel in the .env, the GPU mode) and picks its steps, before anything changes; the `Prepared` it
returns announces that plan and then runs it. `update <target>` and `update all` both go through it, so
`update all`'s plan is its targets' plans one after the other.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from comfyui_sandbox import plans, steps
from comfyui_sandbox.container import comfy_command, run_in_container
from comfyui_sandbox.output import Outcome, PlannedStep
from comfyui_sandbox.steps import LONG_STEP_TIMEOUT, START_FOR_STEP, Context, ensure_ok

__all__ = ["Context"]  # the steps' context lives in steps.py (backup, recreate... share it); targets import it here

CHECK_TIMEOUT = 60


@dataclass(frozen=True)
class Pending:
    """What a target's `pending()` returns: `pending` is True (an update is waiting), False (current) or None
    (unknown), `detail` says why, and `current` / `available` are the versions when the target has any.
    `pinned` is the release the target is held on, if it is (it is then never pending)."""

    pending: bool | None
    detail: str
    current: str | None = None
    available: str | None = None
    pinned: str | None = None


# A pending check only reads, so on a stopped container it reports unknown instead of starting it.
STOPPED = "unknown: ComfyUI is stopped, so this was not checked (start it, or run the update, to find out)"
NOT_CHECKED = Pending(None, STOPPED)


def unknown(reason: str) -> Pending:
    return Pending(None, f"unknown: {reason}")


def probe(session, install, argv: Sequence[str]):
    """Run a read-only `argv` in the (running) container for a pending check."""
    return run_in_container(session.wsl(), install, argv, timeout=CHECK_TIMEOUT)


def version_key(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", text))


# -- a target's update as a step table ---------------------------------------------------------------


@dataclass
class Prepared:
    """A target's update, made ready: its situation read and its steps chosen, nothing changed yet.

    `planned()` is the plan to announce; `run()` takes the steps (saying each one), closes what they set
    up, and returns `outcome(context)`."""

    name: str  # the target's NAME
    steps: list[plans.Step]
    context: Context
    outcome: Callable[[Any], Outcome]

    def planned(self) -> list[PlannedStep]:
        return plans.planned(self.steps)

    def run(self) -> Outcome:
        self.context.run(self.steps)
        return self.outcome(self.context)


def prepared(name: str, table: Sequence[plans.Step], context: Context, outcome: Callable[[Any], Outcome]) -> Prepared:
    """A target's `Prepared`, with the steps of `table` its context selects."""
    return Prepared(name, plans.select(table, context), context, outcome)


def run_ending_stopped(session, prepare, *args) -> Outcome:
    """What every `update <target>` command does: resolve the install, make the target's update ready
    (`prepare(session, install, *args)`), then announce its plan and run it between stopping ComfyUI and
    ending with it stopped (`steps.ending_stopped`). A target with no steps to take (a pinned ComfyUI)
    changes nothing, so it leaves ComfyUI as it is."""
    session.transcript()
    install = session.resolve_install()
    update = prepare(session, install, *args)
    if not update.steps:
        return update.run()
    return steps.ending_stopped(session, install, update.run, update.planned())


# -- the container-side targets ----------------------------------------------------------------------

# Expected seconds of the steps every container-side target takes (hand-set; see comfyui_sandbox.plans).
RESTART_SECONDS = 20
WAIT_SECONDS = 45


def _start_for_these_steps(context: Context) -> None:
    context.keep_distro_up()
    context.start_for_these_steps()


def _restart(context: Context) -> None:
    steps.restart_container(context.wsl(), context.out, context.install)


def _wait(context: Context) -> None:
    steps.wait_after_restart(context.wsl(), context.out, context.install)


def in_container(what: str, seconds: float, args: Sequence[str]) -> list[plans.Step[Context]]:
    """The step table of a target that runs `comfy --skip-prompt <args>` in the container (`what`, taking
    about `seconds`): ComfyUI started for these steps (it's stopped when they end), the update, then
    `docker compose restart` and the wait until ComfyUI answers."""

    def update(context: Context) -> None:
        ensure_ok(
            run_in_container(
                context.wsl(),
                context.install,
                comfy_command(args),
                on_line=context.out.progress,
                timeout=LONG_STEP_TIMEOUT,
            ),
            what,
        )

    return [
        plans.Step(START_FOR_STEP.title, START_FOR_STEP.seconds, _start_for_these_steps),
        plans.Step(what, seconds, update),
        plans.Step("Restart ComfyUI", RESTART_SECONDS, _restart),
        plans.Step("Wait for ComfyUI to answer", WAIT_SECONDS, _wait),
    ]


def updated_in_container(what: str, data: dict | None = None) -> Callable[[Context], Outcome]:
    """A container-side target's outcome, once its steps have run."""
    return lambda context: Outcome(
        f"{what}: done, ComfyUI answered again after the restart.", {"distro": context.install.distro, **(data or {})}
    )
