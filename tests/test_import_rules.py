"""The CLI and the TUI are two front ends over one core, and these tests keep them apart.

ADR 0017 records the rules; this file is what makes breaking them fail the suite instead of
quietly eroding them:

- the import-linter contracts in `pyproject.toml` (`[tool.importlinter]`): the core never imports
  the `tui` subpackage or Textual, only the TUI's gateway layer imports the core, and nothing in
  the TUI imports the platform layer (`winapi`, `wsl`, `runner`) directly;
- the laziness test: importing the CLI doesn't load Textual, so a plain command never pays for it.

A rule that can't fail proves nothing, so each one is also run against a throwaway copy of the
package with one forbidden import planted in it, and must fail there. The copies live in
`tmp_path`; the real source is never touched.

import-linter is a dev dependency (`uv sync` installs it). Missing, these tests fail with an
install hint rather than skip (see CLAUDE.md, "Development requirements").
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import comfyui_sandbox

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
PACKAGE = Path(comfyui_sandbox.__file__).resolve().parent

# The contract names in pyproject.toml; a broken contract's report names it, which is how the
# planted-violation tests tell that the right contract caught the import.
CORE_RULE = "The core doesn't import the TUI or Textual"
GATEWAY_RULE = "Inside the TUI, only the gateway layer imports the core"
PLATFORM_RULE = "The TUI doesn't import the platform layer directly"


def require_import_linter() -> None:
    """Fails (never skips) when import-linter isn't installed: the rules would go unchecked."""
    if importlib.util.find_spec("importlinter") is None:
        pytest.fail(
            "import-linter is not installed. It is a dev dependency: run `uv sync`, or `uv pip install import-linter`."
        )


def lint_imports(pythonpath: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Runs the project's contracts in a fresh interpreter and returns what it printed.

    A subprocess because the package to check is found by import: pointing PYTHONPATH at a copy
    makes that copy the `comfyui_sandbox` import-linter sees, ahead of the installed one. No
    cache, so a copy is never judged by a graph built from the real source (or the other way).
    """
    require_import_linter()
    code = (
        "import sys; from importlinter.cli import lint_imports; "
        f"sys.exit(lint_imports(config_filename={str(PYPROJECT)!r}, no_cache=True, no_logo=True))"
    )
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    if pythonpath is not None:
        env["PYTHONPATH"] = str(pythonpath)
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=ROOT,
        timeout=300,
        check=False,
    )


def textual_loaded_by_cli(pythonpath: Path | None = None) -> bool:
    """Imports the CLI (and builds its command tree) in a fresh interpreter: is Textual loaded?

    Fresh because this process has long since imported Textual for the TUI tests. Building the
    app as well as importing `cli` covers a command module that's only imported while the
    commands are registered.
    """
    code = "import sys; import comfyui_sandbox.cli as cli; cli.build_app(); print('textual' in sys.modules)"
    env = dict(os.environ)
    if pythonpath is not None:
        env["PYTHONPATH"] = str(pythonpath)
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=ROOT, timeout=120, check=False
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.strip() == "True"


def planted(tmp_path: Path, module: str, line: str) -> Path:
    """A copy of the package with `line` added to the end of `module` (a path under the package).

    Returns the folder to put on PYTHONPATH. The import is appended, not prepended, so the
    module's own `from __future__` import stays first.
    """
    copy = tmp_path / "src" / "comfyui_sandbox"
    shutil.copytree(PACKAGE, copy, ignore=shutil.ignore_patterns("__pycache__"))
    target = copy / module
    assert target.is_file(), f"no module {module} to plant an import in"
    target.write_text(target.read_text(encoding="utf-8") + f"\n{line}\n", encoding="utf-8")
    return copy.parent


def broken(result: subprocess.CompletedProcess[str]) -> str:
    """The contracts' report, after asserting that the run failed."""
    assert result.returncode != 0, result.stdout + result.stderr
    return result.stdout


# --- The real source keeps every rule ---------------------------------------------------------


def test_the_source_keeps_the_import_contracts():
    result = lint_imports()
    assert result.returncode == 0, result.stdout + result.stderr
    for name in (CORE_RULE, GATEWAY_RULE, PLATFORM_RULE):
        assert f"{name} KEPT" in result.stdout


def test_importing_the_cli_does_not_load_textual():
    assert not textual_loaded_by_cli()


# --- Each rule catches a planted violation ----------------------------------------------------


@pytest.mark.parametrize(
    "module, line",
    [
        ("output.py", "import comfyui_sandbox.tui.theme"),
        ("commands/status.py", "from comfyui_sandbox.tui.gateway import Sandbox"),
        ("cli.py", "import textual"),
    ],
    ids=["core imports the tui", "a command imports the gateway", "core imports textual"],
)
def test_the_core_may_not_import_the_tui_or_textual(tmp_path, line, module):
    report = broken(lint_imports(planted(tmp_path, module, line)))
    assert f"{CORE_RULE} BROKEN" in report


@pytest.mark.parametrize(
    "module, line",
    [
        ("tui/home/flows.py", "import comfyui_sandbox.state"),
        ("tui/theme.py", "from comfyui_sandbox.commands import check"),
        ("tui/run_window/app.py", "from comfyui_sandbox import cli"),
    ],
    ids=["a flow imports the core", "a shared module imports a command", "the run window imports the cli"],
)
def test_only_the_gateway_layer_may_import_the_core(tmp_path, line, module):
    report = broken(lint_imports(planted(tmp_path, module, line)))
    assert f"{GATEWAY_RULE} BROKEN" in report


def test_the_gateway_layer_may_import_the_core(tmp_path):
    # The real gateway does: it is the one door from the TUI into the core.
    result = lint_imports(planted(tmp_path, "tui/gateway/protocol.py", "import comfyui_sandbox.state"))
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "module, line",
    [
        ("tui/gateway/protocol.py", "import comfyui_sandbox.winapi"),
        ("tui/gateway/demo/home.py", "from comfyui_sandbox.wsl import Wsl"),
        ("tui/home/app.py", "from comfyui_sandbox.runner import SubprocessRunner"),
        ("tui/gateway/demo/home.py", "import subprocess"),
        ("tui/home/flows.py", "import webbrowser"),
        ("tui/gateway/real/meters.py", "import ctypes"),
    ],
    ids=[
        "the gateway imports winapi",
        "the demo gateway imports wsl",
        "a screen imports the runner",
        "the demo gateway starts a process itself",
        "a flow opens the browser itself",
        "the gateway calls Win32 itself",
    ],
)
def test_the_tui_may_not_import_the_platform_layer(tmp_path, line, module):
    report = broken(lint_imports(planted(tmp_path, module, line)))
    assert f"{PLATFORM_RULE} BROKEN" in report


def test_the_gateway_may_reach_the_platform_layer_through_the_core(tmp_path):
    # `cli` imports the runner and winapi (through deps); going through `main()` is the design.
    result = lint_imports(planted(tmp_path, "tui/gateway/protocol.py", "from comfyui_sandbox import cli"))
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_laziness_test_catches_a_cli_module_importing_textual(tmp_path):
    assert textual_loaded_by_cli(planted(tmp_path, "commands/status.py", "import textual"))
