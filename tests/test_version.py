import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
PROJECT_VERSION = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]


def test_version_prints_the_project_version_on_stdout(cli):
    result = cli("--version")

    assert result.exit_code == 0
    assert result.stdout == f"comfyui-sandbox {PROJECT_VERSION}\n"
