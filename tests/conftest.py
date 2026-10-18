"""The test seam: invoke the CLI with argv, fake Runner, fake winapi, injected home (docs/adr/0013).

`cli(*argv, app=None, platform="win32") -> Invocation(exit_code, stdout, stderr)` runs `main` with the fixtures
below, all function-scoped and shared with `cli`, so a test scripts them first and inspects them after.
`Invocation.json` parses stdout and asserts it is exactly one document. `app=` takes a `build_app()` with extra
test-only commands. With the real app, every successful `--json` document's `data` is checked against
docs/scripting.md (`tests/scripting_doc.py`).

- `runner` (`FakeRunner`): `on(*argv_prefix, returncode=, stdout=, stderr=)` scripts a result (latest matching
  prefix wins; unscripted calls succeed with no output); `on_script(name, ...)` scripts a package script whatever
  its argv; `calls` records every call. `tests/wslcalls.py` reads those calls by meaning (scripts, commands,
  distro-level operations, docker execs).
- `winapi` (`FakeWinapi`): a dataclass; set its fields to script it and read its records to assert.
- `prompter` (`FakePrompter`): queue answers; an unexpected prompt fails the test.
- `environ`: what the CLI sees. `ProgramFiles` is an empty folder, so WSL counts as not installed until a test
  creates `WSL\\wsl.exe` there.
- `home`, `http` (`FakeHttp`); `sleep` is a no-op.
- `launcher` (`FakeLauncher`): the front-end launcher; `opened` records the screens asked for. `cli(...,
  terminal=True)` plays a terminal on both stdin and stdout (or pass `stdin_terminal=` / `stdout_terminal=`
  apart); by default there is none, as under a script, so nothing opens the TUI unless a test asks.

`tests/test_check.py: Machine` scripts a ready host for any command that runs the `check` inspection.
"""

import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from scripting_doc import undocumented_data_keys

from comfyui_sandbox import cli as cli_module
from comfyui_sandbox import cli_command
from comfyui_sandbox.deps import Deps
from fakes import FakeHttp, FakeLauncher, FakePrompter, FakeRunner, FakeWinapi


def pytest_collection_modifyitems(config, items):
    """`@pytest.mark.windows_only` tests hit the real winapi; skip them elsewhere."""
    if sys.platform == "win32":
        return
    skip = pytest.mark.skip(reason="needs Windows (real winapi)")
    for item in items:
        if "windows_only" in item.keywords:
            item.add_marker(skip)


@dataclass
class Invocation:
    exit_code: int
    stdout: str
    stderr: str

    @property
    def json(self) -> dict:
        """The one JSON document on stdout. Fails if stdout holds anything else."""
        decoder = json.JSONDecoder()
        text = self.stdout
        doc, end = decoder.raw_decode(text)
        assert text[end:].strip() == "", f"stdout has more than one document: {text!r}"
        assert isinstance(doc, dict)
        return doc


CLI_ON_PATH = r"C:\tools\bin\comfyui-sandbox.exe"


@pytest.fixture(autouse=True)
def cli_on_path(monkeypatch, tmp_path):
    """`comfyui-sandbox` is at a fixed place on PATH, whatever machine this runs on: the run command is that path."""
    monkeypatch.setattr(cli_command, "which", lambda name: CLI_ON_PATH)
    monkeypatch.setattr(cli_command, "PYTHON", tmp_path / "no-venv" / "python.exe")


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def winapi() -> FakeWinapi:
    return FakeWinapi()


@pytest.fixture
def home(tmp_path: Path) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    return path


@pytest.fixture
def environ(tmp_path: Path, home: Path) -> dict[str, str]:
    """The environment the CLI sees. `ProgramFiles` is an empty temporary folder."""
    program_files = tmp_path / "Program Files"
    program_files.mkdir()
    return {"ProgramFiles": str(program_files), "USERPROFILE": str(home), "USERNAME": "Alex"}


@pytest.fixture
def prompter() -> FakePrompter:
    return FakePrompter()


@pytest.fixture
def cli(runner, winapi, home, environ, prompter, http, launcher):
    def invoke(
        *argv: str,
        app=None,
        platform: str = "win32",
        terminal: bool = False,
        stdin_terminal: bool | None = None,
        stdout_terminal: bool | None = None,
    ) -> Invocation:
        out, err = io.StringIO(), io.StringIO()
        deps = Deps(
            runner=runner,
            winapi=winapi,
            home=home,
            stdout=out,
            stderr=err,
            platform=platform,
            environ=environ,
            prompter=prompter,
            http=http,
            sleep=lambda seconds: None,
            stdin_is_terminal=terminal if stdin_terminal is None else stdin_terminal,
            stdout_is_terminal=terminal if stdout_terminal is None else stdout_terminal,
            launch=launcher,
        )
        code = cli_module.main(list(argv), deps, app=app)
        result = Invocation(code, out.getvalue(), err.getvalue())
        if app is None and result.stdout.startswith("{"):
            doc = result.json
            if doc["ok"]:
                assert not undocumented_data_keys(doc), (
                    f"`{doc['command']}` returns `data` keys docs/scripting.md doesn't list: "
                    f"{sorted(undocumented_data_keys(doc))}"
                )
        return result

    return invoke


@pytest.fixture
def http() -> FakeHttp:
    return FakeHttp()


@pytest.fixture
def launcher() -> FakeLauncher:
    return FakeLauncher()
