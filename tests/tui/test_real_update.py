"""Update… for real: `update <target> --distro x` run in-process under home's live view.

The gateway half runs the real gateway on the CLI's fake `Deps` (the spec's seam B): the command's
plan reaches the operation's progress as the steps it will take, each step is marked as the command
says it, and the step it fails at is the one marked failed. The screen half drives home on that
gateway with Textual's pilot: the live view, L, no Esc-cancel, and the outcome card.
"""

from __future__ import annotations

import asyncio
import io
import json
import threading

import pytest
from test_check import Machine
from test_real_actions import ran_lines
from test_real_gateway import installed
from test_real_home_reads import APT, DOCKER_UPGRADE, apt_calls, checks_ended, in_distro, marks
from test_update_sandbox import ENV, PROJECT, READY

import scenarios
from comfyui_sandbox.commands import update
from comfyui_sandbox.deps import Deps
from comfyui_sandbox.logs import transcripts
from comfyui_sandbox.tui.gateway.real import RealGateway
from comfyui_sandbox.tui.home.app import SandboxApp
from comfyui_sandbox.tui.home.model import UPDATE_TARGETS
from comfyui_sandbox.tui.states import StepStatus
from fakes import FakeContainer

UPDATE_COMFYUI = [
    "Stop ComfyUI",
    "Start ComfyUI for this step",
    "Update ComfyUI (latest)",
    "Restart ComfyUI",
    "Wait for ComfyUI to answer",
]
COMFY_UPDATE = ("comfy", "--skip-prompt", "update", "comfy")


@pytest.fixture
def deps(runner, winapi, home, environ, prompter, http) -> Deps:
    """The Deps home was given: all fakes, on a machine whose distro `x` holds a running sandbox
    (its container up, its ComfyUI answering) that updates cleanly."""
    Machine(distros=["Ubuntu", "x"]).script(runner, environ)
    installed(home, "x", 8188)
    runner.on(*in_distro("x", "cat", f"{PROJECT}/.env"), stdout=ENV + 'COMFY_INSTALL_ARGS="--version latest"\n')
    runner.on_script("wait_ready.sh", stdout=READY)
    FakeContainer(runner, running=True)
    return Deps(
        runner=runner,
        winapi=winapi,
        home=home,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        platform="win32",
        environ=environ,
        prompter=prompter,
        http=http,
        sleep=lambda seconds: None,
    )


def comfy_update_in_container(argv) -> bool:
    return "exec" in argv and tuple(argv[-len(COMFY_UPDATE) - 2 : -2]) == COMFY_UPDATE


def progress_of(deps: Deps, argv: list[str]):
    """Runs argv on the real gateway; how it ended, and its progress."""
    gateway = RealGateway(deps)

    async def go():
        handle = gateway.run(argv)
        return await handle.wait(), handle.progress

    return asyncio.run(go())


# -- the gateway --------------------------------------------------------------------------------


def test_an_updates_plan_is_its_progress_and_every_step_ends_done(deps):
    result, progress = progress_of(deps, ["update", "comfyui", "--distro", "x"])

    assert result.ok, result.message
    assert [step.title for step in progress.plan] == UPDATE_COMFYUI
    assert all(step.seconds > 0 for step in progress.plan)
    assert [step.status for step in progress.plan] == [StepStatus.DONE] * len(UPDATE_COMFYUI)
    assert all(step.started is not None and step.ended is not None for step in progress.plan)
    assert "==> [3/5] Update ComfyUI (latest)" in progress.lines  # the full log reads as the CLI's does
    assert ran_lines(deps) == ["update comfyui --distro x"]


def test_the_step_an_update_fails_at_is_marked_failed_and_the_rest_not_run(deps, runner):
    runner.on_match(comfy_update_in_container, lambda argv: (1, "no network"))

    result, progress = progress_of(deps, ["update", "comfyui", "--distro", "x"])

    assert result.exit_code == 1
    assert [step.status for step in progress.plan] == [
        StepStatus.DONE,
        StepStatus.DONE,
        StepStatus.FAILED,
        StepStatus.PENDING,
        StepStatus.PENDING,
    ]


def test_a_cancelled_update_marks_the_step_it_was_on_cancelled(deps, runner):
    started = threading.Event()
    runner.on_match(comfy_update_in_container, lambda argv: (started.set(), runner.until_terminated(), (0, ""))[2])
    gateway = RealGateway(deps)

    async def go():
        handle = gateway.run(["update", "comfyui", "--distro", "x"])
        while not started.is_set():
            await asyncio.sleep(0.01)
        handle.cancel()  # what quitting home does to whatever runs
        return await handle.wait(), handle.progress

    result, progress = asyncio.run(go())

    assert result.interrupted
    assert progress.plan[2].status == StepStatus.CANCELLED


def test_an_update_that_cant_be_made_ready_fails_with_no_plan_and_nothing_changed(deps, runner):
    runner.on(*in_distro("x", "cat", f"{PROJECT}/.env"), returncode=1, stderr="No such file")

    result, progress = progress_of(deps, ["update", "all", "--distro", "x"])

    assert result.exit_code == 1 and "update comfyui failed" in result.message
    assert progress.plan == []


def test_after_an_update_the_sandboxs_update_check_runs_again(deps, runner):
    runner.on(*in_distro("x", *APT), stdout=DOCKER_UPGRADE)
    gateway = RealGateway(deps)
    assert "system" in marks(gateway, "x")
    assert len(apt_calls(runner, "x")) == 1

    async def go():
        return await gateway.run(["update", "system", "--distro", "x"]).wait()

    assert asyncio.run(go()).ok
    runner.on(*in_distro("x", *APT), stdout="Listing...\n")  # the upgrade went in

    assert marks(gateway, "x", until=lambda found: found == ("setup",)) == ("setup",)  # checked anew
    assert len(apt_calls(runner, "x")) == 2


def test_a_target_found_pending_is_marked_without_waiting_for_the_slower_targets_after_it(deps, runner):
    gate = threading.Event()
    runner.on_match(lambda argv: argv[-3:] == ("describe", "--tags", "--exact-match"), lambda argv: (0, "v0.3.9\n"))
    runner.on_match(
        lambda argv: argv[-3:] == ("--tags", "--refs", "origin"), lambda argv: (0, "abc\trefs/tags/v0.3.10\n")
    )
    runner.on(*in_distro("x", *APT), stdout="Listing...\n", then=lambda: gate.wait(10))
    gateway = RealGateway(deps)

    try:
        found = marks(gateway, "x", until=lambda found: "comfyui" in found, timeout=3)
    finally:
        gate.set()

    assert "comfyui" in found  # while `system`'s check is still held
    checks_ended()


def test_an_update_check_that_ends_after_an_update_doesnt_bring_back_a_stale_mark(deps, runner):
    gate = threading.Event()
    runner.on(*in_distro("x", *APT), stdout=DOCKER_UPGRADE, then=lambda: gate.wait(10))
    gateway = RealGateway(deps)
    marks(gateway, "x", until=lambda found: False, timeout=0.2)  # its check is under way, held

    async def go():
        return await gateway.run(["update", "system", "--distro", "x"]).wait()

    try:
        assert asyncio.run(go()).ok
    finally:
        runner.on(*in_distro("x", *APT), stdout="Listing...\n")
        gate.set()

    assert "system" not in marks(gateway, "x", until=lambda found: False, timeout=0.5)


# -- the screens ----------------------------------------------------------------------------------


def home_on(deps: Deps, test, size=(160, 45)) -> None:
    async def scenario() -> None:
        app = SandboxApp(RealGateway(deps))
        async with app.run_test(size=size) as pilot:
            r = scenarios.Rec(pilot)
            await r.wait("Pick a sandbox or an action")
            await r.settle(0.6)
            await test(r)

    asyncio.run(scenario())


def held_update(runner) -> threading.Event:
    """The comfy update in the container takes until the returned event is set (what's on screen
    meanwhile can be looked at); it prints a line first."""
    release = threading.Event()

    def update(argv) -> tuple[int, str]:
        release.wait(30)
        return 0, "Updating ComfyUI to v0.4.12"

    runner.on_match(comfy_update_in_container, update)
    return release


def test_update_runs_the_command_line_it_shows_under_the_live_view(deps, runner):
    release = held_update(runner)

    async def test(r: scenarios.Rec) -> None:
        await r.row("x")
        await r.choose("Update…")
        await r.choose("ComfyUI")
        await r.choose("Newest release (now)")
        await r.wait("Update ComfyUI now?")
        await r.press("enter")
        await r.wait("Update ComfyUI (latest)")
        assert "comfyui-sandbox update comfyui --distro x" in r.text
        for title in UPDATE_COMFYUI:  # the whole plan, before it's done
            assert title in r.text
        assert "%" in r.text  # the overall bar
        release.set()
        await r.wait("done in")

    try:
        home_on(deps, test)
    finally:
        release.set()
    assert ran_lines(deps) == ["update comfyui --distro x"]


def test_l_shows_the_running_updates_full_log(deps, runner):
    release = held_update(runner)

    async def test(r: scenarios.Rec) -> None:
        await r.row("x")
        await r.choose("Update…")
        await r.choose("ComfyUI")
        await r.choose("Newest release (now)")
        await r.wait("Update ComfyUI now?")
        await r.press("enter")
        await r.wait("Update ComfyUI (latest)")
        await r.press("l")
        await r.wait("==> [3/5] Update ComfyUI (latest)")  # the CLI's own lines
        await r.press("escape")
        release.set()
        await r.wait("done in")

    try:
        home_on(deps, test)
    finally:
        release.set()


def test_esc_doesnt_cancel_an_update(deps, runner):
    release = held_update(runner)

    async def test(r: scenarios.Rec) -> None:
        await r.row("x")
        await r.choose("Update…")
        await r.choose("ComfyUI")
        await r.choose("Newest release (now)")
        await r.wait("Update ComfyUI now?")
        await r.press("enter")
        await r.wait("Update ComfyUI (latest)")
        await r.press("escape", "escape")
        await r.settle(0.3)
        assert "again to cancel" not in r.text and "(cancelled)" not in r.text
        release.set()
        await r.wait("done in")

    try:
        home_on(deps, test)
    finally:
        release.set()


def test_the_outcome_card_shows_the_versions_how_to_start_and_the_transcripts_path(deps, runner):
    # The tag it reads is the old one until the update has run (the background check reads it too).
    runner.on_match(
        lambda argv: argv[-3:] == ("describe", "--tags", "--exact-match"),
        lambda argv: (0, "v0.3.10\n" if any(comfy_update_in_container(c.argv) for c in runner.calls) else "v0.3.9\n"),
    )

    async def test(r: scenarios.Rec) -> None:
        await r.row("x")
        await r.choose("Update…")
        await r.choose("ComfyUI")
        await r.choose("Newest release (now)")
        await r.wait("Update ComfyUI now?")
        await r.press("enter")
        await r.wait("v0.3.9 → v0.3.10")
        await r.wait("ComfyUI is stopped. Choose Start")
        await r.wait(transcripts(deps.app_dir)[0].name)

    home_on(deps, test, size=(220, 50))


def test_a_failed_update_shows_the_cores_message_and_the_transcripts_path(deps, runner):
    runner.on_match(comfy_update_in_container, lambda argv: (1, "no network"))

    async def test(r: scenarios.Rec) -> None:
        await r.row("x")
        await r.choose("Update…")
        await r.choose("ComfyUI")
        await r.choose("Newest release (now)")
        await r.wait("Update ComfyUI now?")
        await r.press("enter")
        await r.wait("failed at step 3 of 5")
        await r.wait("Update ComfyUI (latest) failed")
        await r.wait(transcripts(deps.app_dir)[0].name)

    home_on(deps, test, size=(220, 50))


def pinned(home, distro: str, channel: str) -> None:
    """The record of `distro` says its ComfyUI follows `channel` (as `update comfyui --version` leaves it)."""
    path = home / ".comfyui-sandbox" / "installs.json"
    doc = json.loads(path.read_text("utf-8"))
    next(i for i in doc["installs"] if i["distro"] == distro)["answers"] = {"channel": channel}
    path.write_text(json.dumps(doc), "utf-8")


def test_switching_comfyui_to_a_release_runs_update_comfyui_with_that_version(deps, runner):
    async def test(r: scenarios.Rec) -> None:
        await r.row("x")
        await r.choose("Update…")
        await r.choose("ComfyUI")
        await r.choose("A release you pick…")
        await r.wait("Release number")
        await r.press("v", "0", ".", "3", ".", "4", "0", "enter")
        await r.wait("Switch ComfyUI to 0.3.40 now?")
        await r.press("enter")
        await r.wait("Switch ComfyUI to 0.3.40")
        await r.wait("pinned there")  # the card
        await r.wait("Press any key to go back")

    home_on(deps, test, size=(220, 50))
    assert ran_lines(deps) == ["update comfyui --version 0.3.40 --distro x"]
    assert any(comfy_update_in_container(c.argv) and c.argv[-2:] == ("--version", "0.3.40") for c in runner.calls)


def test_a_pinned_sandbox_is_offered_its_pin_and_unpinning_names_latest(deps, runner, home):
    pinned(home, "x", "0.3.40")

    async def test(r: scenarios.Rec) -> None:
        await r.row("x")
        await r.choose("Update…")
        await r.choose("ComfyUI (pinned to 0.3.40)")
        await r.choose("Newest release")
        await r.wait("Update ComfyUI now?")
        await r.press("enter")
        await r.wait("Press any key to go back")

    home_on(deps, test, size=(220, 50))
    assert ran_lines(deps) == ["update comfyui --version latest --distro x"]


def test_update_offers_the_same_targets_as_the_update_command():
    """Update…'s choices are `update`'s targets, by the names the command takes, in its order."""

    assert list(UPDATE_TARGETS) == [target.NAME for target in update.TARGETS]
