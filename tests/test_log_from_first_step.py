"""Every command that changes something opens its transcript first (docs/scripting.md, "Transcripts"),
so a refusal leaves a log to diagnose from. Tested through the CLI entry point."""

import json
from pathlib import Path

import pytest
from test_check import Machine
from test_prereqs import install_wsl
from test_verify import own

CHANGERS = [
    ("backup",),
    ("restore", "--backup", "comfyui-20260101-100000.tar", "--confirm", "comfy-docker"),
    ("config", "--port", "9100"),
    ("node", "install", "no-such-node"),
    ("rebuild",),
    ("uninstall", "--confirm", "comfy-docker"),
    ("start",),
    ("stop",),
    ("comfy", "--version"),
    ("cm-cli", "show"),
    ("exec", "--", "id"),
    ("update", "comfyui"),
    ("update", "nodes"),
    ("update", "comfy-cli"),
    ("update", "system"),
    ("update", "setup"),
    ("update", "all"),
    ("reinstall", "--confirm", "comfy-docker"),
]


def logs(home: Path) -> list[Path]:
    folder = home / ".comfyui-sandbox" / "logs"
    return sorted(folder.glob("*.log")) if folder.exists() else []


@pytest.fixture
def machine(runner, environ, home):
    Machine().script(runner, environ)
    own(home)


@pytest.mark.parametrize("argv", CHANGERS, ids=[" ".join(a[:2]) for a in CHANGERS])
def test_a_distro_that_is_not_ours_is_refused_with_a_transcript_recording_it(cli, home, machine, argv):
    result = cli("--distro", "Ubuntu", "--json", *argv)

    assert result.exit_code == 1
    [path] = logs(home)
    assert result.json["log"] == str(path)
    text = path.read_text("utf-8")
    assert "error " in text and "Ubuntu" in text
    assert text.rstrip("\n").splitlines()[-1].endswith(" exit=1")


@pytest.mark.parametrize(
    "argv", [("backup",), ("update", "comfyui"), ("reinstall",)], ids=["backup", "update", "reinstall"]
)
def test_a_missing_install_is_refused_with_a_transcript(cli, home, environ, runner, argv):
    Machine().script(runner, environ)  # no installs.json

    result = cli(*argv, "--json")

    assert result.exit_code == 1
    [path] = logs(home)
    assert "error " in path.read_text("utf-8")


def test_recreate_refused_at_the_gate_leaves_a_transcript_with_the_refusal(cli, home, machine):
    result = cli("reinstall", "--non-interactive", "--json")

    assert result.exit_code == 8
    [path] = logs(home)
    assert result.json["log"] == str(path)
    text = path.read_text("utf-8")
    assert "--confirm comfy-docker" in text
    assert text.rstrip("\n").splitlines()[-1].endswith(" exit=8")


def test_an_update_target_refused_for_several_sandboxes_leaves_a_transcript(cli, home, machine):
    path = home / ".comfyui-sandbox" / "installs.json"
    doc = json.loads(path.read_text("utf-8"))
    doc["installs"].append({**doc["installs"][0], "distro": "comfy-docker-2"})
    path.write_text(json.dumps(doc), "utf-8")

    result = cli("update", "nodes", "--non-interactive", "--json")

    assert result.exit_code == 9
    [log] = logs(home)
    assert "several sandboxes" in log.read_text("utf-8")


def test_prereqs_logs_its_wsl_version_check(cli, winapi, runner, environ, home):
    install_wsl(environ, runner, version="2.6.1.0")

    result = cli("prereqs", "--json")

    assert result.exit_code == 0
    [path] = logs(home)
    assert "run   wsl.exe --version" in path.read_text("utf-8")
