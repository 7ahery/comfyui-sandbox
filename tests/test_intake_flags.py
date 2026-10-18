"""`install` intake through flags: every question has a flag (ticket 07).

Same fake host as test_install: a ready RTX 4090 box with 64 GB RAM and drives C: and D:.
"""

import json

import pytest
from test_check import GIB, Machine

import wslcalls


@pytest.fixture
def machine():
    return Machine(distros=["Ubuntu"])


@pytest.fixture
def install(cli, runner, environ, machine):
    def run(*argv: str):
        machine.script(runner, environ)
        return cli("install", "--json", *argv)

    return run


def answers_saved(home) -> dict:
    saved = json.loads((home / ".comfyui-sandbox" / "installs.json").read_text("utf-8"))
    [record] = saved["installs"]
    return record["answers"]


ALL_FLAGS = [
    "--distro",
    "my-comfy",
    "--user",
    "artist",
    "--models",
    "wsl",
    "--models-path",
    "~/models",
    "--memory",
    "both",
    "--manager-ui",
    "standard",
    "--security",
    "weak",
    "--channel",
    "nightly",
    "--wsl-limits",
    "memory=40 swap=8",
    "--location",
    r"D:\WSL\my-comfy",
    "--port",
    "8200",
]

# Everything the questions always ask, in the order they are asked.
REQUIRED = {
    "--models": "wsl",
    "--memory": "neither",
    "--manager-ui": "legacy",
    "--security": "normal",
    "--distro": "x1",
    "--user": "comfy",
    "--models-path": "~/comfyui-models",
}


def test_every_question_is_a_flag_and_nothing_is_asked_when_all_are_given(install, home, prompter):
    result = install("--non-interactive", *ALL_FLAGS)

    assert result.exit_code == 0, result.stderr
    a = answers_saved(home)
    assert (a["distro"], a["linux_user"], a["models"], a["models_dir"]) == (
        "my-comfy",
        "artist",
        "wsl",
        "/home/artist/models",
    )
    assert (a["memory"], a["manager_ui"], a["security"], a["channel"], a["port"]) == (
        "both",
        "standard",
        "weak",
        "nightly",
        8200,
    )
    assert (a["wsl_memory_gb"], a["wsl_processors"], a["wsl_swap_gb"]) == (40, 24, 8)
    assert a["location"] == r"D:\WSL\my-comfy"
    assert prompter.asked == []


def test_non_interactive_names_each_missing_flag_in_question_order(install, home):
    flags: list[str] = []
    seen: list[str] = []
    for _ in range(len(REQUIRED) + 1):
        result = install("--non-interactive", *flags)
        if result.exit_code != 9:
            break
        flag = result.json["flag"]
        assert flag in result.json["message"]
        seen.append(flag)
        flags += [flag, REQUIRED[flag]]

    assert seen == list(REQUIRED)
    assert result.exit_code == 0, result.stderr
    assert answers_saved(home)["distro"] == "x1"


def test_a_missing_answer_saves_nothing(install, home):
    assert install("--non-interactive").exit_code == 9
    assert not (home / ".comfyui-sandbox" / "installs.json").exists()


def test_yes_keeps_models_inside_wsl_and_needs_nothing_on_windows(install, home):
    result = install("--yes")

    assert result.exit_code == 0, result.stderr
    a = answers_saved(home)
    assert (a["models"], a["models_dir"]) == ("wsl", "/home/comfy/comfyui-models")
    assert (a["memory"], a["manager_ui"], a["security"], a["channel"]) == ("both", "standard", "normal", "latest")
    assert (a["wsl_memory_gb"], a["wsl_processors"], a["wsl_swap_gb"]) == (56, 24, 8)
    assert a["location"] is None


def test_a_flag_overrides_one_default_under_yes(install, home):
    assert install("--yes", "--channel", "nightly", "--security", "weak").exit_code == 0
    a = answers_saved(home)
    assert (a["channel"], a["security"], a["manager_ui"]) == ("nightly", "weak", "standard")


@pytest.mark.parametrize("given, saved", [("0.3.40", "0.3.40"), ("v0.3.40", "0.3.40"), ("Nightly", "nightly")])
def test_the_channel_flag_takes_a_release_number(install, home, given, saved):
    assert install("--yes", "--channel", given).exit_code == 0
    assert answers_saved(home)["channel"] == saved


@pytest.mark.parametrize(
    "flags",
    [
        ["--user", "Comfy"],
        ["--user", "1comfy"],
        ["--user", "co mfy"],
        ["--user", "root"],
        ["--models", "cloud"],
        ["--memory", "lots"],
        ["--security", "off"],
        ["--channel", "beta"],
        ["--channel", "0.3.x"],
        ["--manager-ui", "old"],
        ["--port", "80"],
        ["--wsl-limits", "memory=lots"],
        ["--wsl-limits", "memory=500"],
        ["--distro", "bad name!"],
    ],
)
def test_an_invalid_flag_value_is_a_usage_error_before_anything_is_saved(install, home, flags):
    result = install("--yes", *flags)

    assert result.exit_code == 2, result.stderr
    assert not (home / ".comfyui-sandbox" / "installs.json").exists()


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/home",
        "/home/comfy",
        "/home/comfy/",
        "~",
        "~/comfyui-docker",
        "~/comfyui-docker/models",
        "/home/comfy/comfyui-docker/../comfyui-docker",
        "relative/path",
        "/models;rm",
    ],
)
def test_wsl_models_paths_that_would_be_deleted_or_are_unsafe_are_rejected(install, home, path):
    result = install("--yes", "--models-path", path)

    assert result.exit_code == 2, result.stderr
    assert not (home / ".comfyui-sandbox" / "installs.json").exists()


def test_a_wsl_models_path_is_expanded_for_the_chosen_user(install, home):
    assert install("--yes", "--user", "artist", "--models-path", "~/my models").exit_code == 0
    assert answers_saved(home)["models_dir"] == "/home/artist/my models"


def test_an_absolute_wsl_models_path_is_kept(install, home):
    assert install("--yes", "--models-path", "/data/models").exit_code == 0
    assert answers_saved(home)["models_dir"] == "/data/models"


# -- the Windows models folder -------------------------------------------------


def windows_flags(path) -> list[str]:
    return ["--models", "windows", "--models-path", str(path)]


def required_except(*skip: str) -> list[str]:
    return [part for flag, value in REQUIRED.items() if flag not in skip for part in (flag, value)]


def test_a_missing_windows_models_folder_is_created_with_comfyuis_subfolders(install, tmp_path, runner):
    models = tmp_path / "AI" / "Models"

    result = install("--yes", *windows_flags(models))

    assert result.exit_code == 0, result.stderr
    for sub in [
        "checkpoints",
        "loras",
        "vae",
        "text_encoders",
        "diffusion_models",
        "controlnet",
        "upscale_models",
        "embeddings",
    ]:
        assert (models / sub).is_dir(), sub
    harden = wslcalls.script(runner.calls, "harden.sh")
    assert harden.env["MODELS_MODE"] == "windows" and harden.env["MODELS_WIN"] == str(models)


def test_a_non_empty_folder_without_models_subfolders_stops_with_a_did_you_mean_hint(install, tmp_path, home):
    folder = tmp_path / "Stuff"
    (folder / "models" / "checkpoints").mkdir(parents=True)
    (folder / "notes.txt").write_text("x")

    result = install("--yes", *windows_flags(folder))

    assert result.exit_code == 1
    assert "did you mean" in result.json["message"].lower()
    assert str(folder / "models") in result.json["message"]
    assert not (home / ".comfyui-sandbox" / "installs.json").exists()
    assert not (folder / "checkpoints").exists()


def test_an_existing_folder_with_some_structure_needs_consent_to_create_the_missing_ones(install, tmp_path):
    models = tmp_path / "Models"
    (models / "checkpoints").mkdir(parents=True)
    rest = required_except("--models", "--models-path")

    refused = install("--non-interactive", *windows_flags(models), *rest)
    assert refused.exit_code == 9
    assert refused.json["flag"] == "--create-models-folders"
    assert not (models / "loras").exists()

    done = install("--non-interactive", *windows_flags(models), *rest, "--create-models-folders")
    assert done.exit_code == 0, done.stderr
    assert (models / "loras").is_dir() and (models / "checkpoints").is_dir()


def test_a_windows_models_path_must_be_absolute(install):
    assert install("--yes", "--models", "windows", "--models-path", r"AI\Models").exit_code == 2


@pytest.mark.parametrize("root", ["D:\\", "D:", "D:/"])
@pytest.mark.parametrize("mode", ["--yes", "--non-interactive"])
def test_a_drive_root_as_the_windows_models_folder_is_an_error_not_a_fallback(install, home, root, mode):
    rest = required_except("--models", "--models-path") if mode == "--non-interactive" else []

    result = install(mode, *windows_flags(root), *rest)

    assert result.exit_code == 2, result.stderr
    assert "folder on that drive" in result.json["message"]
    assert not (home / ".comfyui-sandbox" / "installs.json").exists()


def test_windows_models_without_a_path_names_the_flag(install):
    result = install("--non-interactive", "--models", "windows", *required_except("--models", "--models-path"))

    assert result.exit_code == 9
    assert result.json["flag"] == "--models-path"


def test_yes_puts_the_distro_on_the_other_drive_when_c_is_low_and_says_why(install, machine, home):
    machine.drives = [("C:", 30 * GIB), ("D:", 800 * GIB)]

    result = install("--yes")

    assert result.exit_code == 0, result.stderr
    assert answers_saved(home)["location"] == "D:\\WSL\\comfy-docker"
    assert "C: has only 30 GB free" in result.stderr


def test_non_interactive_with_c_low_and_no_location_exits_9_naming_the_flag(install, machine, home):
    machine.drives = [("C:", 30 * GIB), ("D:", 800 * GIB)]

    result = install("--non-interactive", *required_except("--location"))

    assert result.exit_code == 9
    assert result.json["flag"] == "--location"
    assert not (home / ".comfyui-sandbox" / "installs.json").exists()


def test_c_low_with_no_other_drive_keeps_the_wsl_default(install, machine, home):
    machine.drives = [("C:", 30 * GIB)]
    assert install("--yes").exit_code == 0
    assert answers_saved(home)["location"] is None


def test_enough_room_on_c_keeps_the_wsl_default(install, home):
    assert install("--yes").exit_code == 0
    assert answers_saved(home)["location"] is None
