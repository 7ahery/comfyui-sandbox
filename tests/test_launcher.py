"""`comfyui-sandbox launcher install|remove`: Desktop and Start menu shortcuts (ticket 12).

Tests drive the CLI with the fake winapi (what shortcuts exist) and an injected installs.json.
"""

import json
import sys
from pathlib import PureWindowsPath

import pytest
from test_check import Machine

from comfyui_sandbox.winapi import Shortcut

DESKTOP = PureWindowsPath(r"C:\Users\user\Desktop")
PROGRAMS = PureWindowsPath(r"C:\Users\user\AppData\Roaming\Microsoft\Windows\Start Menu\Programs")


def own(home, *names):
    folder = home / ".comfyui-sandbox"
    folder.mkdir(exist_ok=True)
    installs = [
        {
            "distro": n,
            "linux_user": "comfy",
            "state": "complete",
            "port": 8188,
            "models": {"mode": "wsl", "path": "/home/comfy/m"},
            "gpu": {"mode": "cpu"},
            "deployed_by": "1.0.0",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "answers": {},
        }
        for n in names
    ]
    (folder / "installs.json").write_text(json.dumps({"schema": 1, "installs": installs}), "utf-8")


@pytest.fixture(autouse=True)
def wsl_present(runner, environ):
    Machine().script(runner, environ)


def test_install_creates_desktop_and_start_menu_shortcuts(cli, winapi, home):
    own(home, "comfy-docker")

    result = cli("launcher", "install", "--distro", "comfy-docker", "--json")

    assert result.exit_code == 0
    name = "ComfyUI (comfy-docker).lnk"
    assert set(winapi.shortcuts) == {DESKTOP / name, PROGRAMS / name}
    for shortcut in winapi.shortcuts.values():
        assert PureWindowsPath(shortcut.target).stem == "comfyui-sandbox"
        assert shortcut.arguments == "run --distro comfy-docker"
        assert "comfy-docker" in shortcut.description
    assert sorted(result.json["data"]["shortcuts"]) == sorted(map(str, winapi.shortcuts))


def test_install_is_rerunnable(cli, winapi, home):
    own(home, "comfy-docker")
    cli("launcher", "install", "--distro", "comfy-docker")

    result = cli("launcher", "install", "--distro", "comfy-docker")

    assert result.exit_code == 0
    assert len(winapi.shortcuts) == 2


def test_remove_deletes_both_shortcuts(cli, winapi, home):
    own(home, "comfy-docker")
    cli("launcher", "install", "--distro", "comfy-docker")

    result = cli("launcher", "remove", "--distro", "comfy-docker")

    assert result.exit_code == 0
    assert winapi.shortcuts == {}


def test_remove_when_already_gone_is_not_an_error(cli, winapi, home):
    own(home, "comfy-docker")

    result = cli("launcher", "remove", "--distro", "comfy-docker", "--json")

    assert result.exit_code == 0
    assert result.json["data"]["removed"] == []


def test_remove_works_for_a_distro_no_longer_in_installs_json(cli, winapi, home):
    own(home, "other")
    winapi.create_shortcut(Shortcut(DESKTOP / "ComfyUI (gone).lnk", "x"))

    result = cli("launcher", "remove", "--distro", "gone")

    assert result.exit_code == 0
    assert winapi.shortcuts == {}


def test_remove_for_a_name_never_made_reports_none_and_leaves_other_shortcuts(cli, winapi, home):
    own(home, "other")
    winapi.create_shortcut(Shortcut(DESKTOP / "ComfyUI (other).lnk", "x"))
    winapi.create_shortcut(Shortcut(DESKTOP / "My own thing.lnk", "x"))

    result = cli("launcher", "remove", "--distro", "never-made", "--json")

    assert result.exit_code == 0
    assert result.json["data"]["removed"] == []
    assert set(winapi.shortcuts) == {DESKTOP / "ComfyUI (other).lnk", DESKTOP / "My own thing.lnk"}


@pytest.mark.parametrize("name", ["..\\evil", "a/b", "x).lnk", "bad:name"])
def test_remove_refuses_a_name_that_could_not_be_a_distro_name(cli, winapi, home, name):
    own(home, "other")
    winapi.create_shortcut(Shortcut(DESKTOP / "ComfyUI (other).lnk", "x"))

    result = cli("launcher", "remove", "--distro", name)

    assert result.exit_code == 1
    assert len(winapi.shortcuts) == 1


def test_remove_after_uninstall_clears_what_install_made(cli, winapi, home):
    own(home, "comfy-docker")
    cli("launcher", "install", "--distro", "comfy-docker")
    own(home)  # the install record is gone, as after uninstall

    result = cli("launcher", "remove", "--distro", "comfy-docker", "--json")

    assert result.exit_code == 0
    assert len(result.json["data"]["removed"]) == 2
    assert winapi.shortcuts == {}


def test_install_in_a_packaged_app_refuses_and_creates_nothing(cli, winapi, home):
    own(home, "comfy-docker")
    winapi.packaged_app = True

    result = cli("launcher", "install", "--distro", "comfy-docker")

    assert result.exit_code == 1
    assert "reinstall" in result.stderr.lower()
    assert "own terminal" in result.stderr
    assert winapi.shortcuts == {}


def test_install_refuses_a_distro_not_in_installs_json(cli, winapi, home):
    own(home, "comfy-docker")

    result = cli("launcher", "install", "--distro", "Ubuntu")

    assert result.exit_code == 1
    assert winapi.shortcuts == {}


@pytest.mark.windows_only
def test_real_lnk_is_created_and_removed(tmp_path):
    from comfyui_sandbox.winapi import WindowsApi

    api = WindowsApi()
    path = tmp_path / "ComfyUI (test).lnk"
    shortcut = Shortcut(
        PureWindowsPath(path),
        target=sys.executable,
        arguments="run --distro test",
        working_dir=str(tmp_path),
        description="test",
        icon=sys.executable,
    )

    api.create_shortcut(shortcut)

    assert path.is_file()
    assert api.remove_shortcut(PureWindowsPath(path)) is True
    assert not path.exists()
    assert api.remove_shortcut(PureWindowsPath(path)) is False


@pytest.mark.windows_only
def test_real_special_folders_and_packaged_flag():
    from comfyui_sandbox.winapi import WindowsApi

    api = WindowsApi()

    assert api.special_folder("desktop").name == "Desktop"
    assert api.special_folder("programs").name == "Programs"
    assert api.is_packaged_app() is False  # pytest runs from a plain python.exe
