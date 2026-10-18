"""The command line that re-runs this CLI (ticket 13): `start`'s run window and the launcher shortcuts build it the same way.

The CLI is found on PATH, else next to the running interpreter (a venv under `uv run` has it off PATH),
else `python -m comfyui_sandbox`. Tests drive `start` and `launcher install` with the fake winapi and a controlled PATH.
"""

import subprocess
import sys
from pathlib import PureWindowsPath

import pytest
from test_check import Machine
from test_launcher import own as own_launcher
from test_verify import own

from comfyui_sandbox import cli_command

ON_PATH = r"C:\tools\bin\comfyui-sandbox.EXE"
BESIDE_PYTHON = r"C:\proj\.venv\Scripts\comfyui-sandbox.exe"


@pytest.fixture
def machine(runner, environ, home):
    Machine().script(runner, environ)
    own(home)


@pytest.fixture
def python_in_venv(tmp_path, monkeypatch):
    scripts = tmp_path / "venv" / "Scripts"
    scripts.mkdir(parents=True)
    monkeypatch.setattr(cli_command, "PYTHON", scripts / "python.exe")
    return scripts


def test_start_opens_the_run_window_with_the_full_path_when_the_cli_is_on_path(machine, cli, winapi, http, monkeypatch):
    monkeypatch.setattr(cli_command, "which", lambda name: ON_PATH)

    result = cli("start")

    assert result.exit_code == 0, result.stderr
    assert winapi.consoles == [(ON_PATH, "run", "--distro", "comfy-docker")]


def test_start_works_when_the_cli_is_not_on_path_but_sits_next_to_the_interpreter(
    machine, cli, winapi, http, monkeypatch, python_in_venv
):
    monkeypatch.setattr(cli_command, "which", lambda name: None)
    exe = python_in_venv / "comfyui-sandbox.exe"
    exe.write_bytes(b"")

    result = cli("start")

    assert result.exit_code == 0, result.stderr
    assert winapi.consoles == [(str(exe), "run", "--distro", "comfy-docker")]


def test_start_runs_the_package_with_dash_m_when_the_cli_is_nowhere_to_be_found(
    machine, cli, winapi, http, monkeypatch, python_in_venv
):
    monkeypatch.setattr(cli_command, "which", lambda name: None)

    result = cli("start")

    assert result.exit_code == 0, result.stderr
    assert winapi.consoles == [
        (str(python_in_venv / "python.exe"), "-m", "comfyui_sandbox", "run", "--distro", "comfy-docker")
    ]


def test_the_package_answers_to_dash_m():
    done = subprocess.run(
        [sys.executable, "-m", "comfyui_sandbox", "--version"], capture_output=True, text=True, timeout=60
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith("comfyui-sandbox ")


def test_the_launcher_shortcut_falls_back_to_dash_m_with_the_arguments_as_one_string(
    machine, cli, winapi, http, monkeypatch, python_in_venv, home
):
    monkeypatch.setattr(cli_command, "which", lambda name: None)
    own_launcher(home, "comfy-docker")

    cli("launcher", "install")

    assert winapi.shortcuts
    for shortcut in winapi.shortcuts.values():
        assert shortcut.target == str(python_in_venv / "python.exe")
        assert shortcut.arguments == "-m comfyui_sandbox run --distro comfy-docker"
        assert PureWindowsPath(shortcut.working_dir) == PureWindowsPath(python_in_venv)


def test_the_launcher_shortcut_runs_the_same_command_start_opens(
    machine, cli, winapi, http, monkeypatch, python_in_venv, home
):
    monkeypatch.setattr(cli_command, "which", lambda name: None)
    exe = python_in_venv / "comfyui-sandbox.exe"
    exe.write_bytes(b"")
    own_launcher(home, "comfy-docker")

    cli("start")
    cli("launcher", "install")

    (console,) = winapi.consoles
    for shortcut in winapi.shortcuts.values():
        assert (shortcut.target, *shortcut.arguments.split()) == console
        assert PureWindowsPath(shortcut.working_dir) == PureWindowsPath(exe).parent
