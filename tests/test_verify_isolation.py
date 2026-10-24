"""Exercise the real isolation checklist with local config and binfmt fixtures.

These tests establish conservative reporting, not cross-distro WSL isolation.
"""

import subprocess
from pathlib import Path

import pytest

from comfyui_sandbox.wsl import script_text
from toolchain import bash

CONFIG = """\
[automount]
enabled = false
[interop]
enabled = false
appendWindowsPath = false
[user]
default = comfy
"""


def isolation(
    tmp_path: Path,
    config: str | None = CONFIG,
    *,
    status="enabled",
    handlers=(),
    enumeration_fails=False,
    isolation_only=False,
    mounts_available=True,
):
    conf = tmp_path / "wsl.conf"
    if config is not None:
        conf.write_text(config, newline="\n")
    registry = tmp_path / "binfmt_misc"
    registry.mkdir()
    if status is not None:
        (registry / "status").write_text(status + "\n", newline="\n")
    for index, handler in enumerate(handlers):
        (registry / f"WSLInterop{index}").write_text(handler + "\ninterpreter /init\n", newline="\n")
    before = {p.name: p.read_bytes() for p in registry.iterdir()}
    script = script_text("verify.sh")
    if not isolation_only:
        script = script.split("while read -r tgt src;", 1)[0]
    script = script.replace("/etc/wsl.conf", conf.as_posix())
    script = script.replace("/proc/sys/fs/binfmt_misc", registry.as_posix())
    mounts = 'findmnt(){ case " $* " in *" -t "*) return 0;; esac; echo /; }\n'
    if not mounts_available:
        mounts = "findmnt(){ return 1; }\n"
    script = mounts + script + "\nexit $rc\n"
    if enumeration_fails:
        script = "find(){ return 1; }\n" + script
    done = subprocess.run(
        [bash(), "-s"],
        input=script,
        env={"PATH": "/usr/bin:/bin", "ISOLATION_ONLY": "1"},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert "command not found" not in done.stderr, done.stderr
    done.stdout = done.stdout.replace(conf.as_posix(), "/etc/wsl.conf")
    assert {p.name: p.read_bytes() for p in registry.iterdir()} == before
    return done


@pytest.mark.parametrize("handlers, expected", [((), 0), (("enabled",), 1)])
def test_startup_mode_runs_only_the_isolation_checklist(tmp_path, handlers, expected):
    done = isolation(tmp_path, handlers=handlers, isolation_only=True)
    assert done.returncode == expected, done.stdout + done.stderr
    assert "[INFO] WSL isolation check complete" in done.stdout
    assert "== Resources ==" not in done.stdout
    assert "== Docker ==" not in done.stdout


def test_unavailable_mount_listing_cannot_report_isolation_success(tmp_path):
    done = isolation(tmp_path, isolation_only=True, mounts_available=False)
    assert done.returncode == 1
    assert "[FAIL] Windows drive mounts could not be checked" in done.stdout


@pytest.mark.parametrize("handlers", [("enabled",), ("disabled", "enabled")])
def test_an_enabled_shared_handler_is_inconclusive_not_proof_of_windows_execution(tmp_path, handlers):
    done = isolation(tmp_path, handlers=handlers)
    assert done.returncode == 1
    assert "[FAIL] WSL interop runtime isolation could not be verified" in done.stdout
    assert "[FAIL] WSL interop is enabled" not in done.stdout
    assert "[PASS] WSL interop disabled\n" not in done.stdout


@pytest.mark.parametrize(
    "config",
    [
        None,
        "",
        CONFIG.replace("[interop]", "[other]"),
        CONFIG.replace("appendWindowsPath = false\n", ""),
        CONFIG.replace("enabled = false\nappend", "enabled = true\nappend"),
        CONFIG.replace("appendWindowsPath = false", "appendWindowsPath = true"),
        CONFIG.replace("[user]", "enabled = true\n[user]"),
        CONFIG + "[interop]\nenabled = true\n",
        CONFIG.replace("[interop]", "[interop"),
        CONFIG.replace("appendWindowsPath = false", "appendWindowsPath false"),
        CONFIG.replace("appendWindowsPath = false", "append WindowsPath = false"),
        CONFIG.replace("[interop]", "[user]\ndefault = comfy\\\n[interop]"),
        CONFIG.replace("[interop]", '[user]\ndefault = "comfy\n[interop]'),
        CONFIG.replace("[interop]", "[user]\ndefault = 'comfy\n[interop]"),
    ],
)
def test_missing_or_ambiguous_config_fails_even_without_a_handler(tmp_path, config):
    done = isolation(tmp_path, config)
    assert done.returncode == 1
    assert "[FAIL] WSL interop settings are missing, invalid or not disabled" in done.stdout


@pytest.mark.parametrize("status,handlers", [("enabled", ()), ("enabled", ("disabled",)), ("disabled", ())])
def test_valid_config_and_an_inactive_handler_report_the_observed_state(tmp_path, status, handlers):
    done = isolation(tmp_path, status=status, handlers=handlers)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "[PASS] WSL interop disabled in /etc/wsl.conf" in done.stdout
    assert "[PASS] WSL Windows-binary handler inactive" in done.stdout


@pytest.mark.parametrize(
    "status,handlers",
    [(None, ()), ("unknown", ()), ("enabled", ("unknown",)), ("disabled", ("enabled",)), ("disabled", ("unknown",))],
)
def test_unavailable_or_malformed_runtime_state_fails_closed(tmp_path, status, handlers):
    done = isolation(tmp_path, status=status, handlers=handlers)
    assert done.returncode == 1
    assert "[FAIL] WSL interop runtime isolation could not be verified" in done.stdout


def test_a_registry_that_cannot_be_enumerated_does_not_look_empty(tmp_path):
    done = isolation(tmp_path, enumeration_fails=True)
    assert done.returncode == 1
    assert "[FAIL] WSL interop runtime isolation could not be verified" in done.stdout


@pytest.mark.parametrize("previous_helper", [False, True])
def test_hardening_removes_the_early_pr_service_without_changing_binfmt(tmp_path, previous_helper):
    etc = tmp_path / "etc"
    wants = etc / "systemd/system/multi-user.target.wants"
    wants.mkdir(parents=True)
    lib = tmp_path / "lib"
    helper_dir = lib / "comfyui-sandbox"
    helper_dir.mkdir(parents=True)
    service = etc / "systemd/system/comfyui-sandbox-disable-interop.service"
    enabled_service = wants / service.name
    helper = helper_dir / "disable-interop.sh"
    registry = tmp_path / "binfmt_misc"
    registry.mkdir()
    handler = registry / "WSLInterop"
    handler.write_text("enabled\n", newline="\n")
    if previous_helper:
        for path in (service, enabled_service, helper):
            path.write_text("early PR build\n", newline="\n")
    script = script_text("harden.sh").split("touch /etc/fstab", 1)[0]
    script = script.replace("/etc/", etc.as_posix() + "/")
    script = script.replace("/usr/local/lib/", lib.as_posix() + "/")
    script = script.replace("/proc/sys/fs/binfmt_misc", registry.as_posix())
    prelude = """\
set -euo pipefail
LINUX_USER=comfy; MODELS_MODE=wsl
id(){ echo 1000; }
systemctl(){ [ "$*" = daemon-reload ]; }
"""
    done = subprocess.run(
        [bash(), "-s"],
        input=prelude + script,
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert not any(path.exists() for path in (service, enabled_service, helper))
    config = (etc / "wsl.conf").read_text()
    assert (
        "[interop]\n# Linux processes cannot start Windows programs.\nenabled = false\nappendWindowsPath = false"
        in config
    )
    assert handler.read_text() == "enabled\n"
