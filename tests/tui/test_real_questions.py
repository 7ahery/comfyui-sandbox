"""Questions a command asks while home runs it: the TUI's `Prompter`.

A command line home runs in-process asks its questions through the TUI's `Prompter`, which puts
each on the operation's progress and blocks the command's worker thread until the screen answers
(or cancels). These tests play the screen's part on the gateway (the spec's seam B, on the CLI's
fake `Deps`): they read each question as the screen would, answer it, and assert on what the
command then did, as a script would observe it: the result, `installs.json`, the `.env` written,
what was unregistered.
"""

from __future__ import annotations

import asyncio
import base64
import json

import pytest
from test_real_actions import deps, ran_lines
from test_real_gateway import installed
from test_uninstall import REPORT, Distros, remaining, unregisters

import wslcalls
from comfyui_sandbox import envfile
from comfyui_sandbox.tui.gateway import Question, QuestionKind
from comfyui_sandbox.tui.gateway.real import RealGateway
from fakes import CANCEL

PROJECT = "/home/comfy/comfyui-docker"
ENV = """\
PUID=1001
PGID=1002
MODELS_DIR="/home/comfy/comfyui-models"
COMFY_PORT=8188
COMFY_ARGS="--enable-manager"
"""


def answered(deps, argv: list[str], answers: list) -> tuple:
    """Runs argv on the real gateway, as home does, answering each question it asks with the next
    of answers (CANCEL backs out of it, as Esc does). Returns how it ended and the questions, in the
    order they were asked."""
    gateway = RealGateway(deps)
    asked: list[Question] = []

    async def go():
        handle = gateway.run(argv)
        left = list(answers)
        while handle.result is None:
            question = handle.progress.question
            if question is not None and (not asked or question is not asked[-1]):
                asked.append(question)
                answer = left.pop(0)
                handle.cancel() if answer is CANCEL else handle.answer(answer)
            await asyncio.sleep(0.005)
        return handle.result

    return asyncio.run(go()), asked


def port_saved(home, distro: str) -> int:
    doc = json.loads((home / ".comfyui-sandbox" / "installs.json").read_text("utf-8"))
    return next(i["port"] for i in doc["installs"] if i["distro"] == distro)


def env_written(runner) -> dict[str, str] | None:
    """The `.env` config wrote, parsed; None when it wrote none."""
    writes = [s for s in wslcalls.scripts(runner.calls) if s.script == "write_env.sh"]
    if not writes:
        return None
    return envfile.parse(base64.b64decode(writes[-1].env["ENV_B64"]).decode())


@pytest.fixture
def settings(home, runner):
    """A sandbox x whose distro holds ENV as its .env."""
    installed(home, "x", 8188)
    runner.on("wsl.exe", "-d", "x", "-u", "comfy", "--cd", "/", "--exec", "env", "cat", f"{PROJECT}/.env", stdout=ENV)


# -- Settings: config's own menu ----------------------------------------------------------------------


def test_settings_asks_configs_menu_with_its_hints_and_saves_the_change_as_the_cli_would(deps, settings, home, runner):
    result, asked = answered(deps, ["config", "--distro", "x"], ["Port (8188)", "9000", "Apply changes"])

    assert result.ok, result.message
    menu, port, again = asked
    assert menu.kind == QuestionKind.SELECT and menu.message == "What do you want to change?"
    assert [value for _, value in menu.choices][-1] == "Apply changes"
    assert menu.hint and set(menu.hints) == {value for _, value in menu.choices}
    assert port.kind == QuestionKind.TEXT and port.hint
    assert again.message == menu.message
    assert port_saved(home, "x") == 9000 and (env_written(runner) or {}).get("COMFY_PORT") == "9000"
    assert ran_lines(deps) == ["config --distro x"]


def test_a_text_answer_that_fails_its_check_is_asked_again(deps, settings, home):
    result, asked = answered(deps, ["config", "--distro", "x"], ["Port (8188)", "80", "9000", "Apply changes"])

    assert result.ok, result.message
    assert [q.message for q in asked][1:3] == ["Port (1024-65535):"] * 2
    assert asked[1].validate("80") and asked[1].validate("9000") is None  # the screen checks as you type
    assert port_saved(home, "x") == 9000


def test_backing_out_of_a_question_ends_as_interrupted_and_changes_nothing(deps, settings, home, runner):
    result, _ = answered(deps, ["config", "--distro", "x"], ["Port (8188)", "9000", CANCEL])

    assert result.interrupted and result.message == "Interrupted."
    assert env_written(runner) is None and port_saved(home, "x") == 8188


def test_any_question_a_command_asks_is_asked_on_the_screen_never_on_the_terminal(deps, home, prompter):
    installed(home, "x", 8188)
    installed(home, "y", 8189)

    result, [which] = answered(deps, ["stop"], ["x"])  # no --distro, two sandboxes: the CLI asks which

    assert result.ok, result.message
    assert which.message == "Which sandbox?" and [v for _, v in which.choices] == ["x", "y"]
    assert prompter.questions == []  # the terminal's prompter, which Textual's screen would fight
    assert ran_lines(deps) == ["stop"]


# -- Recreate and Uninstall: the typed confirmation ---------------------------------------------------


@pytest.fixture
def distros(runner, environ, home):
    installed(home, "x", 8188)
    d = Distros(runner, environ, ["Ubuntu", "x"])
    runner.on_script("uninstall_report.sh", stdout=REPORT)
    return d


def test_uninstall_asks_for_the_typed_name_and_a_wrong_one_is_refused_as_the_cli_refuses_it(
    deps, distros, runner, home
):
    result, [typed] = answered(deps, ["uninstall", "--distro", "x"], ["comfyui"])

    assert result.exit_code == 8 and "Nothing was changed" in result.message
    assert typed.kind == QuestionKind.TEXT and typed.message == "Type the sandbox name to confirm (x):"
    assert "can't be undone" in typed.hint
    assert typed.confirms == ("x",)  # a type-the-name confirmation: the screen draws it as one
    assert unregisters(runner) == [] and remaining(home) == ["x"]


def test_uninstall_goes_ahead_once_the_name_is_typed(deps, distros, runner, home):
    result, _ = answered(deps, ["uninstall", "--distro", "x"], ["x"])

    assert result.ok, result.message
    assert len(unregisters(runner)) == 1 and remaining(home) == []


def test_backing_out_of_the_typed_name_ends_as_interrupted_and_deletes_nothing(deps, distros, runner, home):
    result, _ = answered(deps, ["uninstall", "--distro", "x"], [CANCEL])

    assert result.interrupted
    assert unregisters(runner) == [] and remaining(home) == ["x"]


def test_recreate_refuses_a_wrong_typed_name_before_touching_anything(deps, home, runner):
    installed(home, "x", 8188)

    result, [typed] = answered(deps, ["reinstall", "--distro", "x"], ["X-files"])

    assert result.exit_code == 8
    assert typed.message == "Type the sandbox name to confirm (x):"
    assert not [c for c in wslcalls.commands(runner.calls) if c.argv[:1] == ("rm",) or "compose" in c.argv]
