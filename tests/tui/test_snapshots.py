"""Every TUI flow still draws the screens recorded in snapshots/ (see scenarios.py). It runs with the
rest of the suite (`uv run pytest`), so a screen never changes without someone looking at it.

After a change that's meant to change the screens, record them again and review the diff:

    SNAPSHOT_UPDATE=1 uv run pytest tests/tui/test_snapshots.py

Textual is a runtime dependency, installed by `uv sync`. Without it these tests fail; they never
skip, since a skipped screen test is a screen nobody checked.
"""

import difflib
import importlib.util
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

HERE = Path(__file__).parent
SNAPSHOTS = HERE / "snapshots"
UPDATE = os.environ.get("SNAPSHOT_UPDATE") == "1"


def scenario_names() -> list[str]:
    # scenarios.py imports Textual, so the names are read from the snapshots on disk; a scenario
    # without one fails below rather than going untested.
    names = {p.stem for p in SNAPSHOTS.glob("*.json")}
    if importlib.util.find_spec("textual") is not None:
        import scenarios  # tests/tui is on sys.path: pytest puts a test's own folder there

        names |= set(scenarios.SCENARIOS)
    return sorted(names)


def run(name: str, out: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(HERE / "scenarios.py"), name, str(out)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=600,
    )


_pool_results: dict[str, tuple[subprocess.CompletedProcess, Path]] | None = None


def _run_all(tmp_path_factory) -> dict[str, tuple[subprocess.CompletedProcess, Path]]:
    """Runs every scenario, four at a time (each takes seconds of real time)."""
    out = tmp_path_factory.mktemp("snapshots")
    names = scenario_names()
    with ThreadPoolExecutor(4) as pool:
        done = pool.map(lambda n: run(n, out / f"{n}.json"), names)
    return {n: (proc, out / f"{n}.json") for n, proc in zip(names, done, strict=True)}


@pytest.fixture
def result(request, tmp_path_factory) -> tuple[subprocess.CompletedProcess, Path]:
    """The run of the scenario under test. Serially, all of them run up front, four at a time.
    Under pytest-xdist each worker runs only its own scenarios, so the workers (not a pool in
    every worker) provide the parallelism."""
    global _pool_results
    if importlib.util.find_spec("textual") is None:
        pytest.fail("Textual isn't installed. It's a runtime dependency: run `uv sync`.")
    name = request.node.callspec.params["name"]
    if os.environ.get("PYTEST_XDIST_WORKER"):
        out = tmp_path_factory.mktemp("snapshots")
        return run(name, out / f"{name}.json"), out / f"{name}.json"
    if _pool_results is None:
        _pool_results = _run_all(tmp_path_factory)
    return _pool_results[name]


@pytest.mark.parametrize("name", scenario_names())
def test_screens_match(name: str, result) -> None:
    proc, path = result
    assert proc.returncode == 0, f"scenarios.py {name} crashed:\n{proc.stderr}"
    got = json.loads(path.read_text(encoding="utf-8"))
    assert got["error"] is None, f"{name} didn't get through its flow:\n{got['error']}"
    golden = SNAPSHOTS / f"{name}.json"
    if UPDATE:
        SNAPSHOTS.mkdir(exist_ok=True)
        golden.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        return
    assert golden.exists(), f"No snapshots for {name}: record them with SNAPSHOT_UPDATE=1"
    want = json.loads(golden.read_text(encoding="utf-8"))
    assert [n for n, _ in got["snapshots"]] == [n for n, _ in want["snapshots"]], "the screens taken differ"
    for (shot, lines), (_, expected) in zip(got["snapshots"], want["snapshots"], strict=True):
        if lines != expected:
            diff = "\n".join(difflib.unified_diff(expected, lines, "recorded", "now", lineterm=""))
            pytest.fail(f"{name}, screen {shot!r} changed:\n{diff}")
    assert got["opened"] == want["opened"]
    assert got["return_code"] == want["return_code"]
