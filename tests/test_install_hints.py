"""`install`'s questions carry display hints (prompts.Prompter): a line explaining each question, and
none for the choices, whose labels say enough. The TUI shows the hints; the terminal's
questionary prompts ignore them (test_prompts), so the CLI's questions read as before.

Through seam B: `install` on the CLI's fakes, its questions answered by the scripted Prompter, which
records each question's hints.
"""

import pytest
from test_check import Machine
from test_intake_interactive import DEFAULTS

from comfyui_sandbox.winapi import PortOwner


@pytest.fixture
def machine():
    return Machine(distros=["Ubuntu"])


@pytest.fixture
def install(cli, runner, environ, machine):
    def run(*argv: str):
        machine.script(runner, environ)
        return cli("install", "--json", *argv)

    return run


def hints_of(prompter, start: str) -> tuple[str, dict[str, str]]:
    [(hint, choices)] = [(hint, choices) for message, hint, choices in prompter.hints if message.startswith(start)]
    return hint, choices


def test_every_install_question_explains_itself(install, prompter):
    prompter.answers += DEFAULTS

    assert install().exit_code == 0

    assert len(prompter.hints) == len(DEFAULTS)
    assert all(hint for message, hint, choices in prompter.hints), prompter.hints


def test_no_install_option_carries_a_hint(install, prompter, winapi):
    winapi.port_owners[8188] = PortOwner(4120, "python.exe")
    prompter.answers += [*DEFAULTS, "Use port 8189"]

    assert install().exit_code == 0

    assert all(not choices for message, hint, choices in prompter.hints), prompter.hints


def test_a_taken_distro_name_explains_why_it_asks(install, prompter, machine):
    machine.distros = ["Ubuntu", "Sandbox"]
    prompter.answers += [*DEFAULTS[:4], *DEFAULTS[5:], "comfyui"]

    result = install("--distro", "SANDBOX")

    assert result.exit_code == 0, result.stderr
    hint, choices = hints_of(prompter, "A distro named SANDBOX already exists")
    assert hint and not choices


def test_the_manager_ui_question_recommends_standard_first(install, prompter):
    prompter.answers += DEFAULTS

    install()

    [(_message, labels)] = [(m, c) for m, c in prompter.asked if m.startswith("Which Manager UI")]
    assert labels[0].startswith("Standard UI only") and labels[0].endswith("(Recommended)")


def test_the_windows_models_question_is_short(install, prompter, tmp_path):
    prompter.answers += ["Windows folder", "Other", str(tmp_path / "Models"), *DEFAULTS[1:6], *DEFAULTS[7:]]

    install()

    [message] = [m for m, h, c in prompter.hints if m.startswith("Windows models folder")]
    assert message == "Windows models folder?"
