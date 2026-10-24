"""Kernel readiness must not depend on translated labels or update a custom kernel."""

import pytest
from test_check import Machine

from comfyui_sandbox.errors import CliError
from comfyui_sandbox.runner import Completed
from comfyui_sandbox.steps import ensure_ok


@pytest.mark.parametrize("command", ["check", "prereqs"])
@pytest.mark.parametrize(
    "label", ["Kernel version", "Version du noyau", "Версия ядра", "カーネル バージョン", "内核版本"]
)
def test_supported_kernel_is_ready_in_each_locale(command, label, cli, runner, environ, winapi):
    Machine(wsl_version=f"WSL version: 2.6.1.0\n{label}: 6.6.87.2-1\n").script(runner, environ)

    result = cli(command, "--json", "--yes")

    assert result.exit_code == 0
    assert winapi.elevated == []
    assert "not verified" not in result.stderr
    if command == "check":
        assert result.json["data"]["wsl"]["kernel"] == "6.6.87.2"


def test_fullwidth_colon_in_localized_output(cli, runner, environ):
    Machine(wsl_version="WSL 版本：2.6.1.0\n内核版本：6.6.87.2-1\n").script(runner, environ)

    result = cli("check", "--json")

    assert result.exit_code == 0
    assert result.json["data"]["wsl"]["kernel"] == "6.6.87.2"


@pytest.mark.parametrize("command", ["check", "prereqs"])
def test_custom_kernel_does_not_bypass_minimum_wsl_package(command, cli, runner, environ, home, winapi):
    Machine(wsl_version="WSL version: 2.4.3.0\nKernel version: 6.6.87.2-1\n").script(runner, environ)
    (home / ".wslconfig").write_text("[wsl2]\nkernel=custom\n", encoding="utf-8")

    result = cli(command, "--json", "--yes")

    assert result.exit_code == (7 if command == "check" else 0)
    assert bool(winapi.elevated) == (command == "prereqs")


@pytest.mark.parametrize("command", ["check", "prereqs"])
@pytest.mark.parametrize("kernel", ["", "not-a-version\nWSLg version: 9.9.9.9"])
def test_unknown_kernel_does_not_trigger_updates_or_claim_verified(command, kernel, cli, runner, environ, winapi):
    Machine(wsl_version=f"WSL version: 2.6.1.0\nKernel version: {kernel}\n").script(runner, environ)

    for _ in range(2):
        result = cli(command, "--json", "--yes")

        assert result.exit_code == 0
        assert "not verified" in result.stderr
        assert "too old" not in result.stderr
        if command == "check":
            assert result.json["data"]["wsl"]["kernel"] is None
        else:
            assert result.json["data"]["action"] == "none"
            assert "ready" not in result.json["message"].lower()
    assert winapi.elevated == []


@pytest.mark.parametrize("command", ["check", "prereqs"])
@pytest.mark.parametrize("packaged_kernel", ["5.15.167.4-1", "6.6.87.2-1"])
@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16"])
def test_custom_kernel_is_unverified_without_changing_it(
    command, packaged_kernel, encoding, cli, runner, environ, home, winapi
):
    Machine(wsl_version=f"WSL version: 2.6.1.0\nKernel version: {packaged_kernel}\n").script(runner, environ)
    config = home / ".wslconfig"
    original = "[WsL2]\r\nKeRnEl = C:\\private\\custom-kernel\r\nmemory=8GB\r\n".encode(encoding)
    config.write_bytes(original)

    result = cli(command, "--json", "--yes")

    assert result.exit_code == 0
    assert "custom" in result.stderr.lower()
    assert "not verified" in result.stderr
    assert "C:\\private" not in result.stdout + result.stderr
    assert winapi.elevated == []
    assert config.read_bytes() == original
    assert all("-d" not in call.argv and "--shutdown" not in call.argv for call in runner.calls)


@pytest.mark.parametrize("command", ["check", "prereqs"])
@pytest.mark.parametrize("kernel,old", [("6.5.99.9", True), ("6.6.0", False), ("6.18.33.2-2", False)])
def test_bundled_kernel_boundary(command, kernel, old, cli, runner, environ, winapi):
    Machine(wsl_version=f"WSL version: 2.6.1.0\nVersion du noyau: {kernel}\n").script(runner, environ)

    result = cli(command, "--json", "--yes")

    assert result.exit_code == (7 if old and command == "check" else 0)
    assert bool(winapi.elevated) == (old and command == "prereqs")


@pytest.mark.parametrize("command", ["check", "prereqs"])
@pytest.mark.parametrize("config", ["[wsl2]\nkernel=\n", '[wsl2]\nkernel=""\n', "[other]\nkernel=custom\n"])
def test_empty_or_unrelated_kernel_setting_does_not_bypass_old_kernel_check(
    command, config, cli, runner, environ, home, winapi
):
    Machine(wsl_version="WSL version: 2.6.1.0\nKernel version: 5.15.167.4-1\n").script(runner, environ)
    (home / ".wslconfig").write_text(config, encoding="utf-8")

    result = cli(command, "--json", "--yes")

    assert result.exit_code == (7 if command == "check" else 0)
    assert bool(winapi.elevated) == (command == "prereqs")


@pytest.mark.parametrize("command", ["check", "prereqs"])
def test_unreadable_config_cannot_trigger_an_ineffective_kernel_update(
    command, cli, runner, environ, home, winapi, monkeypatch
):
    from pathlib import Path

    Machine(wsl_version="WSL version: 2.6.1.0\nKernel version: 5.15.167.4-1\n").script(runner, environ)
    read_bytes = Path.read_bytes

    def read(path):
        if path == home / ".wslconfig":
            raise PermissionError("denied")
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read)

    result = cli(command, "--json", "--yes")

    assert result.exit_code == 0
    assert "Could not read .wslconfig" in result.stderr
    assert "not verified" in result.stderr
    assert winapi.elevated == []


def test_unexpected_wsl_error_keeps_original_diagnostics():
    done = Completed(("wsl.exe",), 4294967295, b"Wsl/Service/E_UNEXPECTED\n", b"Specific startup detail\n")

    with pytest.raises(CliError) as raised:
        ensure_ok(done, "Creating the Linux user")

    message = str(raised.value)
    assert "4294967295" in message
    assert "Specific startup detail" in message
    assert "Wsl/Service/E_UNEXPECTED" in message
    assert "prereqs" in message
    assert "check" in message
