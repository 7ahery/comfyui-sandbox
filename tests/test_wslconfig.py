""".wslconfig: `wslconfig show|apply` (ticket 08).

The fake host is the 64 GB / 24 CPU box from test_check (suggested: 56 GB, 24 CPUs, 8 GB swap; "low" 48 GB).
The `.wslconfig` is `<injected home>\\.wslconfig`; the real one is never touched. Nothing here runs a real wsl.exe.
"""

import json

import pytest
from test_check import Machine

import wslcalls
from comfyui_sandbox.state import Gpu, Install, Installs, Models


@pytest.fixture
def machine():
    return Machine(distros=["Ubuntu", "comfy-docker"])


@pytest.fixture
def wslconfig(cli, runner, environ, machine):
    def run(*argv: str):
        machine.script(runner, environ)
        return cli("wslconfig", "--json", *argv)

    return run


@pytest.fixture
def config(home):
    return home / ".wslconfig"


def backups(home):
    return sorted(home.glob(".wslconfig.bak-*"))


def ours(home, *names: str) -> None:
    installs = Installs.load(home / ".comfyui-sandbox")
    for name in names:
        installs.put(
            Install(
                distro=name,
                linux_user="comfy",
                port=8188,
                models=Models("wsl", "/home/comfy/m"),
                gpu=Gpu("nvidia", "RTX"),
                deployed_by="1.0.0",
            )
        )
    installs.save()


# -- show -----------------------------------------------------------------------------


def test_show_prints_the_suggested_limits_and_the_current_file(wslconfig, config):
    config.write_text("[wsl2]\nmemory=8GB\n[experimental]\nautoMemoryReclaim=gradual\n", "utf-8")

    result = wslconfig("show")

    assert result.exit_code == 0, result.stderr
    assert "memory 56 GB" in result.stderr and "24 processors" in result.stderr and "swap 8 GB" in result.stderr
    assert "48 GB for more room for Windows" in result.stderr
    assert "autoMemoryReclaim=gradual" in result.stderr
    data = result.json["data"]
    assert data["limits"] == {
        "host_ram_gb": 64,
        "host_cpus": 24,
        "memory_gb": 56,
        "memory_low_gb": 48,
        "processors": 24,
        "swap_gb": 8,
    }
    assert data["exists"] is True and data["path"] == str(config)
    assert "autoMemoryReclaim=gradual" in data["content"]


def test_show_without_a_file_says_so_and_writes_nothing(wslconfig, config, home):
    result = wslconfig("show")

    assert result.exit_code == 0, result.stderr
    assert "(file does not exist)" in result.stderr
    assert result.json["data"]["exists"] is False and result.json["data"]["content"] is None
    assert not config.exists()
    assert not (home / ".comfyui-sandbox" / "logs").exists()


def test_show_reads_a_utf16_file(wslconfig, config):
    config.write_bytes("[wsl2]\r\nmemory=4GB\r\n".encode("utf-16"))

    result = wslconfig("show")

    assert "memory=4GB" in result.json["data"]["content"]
    assert "\r" not in result.json["data"]["content"]


# -- apply: the merge --------------------------------------------------------------------


def test_apply_creates_the_file_when_there_is_none_and_needs_no_backup(wslconfig, config, home):
    result = wslconfig("apply", "--yes")

    assert result.exit_code == 0, result.stderr
    assert config.read_bytes() == b"[wsl2]\nmemory=56GB\nprocessors=24\nswap=8GB\n"
    assert backups(home) == []
    data = result.json["data"]
    assert data["limits"] == {"memory_gb": 56, "processors": 24, "swap_gb": 8}
    assert data["backup"] is None and data["changed"] is True


def test_apply_merges_only_the_three_keys_keeps_everything_else_and_writes_a_backup(wslconfig, config, home):
    original = (
        "# my settings\n[wsl2]\n  Memory = 4GB\nnestedVirtualization=true\n# swap=1GB\nSWAP=2GB\n"
        "\n[experimental]\nautoMemoryReclaim=gradual\nmemory=1\n"
    )
    config.write_text(original, "utf-8")

    result = wslconfig("apply", "--yes")

    assert result.exit_code == 0, result.stderr
    assert config.read_text("utf-8") == (
        "# my settings\n[wsl2]\nmemory=56GB\nnestedVirtualization=true\n# swap=1GB\nswap=8GB\nprocessors=24\n"
        "\n[experimental]\nautoMemoryReclaim=gradual\nmemory=1\n"
    )
    [backup] = backups(home)
    assert backup.read_text("utf-8") == original
    assert result.json["data"]["backup"] == str(backup)


def test_a_missing_section_is_added_after_the_existing_settings(wslconfig, config):
    config.write_text("[experimental]\nsparseVhd=true", "utf-8")

    assert wslconfig("apply", "--yes").exit_code == 0

    assert config.read_text("utf-8") == (
        "[experimental]\nsparseVhd=true\n\n[wsl2]\nmemory=56GB\nprocessors=24\nswap=8GB\n"
    )


def test_the_section_name_is_case_insensitive_so_no_second_section_is_added(wslconfig, config):
    config.write_text("[WSL2]\nprocessors=2\n", "utf-8")

    assert wslconfig("apply", "--yes").exit_code == 0

    text = config.read_text("utf-8")
    assert text.lower().count("[wsl2]") == 1
    assert text == "[WSL2]\nprocessors=24\nmemory=56GB\nswap=8GB\n"


def test_a_utf16_file_with_crlf_is_merged_not_duplicated_and_written_as_utf8_without_a_bom(wslconfig, config):
    config.write_bytes("[wsl2]\r\nmemory=4GB\r\nlocalhostForwarding=true\r\n".encode("utf-16"))

    assert wslconfig("apply", "--yes").exit_code == 0

    raw = config.read_bytes()
    assert not raw.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff"))
    assert raw == b"[wsl2]\r\nmemory=56GB\r\nlocalhostForwarding=true\r\nprocessors=24\r\nswap=8GB\r\n"


def test_applying_the_limits_that_are_already_set_changes_nothing_and_makes_no_backup(wslconfig, config, home):
    config.write_text("[wsl2]\nmemory=56GB\nprocessors=24\nswap=8GB\n", "utf-8")

    result = wslconfig("apply", "--yes")

    assert result.exit_code == 0, result.stderr
    assert backups(home) == []
    assert result.json["data"]["changed"] is False and result.json["data"]["restart"] == "not_needed"


@pytest.mark.parametrize(
    ("limits", "expected"),
    [
        ("suggested", "memory=56GB\nprocessors=24\nswap=8GB"),
        ("low", "memory=48GB\nprocessors=24\nswap=8GB"),
        ("memory=32 processors=8 swap=0", "memory=32GB\nprocessors=8\nswap=0GB"),
    ],
)
def test_the_limits_flag_picks_suggested_low_or_custom_values(wslconfig, config, limits, expected):
    assert wslconfig("apply", "--limits", limits).exit_code == 0
    assert config.read_text("utf-8") == f"[wsl2]\n{expected}\n"


def test_a_bad_limits_value_is_a_usage_error_and_changes_nothing(wslconfig, config):
    result = wslconfig("apply", "--limits", "memory=999")

    assert result.exit_code == 2
    assert "--limits" in result.json["message"]
    assert not config.exists()


def test_without_the_limits_flag_non_interactive_exits_9_naming_it(wslconfig, config):
    result = wslconfig("apply", "--non-interactive")

    assert result.exit_code == 9 and result.json["flag"] == "--limits"
    assert not config.exists()


def test_without_the_limits_flag_a_terminal_is_asked(wslconfig, config, prompter):
    prompter.answers += ["More room for Windows"]

    result = wslconfig("apply", "--shutdown", "later")

    assert result.exit_code == 0, result.stderr
    assert config.read_text("utf-8") == "[wsl2]\nmemory=48GB\nprocessors=24\nswap=8GB\n"


def test_apply_writes_a_transcript(wslconfig, home):
    result = wslconfig("apply", "--yes")

    assert result.json["log"].endswith("-wslconfig-apply.log")


# -- apply: restarting WSL -----------------------------------------------------------------


def shutdowns(runner):
    return wslcalls.distro_ops(runner.calls, "--shutdown")


def terminates(runner):
    return [c.argv[c.argv.index("--terminate") + 1] for c in wslcalls.distro_ops(runner.calls, "--terminate")]


def test_nothing_running_means_nothing_to_restart(wslconfig, runner):
    result = wslconfig("apply", "--yes")

    assert result.json["data"]["restart"] == "not_needed"
    assert shutdowns(runner) == [] and terminates(runner) == []
    assert "next time WSL starts" in result.stderr


def test_only_our_own_distros_running_are_terminated_not_shut_down(wslconfig, runner, home, prompter):
    ours(home, "comfy-docker")
    runner.on("wsl.exe", "--list", "--running", "--quiet", stdout="comfy-docker\n")
    prompter.answers += ["Shut down now"]

    result = wslconfig("apply", "--limits", "suggested")

    assert result.exit_code == 0, result.stderr
    assert terminates(runner) == ["comfy-docker"] and shutdowns(runner) == []
    assert result.json["data"]["restart"] == "now"


def test_later_leaves_wsl_running_and_reminds_the_user(wslconfig, runner, home, prompter):
    ours(home, "comfy-docker")
    runner.on("wsl.exe", "--list", "--running", "--quiet", stdout="comfy-docker\n")
    prompter.answers += ["Later"]

    result = wslconfig("apply", "--limits", "suggested")

    assert result.exit_code == 0, result.stderr
    assert terminates(runner) == [] and shutdowns(runner) == []
    assert result.json["data"]["restart"] == "later" and result.json["data"]["restart_pending"] is True
    assert "not active until" in result.stderr


def test_yes_terminates_our_own_distros(wslconfig, runner, home):
    ours(home, "comfy-docker")
    runner.on("wsl.exe", "--list", "--running", "--quiet", stdout="comfy-docker\n")

    result = wslconfig("apply", "--yes")

    assert result.exit_code == 0, result.stderr
    assert terminates(runner) == ["comfy-docker"] and shutdowns(runner) == []


def test_yes_never_stops_distros_that_are_not_ours_it_waits(wslconfig, runner, home):
    ours(home, "comfy-docker")
    runner.on("wsl.exe", "--list", "--running", "--quiet", stdout="comfy-docker\nUbuntu\n")

    result = wslconfig("apply", "--yes")

    assert result.exit_code == 0, result.stderr
    assert result.json["data"]["restart"] == "later"
    assert shutdowns(runner) == [] and terminates(runner) == []


def test_non_interactive_with_running_distros_and_no_answer_exits_9_naming_shutdown(wslconfig, runner, config):
    runner.on("wsl.exe", "--list", "--running", "--quiet", stdout="Ubuntu\n")

    result = wslconfig("apply", "--limits", "suggested", "--non-interactive")

    assert result.exit_code == 9 and result.json["flag"] == "--shutdown"
    assert "Ubuntu" in result.json["message"]
    assert not config.exists()


def test_shutting_down_other_distros_is_refused_without_confirm_and_nothing_is_written(wslconfig, runner, config):
    runner.on("wsl.exe", "--list", "--running", "--quiet", stdout="Ubuntu\nDDEV\n")

    result = wslconfig("apply", "--yes", "--shutdown", "now", "--non-interactive")

    assert result.exit_code == 8
    assert "--confirm Ubuntu" in result.json["message"] and "Ubuntu" in result.json["message"]
    assert shutdowns(runner) == [] and terminates(runner) == []
    assert not config.exists()


def test_confirm_with_a_running_distro_name_allows_the_shutdown(wslconfig, runner, config):
    runner.on("wsl.exe", "--list", "--running", "--quiet", stdout="Ubuntu\nDDEV\n")

    result = wslconfig("apply", "--yes", "--shutdown", "now", "--confirm", "ddev", "--non-interactive")

    assert result.exit_code == 0, result.stderr
    assert len(shutdowns(runner)) == 1
    assert config.exists()
    assert result.json["data"]["restart"] == "now"


def test_a_terminal_confirms_the_shutdown_by_typing_a_running_distro_name(wslconfig, runner, prompter):
    runner.on("wsl.exe", "--list", "--running", "--quiet", stdout="Ubuntu\n")
    prompter.answers += ["Shut down now", "Ubuntu"]

    result = wslconfig("apply", "--limits", "suggested")

    assert result.exit_code == 0, result.stderr
    assert len(shutdowns(runner)) == 1


def test_typing_the_wrong_name_refuses_the_shutdown(wslconfig, runner, prompter, config):
    runner.on("wsl.exe", "--list", "--running", "--quiet", stdout="Ubuntu\n")
    prompter.answers += ["Shut down now", "nope"]

    result = wslconfig("apply", "--limits", "suggested")

    assert result.exit_code == 8
    assert shutdowns(runner) == [] and not config.exists()


def test_an_invalid_shutdown_value_is_a_usage_error(wslconfig):
    result = wslconfig("apply", "--yes", "--shutdown", "soon")

    assert result.exit_code == 2 and "--shutdown" in result.json["message"]


def test_wsl_is_never_called_when_it_is_not_installed(wslconfig, machine, runner):
    machine.wsl_installed = False

    result = wslconfig("apply", "--yes")

    assert result.exit_code == 0, result.stderr
    assert not any(wslcalls.is_wsl(c) for c in runner.calls)
    assert json.loads(result.stdout)["data"]["restart"] == "not_needed"
