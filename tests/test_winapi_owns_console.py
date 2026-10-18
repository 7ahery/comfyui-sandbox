"""The real `owns_console` (review-fix 05). Windows only.

A helper Python process asks `WindowsApi().owns_console()` and writes the answer to a file. Opened by
`open_console` it has a window of its own (a shortcut or `start`); started under `cmd /c` in a new
console it shares the window with a shell, like a terminal the user already had open.
"""

import subprocess
import sys
import time

import pytest

from comfyui_sandbox.winapi import WindowsApi

HELPER = """
import sys
from comfyui_sandbox.winapi import WindowsApi
open(sys.argv[1], "w").write(str(WindowsApi().owns_console()))
"""


def _answer(path, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.read_text():
            return path.read_text()
        time.sleep(0.2)
    raise AssertionError("the helper never answered")


@pytest.mark.windows_only
def test_a_process_in_a_console_of_its_own_owns_it(tmp_path):
    out = tmp_path / "answer.txt"

    WindowsApi().open_console([sys.executable, "-c", HELPER, str(out)])

    assert _answer(out) == "True"


@pytest.mark.windows_only
def test_a_process_sharing_its_console_with_a_shell_does_not(tmp_path):
    out, script = tmp_path / "answer.txt", tmp_path / "helper.py"
    script.write_text(HELPER)

    shell = subprocess.Popen(
        ["cmd", "/c", sys.executable, str(script), str(out)], creationflags=0x00000010, stdin=subprocess.DEVNULL
    )  # CREATE_NEW_CONSOLE

    assert _answer(out) == "False"
    shell.wait(timeout=20)
