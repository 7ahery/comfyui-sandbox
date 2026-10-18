"""`install` preflight: the questions only preflight can raise (ticket 08).

Port taken, a distro name that already exists, Docker Desktop. Same fake host as test_install.
Interactively these are prompted; under --non-interactive (or --yes) they exit 9 naming the flag.
"""

import json
from pathlib import Path

import pytest
from test_check import Machine
from test_intake_interactive import DEFAULTS

import wslcalls
from comfyui_sandbox.winapi import PortOwner


@pytest.fixture
def machine():
    return Machine(distros=["Ubuntu"])


@pytest.fixture
def install(cli, runner, environ, machine):
    def run(*argv: str):
        machine.script(runner, environ)
        return cli("install", "--json", *argv)

    return run


def saved(home) -> dict:
    path = home / ".comfyui-sandbox" / "installs.json"
    return json.loads(path.read_text("utf-8")) if path.exists() else {"installs": []}


def test_a_taken_port_names_the_program_and_the_next_free_port_under_non_interactive(install, winapi, home, runner):
    winapi.port_owners[8188] = PortOwner(4120, "python.exe")

    result = install("--yes", "--non-interactive")

    assert result.exit_code == 9
    doc = result.json
    assert doc["flag"] == "--port"
    assert doc["message"] == (
        "Port 8188 is used by python.exe (PID 4120). Re-run with `--port <n>`. Port 8189 is free."
    )
    assert saved(home)["installs"] == []
    assert wslcalls.distro_ops(runner.calls, "--install") == []


def test_the_suggested_port_skips_ports_that_are_taken_too(install, winapi):
    winapi.port_owners[8188] = PortOwner(4120, "python.exe")
    winapi.port_owners[8189] = PortOwner(77, "wslrelay.exe")

    result = install("--yes", "--non-interactive")

    assert result.exit_code == 9
    assert "8190 is free" in result.json["message"]


def test_a_taken_port_given_with_the_port_flag_is_refused_the_same_way(install, winapi):
    winapi.port_owners[9000] = PortOwner(12, "node.exe")

    result = install("--yes", "--non-interactive", "--port", "9000")

    assert result.exit_code == 9
    assert "Port 9000 is used by node.exe (PID 12)" in result.json["message"]
    assert "9001 is free" in result.json["message"]


def test_a_free_port_asks_nothing(install, home):
    result = install("--yes", "--non-interactive")

    assert result.exit_code == 0, result.stderr
    assert saved(home)["installs"][0]["port"] == 8188


# -- interactive ---------------------------------------------------------------


def test_a_taken_port_is_asked_and_the_recommended_answer_is_the_next_free_port(install, winapi, prompter, home):
    winapi.port_owners[8188] = PortOwner(4120, "python.exe")
    prompter.answers += [*DEFAULTS, "Use port 8189"]

    result = install()

    assert result.exit_code == 0, result.stderr
    [(message, choices)] = [q for q in prompter.asked if "Port" in q[0]]
    assert "python.exe (PID 4120)" in message
    assert choices[0] == "Use port 8189 instead (Recommended)"
    assert choices[1] == "Keep 8188, I'll close python.exe first"
    assert saved(home)["installs"][0]["port"] == 8189
    assert saved(home)["installs"][0]["answers"]["port"] == 8189


def test_keeping_the_port_waits_until_the_program_is_closed(install, winapi, prompter, home):
    winapi.port_owners[8188] = PortOwner(4120, "python.exe")
    prompter.answers += [*DEFAULTS, "Keep 8188", True]
    confirm = prompter.confirm
    prompter.confirm = lambda message, default=True: (winapi.port_owners.clear(), confirm(message, default))[1]

    result = install()

    assert result.exit_code == 0, result.stderr
    assert saved(home)["installs"][0]["port"] == 8188


def test_a_typed_port_is_validated_and_checked_again(install, winapi, prompter, home):
    winapi.port_owners[8188] = PortOwner(4120, "python.exe")
    winapi.port_owners[9000] = PortOwner(5, "node.exe")
    prompter.answers += [
        *DEFAULTS,
        "Other",
        "80",
        "9000",
        "Use port 9001",
    ]

    result = install()

    assert result.exit_code == 0, result.stderr
    assert prompter.rejected[0][0] == "80"
    assert saved(home)["installs"][0]["port"] == 9001


# -- a distro name that already exists -----------------------------------------------


def test_an_existing_distro_name_names_the_flag_and_two_free_names_under_non_interactive(
    install, machine, runner, home
):
    machine.distros = ["Ubuntu", "Sandbox"]

    result = install("--yes", "--non-interactive", "--distro", "sandbox")

    assert result.exit_code == 9
    doc = result.json
    assert doc["flag"] == "--distro"
    assert doc["message"] == (
        "A distro named sandbox already exists, and install never touches an existing "
        "distro. Re-run with `--distro <name>`, comfy-docker and comfyui are free."
    )
    assert saved(home)["installs"] == []
    touched = [c for c in runner.calls if wslcalls.is_wsl(c) and "sandbox" in [a.lower() for a in c.argv]]
    assert touched == []


def test_an_existing_distro_name_is_asked_again_in_a_terminal(install, machine, prompter, home, runner):
    machine.distros = ["Ubuntu", "Sandbox"]
    prompter.answers += [*DEFAULTS[:4], *DEFAULTS[5:], "comfyui"]
    # --distro is a flag, so the name question is skipped; preflight asks it once more.

    result = install("--distro", "SANDBOX")

    assert result.exit_code == 0, result.stderr
    [(message, choices)] = [q for q in prompter.asked if "already exists" in q[0]]
    assert "SANDBOX" in message
    assert choices[0] == "comfy-docker (Recommended)" and choices[1] == "comfyui"
    assert saved(home)["installs"][0]["distro"] == "comfyui"
    [create] = wslcalls.distro_ops(runner.calls, "--install")
    assert create.argv[create.argv.index("--name") + 1] == "comfyui"


def test_a_typed_replacement_name_must_be_new(install, machine, prompter, home):
    machine.distros = ["Ubuntu", "Sandbox"]
    prompter.answers += [*DEFAULTS[:4], *DEFAULTS[5:], "Other", "sandbox", "my-comfy"]

    result = install("--distro", "sandbox")

    assert result.exit_code == 0, result.stderr
    assert "already exists" in prompter.rejected[0][1]
    assert saved(home)["installs"][0]["distro"] == "my-comfy"


# -- Docker Desktop --------------------------------------------------------------------


def test_docker_desktop_installed_tells_the_user_its_wsl_integration_must_stay_off(install, environ):
    exe = Path(environ["ProgramFiles"]) / "Docker" / "Docker" / "Docker Desktop.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")

    result = install("--yes", "--non-interactive")

    assert result.exit_code == 0, result.stderr
    assert "Docker Desktop is installed" in result.stderr
    assert "WSL integration" in result.stderr and "comfy-docker" in result.stderr


def test_without_docker_desktop_nothing_is_said_about_it(install):
    result = install("--yes", "--non-interactive")

    assert "Docker Desktop" not in result.stderr
