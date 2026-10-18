"""The code stays lint-clean and formatted, and this file is what makes drift fail the suite.

Ruff's configuration is in `pyproject.toml` (`[tool.ruff]`). Both checks run over the whole project
with the ruff from the dev environment, so the result doesn't depend on what is on PATH. When one
fails, ruff's own report is the failure message: `uv run ruff format` and `uv run ruff check --fix`
fix most findings.

ruff is a dev dependency (`uv sync` installs it). Missing, these tests fail with an install hint
rather than skip (see CLAUDE.md, "Development requirements").
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def require_ruff() -> None:
    """Fails (never skips) when ruff isn't installed: the style would go unchecked."""
    if importlib.util.find_spec("ruff") is None:
        pytest.fail("ruff is not installed. It is a dev dependency: run `uv sync`, or `uv pip install ruff`.")


def ruff(*args: str) -> subprocess.CompletedProcess[str]:
    """Runs ruff from the repo root, so the project's configuration and exclusions apply."""
    require_ruff()
    return subprocess.run(
        [sys.executable, "-m", "ruff", *args, "--no-cache"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
        timeout=300,
        check=False,
    )


def test_the_source_passes_ruff_check():
    result = ruff("check")
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_source_is_formatted_by_ruff():
    result = ruff("format", "--check", "--diff")
    assert result.returncode == 0, result.stdout + result.stderr
