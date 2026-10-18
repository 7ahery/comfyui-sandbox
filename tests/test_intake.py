"""The pure `intake` module: defaults, validation, the next-free-name rule, the models folder rules."""

import pytest

from comfyui_sandbox import intake, wslconfig
from comfyui_sandbox.errors import AnswerMissing, UsageError
from comfyui_sandbox.intake import Answers, Facts, Flags, InvalidAnswer
from fakes import FakePrompter


def machine(**over):
    data = {
        "wsl": {"distros": ["Ubuntu"]},
        "gpu": {"mode": "nvidia", "gpus": [{"name": "RTX 4090"}], "cuda_version_suggested": None},
        "drives": [{"drive": "C:", "free_gb": 120}, {"drive": "D:", "free_gb": 800}],
        "user_profile": r"C:\Users\Alex",
        "win_user_suggested": "alex",
        "wsl_limits": wslconfig.suggest(64, 24).as_dict(),
    }
    data.update(over)
    return data


def facts(**over) -> Facts:
    taken = over.pop("taken", ["Ubuntu"])
    current = over.pop("current", None)
    return Facts.from_check(machine(**over), taken, current)


def yes(flags: Flags | None = None, **kw) -> Answers:
    return intake.gather(kw.pop("facts", facts()), flags or Flags(), FakePrompter(), yes=True, interactive=False)


# -- defaults -------------------------------------------------------------------


def test_defaults_come_from_the_machine():
    a = yes()

    assert (a.distro, a.linux_user, a.models, a.models_dir, a.port) == (
        "comfy-docker",
        "comfy",
        "wsl",
        "/home/comfy/comfyui-models",
        8188,
    )
    assert (a.gpu, a.gpu_name, a.location) == ("nvidia", "RTX 4090", None)
    assert (a.memory, a.wsl_memory_gb, a.wsl_processors, a.wsl_swap_gb) == ("both", 56, 24, 8)
    assert (a.manager_ui, a.security, a.channel) == ("standard", "normal", "latest")
    assert a.tune_wslconfig and a.disable_pinned_memory and not a.manager_legacy


def test_a_small_machine_defaults_to_both_and_without_a_cuda_card_to_cpu():
    a = yes(facts=facts(wsl_limits=wslconfig.suggest(16, 8).as_dict(), gpu={"mode": "none", "gpus": []}))

    assert (a.memory, a.gpu, a.gpu_name) == ("both", "cpu", None)
    assert a.tune_wslconfig and a.disable_pinned_memory


def test_unknown_ram_means_no_tuning():
    a = yes(facts=facts(wsl_limits=None))
    assert (a.memory, a.wsl_memory_gb) == ("pinned", None)
    with pytest.raises(UsageError):
        yes(Flags(memory="tune"), facts=facts(wsl_limits=None))


def test_keep_current_is_the_default_when_memory_is_already_enough():
    assert yes(facts=facts(current=56)).memory == "keep"
    assert yes(facts=facts(current=55)).memory == "both"
    assert yes(facts=facts(current=11, wsl_limits=wslconfig.suggest(16, 8).as_dict())).memory == "both"
    assert yes(facts=facts(current=12, wsl_limits=wslconfig.suggest(16, 8).as_dict())).memory == "keep-pinned"


def test_the_cuda_suggestion_is_carried_into_the_answers():
    assert (
        yes(
            facts=facts(gpu={"mode": "nvidia", "gpus": [{"name": "RTX 5090"}], "cuda_version_suggested": "12.8"})
        ).cuda_version
        == "12.8"
    )


def test_answers_round_trip_and_unknown_keys_are_ignored():
    a = yes()
    assert Answers.from_dict({**a.to_dict(), "future_key": 1}) == a


def test_old_saved_answers_without_the_new_keys_still_load():
    old = {"distro": "x", "linux_user": "comfy", "models": "wsl", "models_dir": "/m", "port": 8188, "gpu": "cpu"}
    a = Answers.from_dict(old)
    assert (a.memory, a.manager_ui, a.security, a.channel) == ("neither", "standard", "normal", "latest")


# -- the next-free-name rule --------------------------------------------------------


@pytest.mark.parametrize(
    "taken, expected",
    [
        ([], "comfy-docker"),
        (["comfy-docker"], "comfy-docker-2"),
        (["COMFY-DOCKER", "Comfy-Docker-2"], "comfy-docker-3"),
        (["comfy-docker-2"], "comfy-docker"),
    ],
)
def test_next_free_name(taken, expected):
    assert intake.next_free_name("comfy-docker", taken) == expected


def test_the_default_distro_skips_distros_and_our_own_installs():
    assert yes(facts=facts(taken=["Ubuntu", "comfy-docker", "comfy-docker-2"])).distro == "comfy-docker-3"


# -- validation ---------------------------------------------------------------------


@pytest.mark.parametrize("name", ["comfy", "a", "c0mfy-ui_2", "x" * 32])
def test_valid_linux_usernames(name):
    assert intake.parse_user(name) == name


@pytest.mark.parametrize("name", ["", "Comfy", "1comfy", "-comfy", "_c", "co mfy", "comfy!", "x" * 33, "root"])
def test_invalid_linux_usernames(name):
    with pytest.raises(InvalidAnswer):
        intake.parse_user(name)


@pytest.mark.parametrize(
    "path, expected",
    [
        ("~/comfyui-models", "/home/comfy/comfyui-models"),
        ("~/models", "/home/comfy/models"),
        ("/data/models/", "/data/models"),
        ("/data/my models", "/data/my models"),
        ("/home/comfy/comfyui-docker2", "/home/comfy/comfyui-docker2"),
        ("/home/other/comfyui-docker", "/home/other/comfyui-docker"),
    ],
)
def test_valid_wsl_models_paths(path, expected):
    assert intake.parse_wsl_models_path(path, "comfy") == expected


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "//",
        "/home",
        "/home/",
        "/home/comfy",
        "~",
        "~/",
        "/home/comfy/.",
        "/home/comfy/comfyui-docker",
        "/home/comfy/comfyui-docker/data",
        "~/comfyui-docker",
        "/home/comfy/x/../comfyui-docker/y",
        "",
        "models",
        "./models",
        "/data/$HOME",
        "/data/a;b",
        "/data/ü",
    ],
)
def test_rejected_wsl_models_paths(path):
    with pytest.raises(InvalidAnswer):
        intake.parse_wsl_models_path(path, "comfy")


@pytest.mark.parametrize("path", [r"C:\AI\Models", "d:/ai/models", r"C:\Models\\"])
def test_valid_windows_paths(path):
    assert intake.parse_windows_path(path)[1] == ":"


@pytest.mark.parametrize("path", ["", r"AI\Models", r"\AI\Models", "C:AI", r"\\server\share\models", "/mnt/c/x"])
def test_invalid_windows_paths(path):
    with pytest.raises(InvalidAnswer):
        intake.parse_windows_path(path)


@pytest.mark.parametrize("path", ["D:\\", "D:", "D:/", "d:\\", "D:\\\\", "D:\\.", "D:\\x\\..", "D:/x/../"])
def test_a_drive_root_is_not_a_windows_models_folder(path):
    with pytest.raises(InvalidAnswer, match="folder on that drive"):
        intake.parse_windows_models_path(path)


@pytest.mark.parametrize("path", [r"\\server\share", r"\\server\share\models", "\\\\?\\D:\\", "\\\\?\\D:\\x"])
def test_a_network_share_or_device_path_is_not_a_windows_models_folder(path):
    with pytest.raises(InvalidAnswer, match="drive letter"):
        intake.parse_windows_models_path(path)


@pytest.mark.parametrize("path", [r"D:\Models", "d:/ai/models", "D:\\Models\\"])
def test_a_folder_on_a_drive_is_a_windows_models_folder(path):
    assert intake.parse_windows_models_path(path)[1:3] == ":\\"


@pytest.mark.parametrize("port", [1023, 65536, 0, -1, "abc"])
def test_invalid_ports(port):
    with pytest.raises(InvalidAnswer):
        intake.parse_port(port)


def test_ports_in_range():
    assert (intake.parse_port(1024), intake.parse_port("65535")) == (1024, 65535)


def test_wsl_limits_forms():
    s = wslconfig.suggest(64, 24)
    assert intake.parse_limits("suggested", s) == (56, 24, 8)
    assert intake.parse_limits("low", s) == (48, 24, 8)
    assert intake.parse_limits("memory=40GB swap=4", s) == (40, 24, 4)
    assert intake.parse_limits("memory=48, processors=16, swap=16", s) == (48, 16, 16)
    for bad in ["memory=1", "memory=65", "processors=0", "processors=25", "swap=600", "ram=4", "48"]:
        with pytest.raises(InvalidAnswer):
            intake.parse_limits(bad, s)


def test_a_distro_name_may_not_use_spaces_or_leading_dashes():
    assert intake.parse_distro("My-Comfy.2") == "My-Comfy.2"
    for bad in ["", "-x", "a b", "a/b", "x" * 64]:
        with pytest.raises(InvalidAnswer):
            intake.parse_distro(bad)


def test_the_flag_is_named_in_a_usage_error():
    with pytest.raises(UsageError, match="--channel"):
        yes(Flags(channel="beta"))


def test_missing_answers_under_non_interactive_name_the_flag():
    with pytest.raises(AnswerMissing) as caught:
        intake.gather(facts(), Flags(models="wsl", memory="neither"), FakePrompter(), yes=False, interactive=False)
    assert caught.value.flag == "--manager-ui"


def test_yes_wins_over_non_interactive():
    assert yes().models == "wsl"


def test_the_default_models_path_follows_the_chosen_user_and_mode():
    assert yes(Flags(user="artist")).models_dir == "/home/artist/comfyui-models"
    assert yes(Flags(models="windows", models_path=r"C:\AI\Models")).models_dir == r"C:\AI\Models"


def test_a_windows_models_folder_default_is_c_ai_models(tmp_path, monkeypatch):
    # the default is only a path; nothing is created at gather time
    a = intake.gather(facts(), Flags(models="windows"), FakePrompter(), yes=True, interactive=False)
    assert a.models_dir == r"C:\AI\Models"


# -- the Windows models folder rules --------------------------------------------------


def test_a_missing_folder_is_created_with_every_comfyui_subfolder(tmp_path):
    folder = tmp_path / "AI" / "Models"
    plan = intake.plan_models_folder(folder)
    assert plan.action == "create"

    made = intake.create_models_folders(folder)

    assert set(made) == set(intake.MODEL_FOLDERS)
    assert all(
        (folder / name).is_dir()
        for name in [
            "checkpoints",
            "loras",
            "vae",
            "text_encoders",
            "diffusion_models",
            "controlnet",
            "upscale_models",
            "embeddings",
        ]
    )


def test_an_empty_existing_folder_is_filled_without_asking(tmp_path):
    assert intake.plan_models_folder(tmp_path).action == "create"


def test_a_complete_folder_needs_nothing(tmp_path):
    for name in intake.MODEL_FOLDERS:
        (tmp_path / name).mkdir()
    assert intake.plan_models_folder(tmp_path).action == "ok"


def test_a_partial_folder_asks_and_creating_never_touches_existing_files(tmp_path):
    (tmp_path / "checkpoints").mkdir()
    (tmp_path / "checkpoints" / "model.safetensors").write_text("weights")
    (tmp_path / "Loras").mkdir()

    plan = intake.plan_models_folder(tmp_path)
    assert plan.action == "ask" and "checkpoints" not in plan.missing and "loras" not in plan.missing

    intake.create_models_folders(tmp_path)
    assert (tmp_path / "checkpoints" / "model.safetensors").read_text() == "weights"


def test_a_non_empty_folder_without_known_folders_is_refused_with_a_hint(tmp_path):
    (tmp_path / "models").mkdir()
    (tmp_path / "readme.txt").write_text("x")
    plan = intake.plan_models_folder(tmp_path)
    assert plan.action == "refuse" and f"Did you mean {tmp_path / 'models'}?" in (plan.problem or "")


def test_pointing_at_checkpoints_itself_suggests_the_parent(tmp_path):
    checkpoints = tmp_path / "checkpoints"
    checkpoints.mkdir()
    (checkpoints / "a.safetensors").write_text("x")
    plan = intake.plan_models_folder(checkpoints)
    assert plan.action == "refuse" and f"Did you mean {tmp_path}?" in (plan.problem or "")


def test_a_file_is_not_a_models_folder(tmp_path):
    f = tmp_path / "f"
    f.write_text("x")
    assert intake.plan_models_folder(f).action == "refuse"


# -- .wslconfig memory ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text, gb",
    [
        ("[wsl2]\nmemory=48GB\n", 48),
        ("[wsl2]\nMemory = 8192MB # comment\n", 8),
        ("[wsl2]\nmemory=17179869184\n", 16),
        ("[experimental]\nmemory=99GB\n[wsl2]\nprocessors=4\n", None),
        ("[wsl2]\nmemory=lots\n", None),
        ("", None),
    ],
)
def test_read_memory_gb(text, gb):
    assert wslconfig.read_memory_gb(text) == gb
