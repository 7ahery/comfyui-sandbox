"""A long operation's steps fold around the current one when they don't all fit: the steps before
it into "✓ N steps done", the ones after into "○ N more steps".

Checked at the edges: no steps, an exact fit, one over, and the narrowest budget (3 rows), plus
the rules that hold at every size: never more rows than the budget, the current step always in
sight, and never a folded row that stands for a single step.
"""

import time

import pytest

from comfyui_sandbox.tui.gateway import Progress, Step
from comfyui_sandbox.tui.home.live_view import LiveView
from comfyui_sandbox.tui.states import StepStatus


def view(n: int, *, running: int | None = None, failed: int | None = None) -> LiveView:
    """A live view of n steps: the ones before `running` (or `failed`) done, that one running (or
    failed, and the operation over), the rest not started. With neither, nothing has started."""
    # step_rows reads only the steps and their states; the app is for measuring the screen.
    progress = Progress([Step(f"step {k}", 1.0) for k in range(n)])
    v = LiveView(None, "Updating", progress)  # ty: ignore[invalid-argument-type]  # no app: only the steps are measured
    current = running if running is not None else failed
    for k in range(current or 0):
        progress.begin(k)
        progress.end(k, StepStatus.DONE)
    if running is not None:
        progress.begin(running)
    if failed is not None:
        progress.begin(failed)
        progress.end(failed, StepStatus.FAILED)
        v.ended = time.monotonic()
    return v


def names(v: LiveView, budget: int) -> list[str]:
    return [row[1].plain for row in v.step_rows(time.monotonic(), tail=False, budget=budget)]


def test_no_steps_draw_no_rows() -> None:
    assert names(view(0), 5) == []


def test_steps_that_exactly_fit_are_all_shown() -> None:
    assert names(view(5, running=2), 5) == ["step 0", "step 1", "step 2", "step 3", "step 4"]


def test_one_over_at_the_start_folds_the_steps_to_come() -> None:
    assert names(view(6), 5) == ["step 0", "step 1", "step 2", "step 3", "2 more steps"]


def test_one_over_at_the_end_folds_the_steps_done() -> None:
    assert names(view(6, running=5), 5) == ["2 steps done", "step 2", "step 3", "step 4", "step 5"]


def test_a_fold_of_one_step_shows_the_step_instead() -> None:
    # Near the start: "1 steps done" would take the same row as step 0 itself.
    assert names(view(6, running=1), 5) == ["step 0", "step 1", "step 2", "step 3", "2 more steps"]
    # Near the end: the same for the last step.
    assert names(view(6, running=2), 5) == ["2 steps done", "step 2", "step 3", "step 4", "step 5"]


def test_many_steps_keep_a_window_around_the_current_one() -> None:
    assert names(view(20, running=10), 6) == ["9 steps done", "step 9", "step 10", "step 11", "step 12", "7 more steps"]


def test_the_narrowest_budget_shows_just_the_current_step_between_the_folds() -> None:
    assert names(view(20, running=10), 3) == ["10 steps done", "step 10", "9 more steps"]


def test_after_a_failure_the_steps_after_it_were_not_run() -> None:
    assert names(view(20, failed=3), 5) == ["3 steps done", "step 3", "step 4", "step 5", "14 steps not run"]


@pytest.mark.parametrize("n", [4, 7, 20])
def test_at_every_budget_the_current_step_shows_and_the_rows_fit(n: int) -> None:
    for budget in range(3, n + 2):
        for current in range(n):
            got = names(view(n, running=current), budget)
            assert len(got) <= budget, (budget, current, got)
            assert f"step {current}" in got, (budget, current, got)
            assert "1 steps done" not in got and "1 more steps" not in got, (budget, current, got)


@pytest.mark.parametrize("n", [1, 2, 3])
@pytest.mark.parametrize("budget", [-3, 0, 1, 2])
def test_a_budget_below_the_narrowest_never_reads_past_the_steps(n: int, budget: int) -> None:
    # A short window (a long explanation above the steps) can leave less room than the narrowest
    # fold; a plan of a few steps then crashed the page with an IndexError instead of drawing.
    assert names(view(n, running=0), budget)
