"""`run`'s two modes (docs/scripting.md, "`run`, `start`, `stop` and `open`"; feat-tui-integration 14).

At a terminal, `run` opens the Textual run window through `Deps.launch`; `--plain`, no terminal,
`--json` and `--non-interactive` give the plain run window, which these tests (and test_run.py)
play with the fake Runner. The run lifecycle is the core command's in both modes, so what the
plain mode does is also what the window shows: its plan, its stop notice, ComfyUI's lines.
"""

from __future__ import annotations

import io
from typing import Any

import pytest
from test_check import Machine
from test_update_sandbox import ENV, PROJECT, READY, set_env
from test_verify import own

import wslcalls
from comfyui_sandbox import cli as cli_module
from comfyui_sandbox.deps import Deps
from comfyui_sandbox.front_end import RunWindow
from comfyui_sandbox.instance import instance_name
from comfyui_sandbox.output import Output


@pytest.fixture(autouse=True)
def wsl_present(runner, environ, home):
    Machine().script(runner, environ)
    own(home)


def ran_in_the_distro(runner) -> bool:
    return bool(wslcalls.scripts(runner.calls))


def test_run_at_a_terminal_opens_the_run_window_for_its_sandbox(cli, launcher, runner):
    launcher.exit_code = 0

    result = cli("run", terminal=True)

    assert launcher.opened == [RunWindow("comfy-docker")]
    assert result.exit_code == 0
    assert result.stdout == "" and "Error" not in result.stderr
    assert not ran_in_the_distro(runner)  # the window runs `run` itself; nothing ran twice


def test_the_run_windows_exit_code_is_runs(cli, launcher):
    launcher.exit_code = 1  # e.g. ComfyUI failed: the window showed why, and stayed open until closed

    result = cli("run", terminal=True)

    assert result.exit_code == 1
    assert "Error" not in result.stderr  # the window has said it already: nothing more is printed


@pytest.mark.parametrize(
    "options, terminal",
    [
        pytest.param(("--plain",), {"terminal": True}, id="--plain"),
        pytest.param((), {"terminal": False}, id="no terminal"),
        pytest.param((), {"stdin_terminal": False, "stdout_terminal": True}, id="stdin piped"),
        pytest.param((), {"stdin_terminal": True, "stdout_terminal": False}, id="stdout piped"),
        pytest.param(("--json",), {"terminal": True}, id="--json"),
        pytest.param(("--non-interactive",), {"terminal": True}, id="--non-interactive"),
    ],
)
def test_where_the_run_window_cant_or_mustnt_open_run_is_the_plain_run_window(cli, launcher, runner, options, terminal):
    runner.on_script("watch.sh", stdout="[ok] ComfyUI stopped (container exit code 0)\n")

    result = cli("run", *options, **terminal)

    assert launcher.opened == []
    assert result.exit_code == 0, result.stderr
    assert wslcalls.script(runner.calls, "watch.sh")  # it ran, and held, here
    assert "Close this window to stop ComfyUI." in result.stderr


def test_a_crash_in_the_run_window_goes_through_mains_crash_path(cli, launcher, home):
    launcher.crash = RuntimeError("the screen broke")

    result = cli("run", terminal=True)

    assert result.exit_code == 1
    assert "RuntimeError: the screen broke" in result.stderr
    [log] = (home / ".comfyui-sandbox" / "logs").glob("*-run.log")
    assert "the screen broke" in log.read_text("utf-8")


def test_run_help_at_a_terminal_is_help_not_the_run_window(cli, launcher):
    result = cli("run", "--help", terminal=True)

    assert result.exit_code == 0
    assert launcher.opened == []
    assert "--plain" in result.stdout


class Recording(Output):
    """An Output that keeps, in order, what a front end is told besides the lines: the plan, the
    steps, the standing notice and the plan's end."""

    def __init__(self) -> None:
        self.stderr = io.StringIO()
        super().__init__(io.StringIO(), self.stderr, json_mode=False)
        self.said: list[tuple[str, Any]] = []

    def plan(self, steps) -> None:
        super().plan(steps)
        self.said.append(("plan", [(step.title, step.seconds > 0) for step in steps]))

    def step(self, text: str) -> None:
        super().step(text)
        self.said.append(("step", text))

    def notice(self, text: str) -> None:
        super().notice(text)
        self.said.append(("notice", text))

    def plan_done(self) -> None:
        super().plan_done()
        self.said.append(("plan done", None))


@pytest.fixture
def deps(runner, winapi, home, environ, prompter, http) -> Deps:
    return Deps(
        runner=runner,
        winapi=winapi,
        home=home,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        environ=environ,
        prompter=prompter,
        http=http,
        sleep=lambda seconds: None,
    )


STAGES = [
    ("Starting ComfyUI", True),
    ("Waiting for ComfyUI to answer", True),
    ("Checking that Windows reaches the container", True),
]


def test_run_announces_its_stages_as_a_plan_and_the_stop_notice_through_output(deps, runner):
    runner.on_script("watch.sh", stdout="[ok] ComfyUI stopped (container exit code 0)\n")
    out = Recording()

    result = cli_module.call(["run"], deps, out)

    assert result.exit_code == 0, out.stderr.getvalue()
    assert out.said == [
        ("plan", STAGES),
        ("notice", "Closing this window stops ComfyUI."),  # from the start: closing it stops ComfyUI from then on
        ("step", "Starting ComfyUI"),
        ("step", "Waiting for ComfyUI to answer"),
        ("step", "Checking that Windows reaches the container"),
        ("plan done", None),  # ComfyUI is up: what follows is holding it, not a step
        ("notice", ""),  # stopping: closing the window no longer stops anything
    ]


class Showing(Recording):
    """A front end's Output that shows ComfyUI's own output, as the Textual run window's does."""

    shows_comfyui = True

    def __init__(self) -> None:
        super().__init__()
        self.comfyui_lines: list[str] = []

    def comfyui(self, line: str) -> None:
        self.comfyui_lines.append(line)

    def info(self, text: str) -> None:
        super().info(text)
        self.said.append(("info", text))


AS = ("wsl.exe", "-d", "comfy-docker", "-u", "comfy", "--cd", "/", "--exec", "env")
GIT = (*AS, "docker", "exec", "comfyui", "git", "-C", "/workspace/ComfyUI")
PS = (*AS, "env", "--chdir", PROJECT, "docker", "compose", "ps", "--status", "running", "--quiet")
LOG = (
    "\x1b[32m** ComfyUI startup time: 2026-10-12 09:00:00\x1b[0m\n"
    "ComfyUI version: 0.3.9\n"
    "   0.0 seconds (IMPORT FAILED): /app/ComfyUI/custom_nodes/broken\n"
)


def comfyui_is_on(runner, tag: str, *, channel: str = "latest") -> None:
    """The container runs ComfyUI at release `tag`, and its repository has v0.3.10 as the newest."""
    set_env(
        runner,
        ENV.replace("COMFY_PORT=9000", "COMFY_PORT=8188") + f'COMFY_INSTALL_ARGS="--nvidia --version {channel}"\n',
    )
    runner.on(*PS, stdout="3f2a9c1d7e44\n")
    runner.on(*GIT, "describe", "--tags", "--exact-match", stdout=f"{tag}\n")
    runner.on(
        *GIT, "ls-remote", "--tags", "--refs", "origin", stdout="1111\trefs/tags/v0.3.9\n2222\trefs/tags/v0.3.10\n"
    )


def follows_the_log(runner) -> None:
    runner.on_match(lambda argv: "docker logs --follow" in argv[-1], lambda argv: (0, LOG))


def test_for_an_output_that_shows_comfyui_run_passes_its_log_on_and_says_a_newer_comfyui_is_out(deps, runner):
    comfyui_is_on(runner, "v0.3.9")
    follows_the_log(runner)
    runner.on_script("wait_ready.sh", stdout=READY)
    runner.on_script("watch.sh", stdout="[ok] ComfyUI stopped (container exit code 0)\n")
    out = Showing()

    result = cli_module.call(["run"], deps, out)

    assert result.exit_code == 0, out.stderr.getvalue()
    assert out.comfyui_lines == [
        "** ComfyUI startup time: 2026-10-12 09:00:00",  # its colours left out
        "ComfyUI version: 0.3.9",
        "   0.0 seconds (IMPORT FAILED): /app/ComfyUI/custom_nodes/broken",
    ]
    assert (
        "info",
        "ComfyUI v0.3.10 is available: 'comfyui-sandbox update comfyui --distro comfy-docker' updates it.",
    ) in out.said
    # wait_ready.sh's lines summarise that same log: they'd show it twice (nodes counted twice).
    assert not wslcalls.script(runner.calls, "wait_ready.sh").call.streamed
    [reader] = [p for p in runner.started if "docker logs --follow" in p.call.argv[-1]]
    assert reader.terminated  # the log reader ends with the window


def test_a_second_window_that_shows_comfyui_gets_the_running_ones_log_so_far(deps, runner, winapi):
    winapi.instances.add(instance_name("comfy-docker"))  # the first window holds ComfyUI
    runner.on_match(lambda argv: "docker logs --since" in argv[-1], lambda argv: (0, LOG))
    out = Showing()

    result = cli_module.call(["run"], deps, out)

    assert result.exit_code == 0, out.stderr.getvalue()
    assert result.data["already_running"] is True
    assert out.comfyui_lines == [
        "** ComfyUI startup time: 2026-10-12 09:00:00",
        "ComfyUI version: 0.3.9",
        "   0.0 seconds (IMPORT FAILED): /app/ComfyUI/custom_nodes/broken",
    ]
    assert runner.started == []  # read once, not followed


def test_a_current_comfyui_says_nothing_about_updates(deps, runner):
    comfyui_is_on(runner, "v0.3.10")
    runner.on_script("watch.sh", stdout="[ok] ComfyUI stopped (container exit code 0)\n")
    out = Showing()

    assert cli_module.call(["run"], deps, out).exit_code == 0

    assert not [text for kind, text in out.said if kind == "info" and "available" in str(text)]


def test_a_pinned_comfyui_says_what_it_is_pinned_to_instead_of_a_newer_release(deps, runner):
    comfyui_is_on(runner, "v0.3.9", channel="0.3.9")
    runner.on_script("watch.sh", stdout="[ok] ComfyUI stopped (container exit code 0)\n")
    out = Showing()

    assert cli_module.call(["run"], deps, out).exit_code == 0

    assert not [text for kind, text in out.said if kind == "info" and "available" in str(text)]
    assert (
        "info",
        "ComfyUI is pinned to 0.3.9: 'comfyui-sandbox update comfyui --version latest' unpins it.",
    ) in out.said


def test_the_plain_run_window_neither_follows_the_log_nor_checks_for_updates(cli, runner):
    comfyui_is_on(runner, "v0.3.9")
    runner.on_script("watch.sh", stdout="[ok] ComfyUI stopped (container exit code 0)\n")

    result = cli("run", "--plain", terminal=True)

    assert result.exit_code == 0, result.stderr
    assert "available" not in result.stderr
    assert not [c for c in runner.calls if "ls-remote" in c.argv or "docker logs --follow" in c.argv[-1]]


def test_the_plain_run_window_doesnt_print_the_notice_its_lines_already_say_so(cli, runner):
    runner.on_script("watch.sh", stdout="[ok] ComfyUI stopped (container exit code 0)\n")

    result = cli("run", "--plain", terminal=True)

    assert "Closing this window stops ComfyUI." not in result.stderr
    assert result.stderr.count("Close this window to stop ComfyUI.") == 1
    assert "==> [1/3] Starting ComfyUI" in result.stderr
