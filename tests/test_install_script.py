"""`install.ps1`, the README's one-line install, run the way the README runs it: piped into `iex`.

Each test runs Windows PowerShell 5.1 (the one every Windows has) with a PATH holding only a folder
of fake `uv` and `winget` commands and System32, so nothing real is installed. The fakes append
their arguments to `calls.log`, and a fake winget "installs" uv by writing a fake `uv.cmd`.
"""

import os
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "install.ps1"
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
SOURCE = f"git+https://github.com/7ahery/comfyui-sandbox@v{VERSION}"
# Printed after `iex` returns: the script must hand the window back, never `exit` it.
AFTER = "still-open"

pytestmark = pytest.mark.windows_only


def fake(folder: Path, name: str, *, exit_code: int = 0, body: str = "") -> None:
    log = folder / "calls.log"
    (folder / f"{name}.cmd").write_text(
        f'@echo off\r\necho {name} %*>>"{log}"\r\n{body}exit /b {exit_code}\r\n', encoding="utf-8"
    )


def fake_uv(folder: Path, *, install_exit: int = 0) -> None:
    fake(folder, "uv", body=f'if "%1 %2"=="tool install" exit /b {install_exit}\r\n')


def run_script(folder: Path) -> tuple[str, list[str]]:
    system32 = Path(os.environ["SYSTEMROOT"]) / "System32"
    env = {**os.environ, "PATH": f"{folder};{system32};{system32 / 'WindowsPowerShell' / 'v1.0'}"}
    command = f"Get-Content -Raw '{SCRIPT}' | Invoke-Expression; '{AFTER}'"
    done = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.rstrip().endswith(AFTER), done.stdout
    log = folder / "calls.log"
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return done.stdout, [call.strip() for call in calls]


def test_with_uv_present_it_installs_the_tagged_release_and_puts_it_on_path(tmp_path):
    fake_uv(tmp_path)
    fake(tmp_path, "winget")

    out, calls = run_script(tmp_path)

    assert calls == [f"uv tool install {SOURCE}", "uv tool update-shell"]
    assert f"comfyui-sandbox v{VERSION} is installed" in out


def test_without_uv_it_installs_uv_with_winget_first(tmp_path):
    uv = tmp_path / "uv.cmd"
    fake(tmp_path, "winget", body=f'echo @echo off^&echo uv %%*^>^>"{tmp_path / "calls.log"}">"{uv}"\r\n')

    out, calls = run_script(tmp_path)

    assert calls == [
        "winget install --id astral-sh.uv --exact --source winget",
        f"uv tool install {SOURCE}",
        "uv tool update-shell",
    ]
    assert "is installed" in out


def test_without_uv_or_winget_it_says_where_to_get_uv(tmp_path):
    out, calls = run_script(tmp_path)

    assert calls == []
    assert "https://docs.astral.sh/uv/" in out


def test_a_failed_winget_stops_before_installing(tmp_path):
    fake(tmp_path, "winget", exit_code=5)

    out, calls = run_script(tmp_path)

    assert calls == ["winget install --id astral-sh.uv --exact --source winget"]
    assert "exit code 5" in out


def test_a_failed_install_is_reported_and_not_called_a_success(tmp_path):
    fake_uv(tmp_path, install_exit=2)

    out, calls = run_script(tmp_path)

    assert calls == [f"uv tool install {SOURCE}"]
    assert "could not install comfyui-sandbox (exit code 2)" in out
    assert "is installed" not in out


def test_the_script_and_the_readme_pin_the_project_version():
    script = SCRIPT.read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    tag = f"v{VERSION}"

    assert re.findall(r"\$Version = '([^']+)'", script) == [tag]
    assert set(re.findall(r"comfyui-sandbox@(v[\d.]+)", readme)) == {tag}
    # The one-liner fetches the script from main, so a release only has to bump the pins above.
    assert "raw.githubusercontent.com/7ahery/comfyui-sandbox/main/install.ps1 | iex" in readme
