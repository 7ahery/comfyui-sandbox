"""Install a new sandbox and Resume install for real: `install` run in-process, its questions asked
through the TUI's Prompter.

The gateway half runs the real gateway on the CLI's fake `Deps` (the spec's seam B), playing the
screen's part: it reads each question as the screen would and answers it (test_real_questions'
pattern). It asserts what a user or a script observes: the questions asked and their defaults
(compared with the CLI's own, asked by the terminal's scripted Prompter), the plan on the
operation's progress, `installs.json`, the command line the transcript says ran.
"""

from __future__ import annotations

import asyncio
import io
import json
import threading

import pytest
from test_check import Machine
from test_intake_interactive import DEFAULTS
from test_real_actions import ran_lines

import wslcalls
from comfyui_sandbox import cli as cli_module
from comfyui_sandbox.deps import Deps
from comfyui_sandbox.output import Output
from comfyui_sandbox.tui.gateway import Question, QuestionKind
from comfyui_sandbox.tui.gateway.real import RealGateway
from comfyui_sandbox.tui.states import StepStatus
from comfyui_sandbox.winapi import PortOwner
from fakes import CANCEL, FakePrompter

CHANGE = "Change something"


@pytest.fixture
def machine() -> Machine:
    return Machine(distros=["Ubuntu"])


@pytest.fixture
def deps(runner, winapi, home, environ, http, machine) -> Deps:
    """The Deps home was given: all fakes, on a ready RTX 4090 machine with no sandbox yet."""
    machine.script(runner, environ)
    return Deps(
        runner=runner,
        winapi=winapi,
        home=home,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        platform="win32",
        environ=environ,
        prompter=FakePrompter(),
        http=http,
        sleep=lambda seconds: None,
    )


def choice(question: Question, start: str) -> str:
    """The value of question's one choice that starts with start (as FakePrompter matches)."""
    [value] = [value for title, value in question.choices if value.startswith(start)]
    return value


def installing(deps: Deps, argv: list[str], answers: list, *, during=None):
    """Runs argv on the real gateway, answering each question with the next of answers (a select
    by the start of a choice; CANCEL backs out, as Esc does). during(handle, question) is called
    as each question comes. Returns how it ended, the questions and the progress."""
    gateway = RealGateway(deps)
    asked: list[Question] = []

    async def go():
        handle = gateway.run(argv)
        left = list(answers)
        while handle.result is None:
            question = handle.progress.question
            if question is not None and (not asked or question is not asked[-1]):
                asked.append(question)
                if during is not None:
                    during(handle, question)
                answer = left.pop(0)
                if answer is CANCEL:
                    handle.cancel()
                else:
                    handle.answer(choice(question, answer) if question.kind == QuestionKind.SELECT else answer)
            await asyncio.sleep(0.005)
        return handle.result, handle.progress

    result, progress = asyncio.run(go())
    return result, asked, progress


def saved(home) -> list[dict]:
    path = home / ".comfyui-sandbox" / "installs.json"
    return json.loads(path.read_text("utf-8"))["installs"] if path.exists() else []


# -- the questions --------------------------------------------------------------------------------


def test_install_asks_the_clis_questions_in_its_order_with_its_defaults_and_change_something_loops(deps, home):
    answers = [*DEFAULTS[:-1], CHANGE, *DEFAULTS]
    # The CLI's own questions, asked at a terminal (the scripted Prompter), in a home of their own.
    terminal = FakePrompter(answers=list(answers))
    deps.prompter = terminal
    assert cli_module.call(["install"], deps, Output(io.StringIO(), io.StringIO(), json_mode=False)).ok
    # The same machine again, as it was before: no sandbox, no .wslconfig.
    (home / ".comfyui-sandbox" / "installs.json").unlink()
    (home / ".wslconfig").unlink()

    result, asked, _ = installing(deps, ["install"], answers)

    assert result.ok, result.message
    assert [(q.message, tuple(value for _, value in q.choices)) for q in asked] == terminal.asked
    assert [(q.message, q.default) for q in asked] == terminal.defaults
    assert [q.message for q in asked].count("Ready to install?") == 2  # Change something asked them all again
    assert saved(home)[0]["distro"] == "comfy-docker"


def test_the_questions_carry_their_hint_to_the_screen(deps):
    result, asked, _ = installing(deps, ["install"], DEFAULTS)

    assert result.ok, result.message
    models = next(q for q in asked if q.message.startswith("Where should your models live?"))
    assert models.hint and not models.hints


def test_the_plan_is_announced_once_after_the_questions_with_only_the_steps_they_select(deps):
    seen: list[list[tuple[str, StepStatus]]] = []

    def look(handle, question):
        seen.append([(step.title, step.status) for step in handle.progress.plan])

    result, _asked, progress = installing(deps, ["install"], DEFAULTS, during=look)

    assert result.ok, result.message
    assert all(plan == [] for plan in seen)  # nothing is planned while the questions are asked
    titles = [step.title for step in progress.plan]
    assert len(titles) > 15  # the whole setup, announced in one go
    assert titles[0] == "Create the distro" or titles[0].startswith("Set the WSL limits")
    assert not {"Check the machine", "Questions", "Preflight"} & set(titles)
    assert all(step.status == StepStatus.DONE for step in progress.plan), progress.plan


# -- questions in place ---------------------------------------------------------------------------


def test_a_taken_port_is_asked_in_place_and_the_install_goes_on_with_the_new_one(deps, winapi, home):
    winapi.port_owners[8188] = PortOwner(4120, "python.exe")

    result, asked, _ = installing(deps, ["install"], [*DEFAULTS, "Use port 8189"])

    assert result.ok, result.message
    assert asked[-1].message == "Port 8188 is used by python.exe (PID 4120)."
    assert saved(home)[0]["port"] == 8189


def test_a_taken_name_is_asked_in_place_and_the_install_goes_on_with_the_new_one(deps, machine, runner, environ, home):
    machine.distros = ["Ubuntu", "Sandbox"]
    machine.script(runner, environ)
    answers = [*DEFAULTS[:4], *DEFAULTS[5:], "comfyui"]  # no name question: --distro named it

    result, asked, _ = installing(deps, ["install", "--distro", "sandbox"], answers)

    assert result.ok, result.message
    assert any(q.message.startswith("A distro named sandbox already exists") for q in asked)
    assert saved(home)[0]["distro"] == "comfyui"


# -- cancelling ---------------------------------------------------------------------------------


def test_esc_on_a_question_ends_interrupted_with_nothing_created(deps, runner, home):
    result, _asked, _ = installing(deps, ["install"], [*DEFAULTS[:3], CANCEL])

    assert result.exit_code == 130
    assert saved(home) == []
    assert wslcalls.distro_ops(runner.calls, "--install") == []


def test_a_cancel_during_a_step_stops_the_running_call_and_leaves_it_resumable(deps, runner, home, machine, environ):
    started = threading.Event()
    runner.on_script("install_docker.sh", then=lambda: (started.set(), runner.until_terminated()))
    gateway = RealGateway(deps)

    async def go():
        handle = gateway.run(["install"])
        left = list(DEFAULTS)
        asked = None
        while not started.is_set():
            question = handle.progress.question
            if question is not None and question is not asked:
                asked = question
                handle.answer(choice(question, left.pop(0)))
            await asyncio.sleep(0.005)
        handle.cancel()  # Esc twice
        return await handle.wait(), handle.progress

    result, progress = asyncio.run(go())

    assert result.interrupted
    assert [call for call in runner.terminated if b"install_docker.sh" in (call.stdin or b"")]
    [step] = [step for step in progress.plan if step.status == StepStatus.CANCELLED]
    assert step.title == "Install Docker Engine"
    assert saved(home)[0]["state"] == "in_progress"  # Incomplete: Resume install carries on

    # Resume install: the saved answers, no questions, exactly the command line shown.
    runner.on_script("install_docker.sh")
    machine.distros = ["Ubuntu", "comfy-docker"]
    machine.script(runner, environ)
    resumed, asked, progress = installing(deps, ["install", "--distro", "comfy-docker"], [])

    assert resumed.ok, resumed.message
    assert asked == []
    assert resumed.data["resumed"] is True
    assert saved(home)[0]["state"] == "complete"
    assert ran_lines(deps)[0] == "install --distro comfy-docker"
    assert progress.plan[0].title == "Set the WSL limits in .wslconfig"
