"""`comfyui-sandbox uninstall` (ticket 15): counts-only report, the destructive gate, then unregister.

The fake Runner plays wsl.exe and uninstall_report.sh; the fake winapi holds the shortcuts. No real distro is touched.
"""

from pathlib import PureWindowsPath

import pytest
from test_check import Machine

import wslcalls
from comfyui_sandbox.state import Gpu, Install, Installs, Models
from comfyui_sandbox.winapi import Shortcut
from fakes import CANCEL

DESKTOP = PureWindowsPath(r"C:\Users\user\Desktop")
PROGRAMS = PureWindowsPath(r"C:\Users\user\AppData\Roaming\Microsoft\Windows\Start Menu\Programs")
SECRET = "my-private-workflow.json"

REPORT = """\
workflows=3
input_files=12
output_files=240
custom_nodes=7
backups=2
models=wsl
model_files=41
disk_used=58G
"""


class Distros:
    """The registered distros; `wsl --unregister` removes one, like the real thing."""

    def __init__(self, runner, environ, names):
        self.names, self.runner, self.environ = list(names), runner, environ
        self.publish()
        original = runner.run

        def run(argv, **kwargs):
            if tuple(argv[:2]) == ("wsl.exe", "--unregister"):
                self.names = [n for n in self.names if n.lower() != argv[2].lower()]
                self.publish()
            return original(argv, **kwargs)

        runner.run = run

    def publish(self):
        Machine(distros=self.names).script(self.runner, self.environ)


@pytest.fixture
def distros(runner, environ):
    d = Distros(runner, environ, ["Ubuntu", "comfy-docker"])
    runner.on_script("uninstall_report.sh", stdout=REPORT)
    return d


def own(home, *names, location=None, models=("wsl", "/home/comfy/models")):
    installs = Installs.load(home / ".comfyui-sandbox")
    for name in names:
        installs.put(
            Install(
                distro=name,
                linux_user="comfy",
                port=8188,
                models=Models(*models),
                gpu=Gpu("cpu"),
                deployed_by="1.0.0",
                state="complete",
                location=location,
            )
        )
    installs.save()


def remaining(home):
    return [i.distro for i in Installs.load(home / ".comfyui-sandbox").all()]


def shortcuts(winapi, *names):
    for name in names:
        for folder in (DESKTOP, PROGRAMS):
            winapi.create_shortcut(Shortcut(folder / f"ComfyUI ({name}).lnk", "x"))


def unregisters(runner):
    return [c for c in runner.calls if wslcalls.is_wsl(c) and "--unregister" in c.argv]


def test_non_interactive_without_confirm_exits_8_after_showing_counts_and_destroys_nothing(
    cli, runner, winapi, home, distros
):
    own(home, "comfy-docker")
    shortcuts(winapi, "comfy-docker")

    result = cli("uninstall", "--non-interactive", "--yes", "--json")

    assert result.exit_code == 8
    assert "--confirm comfy-docker" in result.json["message"]
    assert unregisters(runner) == [] and wslcalls.distro_ops(runner.calls, "--terminate") == []
    assert remaining(home) == ["comfy-docker"] and len(winapi.shortcuts) == 2
    run = wslcalls.script(runner.calls, "uninstall_report.sh")
    assert (run.distro, run.user) == ("comfy-docker", "comfy")
    assert run.env == {"PROJECT_DIR": "/home/comfy/comfyui-docker"}


def test_the_report_shows_counts_and_what_is_deleted_and_kept_and_never_a_name_or_path(
    cli, runner, winapi, home, distros
):
    own(home, "comfy-docker")
    runner.on_script("uninstall_report.sh", stdout=REPORT + f"/home/comfy/{SECRET}\nC:\\Users\\x\\secret.png\n")

    result = cli("uninstall", "--non-interactive", "--json")

    err = result.stderr
    for line in (
        "Workflows",
        "Input files",
        "Output files",
        "Custom nodes",
        "Backups",
        "Model files in WSL",
        "Distro disk in use",
    ):
        assert line in err
    for number in ("3", "12", "240", "7", "2", "41", "58G"):
        assert number in err
    assert "everything inside the distro comfy-docker" in err
    assert "Windows models folder" in err and ".wslconfig" in err
    assert SECRET not in err + result.stdout and "secret.png" not in err + result.stdout
    assert "/home/comfy/" not in err and "C:\\Users\\x" not in err


def test_confirm_flag_stops_unregisters_once_removes_shortcuts_and_drops_the_install(
    cli, runner, winapi, home, distros
):
    own(home, "comfy-docker", "other")
    shortcuts(winapi, "comfy-docker", "other")

    result = cli("uninstall", "--distro", "comfy-docker", "--non-interactive", "--confirm", "comfy-docker", "--json")

    assert result.exit_code == 0, result.stderr
    assert [c.argv for c in unregisters(runner)] == [("wsl.exe", "--unregister", "comfy-docker")]
    stop = wslcalls.index(runner.calls, wslcalls.distro_ops(runner.calls, "--terminate")[0])
    assert stop < wslcalls.index(runner.calls, unregisters(runner)[0])
    assert remaining(home) == ["other"]
    assert set(winapi.shortcuts) == {DESKTOP / "ComfyUI (other).lnk", PROGRAMS / "ComfyUI (other).lnk"}
    data = result.json["data"]
    assert data["distro"] == "comfy-docker" and data["unregistered"] is True and data["shortcuts_removed"] == 2


def test_typing_the_distro_name_confirms(cli, runner, home, prompter, distros):
    own(home, "comfy-docker")
    prompter.answers.append("comfy-docker")

    result = cli("uninstall")

    assert result.exit_code == 0, result.stderr
    assert len(unregisters(runner)) == 1 and remaining(home) == []


@pytest.mark.parametrize("typed", ["yes", "", "Ubuntu"])
def test_typing_anything_else_cancels_with_exit_8(cli, runner, winapi, home, prompter, distros, typed):
    own(home, "comfy-docker")
    shortcuts(winapi, "comfy-docker")
    prompter.answers.append(typed)

    result = cli("uninstall", "--yes")

    assert result.exit_code == 8
    assert unregisters(runner) == [] and remaining(home) == ["comfy-docker"] and len(winapi.shortcuts) == 2


def test_a_confirm_naming_another_distro_is_refused(cli, runner, home, distros):
    own(home, "comfy-docker")

    result = cli("uninstall", "--non-interactive", "--confirm", "Ubuntu")

    assert result.exit_code == 8 and unregisters(runner) == []


def test_a_distro_not_in_installs_json_is_refused_and_never_touched(cli, runner, home, distros):
    own(home, "comfy-docker")

    result = cli("uninstall", "--distro", "Ubuntu", "--confirm", "Ubuntu", "--non-interactive")

    assert result.exit_code != 0 and result.exit_code != 8
    assert "Ubuntu" in result.stderr
    assert unregisters(runner) == [] and wslcalls.distro_ops(runner.calls, "--terminate") == []
    assert remaining(home) == ["comfy-docker"]


def test_a_custom_location_and_the_wslconfig_limits_are_mentioned_when_they_apply(cli, home, distros):
    own(home, "comfy-docker", location=r"D:\WSL\comfy")
    (home / ".wslconfig").write_text("[wsl2]\nmemory=8GB\n", "utf-8")

    result = cli("uninstall", "--confirm", "comfy-docker", "--non-interactive")

    assert result.exit_code == 0
    assert r"D:\WSL\comfy" in result.stderr and "empty" in result.stderr
    assert "limits" in result.stderr and "other WSL distros" in result.stderr
    assert (home / ".wslconfig").read_text("utf-8") == "[wsl2]\nmemory=8GB\n"


def test_the_notes_are_left_out_when_they_do_not_apply(cli, home, distros):
    own(home, "comfy-docker")

    result = cli("uninstall", "--confirm", "comfy-docker", "--non-interactive")

    assert "remains" not in result.stderr and "still apply" not in result.stderr


def test_a_failed_unregister_leaves_the_shortcuts_and_the_install(cli, runner, winapi, home, distros):
    own(home, "comfy-docker")
    shortcuts(winapi, "comfy-docker")
    runner.on("wsl.exe", "--unregister", returncode=1)

    result = cli("uninstall", "--confirm", "comfy-docker", "--non-interactive")

    assert result.exit_code == 1
    assert remaining(home) == ["comfy-docker"] and len(winapi.shortcuts) == 2


def test_an_already_unregistered_distro_still_loses_its_shortcuts_and_entry(cli, runner, winapi, home, environ):
    Machine(distros=["Ubuntu"]).script(runner, environ)
    own(home, "comfy-docker")
    shortcuts(winapi, "comfy-docker")

    result = cli("uninstall", "--confirm", "comfy-docker", "--non-interactive")

    assert result.exit_code == 0, result.stderr
    assert unregisters(runner) == [] and remaining(home) == [] and winapi.shortcuts == {}


def test_cancelling_the_typed_confirmation_ends_as_interrupted_and_destroys_nothing(
    cli, runner, winapi, home, prompter, distros
):
    own(home, "comfy-docker")
    shortcuts(winapi, "comfy-docker")
    prompter.answers.append(CANCEL)

    result = cli("uninstall")

    assert result.exit_code == 130
    assert unregisters(runner) == [] and remaining(home) == ["comfy-docker"] and len(winapi.shortcuts) == 2


@pytest.mark.parametrize(
    "models, said, not_said",
    [
        ("wsl", "Model files in WSL", "Kept: models are in a Windows folder"),
        ("windows", "Kept: models are in a Windows folder", "Model files in WSL"),
        ("unknown", "the models folder could not be determined", "Model files in WSL"),
    ],
)
def test_the_report_says_whether_the_models_go_with_the_distro_or_stay(
    cli, runner, home, distros, models, said, not_said
):
    # The models folder's place comes from the report (the script looks at where it really is), not
    # from how the install wrote its path: a WSL one is an absolute Linux path, as the fixture has it.
    own(home, "comfy-docker")
    runner.on_script("uninstall_report.sh", stdout=REPORT.replace("models=wsl", f"models={models}"))

    result = cli("uninstall", "--non-interactive", "--json")

    assert said in result.stderr
    assert not_said not in result.stderr
