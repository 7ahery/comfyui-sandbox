"""`comfyui-sandbox backup` / `restore` (ticket 22), through the CLI entry point.

The fake Runner plays wsl.exe and the commands inside the distro; no real distro is touched.
`backup` starts ComfyUI when it is stopped and leaves it as it found it (review-fix 2, ticket 09);
`restore` ends with ComfyUI stopped (review-fix 08).
"""

import re

import pytest
from test_check import Machine
from test_verify import own

import wslcalls
from comfyui_sandbox.instance import instance_name
from fakes import CANCEL, FakeContainer

AS_USER = ("wsl.exe", "-d", "comfy-docker", "-u", "comfy", "--cd", "/", "--exec", "env")
BACKUPS = "/home/comfy/comfyui-backups"
PROJECT = "/home/comfy/comfyui-docker"
SNAPSHOT = "/workspace/ComfyUI/user/node-snapshot.json"
NAME = re.compile(r"^comfyui-\d{8}-\d{6}\.tar$")
COMPOSE = ("env", "--chdir", PROJECT, "docker", "compose")
PS = (*AS_USER, *COMPOSE, "ps", "--status", "running", "--quiet")


@pytest.fixture(autouse=True)
def ready(runner, environ, home):
    Machine().script(runner, environ)
    own(home)


def stopped(runner):
    runner.on(*PS, stdout="")


def running(runner):
    runner.on(*PS, stdout="3f2a9c1d7e44\n")


def compose(runner, verb):
    return [c for c in wslcalls.commands(runner.calls) if wslcalls.compose_verb(c.argv) == verb]


def commands(runner):
    return [run.argv for run in wslcalls.commands(runner.calls)]


def test_backup_saves_a_snapshot_then_tars_user_input_and_output_and_returns_the_name(cli, runner):
    running(runner)
    result = cli("backup", "--json")

    assert result.exit_code == 0, result.stderr
    name = result.json["data"]["name"]
    assert NAME.match(name)
    assert result.json["data"]["path"] == f"{BACKUPS}/{name}"
    assert "log" in result.json
    [snapshot] = wslcalls.docker_execs(runner.calls)
    assert snapshot.user == "comfy"
    assert snapshot.command == ("comfy", "--skip-prompt", "node", "save-snapshot", "--output", SNAPSHOT)
    calls = commands(runner)
    tar = (
        "tar",
        "-C",
        f"{PROJECT}/data",
        "-cf",
        f"{BACKUPS}/{name}",
        "ComfyUI/user",
        "ComfyUI/input",
        "ComfyUI/output",
    )
    assert tar in calls
    assert ("mkdir", "-p", BACKUPS) in calls
    assert calls.index(tar) > calls.index(("mkdir", "-p", BACKUPS))
    assert wslcalls.index(runner.calls, snapshot.call) < wslcalls.index(
        runner.calls, next(run.call for run in wslcalls.commands(runner.calls) if run.argv == tar)
    )


def test_backup_says_the_command_that_restores_it_and_that_command_restores_it(cli, runner):
    """feat-tui-integration 12: home's Back up shows this command, to be run from the CLI."""
    running(runner)
    backed_up = cli("backup", "--json")
    name = backed_up.json["data"]["name"]
    restore = backed_up.json["data"]["restore"]
    assert f"'{restore}'" in backed_up.json["message"]
    prog, *argv = restore.split()
    assert prog == "comfyui-sandbox"

    list_backups(runner, "comfyui-20200101-000000.tar", name, "comfyui-29990101-000000.tar")  # not the newest
    restored = cli(*argv, "--confirm", "comfy-docker")

    assert restored.exit_code == 0, restored.stderr
    assert ("tar", "-C", f"{PROJECT}/data", "-xf", f"{BACKUPS}/{name}") in commands(runner)


def test_backup_fails_and_makes_no_tar_when_the_snapshot_cannot_be_saved(cli, runner):
    running(runner)
    runner.on(*AS_USER, "docker", "exec", returncode=1, stderr="Error: No such container: comfyui")

    result = cli("backup", "--json")

    assert result.exit_code == 1
    assert "No such container" in result.json["error"]
    assert not any(argv[:1] == ("tar",) for argv in commands(runner))
    assert "log" in result.json


def list_backups(runner, *names):
    runner.on(*AS_USER, "ls", "-1", BACKUPS, stdout="\n".join(names) + "\n")


def test_a_confirmed_restore_extracts_the_newest_backup_and_reinstalls_the_snapshot_nodes(cli, runner):
    container = FakeContainer(runner, running=True)
    list_backups(runner, "comfyui-20260101-100000.tar", "comfyui-20260301-090000.tar", "notes.txt")

    result = cli("restore", "--confirm", "comfy-docker", "--yes", "--json")  # --yes: the newest, unasked

    assert result.exit_code == 0, result.stderr
    assert result.json["data"]["name"] == "comfyui-20260301-090000.tar"
    assert "log" in result.json
    untar = ("tar", "-C", f"{PROJECT}/data", "-xf", f"{BACKUPS}/comfyui-20260301-090000.tar")
    [restore_snapshot] = wslcalls.docker_execs(runner.calls)
    assert restore_snapshot.command == ("comfy", "--skip-prompt", "node", "restore-snapshot", SNAPSHOT)
    steps = [
        wslcalls.compose_verb(run.argv) or run.argv[0]
        for run in wslcalls.commands(runner.calls)
        if wslcalls.compose_verb(run.argv) not in (None, "ps")
        or run.argv == untar
        or run.argv[:2] == ("docker", "exec")
    ]
    # stopped first, the files replaced while it is stopped, started for the snapshot, restarted, stopped
    assert steps == ["stop", "tar", "up", "docker", "restart", "stop"]
    assert container.running is False
    waits = [run for run in wslcalls.scripts(runner.calls) if run.script == "wait_ready.sh"]
    assert len(waits) == 2  # after the start (the snapshot needs a running container), and after the restart


def test_restore_can_name_a_backup(cli, runner):
    list_backups(runner, "comfyui-20260101-100000.tar", "comfyui-20260301-090000.tar")

    result = cli("restore", "--backup", "comfyui-20260101-100000.tar", "--confirm", "comfy-docker")

    assert result.exit_code == 0, result.stderr
    assert ("tar", "-C", f"{PROJECT}/data", "-xf", f"{BACKUPS}/comfyui-20260101-100000.tar") in commands(runner)


def test_restore_fails_before_the_gate_when_there_is_no_such_backup(cli, runner):
    list_backups(runner, "comfyui-20260101-100000.tar")

    result = cli("restore", "--backup", "comfyui-nope.tar", "--confirm", "comfy-docker", "--json")

    assert result.exit_code == 1
    assert "comfyui-nope.tar" in result.json["error"]
    assert not any(argv[:1] == ("tar",) for argv in commands(runner))


@pytest.mark.parametrize("flags", [(), ("--yes",), ("--confirm", "other-distro"), ("--yes", "--confirm", "other")])
def test_restore_without_confirmation_exits_8_and_makes_no_destructive_call(cli, runner, flags):
    list_backups(runner, "comfyui-20260101-100000.tar")

    result = cli("restore", "--non-interactive", "--json", *flags)

    assert result.exit_code == 8
    assert "comfy-docker" in result.json["error"]
    calls = commands(runner)
    assert not any(argv[:1] == ("tar",) for argv in calls)
    assert not any(wslcalls.compose_verb(argv) for argv in calls)
    assert wslcalls.docker_execs(runner.calls) == []


def test_restore_fails_when_the_container_does_not_come_back(cli, runner):
    running(runner)
    list_backups(runner, "comfyui-20260101-100000.tar")
    runner.on_script("wait_ready.sh", returncode=1, stdout="[error] the container is exited\nlog line")

    result = cli("restore", "--backup", "comfyui-20260101-100000.tar", "--confirm", "comfy-docker", "--json")

    assert result.exit_code == 1
    assert wslcalls.docker_execs(runner.calls) == []


def test_backup_on_a_stopped_install_starts_comfyui_for_the_snapshot_and_stops_it_again(cli, runner):
    stopped(runner)

    result = cli("backup", "--json")

    assert result.exit_code == 0, result.stderr
    [up] = compose(runner, "up")
    assert up.argv == (*COMPOSE, "up", "-d")
    [snapshot] = wslcalls.docker_execs(runner.calls)
    [stop] = compose(runner, "stop")
    name = result.json["data"]["name"]
    tar = next(run for run in wslcalls.commands(runner.calls) if run.argv[:1] == ("tar",))
    assert tar.argv[-4] == f"{BACKUPS}/{name}"

    def index(run):
        return wslcalls.index(runner.calls, run.call)

    assert index(up) < wslcalls.index(runner.calls, snapshot.call) < index(tar) < index(stop)
    assert index(stop) == max(index(c) for c in wslcalls.commands(runner.calls) if wslcalls.compose_verb(c.argv))
    assert result.json["data"]["started_comfyui"] is True
    assert "stopped again" in result.stderr


def test_backup_on_a_stopped_install_waits_for_comfyui_before_taking_the_snapshot(cli, runner):
    stopped(runner)

    result = cli("backup", "--json")

    assert result.exit_code == 0, result.stderr
    [wait] = [run for run in wslcalls.scripts(runner.calls) if run.script == "wait_ready.sh"]
    [snapshot] = wslcalls.docker_execs(runner.calls)
    [up] = compose(runner, "up")
    assert (
        wslcalls.index(runner.calls, up.call)
        < wslcalls.index(runner.calls, wait.call)
        < wslcalls.index(runner.calls, snapshot.call)
    )


def test_backup_on_a_stopped_install_that_never_comes_up_fails_without_a_snapshot_and_stops_it(cli, runner):
    stopped(runner)
    runner.on_script("wait_ready.sh", returncode=1, stdout="[error] the container is exited\nlog line")

    result = cli("backup", "--json")

    assert result.exit_code == 1
    assert wslcalls.docker_execs(runner.calls) == []
    assert len(compose(runner, "stop")) == 1


def test_backup_on_a_running_install_leaves_it_running_and_says_it_did_not_start_it(cli, runner):
    running(runner)

    result = cli("backup", "--json")

    assert result.exit_code == 0, result.stderr
    assert not [run for run in wslcalls.scripts(runner.calls) if run.script == "wait_ready.sh"]
    assert compose(runner, "up") == [] and compose(runner, "stop") == []
    assert result.json["data"]["started_comfyui"] is False


def test_a_failed_backup_still_stops_a_container_it_had_to_start(cli, runner):
    stopped(runner)
    runner.on(*AS_USER, "docker", "exec", returncode=1, stderr="Error: snapshot failed")

    result = cli("backup", "--json")

    assert result.exit_code == 1
    assert len(compose(runner, "up")) == 1 and len(compose(runner, "stop")) == 1
    assert not any(argv[:1] == ("tar",) for argv in commands(runner))


def restore(cli, *flags):
    return cli("restore", "--backup", "comfyui-20260101-100000.tar", "--confirm", "comfy-docker", "--json", *flags)


def test_restore_on_a_stopped_install_ends_with_comfyui_stopped(cli, runner):
    container = FakeContainer(runner, running=False)
    list_backups(runner, "comfyui-20260101-100000.tar")

    result = restore(cli)

    assert result.exit_code == 0, result.stderr
    assert container.history == ["up", "restart", "stop"]  # started for the snapshot, stopped at the end
    assert container.running is False
    [restore_snapshot] = wslcalls.docker_execs(runner.calls)
    assert restore_snapshot.command[-2:] == ("restore-snapshot", SNAPSHOT)
    assert result.json["data"]["container"] == "stopped"
    assert "run window" not in result.stderr


def test_restore_on_a_running_install_stops_it_first_and_ends_stopped_and_mentions_no_window(cli, runner):
    container = FakeContainer(runner, running=True)
    list_backups(runner, "comfyui-20260101-100000.tar")

    result = restore(cli)

    assert result.exit_code == 0, result.stderr
    assert container.history == ["stop", "up", "restart", "stop"]
    assert container.running is False
    assert result.json["data"]["container"] == "stopped"
    assert "run window" not in result.stderr


def test_restore_with_a_run_window_open_says_how_to_reopen_it(cli, runner, winapi):
    running(runner)
    list_backups(runner, "comfyui-20260101-100000.tar")
    winapi.instances.add(instance_name("comfy-docker"))

    result = restore(cli)

    assert result.exit_code == 0, result.stderr
    assert "run window" in result.stderr and "comfyui-sandbox start" in result.stderr
    assert result.json["data"]["run_window_closed"] is True


def test_a_restore_that_fails_after_stopping_comfyui_still_says_how_to_reopen_the_run_window(cli, runner, winapi):
    running(runner)
    list_backups(runner, "comfyui-20260101-100000.tar")
    winapi.instances.add(instance_name("comfy-docker"))
    runner.on_script("wait_ready.sh", returncode=1, stdout="[error] the container is exited\nlog line")

    result = restore(cli)

    assert result.exit_code == 1
    assert "run window" in result.stderr and "comfyui-sandbox start" in result.stderr


def test_a_restore_that_fails_before_stopping_comfyui_does_not_mention_the_run_window(cli, runner, winapi):
    running(runner)
    list_backups(runner, "comfyui-20260101-100000.tar")
    winapi.instances.add(instance_name("comfy-docker"))
    runner.on(*AS_USER, *wslcalls.COMPOSE_STOP, returncode=1, stderr="stop failed")

    result = restore(cli)

    assert result.exit_code == 1
    assert "run window" not in result.stderr


def test_a_failed_restore_still_stops_a_container_it_had_to_start(cli, runner):
    container = FakeContainer(runner, running=False)
    list_backups(runner, "comfyui-20260101-100000.tar")
    runner.on_script("wait_ready.sh", returncode=1, stdout="[error] the container is exited\nlog line")

    result = restore(cli)

    assert result.exit_code == 1
    assert len(compose(runner, "up")) == 1
    assert container.history[-1] == "stop" and container.running is False


# -- which backup (fix/tui-restore: home's Restore… runs `restore` with no --backup, so the command asks) --

OLD, MIDDLE, NEW = "comfyui-20260101-100000.tar", "comfyui-20260215-083000.tar", "comfyui-20260301-090000.tar"
WHICH = "Which backup do you want to restore?"


def extracted(runner) -> list[str]:
    return [argv[-1].rsplit("/", 1)[1] for argv in commands(runner) if argv[:1] == ("tar",) and "-xf" in argv]


def test_restore_asks_which_backup_newest_first_and_restores_the_one_picked(cli, runner, prompter):
    list_backups(runner, MIDDLE, OLD, NEW, "notes.txt")
    prompter.answers = [MIDDLE, "comfy-docker"]

    result = cli("restore", "--json")

    assert result.exit_code == 0, result.stderr
    assert prompter.asked[0] == (WHICH, (NEW, MIDDLE, OLD))
    assert prompter.defaults[0] == (WHICH, NEW)
    assert extracted(runner) == [MIDDLE]
    assert result.json["data"]["name"] == MIDDLE


def test_the_backup_question_says_when_each_backup_was_made_and_which_is_newest(cli, runner, prompter):
    list_backups(runner, OLD, NEW)
    prompter.answers = [NEW, "comfy-docker"]

    cli("restore")

    message, hint, hints = prompter.hints[0]
    assert message == WHICH
    assert "~/comfyui-backups" in hint
    assert hints == {NEW: "made 1 Mar 2026, 09:00 UTC · the newest", OLD: "made 1 Jan 2026, 10:00 UTC"}


def test_restore_asks_which_backup_before_asking_for_the_name_to_be_typed(cli, runner, prompter):
    list_backups(runner, OLD, NEW)
    prompter.answers = [OLD, "comfy-docker"]

    cli("restore")

    assert [message for _, message in prompter.questions] == [WHICH, "Type the sandbox name to confirm (comfy-docker):"]
    assert prompter.type_to_confirm == [("Type the sandbox name to confirm (comfy-docker):", ("comfy-docker",))]


def test_the_typed_confirmation_names_the_backup_picked(cli, runner, prompter):
    list_backups(runner, OLD, NEW)
    prompter.answers = [OLD, "comfy-docker"]

    result = cli("restore")

    assert f"Restoring {OLD} overwrites" in result.stderr


def test_restore_asks_which_backup_even_when_there_is_only_one_so_its_name_is_seen(cli, runner, prompter):
    list_backups(runner, OLD)
    prompter.answers = [OLD, "comfy-docker"]

    result = cli("restore")

    assert result.exit_code == 0, result.stderr
    assert prompter.asked[0] == (WHICH, (OLD,))
    assert extracted(runner) == [OLD]


@pytest.mark.parametrize(
    "flags",
    [("--backup", OLD), ("--confirm", "comfy-docker", "--yes"), ("--confirm", "comfy-docker", "--non-interactive")],
    ids=["named", "yes", "non-interactive"],
)
def test_restore_does_not_ask_which_backup_when_named_or_told_not_to_ask(cli, runner, prompter, flags):
    list_backups(runner, OLD, NEW)
    prompter.answers = [] if "--confirm" in flags else ["comfy-docker"]

    result = cli("restore", *flags)

    assert result.exit_code == 0, result.stderr
    assert prompter.asked == []
    assert extracted(runner) == [OLD if "--backup" in flags else NEW]


def test_backing_out_of_the_backup_question_changes_nothing(cli, runner, prompter):
    container = FakeContainer(runner, running=True)
    list_backups(runner, OLD, NEW)
    prompter.answers = [CANCEL]

    result = cli("restore")

    assert result.exit_code == 130
    assert not any(argv[:1] == ("tar",) for argv in commands(runner))
    assert container.history == [] and container.running is True
    assert wslcalls.docker_execs(runner.calls) == []


def test_restore_with_no_backup_fails_before_asking_anything(cli, runner, prompter):
    list_backups(runner, "notes.txt")

    result = cli("restore", "--json")

    assert result.exit_code == 1
    assert "no backup" in result.json["error"]
    assert result.json["data"] == {"backups": []}  # how a front end tells there's nothing to restore
    assert prompter.questions == []
