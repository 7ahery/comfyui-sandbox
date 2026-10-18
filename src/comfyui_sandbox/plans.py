"""Step tables: how a long command lays out its steps, so it can announce its plan before it starts.

A long command (install, the update targets, and, as they move over, backup, restore, recreate,
rebuild and uninstall) is a table of `Step`s: a title, the seconds it's expected to take, the
function that does it and a `when` condition. From the table and what the command found out
first (its answers, its install, whether ComfyUI runs), `select` picks the steps that will run;
`planned` is that selection as the plan the command announces (`Output.plan`), and `run` takes the
steps in order, saying each one (`Output.step`) as it starts. Because the same selection is both
announced and run, the plan and the steps said can't drift apart.

Why tables rather than a plain function with `step(...)` calls in it: a front end shows the whole
plan, with an overall progress bar weighted by the expected seconds, before the first step
runs (the TUI's live view). That needs every step, and every condition, known up front.

What a step's `when` may look at is whatever the command put in its context before it selected
the steps: decide anything a condition needs first (read the .env, ask the questions), then
select. A step that depends on what an earlier one finds must still be planned (it can do
nothing), or the plan would promise less than runs.

Expected seconds are hand-set estimates written next to each step: rough is fine (they weight a
progress bar, they're never a timeout), but keep their proportions honest, a rebuild in minutes
against a restart in seconds.

What a person sees: at a terminal each planned step is numbered as it starts, `==> [3/5] Update
ComfyUI (latest)`, and the transcript keeps the bare title. A plan is announced once per command,
so the count never resets. The rules the commands keep:

- `install` announces its plan after a new install's `Check the machine`, `Questions` and
  `Preflight`, which run first, unnumbered; the plan ends with `Starting ComfyUI in a new window`
  (its hand-off to `start`). A resumed install has its saved answers and announces at once.
- The `update` targets, `update all` (each target's steps under an `update <target>` step),
  `backup`, `restore`, `reinstall`, `rebuild` and `uninstall` announce plans. So does `run`, which
  then holds ComfyUI with no step running. `start` and `stop` announce none.
- A command that fails before it could read what its plan depends on (a missing `.env`, say)
  announces none, and has changed nothing.
- A destructive command asks its typed confirmation before it announces its plan, so a refused
  confirmation has announced nothing. `uninstall` shows its counts-only report before both.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from comfyui_sandbox.output import Output, PlannedStep


def always(context: object) -> bool:
    """The `when` of a step every situation takes."""
    return True


@dataclass(frozen=True)
class Step[C]:
    """One row of a step table. `run` does the step, given the command's context (of type C);
    `when` says, from that same context, whether this situation takes the step at all."""

    title: str  # what `Output.step` says as it starts: the plan's title for it
    seconds: float  # expected; hand-set (see the module docstring)
    run: Callable[[C], object]
    when: Callable[[C], bool] = always


def select[C](table: Iterable[Step[C]], context: C) -> list[Step[C]]:
    """The steps of `table` this context takes, in the table's order."""
    return [step for step in table if step.when(context)]


def planned(steps: Iterable[Step]) -> list[PlannedStep]:
    """The plan to announce for `steps` (already selected)."""
    return [PlannedStep(step.title, step.seconds) for step in steps]


def run[C](out: Output, steps: Sequence[Step[C]], context: C) -> None:
    """Takes `steps` (already selected and announced) in order, saying each as it starts."""
    for step in steps:
        out.step(step.title)
        step.run(context)
