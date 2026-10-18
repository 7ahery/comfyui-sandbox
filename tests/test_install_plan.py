"""`install`'s step plan (`Output.plan`, the `plans` module docstring): announced exactly once,
once a new install's check, questions and preflight have decided which steps it takes, filtered by
the answers. Those three run before it, unplanned; a resumed install plans from its saved answers.

Through the spec's seam B: real `install` command lines run in-process (`cli.call`) on the CLI's
fakes, with an `Output` that records the plans and steps it's told (test_step_plans.Recording). The
plans asserted are literals: what install is expected to say it will do.
"""

from __future__ import annotations

import io
import re

import pytest
from test_check import Machine
from test_step_plans import Recording

from comfyui_sandbox import cli as cli_module
from comfyui_sandbox.deps import Deps

INTAKE = ["Check the machine", "Questions", "Preflight"]
WSL_LIMITS = "Set the WSL limits in .wslconfig"
TOOLKIT = "Install the NVIDIA Container Toolkit and test GPU access"
FIRST_START = "Wait for the first start (installs PyTorch and ComfyUI: 10-30 minutes)"
STEPS = [
    WSL_LIMITS,
    "Create the distro",
    "Create the Linux user (no password, no sudo)",
    "Harden the distro (no Windows drives, no interop)",
    "Restart the distro so the hardening applies",
    "Install Docker Engine",
    TOOLKIT,
    "Stop the distro",
    "Prepare the data folder",
    "Deploy ComfyUI and start the container",
    FIRST_START,
    "Create the models structure",
    "Set the Manager security level",
    "Restart ComfyUI",
    "Wait for ComfyUI to answer again",
    "Verify the sandbox (security checks and Windows access)",
    "Create the launcher shortcuts",
    "Stop ComfyUI (it runs from its own window)",
    "Release the keepalive",
    "Starting ComfyUI in a new window",  # the hand-off to `start`
]


def without(*titles: str) -> list[str]:
    return [title for title in STEPS if title not in titles]


@pytest.fixture
def machine() -> Machine:
    return Machine(distros=["Ubuntu"])


@pytest.fixture
def deps(runner, winapi, home, environ, prompter, http, machine) -> Deps:
    machine.script(runner, environ)
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


def ran(deps: Deps, *argv: str) -> Recording:
    out = Recording()
    out.ended = cli_module.call(["install", *argv], deps, out)
    return out


def plans(out: Recording) -> list[list[str]]:
    return [[step.title for step in steps] for kind, steps in out.said if kind == "plan"]


def test_a_new_install_announces_one_plan_with_only_the_steps_its_answers_select(deps):
    out = ran(deps, "--yes", "--memory", "neither")

    assert out.ended.ok, out.ended.message
    assert plans(out) == [without(WSL_LIMITS)]


def test_the_plan_comes_after_preflight_and_before_the_first_step_that_changes_anything(deps):
    out = ran(deps, "--yes")

    assert out.ended.ok, out.ended.message
    said = [(kind, what if kind == "step" else "plan") for kind, what in out.said]
    assert said[: len(INTAKE) + 1] == [("step", title) for title in INTAKE] + [("plan", "plan")]
    assert said[len(INTAKE) + 1] == ("step", STEPS[0])


def test_a_cpu_only_machine_plans_no_nvidia_toolkit(deps, machine, runner, environ):
    machine.nvidia_smi = None
    machine.video_controllers = ["AMD Radeon RX 7900"]
    machine.script(runner, environ)

    out = ran(deps, "--yes")

    assert out.ended.ok, out.ended.message
    assert plans(out) == [without(TOOLKIT)]


def test_each_step_said_is_the_next_planned_step(deps):
    out = ran(deps, "--yes")

    assert out.ended.ok, out.ended.message
    assert out.step_titles[: len(INTAKE + STEPS)] == INTAKE + STEPS


def test_a_resumed_install_announces_its_plan_from_the_saved_answers_with_no_questions(deps, runner, machine, environ):
    runner.on("wsl.exe", "--terminate", returncode=1)  # the first try stops at "Restart the distro"
    assert ran(deps, "--yes", "--memory", "neither").ended.exit_code == 1
    runner.on("wsl.exe", "--terminate")
    machine.distros = ["Ubuntu", "comfy-docker"]
    machine.script(runner, environ)

    out = ran(deps)

    assert out.ended.ok, out.ended.message
    assert out.said[0][0] == "plan"
    assert plans(out) == [without(WSL_LIMITS)]


def test_the_first_start_is_the_step_expected_to_take_longest(deps):
    out = ran(deps, "--yes")

    [steps, *_] = [steps for kind, steps in out.said if kind == "plan"]
    seconds = {step.title: step.seconds for step in steps}
    assert all(value > 0 for value in seconds.values())
    assert max(seconds, key=lambda title: seconds[title]) == FIRST_START
    assert seconds[FIRST_START] >= 10 * 60  # about 15 minutes, the spec's example


def test_the_cli_counter_never_resets(cli, deps, home):
    result = cli("install", "--yes", "--memory", "neither")

    assert result.exit_code == 0, result.stderr
    counted = [(int(k), int(n)) for k, n in re.findall(r"==> \[(\d+)/(\d+)\]", result.stderr)]
    total = len(without(WSL_LIMITS))
    # One total throughout, from 1 to the end: the check, questions and preflight run before it.
    assert counted == [(k, total) for k in range(1, total + 1)]
    assert "==> Check the machine" in result.stderr
    assert f"==> [1/{total}] Create the distro" in result.stderr
    # The transcript keeps the bare titles.
    [log] = (home / ".comfyui-sandbox" / "logs").glob("*-install.log")
    assert "Create the distro" in log.read_text("utf-8") and "[1/" not in log.read_text("utf-8")
