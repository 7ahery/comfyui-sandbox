"""The dev toolchain the suite shells out to is chosen deliberately: bash and shellcheck are
declared dev requirements, and a missing or wrong one fails loudly instead of skipping."""

import subprocess
from pathlib import Path

import pytest

from toolchain import ToolchainError, resolve_bash, resolve_shellcheck

GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"
WSL_LAUNCHER = r"C:\Users\Alex\AppData\Local\Microsoft\WindowsApps\bash.exe"


def fake_run(working: set[str]):
    def run(argv, **kwargs):
        ok = argv[0] in working and argv[1:] == ["-c", "echo ok"]
        return subprocess.CompletedProcess(argv, 0 if ok else 1, stdout="ok\n" if ok else "", stderr="")

    return run


def resolve(environ=None, *, which=None, files=(), working=(), platform="win32"):
    which = which or {}
    files = {str(Path(f)) for f in files}
    return resolve_bash(
        environ or {},
        platform=platform,
        which=lambda name: which.get(name),
        is_file=lambda p: str(Path(p)) in files,
        run=fake_run(set(working)),
    )


def test_override_wins_over_git_and_path():
    got = resolve(
        {"COMFYUI_SANDBOX_TEST_BASH": "D:/mine/bash.exe"},
        which={"git": r"C:\Program Files\Git\cmd\git.exe", "bash": "C:/other/bash.exe"},
        files={GIT_BASH, "D:/mine/bash.exe"},
        working={GIT_BASH, "D:/mine/bash.exe", "C:/other/bash.exe"},
    )
    assert got == "D:/mine/bash.exe"


def test_override_pointing_nowhere_fails_clearly_without_falling_back():
    with pytest.raises(ToolchainError, match=r"COMFYUI_SANDBOX_TEST_BASH.*does-not-exist"):
        resolve(
            {"COMFYUI_SANDBOX_TEST_BASH": "Z:/does-not-exist/bash.exe"},
            which={"bash": "C:/other/bash.exe"},
            working={"C:/other/bash.exe"},
        )


def test_git_for_windows_bash_beats_bash_on_path():
    got = resolve(
        which={"git": r"C:\Program Files\Git\cmd\git.exe", "bash": "C:/other/bash.exe"},
        files={GIT_BASH},
        working={GIT_BASH, "C:/other/bash.exe"},
    )
    assert Path(got) == Path(GIT_BASH)


def test_git_usr_bin_bash_is_preferred_over_the_bin_launcher():
    usr = r"C:\Program Files\Git\usr\bin\bash.exe"
    got = resolve(which={"git": r"C:\Program Files\Git\cmd\git.exe"}, files={GIT_BASH, usr}, working={GIT_BASH, usr})
    assert Path(got) == Path(usr)


def test_git_bash_found_from_a_nested_git_exe():
    got = resolve(which={"git": r"C:\Program Files\Git\mingw64\bin\git.exe"}, files={GIT_BASH}, working={GIT_BASH})
    assert Path(got) == Path(GIT_BASH)


def test_windowsapps_wsl_launcher_is_rejected_even_when_it_runs():
    with pytest.raises(ToolchainError, match="WindowsApps"):
        resolve(which={"bash": WSL_LAUNCHER}, working={WSL_LAUNCHER})


def test_bash_on_path_is_the_last_resort():
    assert resolve(which={"bash": "/usr/bin/bash"}, working={"/usr/bin/bash"}, platform="linux") == "/usr/bin/bash"


def test_a_bash_that_cannot_echo_is_not_used():
    with pytest.raises(ToolchainError, match="COMFYUI_SANDBOX_TEST_BASH"):
        resolve(which={"bash": "/usr/bin/bash"}, working=set(), platform="linux")


def test_no_bash_at_all_says_what_to_install_and_the_override():
    with pytest.raises(ToolchainError) as err:
        resolve(platform="win32")
    assert "Git for Windows" in str(err.value) and "COMFYUI_SANDBOX_TEST_BASH" in str(err.value)


def test_shellcheck_override_then_path():
    assert (
        resolve_shellcheck({"COMFYUI_SANDBOX_TEST_SHELLCHECK": "X/sc"}, which=lambda n: "P/sc", is_file=lambda p: True)
        == "X/sc"
    )
    assert resolve_shellcheck({}, which=lambda n: "P/sc", is_file=lambda p: True) == "P/sc"


def test_shellcheck_missing_fails_with_install_hint():
    with pytest.raises(ToolchainError) as err:
        resolve_shellcheck({}, which=lambda n: None, is_file=lambda p: False)
    text = str(err.value)
    assert "winget" in text and "scoop" in text and "apt" in text and "COMFYUI_SANDBOX_TEST_SHELLCHECK" in text


def test_shellcheck_override_pointing_nowhere_fails():
    with pytest.raises(ToolchainError, match="COMFYUI_SANDBOX_TEST_SHELLCHECK"):
        resolve_shellcheck({"COMFYUI_SANDBOX_TEST_SHELLCHECK": "nope"}, which=lambda n: "P/sc", is_file=lambda p: False)


def test_the_real_machine_has_a_working_bash():
    from toolchain import bash

    done = subprocess.run([bash(), "-c", "echo ok"], capture_output=True, text=True, check=False)
    assert done.stdout.strip() == "ok"
