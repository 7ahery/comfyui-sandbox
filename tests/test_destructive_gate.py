"""The shared destructive-action gate: `comfyui_sandbox.destructive.confirm_destructive`.

`uninstall`, `restore` and `recreate` call it with their `--confirm` value; `install` and
`wslconfig apply` call it before `wsl --shutdown` stops other people's distros. It is public API
(docs/scripting.md, "The destructive-action gate"), so it is tested directly, with a session shaped like the CLI's.
"""

import io
from types import SimpleNamespace

import pytest

from comfyui_sandbox.destructive import confirm_destructive
from comfyui_sandbox.errors import DestructiveRefused, ExitCode
from comfyui_sandbox.output import Output
from fakes import CANCEL, FakePrompter


def session(*, non_interactive=False, yes=False, answers=()):
    prompter = FakePrompter(answers=list(answers))
    out = Output(io.StringIO(), io.StringIO(), json_mode=False)
    return SimpleNamespace(
        non_interactive=non_interactive, yes=yes, out=out, deps=SimpleNamespace(prompter=prompter)
    ), prompter


def test_the_confirm_flag_with_the_distro_name_passes_without_a_question():
    s, prompter = session(non_interactive=True)

    assert confirm_destructive(s, ["comfy-docker"], confirm="comfy-docker", warning="Deletes all.") == "comfy-docker"
    assert prompter.questions == []


def test_the_flag_compares_case_insensitively_and_returns_the_real_name():
    s, _ = session(non_interactive=True)

    assert confirm_destructive(s, ["Comfy-Docker"], confirm="comfy-docker", warning="x") == "Comfy-Docker"


def test_a_flag_naming_another_distro_is_refused_with_exit_8():
    s, prompter = session(non_interactive=True)

    with pytest.raises(DestructiveRefused) as refused:
        confirm_destructive(s, ["comfy-docker"], confirm="Ubuntu", warning="x")

    assert refused.value.code == ExitCode.DESTRUCTIVE_REFUSED == 8
    assert "--confirm comfy-docker" in refused.value.message
    assert prompter.questions == []


@pytest.mark.parametrize("yes", [False, True])
def test_non_interactive_without_the_flag_is_refused_and_yes_does_not_help(yes):
    s, prompter = session(non_interactive=True, yes=yes)

    with pytest.raises(DestructiveRefused) as refused:
        confirm_destructive(s, ["comfy-docker"], confirm=None, warning="Deletes all.")

    assert refused.value.code == 8
    assert "--confirm comfy-docker" in refused.value.message
    assert prompter.questions == []


def test_yes_in_a_terminal_still_asks_for_the_name():
    s, prompter = session(yes=True, answers=["comfy-docker"])

    assert confirm_destructive(s, ["comfy-docker"], confirm=None, warning="Deletes all.") == "comfy-docker"
    assert [kind for kind, _ in prompter.questions] == ["text"]


def test_typing_the_name_confirms_and_the_warning_is_shown_first():
    s, _prompter = session(answers=["COMFY-DOCKER"])

    assert confirm_destructive(s, ["comfy-docker"], confirm=None, warning="Deletes all of it.") == "comfy-docker"
    assert "Deletes all of it." in s.out.console.file.getvalue()


def test_typing_something_else_is_refused():
    s, _ = session(answers=["yes"])

    with pytest.raises(DestructiveRefused) as refused:
        confirm_destructive(s, ["comfy-docker"], confirm=None, warning="x")

    assert refused.value.code == 8


def test_any_of_several_names_confirms():
    s, _ = session(non_interactive=True)

    assert confirm_destructive(s, ["Ubuntu", "DDEV"], confirm="ddev", warning="x") == "DDEV"


def test_the_typed_confirmation_carries_a_hint_for_a_front_end_that_shows_one():
    s, prompter = session(answers=["comfy-docker"])

    confirm_destructive(s, ["comfy-docker"], confirm=None, warning="Deletes all.")

    [(message, hint, _)] = prompter.hints
    assert message.startswith("Type the sandbox name to confirm") and "can't be undone" in hint


@pytest.mark.parametrize("names", [["comfy-docker"], ["Ubuntu", "DDEV"]])
def test_the_typed_confirmation_says_it_is_one_and_which_names_confirm_it(names):
    """So a front end can draw it as typing the name (home's letter-by-letter field)."""
    s, prompter = session(answers=[names[-1]])

    confirm_destructive(s, names, confirm=None, warning="Deletes all.")

    [(message, confirms)] = prompter.type_to_confirm
    assert message.startswith("Type the sandbox name to confirm") and confirms == tuple(names)


def test_a_cancelled_typed_confirmation_is_an_interruption_not_a_refusal():
    s, _ = session(answers=[CANCEL])

    with pytest.raises(KeyboardInterrupt):
        confirm_destructive(s, ["comfy-docker"], confirm=None, warning="x")
