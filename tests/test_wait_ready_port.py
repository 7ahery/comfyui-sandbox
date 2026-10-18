"""wait_ready.sh reads COMFY_PORT the way the `.env` parser does (last assignment wins, quotes
stripped, `export ` allowed), so a hand-edited `.env` still reaches the right readiness probe."""

import subprocess
from pathlib import Path

import pytest

import comfyui_sandbox
from toolchain import bash

SCRIPT = Path(comfyui_sandbox.__file__).parent / "distro" / "wait_ready.sh"


def probed_url(tmp_path: Path, env_text: str | None) -> str:
    """Run the script against fake docker/curl; return the URL curl was asked for."""
    project = tmp_path / "project"
    project.mkdir()
    if env_text is not None:
        (project / ".env").write_text(env_text, newline="\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    seen = tmp_path / "curl-url"
    (bin_dir / "docker").write_text('#!/bin/sh\n[ "$1" = inspect ] && echo running\nexit 0\n', newline="\n")
    (bin_dir / "curl").write_text(
        f'#!/bin/sh\nfor a; do case "$a" in http*) echo "$a" > "{seen.as_posix()}";; esac; done\necho 200\n',
        newline="\n",
    )
    env = {
        "PATH": f"{bin_dir.as_posix()}:/usr/bin:/bin",
        "PROJECT_DIR": project.as_posix(),
        "TIMEOUT_MIN": "0",
        "INTERVAL": "60",
        "TAIL": "1",
    }
    done = subprocess.run([bash(), str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    return seen.read_text().strip()


@pytest.mark.parametrize(
    "env_text",
    [
        'COMFY_PORT="9000"\n',
        "COMFY_PORT='9000'\n",
        "export COMFY_PORT=9000\n",
        "COMFY_PORT=8188\nCOMFY_PORT=9000\n",
        "COMFY_PORT=9000   # my port\n",
        'export COMFY_PORT="9000" # note\r\n',
    ],
)
def test_the_readiness_probe_uses_the_port_the_env_parser_reads(tmp_path, env_text):
    assert probed_url(tmp_path, env_text) == "http://127.0.0.1:9000/"


def test_a_missing_key_probes_8188(tmp_path):
    assert probed_url(tmp_path, "PUID=1000\n") == "http://127.0.0.1:8188/"
