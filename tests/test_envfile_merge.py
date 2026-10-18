"""`envfile` merge (ticket 18): new keys and values go into an existing `.env`; the user's own
edits stay; a backup is written. Reused by `update setup` (ticket 19)."""

import base64
from types import SimpleNamespace

import pytest

import wslcalls
from comfyui_sandbox import envfile
from comfyui_sandbox.wsl import Wsl

EXISTING = """\
# my notes
PUID=1001
PGID=1002
MODELS_DIR="/home/comfy/comfyui-models"
COMFY_PORT=8188   # keep
MY_OWN_SETTING=hello world

COMFY_ARGS="--enable-manager --lowvram"
"""

PROJECT = "/home/comfy/comfyui-docker"
WHO = SimpleNamespace(distro="comfy-docker", linux_user="comfy")


def test_parse_reads_plain_quoted_and_commented_values():
    assert envfile.parse(EXISTING) == {
        "PUID": "1001",
        "PGID": "1002",
        "MODELS_DIR": "/home/comfy/comfyui-models",
        "COMFY_PORT": "8188",
        "MY_OWN_SETTING": "hello world",
        "COMFY_ARGS": "--enable-manager --lowvram",
    }


def test_merge_changes_only_the_named_keys_and_keeps_everything_else_byte_for_byte():
    merged = envfile.merge(EXISTING, {"COMFY_PORT": "9000"}, stamp="20261006-120000")

    assert merged.text == EXISTING.replace("COMFY_PORT=8188   # keep", "COMFY_PORT=9000   # keep")
    assert merged.changes == {"COMFY_PORT": ("8188", "9000")}
    assert merged.changed


def test_merge_keeps_a_quoted_value_quoted_and_the_users_own_keys_and_comments():
    merged = envfile.merge(EXISTING, {"COMFY_ARGS": "--enable-manager --lowvram --disable-pinned-memory"}, stamp="s")

    assert 'COMFY_ARGS="--enable-manager --lowvram --disable-pinned-memory"' in merged.text
    assert "MY_OWN_SETTING=hello world" in merged.text
    assert "# my notes" in merged.text


def test_merge_keeps_a_trailing_comment_an_export_prefix_and_the_quoting_style():
    text = "export COMFY_PORT=8188   # keep\nMODELS_DIR=\"/a\"  # models\nCOMFY_ARGS='--x'\n  export  PGID=1  # ids\n"

    merged = envfile.merge(
        text, {"COMFY_PORT": "9000", "MODELS_DIR": "/b", "COMFY_ARGS": "--y", "PGID": "2"}, stamp="s"
    )

    assert merged.text == (
        "export COMFY_PORT=9000   # keep\nMODELS_DIR=\"/b\"  # models\nCOMFY_ARGS='--y'\n  export  PGID=2  # ids\n"
    )


def test_merge_appends_a_new_key_and_quotes_it_when_needed():
    merged = envfile.merge(EXISTING, {"EXTRA": "a b", "SIMPLE": "x"}, stamp="s")

    assert merged.text == EXISTING + 'EXTRA="a b"\nSIMPLE=x\n'
    assert merged.changes == {"EXTRA": (None, "a b"), "SIMPLE": (None, "x")}


def test_merge_with_nothing_to_change_is_a_no_op_that_needs_no_write_or_backup():
    merged = envfile.merge(EXISTING, {"COMFY_PORT": "8188", "MODELS_DIR": "/home/comfy/comfyui-models"}, stamp="s")

    assert merged.text == EXISTING
    assert merged.changes == {}
    assert not merged.changed


def test_merge_into_a_file_without_a_final_newline():
    assert envfile.merge("A=1", {"B": "2"}, stamp="s").text == "A=1\nB=2\n"


def test_the_last_assignment_of_a_repeated_key_is_the_one_that_changes():
    assert envfile.merge("A=1\nA=2\n", {"A": "3"}, stamp="s").text == "A=1\nA=3\n"


@pytest.mark.parametrize("value", ["a\nb", 'say "hi"', "$HOME", "`id`", "back\\slash"])
def test_a_value_that_could_not_be_written_safely_is_rejected(value):
    with pytest.raises(ValueError):
        envfile.merge(EXISTING, {"COMFY_ARGS": value}, stamp="s")


def test_merge_names_the_backup_after_the_stamp():
    merged = envfile.merge(EXISTING, {"COMFY_PORT": "9000"}, stamp="20261006-120000")

    assert merged.backup == ".env.bak-20261006-120000"


def read_env(runner, text):
    runner.on(
        "wsl.exe",
        "-d",
        "comfy-docker",
        "-u",
        "comfy",
        "--cd",
        "/",
        "--exec",
        "env",
        "cat",
        f"{PROJECT}/.env",
        stdout=text,
    )


def test_apply_reads_the_env_then_writes_the_merge_and_a_backup_stamp(runner):
    read_env(runner, EXISTING)

    merged = envfile.apply(Wsl(runner, {}), WHO, {"COMFY_PORT": "9000"}, stamp="20261006-120000")

    assert merged.changes == {"COMFY_PORT": ("8188", "9000")}
    write = wslcalls.script(runner.calls, "write_env.sh")
    assert (write.distro, write.user) == ("comfy-docker", "comfy")
    assert write.env["PROJECT_DIR"] == PROJECT
    assert write.env["STAMP"] == "20261006-120000"
    assert base64.b64decode(write.env["ENV_B64"]).decode() == merged.text
    assert merged.backup == ".env.bak-20261006-120000"


def test_apply_writes_nothing_when_nothing_changes(runner):
    read_env(runner, EXISTING)

    merged = envfile.apply(Wsl(runner, {}), WHO, {"COMFY_PORT": "8188"}, stamp="s")

    assert not merged.changed
    assert wslcalls.scripts(runner.calls) == []


def test_apply_refuses_a_missing_env(runner):
    runner.on(
        "wsl.exe",
        "-d",
        "comfy-docker",
        "-u",
        "comfy",
        "--cd",
        "/",
        "--exec",
        "env",
        "cat",
        f"{PROJECT}/.env",
        returncode=1,
        stderr="No such file",
    )

    with pytest.raises(envfile.EnvMissing):
        envfile.apply(Wsl(runner, {}), WHO, {"COMFY_PORT": "9000"}, stamp="s")
