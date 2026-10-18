"""The real Runner (docs/adr/0013), using this Python as the child process."""

import sys
import threading
import time
from pathlib import Path

import pytest

from comfyui_sandbox.cancel import CancelSwitch
from comfyui_sandbox.runner import CommandNotFound, CommandTimeout, SubprocessRunner

PY = sys.executable


def py(code: str) -> list[str]:
    return [PY, "-c", code]


def test_run_returns_exit_code_and_output_without_raising_on_failure():
    result = SubprocessRunner().run(
        py(
            "import sys; sys.stdout.buffer.write('h\\u00e9\\r\\n\\0'.encode('utf-8'));"
            "sys.stderr.write('oops'); sys.exit(3)"
        )
    )

    assert result.returncode == 3
    assert result.out() == "hé\n"
    assert result.err() == "oops"


def test_run_feeds_stdin_and_adds_env_to_the_current_environment(monkeypatch):
    monkeypatch.setenv("FROM_PARENT", "kept")
    result = SubprocessRunner().run(
        py("import os, sys; print(sys.stdin.read(), os.environ['FROM_PARENT'], os.environ['WSL_UTF8'])"),
        stdin=b"script",
        env={"WSL_UTF8": "1"},
    )

    assert result.out() == "script kept 1\n"


def test_run_without_stdin_gives_the_child_no_input():
    result = SubprocessRunner().run(py("import sys; print(repr(sys.stdin.read()))"), timeout=30)

    assert result.out() == "''\n"


def test_stream_delivers_merged_lines_as_they_arrive():
    lines: list[str] = []
    result = SubprocessRunner().stream(
        py(
            "import sys; print('one', flush=True); print('two', file=sys.stderr, flush=True);"
            "print('three\\r', flush=True); sys.exit(5)"
        ),
        lines.append,
    )

    assert lines == ["one", "two", "three"]
    assert result.returncode == 5
    assert result.out() == "one\ntwo\nthree\n"


def test_stream_feeds_stdin():
    lines: list[str] = []
    SubprocessRunner().stream(py("import sys; print(sys.stdin.read().upper())"), lines.append, stdin=b"bash script")

    assert lines == ["BASH SCRIPT"]


def wait_until(condition, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.02)


def test_follow_returns_at_once_and_delivers_lines_until_terminated():
    lines: list[str] = []
    process = SubprocessRunner().follow(
        py("import time\nfor n in range(1000):\n    print(n, flush=True)\n    time.sleep(0.05)"), lines.append
    )

    wait_until(lambda: len(lines) >= 3)  # it keeps reporting while the caller gets on with things
    assert process.poll() is None
    assert lines[:3] == ["0", "1", "2"]

    process.terminate()
    wait_until(lambda: process.poll() is not None)


def test_follow_reports_a_process_that_ends_on_its_own():
    lines: list[str] = []
    process = SubprocessRunner().follow(py("print('no GPU\\r', flush=True); raise SystemExit(9)"), lines.append)

    wait_until(lambda: process.poll() is not None)
    assert process.poll() == 9
    wait_until(lambda: lines == ["no GPU"])


def test_a_missing_program_is_a_cli_error():
    with pytest.raises(CommandNotFound):
        SubprocessRunner().run(["definitely-not-a-real-program-xyz"])
    with pytest.raises(CommandNotFound):
        SubprocessRunner().follow(["definitely-not-a-real-program-xyz"], lambda line: None)


@pytest.mark.parametrize("method", ["run", "stream"])
def test_a_timeout_is_a_cli_error(method):
    runner = SubprocessRunner()
    argv = py("import time; time.sleep(30)")
    with pytest.raises(CommandTimeout):
        if method == "run":
            runner.run(argv, timeout=0.5)
        else:
            runner.stream(argv, lambda line: None, timeout=0.5)


# -- the cancel switch (feat-tui-integration 10) ----------------------------------------------------


def call(runner, method: str, argv: list[str]):
    if method == "run":
        return runner.run(argv, timeout=60)
    return runner.stream(argv, lambda line: None, timeout=60)


def busy_child(tmp_path) -> tuple[list[str], Path, Path]:
    """A child that says it has started (a file), then, unless it's stopped first, says it lived on
    (another file) a moment later."""
    started, survived = tmp_path / "started", tmp_path / "survived"
    argv = py(
        f"import pathlib, time\npathlib.Path({str(started)!r}).touch()\ntime.sleep(1.5)\n"
        f"pathlib.Path({str(survived)!r}).touch()\ntime.sleep(30)"
    )
    return argv, started, survived


@pytest.mark.parametrize("method", ["run", "stream"])
def test_flipping_the_switch_terminates_the_running_child_and_raises_the_interruption(tmp_path, method):
    switch = CancelSwitch()
    runner = SubprocessRunner().cancellable(switch)
    argv, started, survived = busy_child(tmp_path)

    def flip_once_started():
        wait_until(started.exists)
        switch.flip()

    threading.Thread(target=flip_once_started, daemon=True).start()
    begun = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        call(runner, method, argv)

    assert time.monotonic() - begun < 20  # not the child's 30 seconds
    time.sleep(2)  # past the moment it would have said it lived on
    assert not survived.exists()


@pytest.mark.parametrize("method", ["run", "stream"])
def test_calls_after_the_interruption_run_normally_for_cleanup(method):
    switch = CancelSwitch()
    runner = SubprocessRunner().cancellable(switch)
    switch.flip()
    with pytest.raises(KeyboardInterrupt):  # flipped between calls: the next one doesn't start
        call(runner, method, py("raise SystemExit(4)"))

    assert call(runner, method, py("print('cleanup')")).out() == "cleanup\n"


def test_a_runner_with_no_switch_flipped_runs_as_before():
    runner = SubprocessRunner().cancellable(CancelSwitch())

    assert runner.run(py("print('hi')")).out() == "hi\n"
