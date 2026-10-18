"""The shared command pieces in steps: project folder, compose argv, backup stamp, models folder."""

import re
from types import SimpleNamespace

import pytest

from comfyui_sandbox import steps
from comfyui_sandbox.errors import CliError
from comfyui_sandbox.wsl import Wsl

WHO = SimpleNamespace(distro="Ubuntu", linux_user="alice")


def test_project_folder_is_under_the_users_home():
    assert steps.project_path(WHO.linux_user) == "/home/alice/comfyui-docker"
    assert steps.project_path("bob") == "/home/bob/comfyui-docker"


def test_compose_argv_runs_compose_inside_the_project_folder():
    # COMPOSE_FILE in .env is relative and compose resolves it against the working directory,
    # not --project-directory: from `wsl --cd /` it looked for /docker-compose.yml.
    assert steps.compose_argv(WHO, "logs", "--no-color") == [
        "env",
        "--chdir",
        "/home/alice/comfyui-docker",
        "docker",
        "compose",
        "logs",
        "--no-color",
    ]


def test_backup_stamp_is_utc_and_sortable(monkeypatch):
    assert re.fullmatch(r"\d{8}-\d{6}", steps.backup_stamp())

    class Fixed(steps.datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is steps.UTC
            return cls(2026, 10, 6, 1, 2, 3, tzinfo=tz)

    monkeypatch.setattr(steps, "datetime", Fixed)
    assert steps.backup_stamp() == "20261006-010203"


def test_models_in_distro_is_the_mount_for_windows_and_the_path_for_wsl():
    assert steps.models_in_distro("windows", r"D:\models") == "/mnt/models"
    assert steps.models_in_distro("wsl", "/home/alice/m") == "/home/alice/m"


def _wsl(runner):
    return Wsl(runner, {})


IDS = ("wsl.exe", "-d", "Ubuntu", "-u", "alice", "--cd", "/", "--exec", "env")
PS = (*IDS, "env", "--chdir", "/home/alice/comfyui-docker", "docker", "compose", "ps", "--status", "running", "--quiet")


def test_container_running_is_true_only_when_compose_lists_a_running_container(runner):
    runner.on(*PS, stdout="abc123\n")
    assert steps.container_running(_wsl(runner), WHO) is True


def test_container_running_is_false_for_no_output_or_a_failed_probe(runner):
    runner.on(*PS, stdout="")
    assert steps.container_running(_wsl(runner), WHO) is False

    runner.on(*PS, returncode=1, stdout="abc123\n")
    assert steps.container_running(_wsl(runner), WHO) is False


def test_linux_ids_reads_the_uid_and_gid_of_the_user(runner):
    runner.on(*IDS, "id", "-u", stdout="1001\n")
    runner.on(*IDS, "id", "-g", stdout="1002\n")

    assert steps.linux_ids(_wsl(runner), WHO) == (1001, 1002)


def test_linux_ids_names_the_user_and_option_when_id_fails(runner):
    runner.on(*IDS, "id", "-g", returncode=1, stdout="")

    with pytest.raises(CliError, match=r"Could not read the id of alice \(id -g exit 1\)"):
        steps.linux_ids(_wsl(runner), WHO)


def test_one_long_step_timeout_is_an_hour():
    assert steps.LONG_STEP_TIMEOUT == 3600


def test_container_running_uses_the_default_call_timeout_unless_given_one(runner):
    from comfyui_sandbox.wsl import CALL_TIMEOUT

    runner.on(*PS, stdout="abc123\n")
    steps.container_running(_wsl(runner), WHO)
    steps.container_running(_wsl(runner), WHO, timeout=7)

    assert [call.timeout for call in runner.calls] == [CALL_TIMEOUT, 7]
