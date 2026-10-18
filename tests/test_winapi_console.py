"""The real `open_console` (ticket 04). Windows only.

A helper Python process opens a harmless long-running command in a new console, prints what it got
back and is then killed the hard way. The command must be running in its own console window, must
not have been waited for, and must outlive the helper.
"""

import subprocess
import sys
import time

import pytest

HELPER = """
import sys, time
from comfyui_sandbox.winapi import WindowsApi
t = time.monotonic()
pid = WindowsApi().open_console(["ping", "-n", "120", "127.0.0.1"])
print(pid, round(time.monotonic() - t, 2), flush=True)
time.sleep(120)
"""


def _alive(pid: int) -> bool:
    out = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True, check=False
    ).stdout
    return f'"{pid}"' in out


@pytest.mark.windows_only
def test_open_console_starts_the_command_without_waiting_and_it_outlives_the_caller():
    parent = subprocess.Popen([sys.executable, "-c", HELPER], stdout=subprocess.PIPE, text=True)
    pid = None
    try:
        assert parent.stdout is not None
        pid_text, seconds = parent.stdout.readline().split()
        pid = int(pid_text)
        assert float(seconds) < 5  # returned at once: ping runs for two minutes
        assert _alive(pid)

        parent.kill()  # the calling terminal going away
        parent.wait(timeout=10)
        time.sleep(1)

        assert _alive(pid)
    finally:
        if parent.poll() is None:
            parent.kill()
        if pid is not None and _alive(pid):
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, check=False)


QUIET_HELPER = """
import gc, subprocess, sys
unraisable = []
sys.unraisablehook = unraisable.append
from comfyui_sandbox.winapi import WindowsApi
pid = WindowsApi().open_console(["ping", "-n", "5", "127.0.0.1"])
gc.collect()
subprocess._cleanup()
gc.collect()
print(pid, len(unraisable), [str(u.exc_value) for u in unraisable], flush=True)
"""


@pytest.mark.windows_only
def test_open_console_leaves_nothing_for_popen_cleanup_to_trip_over():
    done = subprocess.run([sys.executable, "-c", QUIET_HELPER], capture_output=True, text=True, timeout=60)
    pid, rest = done.stdout.split(maxsplit=1)
    try:
        assert rest.startswith("0 "), rest
    finally:
        subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True, check=False)


# isatty() alone can't tell: Windows calls NUL a terminal too. A console handle has a console mode.
TERMINAL_HELPER = """
import ctypes, msvcrt, sys
def console(stream):
    mode = ctypes.c_ulong()
    return bool(ctypes.windll.kernel32.GetConsoleMode(msvcrt.get_osfhandle(stream.fileno()), ctypes.byref(mode)))
open(sys.argv[1], "w").write(f"{console(sys.stdin)} {console(sys.stdout)}")
"""


@pytest.mark.windows_only
def test_the_command_reads_and_writes_its_own_console(tmp_path):
    # `run` opens the run window only when stdin and stdout are a terminal (`can_open_tui`); in a
    # console of its own they must be that console, not NUL, or the window stays blank. The caller's
    # stdout is a pipe here, as under the TUI or a script: the window must not get it either.
    out = tmp_path / "answer.txt"
    caller = (
        "import sys; from comfyui_sandbox.winapi import WindowsApi; "
        f"WindowsApi().open_console([sys.executable, '-c', {TERMINAL_HELPER!r}, {str(out)!r}])"
    )
    subprocess.run(
        [sys.executable, "-c", caller], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, timeout=30, check=True
    )

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not (out.exists() and out.read_text()):
        time.sleep(0.2)
    assert out.read_text() == "True True"
