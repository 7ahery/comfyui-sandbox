"""Bare `update` says what is pending for comfyui, nodes and comfy-cli too (review-fix 09).

The checks are read-only, never start ComfyUI (a stopped container reports "unknown"), and a failed
check is "unknown" with the reason, not an error. The fake Runner plays the distro.
"""

import json

import pytest
from test_check import Machine
from test_update_sandbox import ANSWERS, ENV, PROJECT, READY, set_env
from test_verify import own

import wslcalls
from comfyui_sandbox.runner import CommandTimeout

AS = ("wsl.exe", "-d", "comfy-docker", "-u", "comfy", "--cd", "/", "--exec", "env")
PS = (*AS, "env", "--chdir", PROJECT, "docker", "compose", "ps", "--status", "running", "--quiet")
BOX = (*AS, "docker", "exec", "comfyui")
GIT = (*BOX, "git", "-C", "/workspace/ComfyUI")
ENV_NIGHTLY = ENV + 'COMFY_INSTALL_ARGS="--nvidia --version nightly"\n'
TAGS = (
    "1111111111111111111111111111111111111111\trefs/tags/v0.3.9\n"
    "2222222222222222222222222222222222222222\trefs/tags/v0.3.10\n"
    "3333333333333333333333333333333333333333\trefs/tags/v0.3.2\n"
)


@pytest.fixture(autouse=True)
def distro(runner, environ, home):
    Machine().script(runner, environ)
    own(home)
    path = home / ".comfyui-sandbox" / "installs.json"
    doc = json.loads(path.read_text("utf-8"))
    doc["installs"][0].update(deployed_by="0.0.1", answers=ANSWERS.to_dict())
    path.write_text(json.dumps(doc), "utf-8")
    set_env(runner, ENV + 'COMFY_INSTALL_ARGS="--nvidia --version latest"\n')
    runner.on_script("wait_ready.sh", stdout=READY)
    runner.on(*PS, stdout="3f2a9c1d7e44\n")


def row(cli, name):
    result = cli("update", "--json")
    assert result.exit_code == 0, result.stderr
    return {t["target"]: t for t in result.json["data"]["targets"]}[name]


# -- comfyui -----------------------------------------------------------------------


def test_comfyui_is_pending_when_a_newer_release_tag_exists(cli, runner):
    runner.on(*GIT, "describe", "--tags", "--exact-match", stdout="v0.3.9\n")
    runner.on(*GIT, "ls-remote", "--tags", "--refs", "origin", stdout=TAGS)

    found = row(cli, "comfyui")

    assert found["pending"] is True
    assert (found["current"], found["available"]) == ("v0.3.9", "v0.3.10")


def test_comfyui_is_current_on_the_newest_release_tag(cli, runner):
    runner.on(*GIT, "describe", "--tags", "--exact-match", stdout="v0.3.10\n")
    runner.on(*GIT, "ls-remote", "--tags", "--refs", "origin", stdout=TAGS)

    found = row(cli, "comfyui")

    assert found["pending"] is False
    assert (found["current"], found["available"]) == ("v0.3.10", "v0.3.10")


def test_comfyui_is_unknown_when_the_remote_cannot_be_reached(cli, runner):
    runner.on(*GIT, "describe", "--tags", "--exact-match", stdout="v0.3.9\n")
    runner.on(*GIT, "ls-remote", returncode=128, stderr="fatal: unable to access")

    found = row(cli, "comfyui")

    assert found["pending"] is None
    assert "reach" in found["detail"]


def test_comfyui_on_nightly_compares_the_head_commit_with_the_remote_master(cli, runner):
    set_env(runner, ENV_NIGHTLY)
    runner.on(*GIT, "rev-parse", "HEAD", stdout="aaaaaaa1111\n")
    runner.on(*GIT, "ls-remote", "origin", "refs/heads/master", stdout="bbbbbbb2222\trefs/heads/master\n")

    found = row(cli, "comfyui")

    assert found["pending"] is True
    assert (found["current"], found["available"]) == ("aaaaaaa", "bbbbbbb")


def test_comfyui_on_nightly_is_current_at_the_same_commit(cli, runner):
    set_env(runner, ENV_NIGHTLY)
    runner.on(*GIT, "rev-parse", "HEAD", stdout="aaaaaaa1111\n")
    runner.on(*GIT, "ls-remote", "origin", "refs/heads/master", stdout="aaaaaaa1111\trefs/heads/master\n")

    assert row(cli, "comfyui")["pending"] is False


ENV_PINNED = ENV + 'COMFY_INSTALL_ARGS="--nvidia --version 0.3.9"\n'


def test_a_pinned_comfyui_is_never_pending_and_names_the_newest_release(cli, runner):
    set_env(runner, ENV_PINNED)
    runner.on(*GIT, "describe", "--tags", "--exact-match", stdout="v0.3.9\n")
    runner.on(*GIT, "ls-remote", "--tags", "--refs", "origin", stdout=TAGS)

    found = row(cli, "comfyui")

    assert found["pending"] is False
    assert found["pinned"] == "0.3.9"
    assert "pinned to 0.3.9" in found["detail"] and "v0.3.10" in found["detail"]


def test_a_pinned_comfyui_says_so_while_stopped_without_starting_it(cli, runner):
    set_env(runner, ENV_PINNED)
    runner.on(*PS, stdout="")

    found = row(cli, "comfyui")

    assert (found["pending"], found["pinned"]) == (False, "0.3.9")
    assert wslcalls.docker_execs(runner.calls) == []


def test_an_unpinned_comfyui_has_no_pin(cli, runner):
    runner.on(*GIT, "describe", "--tags", "--exact-match", stdout="v0.3.9\n")
    runner.on(*GIT, "ls-remote", "--tags", "--refs", "origin", stdout=TAGS)

    assert row(cli, "comfyui")["pinned"] is None


# -- nodes -------------------------------------------------------------------------

NODES = (*BOX, "sh", "-c")


def test_nodes_are_pending_when_a_node_is_behind_its_remote(cli, runner):
    runner.on(*NODES, stdout="ComfyUI-Manager aaa aaa\nComfyUI-Fine bbb ccc\n")

    found = row(cli, "nodes")

    assert found["pending"] is True
    assert "ComfyUI-Fine" in found["detail"] and "ComfyUI-Manager" not in found["detail"]


def test_nodes_are_current_when_every_node_matches_its_remote(cli, runner):
    runner.on(*NODES, stdout="ComfyUI-Manager aaa aaa\nComfyUI-Fine bbb bbb\n")

    assert row(cli, "nodes")["pending"] is False


def test_nodes_are_unknown_when_no_remote_answers(cli, runner):
    runner.on(*NODES, stdout="ComfyUI-Manager aaa ?\n")

    found = row(cli, "nodes")

    assert found["pending"] is None
    assert "reach" in found["detail"]


def test_nodes_are_unknown_when_the_check_itself_fails(cli, runner):
    runner.on(*NODES, returncode=1, stderr="boom")

    assert row(cli, "nodes")["pending"] is None


# -- comfy-cli ---------------------------------------------------------------------


def pip(runner, installed, newest):
    runner.on(*BOX, "pip", "show", "comfy-cli", stdout=f"Name: comfy-cli\nVersion: {installed}\n")
    runner.on(
        *BOX,
        "pip",
        "index",
        "versions",
        "comfy-cli",
        stdout=f"comfy-cli ({newest})\nAvailable versions: {newest}, {installed}\n",
    )


def test_comfy_cli_is_pending_when_the_index_has_a_newer_version(cli, runner):
    pip(runner, "1.4.0", "1.5.3")

    found = row(cli, "comfy-cli")

    assert found["pending"] is True
    assert (found["current"], found["available"]) == ("1.4.0", "1.5.3")


def test_comfy_cli_is_current_at_the_newest_version(cli, runner):
    pip(runner, "1.5.3", "1.5.3")

    assert row(cli, "comfy-cli")["pending"] is False


def test_comfy_cli_is_unknown_when_the_index_cannot_be_reached(cli, runner):
    runner.on(*BOX, "pip", "show", "comfy-cli", stdout="Version: 1.4.0\n")
    runner.on(*BOX, "pip", "index", returncode=1, stderr="no network")

    found = row(cli, "comfy-cli")

    assert found["pending"] is None
    assert "reach" in found["detail"]


# -- all three ---------------------------------------------------------------------


@pytest.mark.parametrize("name", ["comfyui", "nodes", "comfy-cli"])
def test_a_stopped_container_is_unknown_and_is_not_started(cli, runner, name):
    runner.on(*PS, stdout="")

    found = row(cli, name)

    assert found["pending"] is None
    assert "stopped" in found["detail"]
    assert wslcalls.docker_execs(runner.calls) == []
    assert [c.argv[-1] for c in wslcalls.commands(runner.calls) if c.argv[3:5] == ("docker", "compose")] == [
        "--quiet"
    ] * 3
    assert wslcalls.scripts(runner.calls) == []


def test_the_human_listing_still_names_every_target_when_checks_fail(cli, runner):
    runner.on(*BOX, returncode=1, stderr="boom")

    result = cli("update")

    assert result.exit_code == 0, result.stderr
    for name in ("comfyui", "nodes", "comfy-cli", "unknown"):
        assert name in result.stdout


def test_comfyui_is_unknown_when_the_env_file_is_missing(cli, runner):
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

    found = row(cli, "comfyui")

    assert found["pending"] is None
    assert ".env" in found["detail"]


def test_the_listing_finishes_and_names_the_checks_that_hung(cli, runner):
    def hang():
        raise CommandTimeout("Timed out after 60s: wsl.exe")

    runner.on(*PS, then=hang)
    runner.on(*AS, "apt", "list", "--upgradable", then=hang)

    result = cli("update", "--json")

    assert result.exit_code == 0, result.stderr
    rows = {t["target"]: t for t in result.json["data"]["targets"]}
    for name in ("comfyui", "nodes", "system"):
        assert rows[name]["pending"] is None
        assert "timed out" in rows[name]["detail"].lower()


# -- the named result ----------------------------------------------------------------


def test_a_pending_check_returns_a_named_result_read_by_field():
    from types import SimpleNamespace

    from comfyui_sandbox.commands import update_sandbox
    from comfyui_sandbox.commands.update_support import Pending, unknown

    result = update_sandbox.pending(None, SimpleNamespace(deployed_by="0.0.1"))

    assert isinstance(result, Pending)
    assert result.pending is True
    assert result.current == "0.0.1" and result.available
    assert "0.0.1" in result.detail

    nothing = unknown("no route")
    assert (nothing.pending, nothing.detail, nothing.current, nothing.available) == (
        None,
        "unknown: no route",
        None,
        None,
    )
