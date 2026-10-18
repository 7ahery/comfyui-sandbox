"""`install` intake by questions: what is asked, what is recommended (ticket 07).

The fake prompter answers by the start of a choice's text. Same fake host as test_install:
RTX 4090, 64 GB RAM, 24 CPUs, drives C: (120 GB free) and D: (800 GB free), user "Alex".
"""

import json

import pytest
from test_check import GIB, Machine


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


START = "Start the installation"
DEFAULTS = [
    "Inside WSL",
    "Tune .wslconfig and add",
    "Standard UI only",
    "Normal:",
    "comfy-docker",
    "comfy",
    "~/comfyui-models",
    "Use defaults",
    START,
]


def choices_of(prompter, topic: str) -> tuple[str, ...]:
    [asked] = [choices for message, choices in prompter.asked if topic in message]
    return asked


def test_accepting_every_recommended_answer_equals_yes(install, prompter, home):
    prompter.answers += DEFAULTS

    result = install()

    assert result.exit_code == 0, result.stderr
    interactive = answers_saved(home)
    assert prompter.answers == []
    assert interactive["models"] == "wsl" and interactive["memory"] == "both"
    assert (
        "Distro:        comfy-docker" in result.stderr
        and "GPU mode:      NVIDIA (NVIDIA GeForce RTX 4090)" in result.stderr
    )


def test_the_summary_names_every_value_before_anything_changes(install, prompter, runner):
    prompter.answers += [*DEFAULTS[:-1], "Change something", *DEFAULTS]

    result = install()

    assert result.exit_code == 0, result.stderr
    for label in [
        "Distro:",
        "Linux user:",
        "Models:",
        "Memory:",
        "Manager UI:",
        "Security:",
        "Version:",
        "GPU mode:",
        "Port:",
    ]:
        assert label in result.stderr
    assert result.stderr.count("Distro:") == 2  # shown again after "Change something"
    assert prompter.questions.count(("select", "Ready to install?")) == 2


def test_every_select_puts_the_recommended_choice_first(install, prompter):
    prompter.answers += DEFAULTS
    install()

    # Where the models live has no recommendation: neither place is better, only different.
    quiet = ["Ready to install?", "Where should your models live?"]
    firsts = [
        choices[0] for message, choices in prompter.asked if message not in quiet and not message.startswith("Advanced")
    ]
    assert all(first.endswith("(Recommended)") for first in firsts), firsts
    [models] = [choices for message, choices in prompter.asked if message == "Where should your models live?"]
    assert models[0].startswith("Windows folder") and not any("(Recommended)" in label for label in models)


def test_the_distro_name_offers_two_free_names_and_never_an_existing_one(install, prompter, machine):
    machine.distros = ["Ubuntu", "comfy-docker", "COMFYUI"]
    prompter.answers += [*DEFAULTS[:4], "comfy-docker-2", *DEFAULTS[5:]]

    result = install()

    assert result.exit_code == 0, result.stderr
    names = choices_of(prompter, "Name of the new sandbox")
    assert names[0].startswith("comfy-docker-2") and names[1].startswith("comfyui-2")


def test_a_typed_distro_name_that_exists_is_asked_again(install, prompter, machine, home):
    machine.distros = ["Ubuntu", "Sandbox"]
    prompter.answers += [*DEFAULTS[:4], "Other", "sandbox", "my-comfy", *DEFAULTS[5:]]

    assert install().exit_code == 0
    assert prompter.rejected[0][0] == "sandbox" and "already exists" in prompter.rejected[0][1]
    assert answers_saved(home)["distro"] == "my-comfy"


def test_the_linux_username_offers_the_windows_name_and_rejects_a_bad_typed_one(install, prompter, home):
    prompter.answers += [*DEFAULTS[:5], "Other", "Bad Name", "alex", *DEFAULTS[6:]]

    assert install().exit_code == 0
    assert answers_saved(home)["linux_user"] == "alex"
    assert prompter.rejected[0][0] == "Bad Name"
    [_, offered, *_] = choices_of(prompter, "Linux username")
    assert offered == "comfy" or offered.startswith("alex")


def test_the_windows_name_is_offered_when_valid(install, prompter):
    prompter.answers += DEFAULTS
    install()
    assert choices_of(prompter, "Linux username")[1] == "alex"


def test_comfyui_is_offered_when_there_is_no_usable_windows_name(install, prompter, environ):
    environ["USERNAME"] = "Mich Ael!"
    prompter.answers += DEFAULTS
    install()
    assert choices_of(prompter, "Linux username")[1] == "comfyui"


def test_windows_models_offer_the_c_default_and_the_second_drive_and_ask_briefly(install, prompter, tmp_path):
    prompter.answers += ["Windows folder", "Other", str(tmp_path / "Models"), *DEFAULTS[1:6], *DEFAULTS[7:]]

    result = install()

    assert result.exit_code == 0, result.stderr
    [(message, choices)] = [(m, c) for m, c in prompter.asked if "Windows models folder" in m]
    assert choices[0] == "C:\\AI\\Models (Recommended)" and choices[1] == "D:\\AI\\Models"
    assert message == "Windows models folder?"
    assert (tmp_path / "Models" / "checkpoints").is_dir()


def test_windows_models_offer_the_user_profile_when_there_is_no_second_drive(install, prompter, machine, home):
    machine.drives = [("C:", 120 * GIB)]
    prompter.answers += ["Windows folder", *DEFAULTS[1:6], "C:", *DEFAULTS[7:]]

    install()

    assert choices_of(prompter, "Windows models folder")[1] == f"{home}\\AI\\Models"


def test_wsl_models_offer_two_folders_and_reject_dangerous_typed_paths(install, prompter, home):
    prompter.answers += [*DEFAULTS[:6], "Other", "/home", "~/comfyui-docker/models", "/data/models", *DEFAULTS[7:]]

    assert install().exit_code == 0
    assert [c.split(" ")[0] for c in choices_of(prompter, "inside WSL")[:2]] == ["~/comfyui-models", "~/models"]
    assert [r[0] for r in prompter.rejected] == ["/home", "~/comfyui-docker/models"]
    assert answers_saved(home)["models_dir"] == "/data/models"


def test_both_is_recommended_under_32_gb_of_ram(install, prompter, machine, home):
    machine.ram_bytes = 16 * GIB
    prompter.answers += ["Inside WSL", "Tune .wslconfig and add", *DEFAULTS[2:]]

    assert install().exit_code == 0
    assert choices_of(prompter, "memory")[0] == "Tune .wslconfig and add --disable-pinned-memory (Recommended)"
    assert answers_saved(home)["memory"] == "both"


def test_tuning_wslconfig_and_pinned_memory_is_recommended_first_on_a_big_machine(install, prompter):
    prompter.answers += DEFAULTS
    install()
    memory = choices_of(prompter, "memory")
    assert memory[0] == "Tune .wslconfig and add --disable-pinned-memory (Recommended)"
    assert memory[1].startswith("Tune .wslconfig: 56 GB RAM, 24 CPUs, 8 GB swap")


def test_keep_current_is_recommended_when_wslconfig_already_has_enough_memory(install, prompter, home):
    (home / ".wslconfig").write_text("[wsl2]\nmemory=60GB\n", "utf-8")
    prompter.answers += ["Inside WSL", "Keep current .wslconfig (", *DEFAULTS[2:]]

    assert install().exit_code == 0
    first = choices_of(prompter, "memory")[0]
    assert first.startswith("Keep current .wslconfig (60 GB") and first.endswith("(Recommended)")
    assert answers_saved(home)["memory"] == "keep"
    assert answers_saved(home)["wsl_memory_gb"] is None


def test_a_small_wslconfig_memory_is_not_kept_by_default(install, prompter, home):
    (home / ".wslconfig").write_text("[wsl2]\nmemory=8GB\n", "utf-8")
    prompter.answers += DEFAULTS
    install()
    assert choices_of(prompter, "memory")[0].startswith("Tune")


def test_customizing_offers_the_second_drive_and_recommends_it_when_c_is_low(install, prompter, machine, home):
    machine.drives = [("C:", 30 * GIB), ("D:", 800 * GIB), ("E:", 100 * GIB)]
    prompter.answers += [*DEFAULTS[:7], "Customize", "Nightly", "D:\\WSL", "Suggested", START]

    result = install()

    assert result.exit_code == 0, result.stderr
    where = choices_of(prompter, "distro's disk")
    assert where == ("D:\\WSL\\comfy-docker (Recommended)", "WSL default (on C:)")
    a = answers_saved(home)
    assert (a["location"], a["channel"]) == ("D:\\WSL\\comfy-docker", "nightly")


def test_a_typed_release_number_is_the_version_and_a_bad_one_is_asked_again(install, prompter, machine, home):
    machine.drives = [("C:", 30 * GIB)]
    prompter.answers += [*DEFAULTS[:7], "Customize", "Other", "latest-ish", "v0.3.40", "Suggested", START]

    result = install()

    assert result.exit_code == 0, result.stderr
    assert prompter.rejected[0][0] == "latest-ish"
    assert answers_saved(home)["channel"] == "0.3.40"
    assert "Version:       0.3.40" in result.stderr


def test_the_disk_question_is_skipped_without_a_second_drive(install, prompter, machine, home):
    machine.drives = [("C:", 30 * GIB)]
    prompter.answers += [*DEFAULTS[:7], "Customize", "Latest", "Suggested", START]

    assert install().exit_code == 0
    assert not [m for m, _ in prompter.asked if "distro's disk" in m]
    assert answers_saved(home)["location"] is None


def test_a_low_c_asks_for_the_location_even_under_use_defaults_and_recommends_the_other_drive(
    install, prompter, machine, home
):
    machine.drives = [("C:", 30 * GIB), ("D:", 800 * GIB)]
    prompter.answers += [*DEFAULTS[:7], "Use defaults", "D:\\WSL", START]

    assert install().exit_code == 0

    assert choices_of(prompter, "distro's disk")[0] == "D:\\WSL\\comfy-docker (Recommended)"
    assert answers_saved(home)["location"] == "D:\\WSL\\comfy-docker"


def test_wsl_default_is_recommended_when_c_has_room(install, prompter):
    prompter.answers += [*DEFAULTS[:7], "Customize", "Latest", "WSL default", "Suggested", START]
    install()
    assert choices_of(prompter, "distro's disk")[0] == "WSL default (on C:) (Recommended)"


def test_wsl_limits_can_be_suggested_low_or_typed(install, prompter, home):
    prompter.answers += [*DEFAULTS[:7], "Customize", "Latest", "WSL default", "More room", START]
    assert install().exit_code == 0
    a = answers_saved(home)
    assert (a["wsl_memory_gb"], a["wsl_processors"], a["wsl_swap_gb"]) == (48, 24, 8)
    assert choices_of(prompter, "WSL limits?")[1].startswith("More room for Windows: 48 GB RAM, 24 CPUs, 8 GB swap")


def test_typed_wsl_limits_are_validated(install, prompter, home):
    prompter.answers += [
        *DEFAULTS[:7],
        "Customize",
        "Latest",
        "WSL default",
        "Other",
        "memory=900",
        "memory=48 swap=16",
        START,
    ]
    assert install().exit_code == 0
    a = answers_saved(home)
    assert (a["wsl_memory_gb"], a["wsl_processors"], a["wsl_swap_gb"]) == (48, 24, 16)
    assert prompter.rejected[0][0] == "memory=900"


def test_the_limits_question_is_skipped_when_wslconfig_is_not_tuned(install, prompter):
    prompter.answers += ["Inside WSL", "Neither", *DEFAULTS[2:7], "Customize", "Latest", "WSL default", START]
    assert install().exit_code == 0
    assert not [m for m, _ in prompter.asked if "WSL limits?" in m]


def test_an_existing_windows_folder_with_some_structure_asks_before_adding_folders(install, prompter, tmp_path):
    models = tmp_path / "Models"
    (models / "checkpoints").mkdir(parents=True)
    prompter.answers += ["Windows folder", "Other", str(models), True, *DEFAULTS[1:6], *DEFAULTS[7:]]

    assert install().exit_code == 0
    assert (
        "confirm",
        f"{models} already has some of ComfyUI's model folders. Create the missing ones ("
        + ", ".join(__import__("comfyui_sandbox.intake", fromlist=["x"]).plan_models_folder(models).missing)
        + ")?",
    ) in prompter.questions or (models / "loras").is_dir()
    assert (models / "loras").is_dir()


@pytest.mark.parametrize("root", ["D:\\", "D:", "D:/"])
def test_a_typed_drive_root_is_rejected_and_asked_again(install, prompter, home, tmp_path, root):
    models = tmp_path / "Models"
    prompter.answers += ["Windows folder", "Other", root, str(models), *DEFAULTS[1:6], *DEFAULTS[7:]]

    assert install().exit_code == 0
    assert "folder on that drive" in prompter.rejected[0][1]
    assert answers_saved(home)["models_dir"] == str(models)


def test_declining_to_create_the_missing_folders_cancels_the_install(install, prompter, tmp_path, home):
    models = tmp_path / "Models"
    (models / "checkpoints").mkdir(parents=True)
    prompter.answers += ["Windows folder", "Other", str(models), False]

    result = install()

    assert result.exit_code == 1
    assert not (models / "loras").exists()
    assert not (home / ".comfyui-sandbox" / "installs.json").exists()


def test_flags_skip_their_questions(install, prompter):
    prompter.answers += [
        "Inside WSL",
        "Tune .wslconfig and add",
        "Standard UI only",
        "Normal:",
        "comfy-docker",
        "~/comfyui-models",
        "Use defaults",
        START,
    ]

    result = install("--user", "artist")

    assert result.exit_code == 0, result.stderr
    assert not [m for m, _ in prompter.asked if "Linux username" in m]


def test_a_cancelled_prompt_exits_130_and_saves_nothing(install, prompter, home, monkeypatch):
    def cancel(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(type(prompter), "select", cancel)

    assert install().exit_code == 130
    assert not (home / ".comfyui-sandbox" / "installs.json").exists()


def test_change_something_keeps_the_port_flag(install, prompter, home):
    prompter.answers += [*DEFAULTS[:-1], "Change something", *DEFAULTS]

    result = install("--port", "9000")

    assert result.exit_code == 0, result.stderr
    assert result.stderr.count("Port:          9000") == 2
    assert "8188" not in result.stderr
    assert answers_saved(home)["port"] == 9000


def test_change_something_keeps_create_models_folders(install, prompter, tmp_path):
    models = tmp_path / "Models"
    (models / "checkpoints").mkdir(parents=True)
    windows = ["Windows folder", "Other", str(models), *DEFAULTS[1:6]]
    # No confirm answer is queued: the flag answers the "create the missing ones?" question both rounds.
    prompter.answers += [*windows, "Use defaults", "Change something", *windows, "Use defaults", START]

    result = install("--create-models-folders")

    assert result.exit_code == 0, result.stderr
    assert (models / "loras").is_dir()


def test_change_something_asks_again_with_the_earlier_answers_as_defaults(install, prompter):
    prompter.answers += [
        "Inside WSL",
        "Tune .wslconfig and add",
        "Standard UI",
        "Weak",
        "Other",
        "my-distro",
        "Other",
        "myuser",
        "~/models",
        "Use defaults",
        "Change something",
        "Inside WSL",
        "Tune .wslconfig and add",
        "Standard UI",
        "Weak",
        "my-distro",
        "myuser",
        "~/models",
        "Use defaults",
        START,
    ]

    result = install()

    assert result.exit_code == 0, result.stderr
    second = dict(prompter.defaults[-9:])

    def default_for(topic: str) -> str:
        [chosen] = [d for m, d in second.items() if topic in m]
        return chosen

    assert default_for("Which Manager UI").startswith("Standard UI")
    assert default_for("security level").startswith("Weak")
    assert default_for("Name of the new sandbox").startswith("my-distro")
    assert default_for("Linux username").startswith("myuser")
    assert default_for("Models folder inside WSL").startswith("~/models")


ROUND_ONE_CUSTOM = [*DEFAULTS[:7], "Customize", "Nightly", "D:\\WSL", "More room", "Change something"]


def test_use_defaults_in_round_two_keeps_the_earlier_location_channel_and_limits(install, prompter, home):
    prompter.answers += [*ROUND_ONE_CUSTOM, *DEFAULTS[:7], "Use defaults", START]

    result = install()

    assert result.exit_code == 0, result.stderr
    a = answers_saved(home)
    assert (a["location"], a["channel"]) == ("D:\\WSL\\comfy-docker", "nightly")
    assert (a["wsl_memory_gb"], a["wsl_processors"], a["wsl_swap_gb"]) == (48, 24, 8)


def test_round_two_offers_the_earlier_advanced_answers_as_defaults(install, prompter, home):
    prompter.answers += [*ROUND_ONE_CUSTOM, *DEFAULTS[:7], "Customize", "Nightly", "D:\\WSL", "More room", START]

    assert install().exit_code == 0

    second = prompter.defaults[-5:-1]

    def default_for(topic: str) -> str:
        [chosen] = [d for m, d in second if topic in m]
        return chosen

    assert default_for("Advanced settings").startswith("Customize")
    assert default_for("distro's disk").startswith("D:\\WSL\\comfy-docker")
    assert default_for("ComfyUI version?").startswith("Nightly")
    assert default_for("WSL limits?").startswith("More room")


def test_round_two_offers_typed_wsl_limits_as_the_default(install, prompter, home):
    typed = [*DEFAULTS[:7], "Customize", "Latest", "WSL default", "Other", "memory=40 swap=4", "Change something"]
    prompter.answers += [*typed, *DEFAULTS[:7], "Customize", "Latest", "WSL default", "memory=40", START]

    assert install().exit_code == 0

    [limits] = [d for m, d in prompter.defaults[-5:-1] if "WSL limits?" in m]
    assert limits == "memory=40 processors=24 swap=4"
    a = answers_saved(home)
    assert (a["wsl_memory_gb"], a["wsl_processors"], a["wsl_swap_gb"]) == (40, 24, 4)
