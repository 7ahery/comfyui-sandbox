"""`comfyui-sandbox update comfyui|nodes|comfy-cli|system|all` and the bare `update` listing (ticket 20).

The fake Runner plays the distro; nothing touches a real one.
"""

import base64
import json
from importlib.metadata import version

import pytest
from test_check import Machine
from test_update_sandbox import ANSWERS, ENV, PROJECT, READY, record, set_env
from test_verify import own

import wslcalls
from comfyui_sandbox import envfile
from comfyui_sandbox.commands import update_comfyui

AS = ("wsl.exe", "-d", "comfy-docker", "-u")
RESTART = ("env", "--chdir", PROJECT, "docker", "compose", "restart")
ENV_NIGHTLY = ENV + 'COMFY_INSTALL_ARGS="--nvidia --version nightly"\n'
ENV_LATEST = ENV + 'COMFY_INSTALL_ARGS="--nvidia --version latest"\n'
IN_CONTAINER = (*AS, "comfy", "--cd", "/", "--exec", "env", "docker", "exec", "comfyui", "comfy")


@pytest.fixture(autouse=True)
def distro(runner, environ, home):
    Machine().script(runner, environ)
    own(home)
    path = home / ".comfyui-sandbox" / "installs.json"
    doc = json.loads(path.read_text("utf-8"))
    doc["installs"][0].update(deployed_by="0.0.1", answers=ANSWERS.to_dict())
    path.write_text(json.dumps(doc), "utf-8")
    set_env(runner, ENV_LATEST)
    runner.on_script("wait_ready.sh", stdout=READY)


def execs(runner):
    return [run.command for run in wslcalls.docker_execs(runner.calls)]


def commands(runner):
    return [run.argv for run in wslcalls.commands(runner.calls)]


def restarted(runner):
    return [c for c in wslcalls.commands(runner.calls) if c.argv == RESTART]


def root_commands(runner):
    return [r.argv for r in wslcalls.commands(runner.calls) if r.user == "root" and r.argv[0] != "sleep"]


# -- comfyui -------------------------------------------------------------------


def test_update_comfyui_moves_to_the_latest_release_then_restarts_and_waits(cli, runner):
    result = cli("update", "comfyui", "--json")

    assert result.exit_code == 0, result.stderr
    [run] = wslcalls.docker_execs(runner.calls)
    assert (run.user, run.container) == ("comfy", "comfyui")
    assert run.command == ("comfy", "--skip-prompt", "update", "comfy", "--version", "latest")
    [restart] = restarted(runner)
    wait = wslcalls.script(runner.calls, "wait_ready.sh")
    assert (
        wslcalls.index(runner.calls, run.call)
        < wslcalls.index(runner.calls, restart.call)
        < wslcalls.index(runner.calls, wait.call)
    )
    assert result.json["data"]["version"] == "latest"
    assert result.json["log"]


def test_update_comfyui_nightly_switches_the_channel_here_and_in_the_env(cli, runner):
    result = cli("update", "comfyui", "--version", "nightly", "--json")

    assert result.exit_code == 0, result.stderr
    assert execs(runner) == [("comfy", "--skip-prompt", "update", "comfy", "--version", "nightly")]
    write = wslcalls.script(runner.calls, "write_env.sh")
    assert write.user == "comfy"
    assert result.json["data"]["version"] == "nightly"
    assert result.json["data"]["env_changed"] is True
    new = envfile.parse(base64.b64decode(write.env["ENV_B64"]).decode())
    assert new["COMFY_INSTALL_ARGS"] == "--nvidia --version nightly"
    assert new["COMFY_PORT"] == "9000" and new["MY_OWN"] == "keep me"


def test_update_comfyui_latest_leaves_a_matching_env_alone(cli, runner):
    assert cli("update", "comfyui", "--version", "latest").exit_code == 0

    assert not [r for r in wslcalls.scripts(runner.calls) if r.script == "write_env.sh"]


def test_update_comfyui_without_a_version_follows_a_nightly_install(cli, runner):
    set_env(runner, ENV_NIGHTLY)

    result = cli("update", "comfyui", "--json")

    assert result.exit_code == 0, result.stderr
    assert execs(runner) == [("comfy", "--skip-prompt", "update", "comfy", "--version", "nightly")]
    assert result.json["data"]["version"] == "nightly"
    assert result.json["data"]["env_changed"] is False
    assert not [r for r in wslcalls.scripts(runner.calls) if r.script == "write_env.sh"]


def test_update_comfyui_without_a_version_is_stable_when_the_install_has_no_channel(cli, runner):
    set_env(runner, ENV)

    assert cli("update", "comfyui").exit_code == 0

    assert execs(runner) == [("comfy", "--skip-prompt", "update", "comfy", "--version", "latest")]


def test_update_comfyui_without_a_version_leaves_an_env_without_a_version_flag_untouched(cli, runner):
    set_env(runner, ENV + 'COMFY_INSTALL_ARGS="--nvidia"\n')

    result = cli("update", "comfyui", "--json")

    assert result.exit_code == 0, result.stderr
    assert result.json["data"]["env_changed"] is False
    assert not [r for r in wslcalls.scripts(runner.calls) if r.script == "write_env.sh"]


def test_update_comfyui_with_an_explicit_version_adds_the_flag_to_the_env(cli, runner):
    set_env(runner, ENV + 'COMFY_INSTALL_ARGS="--nvidia"\n')

    result = cli("update", "comfyui", "--version", "latest", "--json")

    assert result.exit_code == 0, result.stderr
    assert result.json["data"]["env_changed"] is True
    assert [r for r in wslcalls.scripts(runner.calls) if r.script == "write_env.sh"]


def test_an_explicit_version_latest_switches_a_nightly_install_and_says_so(cli, runner):
    set_env(runner, ENV_NIGHTLY)

    result = cli("update", "comfyui", "--version", "latest", "--json")

    assert result.exit_code == 0, result.stderr
    assert execs(runner) == [("comfy", "--skip-prompt", "update", "comfy", "--version", "latest")]
    write = wslcalls.script(runner.calls, "write_env.sh")
    assert (
        envfile.parse(base64.b64decode(write.env["ENV_B64"]).decode())["COMFY_INSTALL_ARGS"]
        == "--nvidia --version latest"
    )
    assert "nightly" in result.stderr and "latest" in result.stderr and "switch" in result.stderr.lower()


def test_an_explicit_version_nightly_switches_a_stable_install_and_says_so(cli, runner):
    result = cli("update", "comfyui", "--version", "nightly", "--json")

    assert result.exit_code == 0, result.stderr
    assert "switch" in result.stderr.lower()


def test_update_comfyui_in_the_same_channel_does_not_announce_a_switch(cli, runner):
    result = cli("update", "comfyui", "--version", "latest", "--json")

    assert "switch" not in result.stderr.lower()


ENV_PINNED = ENV + 'COMFY_INSTALL_ARGS="--nvidia --version 0.3.40"\n'


def _recorded_channel(home):
    doc = json.loads((home / ".comfyui-sandbox" / "installs.json").read_text("utf-8"))
    return doc["installs"][0]["answers"].get("channel")


def test_update_comfyui_to_a_release_number_pins_it_here_in_the_env_and_in_the_record(cli, runner, home):
    result = cli("update", "comfyui", "--version", "v0.3.40", "--json")

    assert result.exit_code == 0, result.stderr
    assert execs(runner) == [("comfy", "--skip-prompt", "update", "comfy", "--version", "0.3.40")]
    write = wslcalls.script(runner.calls, "write_env.sh")
    assert (
        envfile.parse(base64.b64decode(write.env["ENV_B64"]).decode())["COMFY_INSTALL_ARGS"]
        == "--nvidia --version 0.3.40"
    )
    assert result.json["data"]["version"] == "0.3.40"
    assert _recorded_channel(home) == "0.3.40"
    assert "switch" in result.stderr.lower()


def test_update_comfyui_from_a_pin_back_to_latest_unpins_it_in_the_record(cli, runner, home):
    set_env(runner, ENV_PINNED)
    path = home / ".comfyui-sandbox" / "installs.json"
    doc = json.loads(path.read_text("utf-8"))
    doc["installs"][0]["answers"]["channel"] = "0.3.40"
    path.write_text(json.dumps(doc), "utf-8")

    result = cli("update", "comfyui", "--version", "latest", "--json")

    assert result.exit_code == 0, result.stderr
    assert execs(runner) == [("comfy", "--skip-prompt", "update", "comfy", "--version", "latest")]
    assert _recorded_channel(home) == "latest"


def test_update_comfyui_to_a_release_says_it_is_pinned_there(cli, runner):
    _versions(runner, "v0.3.62\n", "v0.3.40\n")

    result = cli("update", "comfyui", "--version", "0.3.40", "--json")

    assert result.exit_code == 0, result.stderr
    assert (result.json["data"]["previous"], result.json["data"]["installed"]) == ("v0.3.62", "v0.3.40")
    assert "from v0.3.62 to v0.3.40" in result.json["message"]
    assert "pinned" in result.json["message"]


def test_update_comfyui_on_a_pinned_sandbox_leaves_it_alone_and_does_not_stop_comfyui(cli, runner):
    set_env(runner, ENV_PINNED)

    result = cli("update", "comfyui", "--json")

    assert result.exit_code == 0, result.stderr
    assert execs(runner) == []
    assert not [c for c in wslcalls.commands(runner.calls) if "stop" in c.argv]
    assert result.json["data"]["pinned"] == "0.3.40"
    assert result.json["data"]["left_pinned"] is True
    assert "pinned to 0.3.40" in result.json["message"]
    assert "--version latest" in result.json["message"]


def test_update_all_on_a_pinned_sandbox_leaves_comfyui_on_its_release(cli, runner):
    set_env(runner, ENV_PINNED)

    result = cli("update", "all", "--json")

    assert result.exit_code == 0, result.stderr
    assert execs(runner)[0] == ("comfy", "--skip-prompt", "node", "update", "all")
    assert result.json["data"]["targets"]["comfyui"]["pinned"] == "0.3.40"


@pytest.mark.parametrize(
    "args, channel, pin",
    [
        ("--nvidia --version 0.3.40", "0.3.40", "0.3.40"),
        ("--nvidia --version v0.3.40", "0.3.40", "0.3.40"),  # written by hand, with the tag's v
        ("--nvidia --version nightly", "nightly", None),
        ("--nvidia --version latest", "latest", None),
        ("--nvidia --version foo", "latest", None),  # junk is stable, never a pin
        ("--nvidia", "latest", None),
        ("", "latest", None),
    ],
)
def test_the_channel_is_read_from_the_install_args(args, channel, pin):
    assert update_comfyui.current_channel(args) == channel
    assert update_comfyui.pinned_release(channel) == pin


def test_switching_to_the_release_it_is_pinned_to_runs_the_switch_and_leaves_the_env_alone(cli, runner):
    set_env(runner, ENV_PINNED)

    result = cli("update", "comfyui", "--version", "0.3.40", "--json")

    assert result.exit_code == 0, result.stderr
    assert execs(runner) == [("comfy", "--skip-prompt", "update", "comfy", "--version", "0.3.40")]
    assert result.json["data"]["env_changed"] is False
    assert not [r for r in wslcalls.scripts(runner.calls) if r.script == "write_env.sh"]
    assert "switching" not in result.stderr.lower()


def test_a_mistyped_release_number_says_what_a_version_looks_like_and_changes_nothing(cli, runner):
    result = cli("update", "comfyui", "--version", "0.3.x", "--json")

    assert result.exit_code == 2
    assert "latest, nightly or a release number such as 0.3.40, not '0.3.x'" in result.json["error"]
    assert wslcalls.docker_execs(runner.calls) == []
    assert not [c for c in wslcalls.commands(runner.calls) if "stop" in c.argv]


def test_update_comfyui_rejects_an_unknown_version(cli, runner):
    result = cli("update", "comfyui", "--version", "v9", "--json")

    assert result.exit_code == 2
    assert wslcalls.docker_execs(runner.calls) == []


def test_a_failed_comfy_update_exits_1_and_does_not_restart(cli, runner):
    runner.on(*IN_CONTAINER, returncode=1, stderr="network down")

    result = cli("update", "comfyui", "--json")

    assert result.exit_code == 1
    assert restarted(runner) == []
    assert result.json["log"]


def test_the_distro_is_held_up_while_comfyui_updates(cli, runner):
    assert cli("update", "comfyui").exit_code == 0

    [keepalive] = runner.started
    assert keepalive.stopped_after is not None


# -- nodes and comfy-cli ---------------------------------------------------------


def test_update_nodes_updates_all_custom_nodes_through_the_container_then_restarts(cli, runner):
    result = cli("update", "nodes", "--json")

    assert result.exit_code == 0, result.stderr
    assert execs(runner) == [("comfy", "--skip-prompt", "node", "update", "all")]
    assert len(restarted(runner)) == 1
    assert wslcalls.script(runner.calls, "wait_ready.sh")


def test_update_comfy_cli_updates_the_tooling_through_the_container_then_restarts(cli, runner):
    result = cli("update", "comfy-cli", "--json")

    assert result.exit_code == 0, result.stderr
    assert execs(runner) == [("comfy", "--skip-prompt", "update", "cli")]
    assert len(restarted(runner)) == 1


def test_a_failed_node_update_exits_1_with_the_reason_and_does_not_restart(cli, runner):
    runner.on(*IN_CONTAINER, returncode=3, stderr="boom")

    result = cli("update", "nodes", "--json")

    assert result.exit_code == 1
    assert "boom" in result.json["message"]
    assert restarted(runner) == []


# -- system ----------------------------------------------------------------------


def test_update_system_upgrades_docker_and_the_nvidia_toolkit_as_root_then_restarts_docker(cli, runner):
    result = cli("update", "system", "--json")

    assert result.exit_code == 0, result.stderr
    ran = root_commands(runner)
    assert ran[0] == ("apt-get", "update")
    [upgrade] = [a for a in ran if "--only-upgrade" in a]
    assert {
        "docker-ce",
        "docker-ce-cli",
        "containerd.io",
        "docker-buildx-plugin",
        "docker-compose-plugin",
        "nvidia-container-toolkit",
    } <= set(upgrade)
    assert ran[-1] == ("systemctl", "restart", "docker")
    assert result.json["data"]["nvidia_toolkit"] is True


def test_update_system_skips_the_nvidia_toolkit_on_cpu(cli, runner, home):
    path = home / ".comfyui-sandbox" / "installs.json"
    doc = json.loads(path.read_text("utf-8"))
    doc["installs"][0]["gpu"] = {"mode": "cpu"}
    path.write_text(json.dumps(doc), "utf-8")

    result = cli("update", "system", "--json")

    assert result.exit_code == 0, result.stderr
    [upgrade] = [a for a in root_commands(runner) if "--only-upgrade" in a]
    assert "docker-ce" in upgrade and "nvidia-container-toolkit" not in upgrade
    assert result.json["data"]["nvidia_toolkit"] is False


def test_update_system_leaves_comfyui_stopped_and_says_how_to_start_it(cli, runner):
    result = cli("update", "system", "--json")

    assert "comfyui-sandbox start" in result.json["message"]
    assert not [r for r in wslcalls.scripts(runner.calls) if r.script == "wait_ready.sh"]


def test_a_failed_apt_upgrade_exits_1_and_docker_is_not_restarted(cli, runner):
    runner.on(
        *AS,
        "root",
        "--cd",
        "/",
        "--exec",
        "env",
        "DEBIAN_FRONTEND=noninteractive",
        "apt-get",
        "-y",
        returncode=100,
        stderr="E: Unable to locate package",
    )

    result = cli("update", "system", "--json")

    assert result.exit_code == 1
    assert ("systemctl", "restart", "docker") not in root_commands(runner)


# -- all -------------------------------------------------------------------------


def test_update_all_runs_every_target_in_order_ending_with_the_setup(cli, runner, home):
    result = cli("update", "all", "--json")

    assert result.exit_code == 0, result.stderr
    assert execs(runner) == [
        ("comfy", "--skip-prompt", "update", "comfy", "--version", "latest"),
        ("comfy", "--skip-prompt", "node", "update", "all"),
        ("comfy", "--skip-prompt", "update", "cli"),
    ]
    calls = runner.calls
    order = [
        wslcalls.index(calls, call)
        for call in (
            wslcalls.docker_execs(calls)[0].call,
            next(c for c in wslcalls.commands(calls) if c.argv == ("systemctl", "restart", "docker")).call,
            wslcalls.script(calls, "deploy_comfyui.sh").call,
        )
    ]
    assert order == sorted(order)
    assert list(result.json["data"]["targets"]) == ["comfyui", "nodes", "comfy-cli", "system", "setup"]
    assert record(home)["deployed_by"] == version("comfyui-sandbox")
    assert result.json["log"]


def test_update_all_on_a_nightly_install_stays_on_nightly_and_leaves_the_env_args(cli, runner):
    set_env(runner, ENV_NIGHTLY)

    result = cli("update", "all", "--json")

    assert result.exit_code == 0, result.stderr
    assert execs(runner)[0] == ("comfy", "--skip-prompt", "update", "comfy", "--version", "nightly")
    assert not [
        r
        for r in wslcalls.scripts(runner.calls)
        if r.script == "write_env.sh" and "COMFY_INSTALL_ARGS" in base64.b64decode(r.env["ENV_B64"]).decode()
    ]


def test_update_all_on_a_stable_install_updates_to_latest(cli, runner):
    result = cli("update", "all", "--json")

    assert result.exit_code == 0, result.stderr
    assert execs(runner)[0] == ("comfy", "--skip-prompt", "update", "comfy", "--version", "latest")


def test_update_all_stops_at_the_first_failure_and_names_the_target(cli, runner, home):
    runner.on(*IN_CONTAINER, "--skip-prompt", "node", returncode=1, stderr="node boom")

    result = cli("update", "all", "--json")

    assert result.exit_code == 1
    assert "nodes" in result.json["message"]
    assert result.json["log"]
    assert ("comfy", "--skip-prompt", "update", "cli") not in execs(runner)
    assert not [r for r in wslcalls.scripts(runner.calls) if r.script == "deploy_comfyui.sh"]
    assert record(home)["deployed_by"] == "0.0.1"


# -- bare update -------------------------------------------------------------------


def test_bare_update_lists_every_target_and_changes_nothing(cli, runner):
    runner.on(
        *AS,
        "comfy",
        "--cd",
        "/",
        "--exec",
        "env",
        "apt",
        "list",
        "--upgradable",
        stdout="Listing...\ndocker-ce/jammy 5:27 amd64 [upgradable from: 5:26]\n"
        "vim/jammy 1 amd64 [upgradable from: 0]\n",
    )

    result = cli("update", "--json")

    assert result.exit_code == 0, result.stderr
    targets = {t["target"]: t for t in result.json["data"]["targets"]}
    assert list(targets) == ["comfyui", "nodes", "comfy-cli", "system", "setup"]
    assert targets["setup"]["pending"] is True
    assert version("comfyui-sandbox") in targets["setup"]["detail"]
    assert targets["system"]["pending"] is True
    assert "docker-ce" in targets["system"]["detail"] and "vim" not in targets["system"]["detail"]
    assert all(isinstance(t["detail"], str) and t["detail"] for t in targets.values())
    assert wslcalls.scripts(runner.calls) == []
    assert execs(runner) == []
    assert not [
        c
        for c in commands(runner)
        if (c[:1] in (("docker",), ("systemctl",)) and c[-1] != "--quiet") or c[:2] == ("apt-get", "update")
    ]  # the only docker call is the read-only `compose ps`


def test_bare_update_says_the_setup_is_current_when_it_is(cli, home):
    path = home / ".comfyui-sandbox" / "installs.json"
    doc = json.loads(path.read_text("utf-8"))
    doc["installs"][0]["deployed_by"] = version("comfyui-sandbox")
    path.write_text(json.dumps(doc), "utf-8")

    targets = {t["target"]: t for t in cli("update", "--json").json["data"]["targets"]}

    assert targets["setup"]["pending"] is False


def test_bare_update_human_output_names_each_target(cli):
    result = cli("update")

    assert result.exit_code == 0, result.stderr
    for name in ("comfyui", "nodes", "comfy-cli", "system", "setup", "all"):
        assert name in result.stdout


def test_the_update_group_lists_every_target_in_help(cli):
    out = cli("update", "--help").stdout
    for name in ("comfyui", "nodes", "comfy-cli", "system", "setup", "all"):
        assert name in out


def _versions(runner, *seen):
    """The container's `git describe` answers in turn: the version before the update, then after it."""
    answers = iter(seen)
    runner.on_match(lambda argv: argv[-3:] == ("describe", "--tags", "--exact-match"), lambda argv: (0, next(answers)))


def test_update_comfyui_says_which_version_it_moved_from_and_to(cli, runner):
    _versions(runner, "v0.3.9\n", "v0.3.10\n")

    result = cli("update", "comfyui", "--json")

    assert result.exit_code == 0, result.stderr
    assert result.json["data"]["previous"] == "v0.3.9"
    assert result.json["data"]["installed"] == "v0.3.10"
    assert "ComfyUI updated from v0.3.9 to v0.3.10 (latest channel)." in result.json["message"]


def test_update_comfyui_on_the_newest_release_says_nothing_changed(cli, runner):
    _versions(runner, "v0.3.10\n", "v0.3.10\n")

    result = cli("update", "comfyui", "--json")

    assert result.exit_code == 0, result.stderr
    assert "already on v0.3.10, the newest release, nothing changed" in result.json["message"]


def test_update_comfyui_whose_version_cannot_be_read_still_succeeds(cli, runner):
    result = cli("update", "comfyui", "--json")

    assert result.exit_code == 0, result.stderr
    assert result.json["data"]["installed"] is None
    assert "its version could not be read" in result.json["message"]


def test_update_comfyui_on_nightly_says_which_commit_it_moved_from_and_to(cli, runner):
    set_env(runner, ENV_NIGHTLY)
    commits = iter(["aaaaaaa\n", "bbbbbbb\n"])
    runner.on_match(lambda argv: argv[-3:] == ("rev-parse", "--short=7", "HEAD"), lambda argv: (0, next(commits)))

    result = cli("update", "comfyui", "--json")

    assert result.exit_code == 0, result.stderr
    assert (result.json["data"]["previous"], result.json["data"]["installed"]) == ("aaaaaaa", "bbbbbbb")
    assert "updated from aaaaaaa to bbbbbbb (nightly channel)" in result.json["message"]


def test_update_comfyui_on_nightly_that_is_current_says_the_newest_commit(cli, runner):
    set_env(runner, ENV_NIGHTLY)
    runner.on_match(lambda argv: argv[-3:] == ("rev-parse", "--short=7", "HEAD"), lambda argv: (0, "aaaaaaa\n"))

    result = cli("update", "comfyui", "--json")

    assert "already on aaaaaaa, the newest commit, nothing changed" in result.json["message"]


def test_update_comfyui_on_a_checkout_without_an_exact_tag_reports_its_commit(cli, runner):
    runner.on_match(lambda argv: argv[-3:] == ("describe", "--tags", "--exact-match"), lambda argv: (128, ""))
    runner.on_match(lambda argv: argv[-3:] == ("rev-parse", "--short=7", "HEAD"), lambda argv: (0, "ccccccc\n"))

    result = cli("update", "comfyui", "--json")

    assert result.exit_code == 0, result.stderr
    assert result.json["data"]["installed"] == "ccccccc"


def test_update_all_reports_comfyuis_versions_among_its_targets(cli, runner):
    _versions(runner, "v0.3.9\n", "v0.3.10\n")

    result = cli("update", "all", "--json")

    assert result.exit_code == 0, result.stderr
    comfyui = result.json["data"]["targets"]["comfyui"]
    assert (comfyui["previous"], comfyui["installed"]) == ("v0.3.9", "v0.3.10")
