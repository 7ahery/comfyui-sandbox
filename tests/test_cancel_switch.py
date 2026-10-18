"""The `Runner` cancel switch (feat-tui-integration 10), through `main(argv, deps)` (seam B).

The TUI runs each command line with a runner a `CancelSwitch` can stop. `run` is the command with
the most cleanup (the keepalive, the container, the run-window marker), so it's the one played here:
the fake runner plays its `wsl.exe` calls, and the switch is flipped while one runs or between two.
`tests/test_runner.py` covers the real runner terminating a real child.
"""

from __future__ import annotations

import io
import threading
import time

import pytest
from test_check import Machine
from test_run import compose_calls
from test_verify import own

import wslcalls
from comfyui_sandbox import cli as cli_module
from comfyui_sandbox.cancel import CancelSwitch
from comfyui_sandbox.deps import Deps
from comfyui_sandbox.instance import instance_name


@pytest.fixture(autouse=True)
def wsl_present(runner, environ, home):
    Machine().script(runner, environ)
    own(home)


@pytest.fixture
def switch() -> CancelSwitch:
    return CancelSwitch()


@pytest.fixture
def main(runner, winapi, home, environ, prompter, http, switch):
    """main(argv) as the TUI calls it: with Deps the switch can stop (Deps.cancellable)."""

    def invoke(*argv: str) -> int:
        deps = Deps(
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
        return cli_module.main(list(argv), deps.cancellable(switch))

    return invoke


def test_flipping_the_switch_interrupts_the_running_call_and_the_cleanup_calls_still_run(main, runner, winapi, switch):
    runner.on_script("watch.sh", then=lambda: (switch.flip(), runner.until_terminated()))

    code = main("run")

    assert code == 130
    watch = wslcalls.script(runner.calls, "watch.sh")
    assert [c.argv for c in runner.terminated] == [watch.call.argv]  # the running call's child was stopped
    [stop] = compose_calls(runner, ("stop",))  # cleanup ran after it, normally
    assert wslcalls.index(runner.calls, stop.call) > wslcalls.index(runner.calls, watch.call)
    assert runner.started[0].terminated  # the keepalive was released
    assert winapi.instances == set()  # and the run-window marker freed


def test_a_cancel_flipped_between_two_calls_stops_before_the_next_one(main, runner, winapi, switch):
    opened = winapi.open_url
    winapi.open_url = lambda url: (opened(url), switch.flip())  # after wait_ready.sh, before watch.sh

    code = main("run")

    assert code == 130
    assert wslcalls.script(runner.calls, "wait_ready.sh")
    assert [run for run in wslcalls.scripts(runner.calls) if run.script == "watch.sh"] == []  # never started
    assert len(compose_calls(runner, ("stop",))) == 1
    assert runner.started[0].terminated
    assert instance_name("comfy-docker") not in winapi.instances


def test_a_second_flip_during_cleanup_does_not_cut_it_short(main, runner, switch):
    runner.on_script("watch.sh", then=switch.flip)
    stops = []
    runner.on_match(
        lambda argv: argv[-3:] == ("docker", "compose", "stop"),
        lambda argv: (switch.flip(), stops.append(argv), (0, ""))[2],
    )

    code = main("run")

    assert code == 130
    assert len(stops) == 1  # flipped again while stopping: the stop still ran to its end
    assert [c.argv for c in runner.terminated] == [wslcalls.script(runner.calls, "watch.sh").call.argv]
    assert runner.started[0].terminated


def test_without_a_switch_the_clis_ctrl_c_is_unchanged(cli, runner):
    def ctrl_c():
        raise KeyboardInterrupt

    runner.on_script("watch.sh", then=ctrl_c)

    result = cli("run", "--json")

    assert result.exit_code == 130
    assert result.json["message"] == "Interrupted."
    assert len(compose_calls(runner, ("stop",))) == 1
    assert runner.terminated == []


def test_a_flip_ends_a_pause_at_once_not_when_its_time_is_up(runner, winapi, home, switch):
    # A command's pauses between tries (start waiting for ComfyUI) are the real clock's here.
    deps = Deps(
        runner=runner, winapi=winapi, home=home, stdout=io.StringIO(), stderr=io.StringIO(), sleep=time.sleep
    ).cancellable(switch)
    ended: list[BaseException] = []

    def pause() -> None:
        try:
            deps.sleep(30)
        except BaseException as exc:
            ended.append(exc)

    pausing = threading.Thread(target=pause)
    pausing.start()
    time.sleep(0.1)  # well into the pause
    flipped = time.monotonic()
    switch.flip()
    pausing.join(timeout=5)

    assert not pausing.is_alive() and time.monotonic() - flipped < 1
    assert [type(exc) for exc in ended] == [KeyboardInterrupt]
