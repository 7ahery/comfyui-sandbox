"""The real gateway's reads: home's sandboxes, their states and their notes, from the real machine.

They run on the CLI's fake `Deps` (the spec's seam B): `installs.json` is a file in a temporary
home, the fake winapi holds the run-window markers and names who holds a port, and the fake Http
plays what answers on each port. What's asserted is what the `Gateway` answers, by meaning (a
state, a note), never how the core found it out.
"""

from __future__ import annotations

import asyncio
import io
import json
import re
from pathlib import Path
from typing import Any

import pytest
from test_check import Machine

import scenarios
import wslcalls
from comfyui_sandbox.deps import Deps
from comfyui_sandbox.instance import instance_name, stopping_name
from comfyui_sandbox.tui.gateway.real import RealGateway
from comfyui_sandbox.tui.home.app import SandboxApp
from comfyui_sandbox.tui.states import SandboxState
from comfyui_sandbox.winapi import PortOwner

WINDOWS_COMFYUI = '{"system": {"os": "win32"}}'
OLD_WINDOWS_COMFYUI = '{"system": {"os": "nt"}}'
OLD_CONTAINER = '{"os": "posix"}'  # older ComfyUI builds report os.name, at the top level
NOT_COMFYUI = "<html>a router's admin page</html>"


def installed(home: Path, distro: str, port: int, *, finished: bool = True, models: dict | None = None) -> None:
    """Adds a sandbox to installs.json, as `install` records it (docs/scripting.md, "installs.json")."""
    path = home / ".comfyui-sandbox" / "installs.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    doc: dict[str, Any] = json.loads(path.read_text("utf-8")) if path.exists() else {"schema": 1, "installs": []}
    doc["installs"].append(
        {
            "distro": distro,
            "linux_user": "comfy",
            "port": port,
            "state": "complete" if finished else "in_progress",
            "models": models or {"mode": "wsl", "path": "/home/comfy/comfyui-models"},
            "gpu": {"mode": "nvidia", "name": "RTX 4090"},
            "deployed_by": "comfyui-sandbox 1.0.0",
        }
    )
    path.write_text(json.dumps(doc), "utf-8")


@pytest.fixture
def deps(runner, winapi, home, environ, prompter, http) -> Deps:
    """The Deps main() would build for a command, all fakes."""
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


def listed(deps: Deps) -> dict:
    """What the real gateway lists, by name."""
    return {s.name: s for s in asyncio.run(RealGateway(deps).sandboxes())}


def window_open(winapi, distro: str) -> None:
    winapi.instances.add(instance_name(distro))


# -- the listing ----------------------------------------------------------------------------------


def test_every_sandbox_in_installs_json_is_listed_with_its_port_and_models_folder(deps, home):
    installed(home, "comfyui", 8188)
    installed(home, "work", 8190, models={"mode": "windows", "path": r"D:\AI\models"})

    sandboxes = listed(deps)

    assert list(sandboxes) == ["comfyui", "work"]
    assert (sandboxes["comfyui"].port, sandboxes["comfyui"].models) == (8188, "/home/comfy/comfyui-models")
    assert (sandboxes["work"].port, sandboxes["work"].models) == (8190, r"D:\AI\models")


def test_with_no_installs_json_there_are_no_sandboxes(deps):
    assert listed(deps) == {}


# -- the state rule -------------------------------------------------------------------------------
# Incomplete, then Starting, then Running, else Stopped (the spec's state rule, in that order).


def test_a_sandbox_whose_install_didnt_finish_is_incomplete_whatever_answers_on_its_port(deps, home, winapi):
    installed(home, "half", 8188, finished=False)
    window_open(winapi, "half")  # a ComfyUI answers and a window is open: still incomplete

    assert listed(deps)["half"].state == SandboxState.INCOMPLETE


def test_a_run_window_whose_comfyui_doesnt_answer_yet_is_starting(deps, home, winapi, http):
    installed(home, "comfyui", 8188)
    window_open(winapi, "comfyui")
    http.refuse(8188)

    assert listed(deps)["comfyui"].state == SandboxState.STARTING


@pytest.mark.parametrize("window", [True, False], ids=["with a run window", "with no run window"])
@pytest.mark.parametrize("reply", ['{"system": {"os": "linux"}}', OLD_CONTAINER], ids=["linux", "posix"])
def test_a_sandbox_whose_own_comfyui_answers_is_running(deps, home, winapi, http, window, reply):
    installed(home, "comfyui", 8188)
    if window:
        window_open(winapi, "comfyui")
    http.answer(8188, reply)

    assert listed(deps)["comfyui"].state == SandboxState.RUNNING


def test_a_finished_sandbox_with_no_window_and_nothing_answering_is_stopped(deps, home, http):
    installed(home, "comfyui", 8188)
    http.refuse(8188)

    assert listed(deps)["comfyui"].state == SandboxState.STOPPED


@pytest.mark.parametrize(
    "reply",
    [WINDOWS_COMFYUI, OLD_WINDOWS_COMFYUI, NOT_COMFYUI],
    ids=["ComfyUI on Windows (win32)", "ComfyUI on Windows (nt)", "another program"],
)
@pytest.mark.parametrize("window", [False, True], ids=["no run window", "run window open"])
def test_a_reply_that_isnt_its_own_comfyui_doesnt_count_as_running(deps, home, winapi, http, reply, window):
    installed(home, "comfyui", 8188)
    if window:
        window_open(winapi, "comfyui")
    http.answer(8188, reply)

    state = listed(deps)["comfyui"].state
    assert state == (SandboxState.STARTING if window else SandboxState.STOPPED)


@pytest.mark.parametrize("reply", [None, '{"system": {"os": "linux"}}'], ids=["no longer answering", "still answering"])
def test_a_sandbox_whose_run_window_is_stopping_it_is_stopping(deps, home, winapi, http, reply):
    installed(home, "comfyui", 8188)
    window_open(winapi, "comfyui")  # the window is open until its cleanup is done
    winapi.instances.add(stopping_name("comfyui"))
    if reply is None:
        http.refuse(8188)
    else:
        http.answer(8188, reply)

    assert listed(deps)["comfyui"].state == SandboxState.STOPPING


def test_each_sandbox_is_asked_about_on_its_own_port_and_under_its_own_name(deps, home, winapi, http):
    installed(home, "up", 8188)
    installed(home, "opening", 8189)
    installed(home, "down", 8190)
    window_open(winapi, "OPENING")  # markers compare the distro's name without case, as `run` takes them
    http.refuse(8189)
    http.refuse(8190)

    states = {name: s.state for name, s in listed(deps).items()}
    assert states == {"up": SandboxState.RUNNING, "opening": SandboxState.STARTING, "down": SandboxState.STOPPED}


# -- notes, not states ----------------------------------------------------------------------------


def notes(sandbox) -> tuple:
    """The notes a row carries: "No run window" and "Port held by ..." (None where there's none)."""
    return (
        "no run window" if sandbox.run_window is False and sandbox.state == SandboxState.RUNNING else None,
        sandbox.port_held_by,
    )


def test_a_running_sandbox_with_no_run_window_says_so(deps, home):
    installed(home, "comfyui", 8188)

    assert notes(listed(deps)["comfyui"]) == ("no run window", None)
    assert listed(deps)["comfyui"].run_window is False


def test_a_running_sandbox_with_its_run_window_open_has_no_note(deps, home, winapi):
    installed(home, "comfyui", 8188)
    window_open(winapi, "comfyui")

    assert listed(deps)["comfyui"].run_window is True
    assert notes(listed(deps)["comfyui"]) == (None, None)


@pytest.mark.parametrize(
    "reply", [WINDOWS_COMFYUI, NOT_COMFYUI, None], ids=["ComfyUI on Windows", "another program", "no HTTP answer"]
)
def test_a_stopped_sandbox_whose_port_another_program_holds_names_it(deps, home, winapi, http, reply):
    installed(home, "comfyui", 8188)
    winapi.port_owners[8188] = PortOwner(4242, "python.exe")
    if reply is None:
        http.refuse(8188)
    else:
        http.answer(8188, reply)

    sandbox = listed(deps)["comfyui"]
    assert sandbox.state == SandboxState.STOPPED
    assert sandbox.port_held_by == "python.exe (PID 4242)"  # as the CLI's own messages name it


@pytest.mark.parametrize("reply", [NOT_COMFYUI, None], ids=["an answer that isn't ComfyUI", "no HTTP answer"])
def test_a_stopped_sandbox_whose_port_wsl_itself_forwards_names_no_one(deps, home, winapi, http, reply):
    # Its distro is up and WSL's localhost relay holds the port (the container stopping, or not
    # answering yet): that's the sandbox's own forwarding, not another program to close.
    installed(home, "comfyui", 8188)
    winapi.port_owners[8188] = PortOwner(77, "wslrelay.exe")
    if reply is None:
        http.refuse(8188)
    else:
        http.answer(8188, reply)

    sandbox = listed(deps)["comfyui"]
    assert sandbox.state == SandboxState.STOPPED
    assert sandbox.port_held_by is None


def test_a_stopped_sandbox_with_a_free_port_has_no_note(deps, home, http):
    installed(home, "comfyui", 8188)
    http.refuse(8188)

    assert notes(listed(deps)["comfyui"]) == (None, None)


@pytest.mark.parametrize("channel, pin", [("0.3.40", "0.3.40"), ("latest", None), ("nightly", None), (None, None)])
def test_the_comfyui_a_sandbox_follows_is_read_from_its_record(deps, home, http, channel, pin):
    installed(home, "comfyui", 8188)
    path = home / ".comfyui-sandbox" / "installs.json"
    doc = json.loads(path.read_text("utf-8"))
    doc["installs"][0]["answers"] = {} if channel is None else {"channel": channel}
    path.write_text(json.dumps(doc), "utf-8")
    http.refuse(8188)

    row = listed(deps)["comfyui"]

    assert (row.channel, row.pinned) == (channel, pin)


def test_port_held_by_is_only_for_stopped_sandboxes(deps, home, winapi, http):
    # Starting: the run window's own container is coming up behind wslrelay, which holds the port.
    installed(home, "opening", 8188)
    window_open(winapi, "opening")
    http.refuse(8188)
    winapi.port_owners[8188] = PortOwner(77, "wslrelay.exe")
    # Incomplete: never asked about its port at all.
    installed(home, "half", 8189, finished=False)
    winapi.port_owners[8189] = PortOwner(78, "python.exe")
    # Running: its own ComfyUI holds it.
    installed(home, "up", 8190)
    winapi.port_owners[8190] = PortOwner(79, "wslrelay.exe")

    assert {name: s.port_held_by for name, s in listed(deps).items()} == {"opening": None, "half": None, "up": None}


# -- polling --------------------------------------------------------------------------------------


def test_reading_the_sandboxes_runs_no_wsl_exe_but_a_running_sandboxs_one_update_check(
    deps, home, winapi, http, runner, environ
):
    wsl = Path(environ["ProgramFiles"]) / "WSL" / "wsl.exe"  # WSL is installed: it could be called
    wsl.parent.mkdir(parents=True)
    wsl.touch()
    installed(home, "up", 8188)
    installed(home, "opening", 8189)
    installed(home, "down", 8190)
    installed(home, "half", 8191, finished=False)
    window_open(winapi, "opening")
    http.refuse(8189)
    http.refuse(8190)

    gateway = RealGateway(deps)
    for _ in range(3):  # three polls
        asyncio.run(gateway.sandboxes())

    # The one exception: "up" is running, so its distro is awake and its once-a-session update
    # check (feat-tui-integration 07) runs in it, in the background. Nothing runs for the others.
    calls = list(runner.calls)
    assert all(wslcalls.is_wsl(call) and call.argv[1:3] == ("-d", "up") for call in calls)
    assert sum(call.argv[-3:] == ("apt", "list", "--upgradable") for call in calls) <= 1  # once, not per poll
    assert runner.started == []  # nothing else runs either


# -- home on the real gateway ---------------------------------------------------------------------


def test_home_lists_the_real_sandboxes_with_their_states_and_notes_and_offers_only_real_actions(
    deps, home, winapi, http
):
    installed(home, "up", 8188)  # its own ComfyUI answers, and no window is open
    installed(home, "opening", 8189)
    window_open(winapi, "opening")
    http.refuse(8189)
    installed(home, "down", 8190)
    http.answer(8190, WINDOWS_COMFYUI)
    winapi.port_owners[8190] = PortOwner(4242, "python.exe")
    installed(home, "half", 8191, finished=False)

    async def scenario() -> None:
        app = SandboxApp(RealGateway(deps))
        async with app.run_test(size=(160, 40)) as pilot:
            r = scenarios.Rec(pilot)
            await r.wait("Pick a sandbox or an action")
            await r.settle(0.6)  # the rows cascade in
            rows = {
                name: line
                for line in r.text.splitlines()
                for name in ("up", "opening", "down", "half")
                if re.match(rf"\s+(?:▌ )?{name}\s", line)
            }
            assert "running" in rows["up"] and "no run window" in rows["up"]
            assert "starting" in rows["opening"]
            assert "stopped" in rows["down"] and "port held by python.exe (PID 4242)" in rows["down"]
            assert "incomplete" in rows["half"]
            # Only what's real is offered: nothing is shown to be faked. Check this PC (07) and
            # Install a new sandbox (13) are real.
            assert "Install a new sandbox" in r.text and "Check this PC" in r.text
            await r.row("up")
            await r.wait("What do you want to do with up?")
            menu = r.text.split("What do you want")[1]
            assert all(
                action in menu
                for action in (
                    "Open in browser",
                    "Stop",
                    "Show logs",
                    "Update…",
                    "Back up",
                    "Settings…",
                    "Reinstall ComfyUI…",
                    "Uninstall…",
                )
            )
            assert "Stop, below, stops it." in r.text

    asyncio.run(scenario())


# -- whether a sandbox's distro is running ----------------------------------------------------------


def test_a_distro_is_running_when_wsl_lists_it_running_whatever_the_case(deps, runner, environ):
    Machine().script(runner, environ)
    runner.on("wsl.exe", "--list", "--running", "--quiet", stdout="Alpha\n")
    gateway = RealGateway(deps)

    assert asyncio.run(gateway.distro_running("alpha")) is True
    assert asyncio.run(gateway.distro_running("beta")) is False
    assert wslcalls.commands(runner.calls) == []  # it only listed: nothing ran in a distro


def test_without_wsl_no_distro_is_running_and_wsl_exe_is_never_run(deps, runner):
    assert asyncio.run(RealGateway(deps).distro_running("alpha")) is False
    assert not any(call.argv[0] == "wsl.exe" for call in runner.calls)
