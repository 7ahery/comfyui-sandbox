"""Step plans (`Output.plan`, the `plans` module docstring): a long command announces every step it
will take, each with its expected seconds, before the first one runs, and then each `step(...)` it
says is the next one of that plan.

Through the spec's seam B: real command lines run in-process (`cli.call`) on the CLI's fakes, with an
`Output` that records what it's told, as a front end's would. The plans asserted are literals: what
the command is expected to say it will do in each situation, not what the code computes.
"""

from __future__ import annotations

import io
import json
import re
from typing import Any

import pytest
from test_backup_restore import AS_USER, BACKUPS
from test_check import Machine
from test_uninstall import Distros
from test_update_sandbox import ANSWERS, ENV, READY, set_env
from test_update_targets import IN_CONTAINER
from test_verify import own

from comfyui_sandbox import cli as cli_module
from comfyui_sandbox.deps import Deps
from comfyui_sandbox.output import CommandResult, Output, PlannedStep
from fakes import FakeContainer

DOCKER = "docker-ce, docker-ce-cli, containerd.io, docker-buildx-plugin, docker-compose-plugin"
IN_THE_CONTAINER = ["Start ComfyUI for this step", "{what}", "Restart ComfyUI", "Wait for ComfyUI to answer"]


def in_container(what: str) -> list[str]:
    return [title.format(what=what) for title in IN_THE_CONTAINER]


TARGET_STEPS = {
    "comfyui": in_container("Update ComfyUI (latest)"),
    "nodes": in_container("Update the custom nodes"),
    "comfy-cli": in_container("Update comfy-cli"),
    "system": ["Refresh the package lists", f"Upgrade {DOCKER}, nvidia-container-toolkit", "Restart Docker"],
    "setup": ["Merge .env", "Redeploy the docker files and rebuild", "Wait for ComfyUI to answer"],
}
TARGETS = pytest.mark.parametrize("target", list(TARGET_STEPS))


class Recording(Output):
    """An Output that remembers the plan and the steps it's told, in order, as a front end would."""

    ended: CommandResult  # how the command ended: the test's `ran()` sets it

    def __init__(self) -> None:
        self.stderr = io.StringIO()
        super().__init__(io.StringIO(), self.stderr, json_mode=False)
        self.said: list[tuple[str, Any]] = []

    def plan(self, steps) -> None:
        self.said.append(("plan", list(steps)))
        super().plan(steps)

    def step(self, text: str) -> None:
        self.said.append(("step", text))
        super().step(text)

    @property
    def plan_titles(self) -> list[str]:
        [plan] = [steps for kind, steps in self.said if kind == "plan"]
        return [step.title for step in plan]

    @property
    def step_titles(self) -> list[str]:
        return [text for kind, text in self.said if kind == "step"]


@pytest.fixture
def deps(runner, winapi, home, environ, prompter, http) -> Deps:
    """A sandbox from an older CLI (so `update setup` has work), on a fake distro that answers."""
    Machine().script(runner, environ)
    own(home)
    path = home / ".comfyui-sandbox" / "installs.json"
    doc = json.loads(path.read_text("utf-8"))
    doc["installs"][0].update(deployed_by="0.0.1", answers=ANSWERS.to_dict())
    path.write_text(json.dumps(doc), "utf-8")
    set_env(runner, ENV + 'COMFY_INSTALL_ARGS="--nvidia --version latest"\n')
    runner.on_script("wait_ready.sh", stdout=READY)
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
    result = cli_module.call(list(argv), deps, out)
    out.ended = result
    return out


# -- each target, and update all ------------------------------------------------------------------


@TARGETS
def test_a_target_announces_its_plan_before_its_first_step(deps, runner, target):
    FakeContainer(runner, running=False)

    out = ran(deps, "update", target)

    assert out.ended.ok, out.ended.message
    assert out.said[0][0] == "plan"
    assert out.plan_titles == TARGET_STEPS[target]


@TARGETS
def test_a_running_sandbox_is_stopped_first_and_its_plan_says_so(deps, runner, target):
    FakeContainer(runner, running=True)

    out = ran(deps, "update", target)

    assert out.ended.ok, out.ended.message
    assert out.plan_titles == ["Stop ComfyUI", *TARGET_STEPS[target]]


@pytest.mark.parametrize("was_running", [True, False], ids=["running", "stopped"])
@pytest.mark.parametrize("target", [*TARGET_STEPS, "all"])
def test_each_step_said_is_the_next_planned_step(deps, runner, target, was_running):
    FakeContainer(runner, running=was_running)

    out = ran(deps, "update", target)

    assert out.ended.ok, out.ended.message
    assert out.step_titles == out.plan_titles


def test_update_all_plans_every_target_under_its_name_in_order(deps, runner):
    FakeContainer(runner, running=True)

    out = ran(deps, "update", "all")

    assert out.plan_titles == [
        "Stop ComfyUI",
        *(title for target, steps in TARGET_STEPS.items() for title in [f"update {target}", *steps]),
    ]


def test_the_plan_follows_the_situation_a_cpu_sandbox_and_a_nightly_one(deps, runner, home):
    own(home, gpu="cpu")
    path = home / ".comfyui-sandbox" / "installs.json"
    doc = json.loads(path.read_text("utf-8"))
    doc["installs"][0].update(answers=ANSWERS.to_dict())
    path.write_text(json.dumps(doc), "utf-8")
    set_env(runner, ENV + 'COMFY_INSTALL_ARGS="--version nightly"\n')
    FakeContainer(runner, running=False)

    out = ran(deps, "update", "all")

    assert f"Upgrade {DOCKER}" in out.plan_titles  # no NVIDIA toolkit on CPU
    assert "Update ComfyUI (nightly)" in out.plan_titles


def test_every_planned_step_expects_some_time_and_the_work_most(deps, runner):
    FakeContainer(runner, running=True)

    out = ran(deps, "update", "all")

    [plan] = [steps for kind, steps in out.said if kind == "plan"]
    assert all(isinstance(step, PlannedStep) and step.seconds >= 0 for step in plan)
    seconds = {step.title: step.seconds for step in plan}
    # Hand-set estimates: a rebuild takes minutes, merging a file next to nothing.
    assert seconds["Redeploy the docker files and rebuild"] > seconds["Restart ComfyUI"] > seconds["Merge .env"]


# -- failures ---------------------------------------------------------------------------------------


def test_a_failed_update_still_announced_its_whole_plan_and_stops_at_the_failed_step(deps, runner):
    FakeContainer(runner, running=False)
    runner.on(*IN_CONTAINER, "--skip-prompt", "node", returncode=1, stderr="node boom")

    out = ran(deps, "update", "all")

    assert out.ended.exit_code == 1
    assert len(out.plan_titles) > len(out.step_titles)
    assert out.step_titles == out.plan_titles[: len(out.step_titles)]
    assert out.step_titles[-1] == "Update the custom nodes"


# -- the CLI's plain output -------------------------------------------------------------------------


def test_the_cli_numbers_each_planned_step(cli, deps, runner):
    FakeContainer(runner, running=True)

    result = cli("update", "comfyui")

    assert result.exit_code == 0, result.stderr
    assert "==> [1/5] Stop ComfyUI" in result.stderr
    assert "==> [3/5] Update ComfyUI (latest)" in result.stderr
    assert "==> [5/5] Wait for ComfyUI to answer" in result.stderr


def test_a_step_outside_the_plan_reads_as_before(cli, deps, runner):
    """A command that announces no plan (stop is short) prints its steps without a counter."""
    FakeContainer(runner, running=True)

    result = cli("stop")

    assert "==> Stop" in result.stderr and "[1/" not in result.stderr


# -- backup, restore, recreate, rebuild and uninstall -------------------------------------------------
# (feat-tui-integration 12: every long command reads the same way, at a terminal or in home's live view.)

BACKUP_TAR = re.compile(r"^Write comfyui-\d{8}-\d{6}\.tar$")
RESTORE = [
    "Restore comfyui-20260101-100000.tar",
    "Start ComfyUI for this step",
    "Wait for ComfyUI to answer",
    "Reinstall the custom nodes from the snapshot",
    "Restart ComfyUI to load the nodes",
    "Wait for ComfyUI to answer again",
]
RECREATE = [
    "Stop and remove the container",
    "Delete data/",
    "Rebuild the image without cache",
    "Start ComfyUI",
    "Wait for the first start (installs PyTorch and ComfyUI: 10-30 minutes)",
    "Create the models structure",
    "Set the Manager security level",
    "Restart ComfyUI",
    "Wait for ComfyUI to answer again",
]
REBUILD = ["Rebuild the image (docker compose up -d --build)", "Wait for ComfyUI to answer"]
UNINSTALL = ["Stop comfy-docker", "Unregister comfy-docker", "Remove the shortcuts and the install record"]


def with_a_backup(runner) -> None:
    runner.on(*AS_USER, "ls", "-1", BACKUPS, stdout="comfyui-20260101-100000.tar\n")


def test_backup_of_a_running_sandbox_plans_the_snapshot_and_the_tar(deps, runner):
    FakeContainer(runner, running=True)

    out = ran(deps, "backup")

    assert out.ended.ok, out.ended.message
    assert out.said[0][0] == "plan"
    assert out.plan_titles[0] == "Save the custom-node snapshot"
    assert len(out.plan_titles) == 2 and BACKUP_TAR.match(out.plan_titles[1])
    assert out.step_titles == out.plan_titles


def test_backup_of_a_stopped_sandbox_plans_starting_it_first(deps, runner):
    FakeContainer(runner, running=False)

    out = ran(deps, "backup")

    assert out.ended.ok, out.ended.message
    assert out.plan_titles[:3] == [
        "Start ComfyUI for the backup",
        "Wait for ComfyUI to answer",
        "Save the custom-node snapshot",
    ]
    assert len(out.plan_titles) == 4 and BACKUP_TAR.match(out.plan_titles[3])
    assert out.step_titles == out.plan_titles


@pytest.mark.parametrize("was_running", [True, False], ids=["running", "stopped"])
@pytest.mark.parametrize(
    "argv, plan",
    [
        (("restore", "--backup", "comfyui-20260101-100000.tar", "--confirm", "comfy-docker"), RESTORE),
        (("reinstall", "--confirm", "comfy-docker"), RECREATE),
        (("rebuild",), REBUILD),
    ],
    ids=["restore", "reinstall", "rebuild"],
)
def test_a_command_that_ends_stopped_plans_its_steps_after_stopping_comfyui(deps, runner, argv, plan, was_running):
    FakeContainer(runner, running=was_running)
    with_a_backup(runner)

    out = ran(deps, *argv)

    assert out.ended.ok, out.ended.message
    assert out.said[0][0] == "plan"
    assert out.plan_titles == [*(["Stop ComfyUI"] if was_running else []), *plan]
    assert out.step_titles == out.plan_titles


def test_uninstall_plans_its_steps_after_the_confirmation(deps, runner, environ):
    Distros(runner, environ, ["Ubuntu", "comfy-docker"])

    out = ran(deps, "uninstall", "--confirm", "comfy-docker")

    assert out.ended.ok, out.ended.message
    assert out.plan_titles == UNINSTALL
    assert out.step_titles == out.plan_titles


def test_uninstall_of_a_distro_already_gone_plans_only_the_cleanup(deps, runner, environ):
    Distros(runner, environ, ["Ubuntu"])

    out = ran(deps, "uninstall", "--confirm", "comfy-docker")

    assert out.ended.ok, out.ended.message
    assert out.plan_titles == ["Remove the shortcuts and the install record"]
    assert out.step_titles == out.plan_titles


def test_the_typed_confirmation_comes_before_the_plan(deps, runner, prompter):
    """The plan says what will run: a refused confirmation has announced nothing."""
    FakeContainer(runner, running=False)
    prompter.answers.append("not-it")

    out = ran(deps, "reinstall")

    assert out.ended.exit_code == 8
    assert out.said == []


@pytest.mark.parametrize(
    "argv",
    [
        ("backup",),
        ("restore", "--backup", "comfyui-20260101-100000.tar", "--confirm", "comfy-docker"),
        ("reinstall", "--confirm", "comfy-docker"),
        ("rebuild",),
    ],
)
def test_the_long_commands_expect_some_time_for_every_step(deps, runner, argv):
    FakeContainer(runner, running=True)
    with_a_backup(runner)

    out = ran(deps, *argv)

    [plan] = [steps for kind, steps in out.said if kind == "plan"]
    assert all(step.seconds > 0 for step in plan)


def test_the_slowest_step_of_a_recreate_is_its_first_start(deps, runner):
    FakeContainer(runner, running=False)

    out = ran(deps, "reinstall", "--confirm", "comfy-docker")

    [plan] = [steps for kind, steps in out.said if kind == "plan"]
    assert max(plan, key=lambda step: step.seconds).title.startswith("Wait for the first start")
