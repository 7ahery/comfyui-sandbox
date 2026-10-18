"""The code stays type-clean, and this file is what makes a type error fail the suite.

ty's configuration is in `pyproject.toml` (`[tool.ty]`). The check runs over the source and the tests
with the ty from the dev environment, so the result doesn't depend on what is on PATH. When it fails,
ty's own report is the failure message. A targeted `# ty: ignore[rule]` with its reason is the way to
accept a finding that can't be fixed; a rule is never turned off for a single case.

ty is pre-1.0, so a ty upgrade can bring new diagnostics: `uv.lock` pins the version, and an upgrade
is a deliberate step that fixes what it finds.

ty is a dev dependency (`uv sync` installs it). Missing, this test fails with an install hint rather
than skips (see CLAUDE.md, "Development requirements").
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def require_ty() -> None:
    """Fails (never skips) when ty isn't installed: the types would go unchecked."""
    if importlib.util.find_spec("ty") is None:
        pytest.fail("ty is not installed. It is a dev dependency: run `uv sync`, or `uv pip install ty`.")


def test_the_source_and_tests_pass_ty_check():
    require_ty()
    result = subprocess.run(
        [sys.executable, "-m", "ty", "check", "--error-on-warning", "--no-progress", "--color", "never"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
