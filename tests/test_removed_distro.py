"""A distro removed outside the CLI (ticket 22): commands point to `uninstall`; `install` needs cleanup first."""

import json

import pytest
from test_check import Machine
from test_install import installs_json
from test_verify import own

import wslcalls

MESSAGE = (
    "Distro comfy-docker is no longer registered in WSL (removed outside comfyui-sandbox). "
    "Run `comfyui-sandbox uninstall --distro comfy-docker` to clean up its shortcuts "
    "and its installs.json entry."
)


def set_state(home, state, created_at=None):
    path = home / ".comfyui-sandbox" / "installs.json"
    doc = json.loads(path.read_text("utf-8"))
    doc["installs"][0]["state"] = state
    doc["installs"][0].pop("distro_created_at", None)
    if created_at:
        doc["installs"][0]["distro_created_at"] = created_at
    path.write_text(json.dumps(doc), "utf-8")


def wsl_calls(runner):
    return [c for c in runner.calls if wslcalls.is_wsl(c)]


@pytest.mark.parametrize("argv", [("status",), ("start",), ("logs",), ("update", "setup"), ("verify",)])
def test_a_command_that_resolves_an_install_stops_when_its_distro_is_gone(argv, cli, runner, environ, home):
    Machine(distros=["Ubuntu"]).script(runner, environ)
    own(home)
    runner.calls.clear()

    result = cli(*argv, "--json")

    assert result.exit_code == 1
    assert result.json["error"] == MESSAGE
    assert [c.argv for c in wsl_calls(runner)] == [("wsl.exe", "--list", "--quiet")]


def test_the_registration_check_ignores_case(cli, runner, environ, home):
    Machine(distros=["Ubuntu", "COMFY-DOCKER"]).script(runner, environ)
    own(home)
    runner.on_script("status.sh", stdout="container=exited\n")

    assert cli("status", "--json").exit_code == 0


def test_commands_that_resolve_no_install_make_no_extra_wsl_call(cli, runner, environ, home):
    Machine(distros=["Ubuntu"]).script(runner, environ)
    own(home)

    for argv in (("check",), ("prereqs",), ("wslconfig",), ("--help",)):
        result = cli(*argv, "--json")
        assert "no longer registered" not in result.stdout + result.stderr, argv


def test_uninstall_of_a_removed_distro_still_cleans_up(cli, runner, environ, home):
    Machine(distros=["Ubuntu"]).script(runner, environ)
    own(home)

    result = cli("uninstall", "--json", "--yes", "--confirm", "comfy-docker")

    assert result.exit_code == 0, result.stderr
    assert result.json["data"]["unregistered"] is False
    assert json.loads((home / ".comfyui-sandbox" / "installs.json").read_text("utf-8"))["installs"] == []


# -- install ----------------------------------------------------------------------


@pytest.fixture
def machine():
    return Machine(distros=["Ubuntu"])


@pytest.fixture
def install(cli, runner, environ, machine):
    def run(*argv: str):
        machine.script(runner, environ)
        return cli("install", "--json", *argv)

    return run


def test_install_of_a_complete_entry_without_its_distro_asks_for_uninstall_first(install, runner, home):
    own(home)  # no distro_created_at either: an older CLI's entry counts as removed by hand

    result = install("--non-interactive", "--distro", "comfy-docker")

    assert result.exit_code == 1
    assert result.json["error"] == MESSAGE
    assert wslcalls.distro_ops(runner.calls, "--install") == []


def test_install_of_a_complete_entry_with_its_distro_still_says_already_installed(install, machine, home):
    own(home)
    machine.distros = ["Ubuntu", "comfy-docker"]

    result = install("--non-interactive", "--distro", "comfy-docker")

    assert result.exit_code == 1
    assert result.json["error"] == "comfy-docker is already installed."


@pytest.mark.parametrize("argv", [("--distro", "comfy-docker"), ()])
def test_install_resume_with_distro_created_at_but_no_distro_asks_for_uninstall_first(argv, install, runner, home):
    own(home)
    set_state(home, "in_progress", "2026-01-01T00:00:00Z")

    result = install("--non-interactive", *argv)

    assert result.exit_code == 1
    assert result.json["error"] == MESSAGE
    assert wslcalls.distro_ops(runner.calls, "--install") == []


def test_a_retry_after_a_failed_wsl_install_resumes_without_uninstall(install, runner, home):
    runner.on("wsl.exe", "--install", returncode=1, stdout="Error: network unreachable\n")
    assert install("--yes").exit_code == 1
    assert installs_json(home)["installs"][0]["distro_created_at"] is None
    runner.on("wsl.exe", "--install")
    runner.calls.clear()

    result = install("--distro", "comfy-docker")

    assert result.exit_code == 0, result.stderr
    assert result.json["data"]["resumed"] is True
    assert len(wslcalls.distro_ops(runner.calls, "--install")) == 1


def test_create_the_distro_saves_distro_created_at(install, home):
    result = install("--yes")

    assert result.exit_code == 0, result.stderr
    stamp = installs_json(home)["installs"][0]["distro_created_at"]
    assert stamp.endswith("Z") and len(stamp) == 20


def test_distro_created_at_is_saved_when_the_distro_already_exists(install, machine, runner, home):
    runner.on("wsl.exe", "--install", returncode=1, stdout="Error: network unreachable\n")
    assert install("--yes").exit_code == 1
    machine.distros = ["Ubuntu", "comfy-docker"]  # the distro appeared meanwhile
    runner.on("wsl.exe", "--install")
    runner.on("wsl.exe", "--terminate", returncode=1)

    install("--non-interactive")

    assert installs_json(home)["installs"][0]["distro_created_at"].endswith("Z")
