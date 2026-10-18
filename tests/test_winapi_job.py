"""The real kill-on-close Job Object (ticket 06). Windows only.

A helper Python process binds a harmless child (`ping`) to a Job Object and is then killed
the hard way (TerminateProcess, no cleanup runs: like a crash). The child must die with it.
"""

import subprocess
import sys
import time

import pytest

HELPER = """
import subprocess, sys, time
from comfyui_sandbox.winapi import WindowsApi
child = subprocess.Popen(["ping", "-n", "120", "127.0.0.1"], stdout=subprocess.DEVNULL)
WindowsApi().kill_with_parent(child.pid)
print(child.pid, flush=True)
time.sleep(120)
"""


def _alive(pid: int) -> bool:
    out = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True, check=False
    ).stdout
    return f'"{pid}"' in out


@pytest.mark.windows_only
def test_a_child_bound_to_the_job_object_dies_when_its_parent_is_killed():
    parent = subprocess.Popen([sys.executable, "-c", HELPER], stdout=subprocess.PIPE, text=True)
    child_pid = None
    try:
        assert parent.stdout is not None
        child_pid = int(parent.stdout.readline())
        assert _alive(child_pid)

        parent.kill()  # TerminateProcess: nothing in the parent gets to clean up
        parent.wait(timeout=10)

        for _ in range(50):
            if not _alive(child_pid):
                break
            time.sleep(0.2)
        assert not _alive(child_pid)
    finally:
        if parent.poll() is None:
            parent.kill()
        if child_pid is not None and _alive(child_pid):
            subprocess.run(["taskkill", "/F", "/PID", str(child_pid)], capture_output=True, check=False)
