"""`python -m comfyui_sandbox.tui.demo` opens home, or with `run` the run window, on fake data.

The demo isn't a CLI command: it's how the screens are shown and redesigned with no WSL, Docker or
GPU. These run it as a real module in a child interpreter, so they check the entry point the way
it's used, `-m` and all. A child's stdin isn't a terminal, so the apps print a note and exit
instead of taking over the screen; that's what lets a test reach them at all.
"""

import subprocess
import sys


def demo(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "comfyui_sandbox.tui.demo", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
        timeout=60,
    )


def test_home_offers_the_speed_fail_port_taken_and_empty_options() -> None:
    proc = demo("--help")
    assert proc.returncode == 0, proc.stderr
    for option in ("--speed", "--fail", "--port-taken", "--empty", "--intro"):
        assert option in proc.stdout
    assert "run" in proc.stdout  # the way to the run window is named in the help


def test_home_is_also_reachable_by_name() -> None:
    proc = demo("home", "--help")
    assert proc.returncode == 0, proc.stderr
    assert "--port-taken" in proc.stdout


def test_the_run_window_has_its_own_options() -> None:
    proc = demo("run", "--help")
    assert proc.returncode == 0, proc.stderr
    for option in (
        "--distro",
        "--port",
        "--models",
        "--fail",
        "--broken-node",
        "--port-taken",
        "--already-running",
        "--update",
        "--speed",
    ):
        assert option in proc.stdout


def test_options_are_checked_before_anything_opens() -> None:
    proc = demo("--speed", "fast")
    assert proc.returncode == 2
    assert "--speed" in proc.stderr


def test_without_a_terminal_home_says_so_and_exits_cleanly() -> None:
    proc = demo("--empty")
    assert proc.returncode == 0, proc.stderr
    assert "Not a terminal" in proc.stdout


def test_without_a_terminal_the_run_window_says_so_and_exits_cleanly() -> None:
    proc = demo("run", "--fail")
    assert proc.returncode == 0, proc.stderr
    assert "Not a terminal" in proc.stdout
