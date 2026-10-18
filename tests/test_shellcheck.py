"""`shellcheck` over every in-distro script in the package (spec: Testing Decisions, Bash).

The scripts have no shebang (they are streamed into `bash -s`), so the dialect is given.
shellcheck is a declared dev requirement (see CLAUDE.md, "Development requirements"); when it is
missing these tests fail with install instructions rather than skip.
"""

import subprocess

import pytest

from comfyui_sandbox.wsl import script_names, script_stdin, script_text
from toolchain import shellcheck


def test_the_package_ships_the_in_distro_scripts():
    assert {
        "create_user.sh",
        "harden.sh",
        "install_docker.sh",
        "install_nvidia_toolkit.sh",
        "prepare_data.sh",
        "deploy_comfyui.sh",
        "models_structure.sh",
        "manager_settings.sh",
        "verify.sh",
        "status.sh",
        "wait_ready.sh",
        "uninstall_report.sh",
    } <= set(script_names())


@pytest.mark.parametrize("name", script_names())
def test_shellcheck_passes(name):
    """Checked as bash runs it: with the strict-mode prelude the CLI streams in front.
    Warnings and errors fail; info and style notes (e.g. verify.sh's `test && PASS || FAIL`
    lines) are advice."""
    stdin = script_stdin(name, script_text(name), {})
    done = subprocess.run(
        [shellcheck(), "--shell=bash", "--severity=warning", "-"], input=stdin, capture_output=True, check=False
    )
    assert done.returncode == 0, (done.stdout + done.stderr).decode("utf-8", "replace")
