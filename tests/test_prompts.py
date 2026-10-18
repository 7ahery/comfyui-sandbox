"""The terminal's `Prompter` (`QuestionaryPrompter`): the display hints a question may carry are for
front ends that have room to show them; at a terminal the questions look exactly as they did.

questionary is the boundary here: each test swaps its question functions for recorders and checks
what they were asked to draw.
"""

from __future__ import annotations

import pytest

from comfyui_sandbox import prompts


class Asked:
    """A stand-in for one of questionary's question functions: records how it was called, and
    answers `answer`."""

    def __init__(self, answer) -> None:
        self.answer, self.calls = answer, []

    def __call__(self, message, **kwargs):
        self.calls.append((message, kwargs))
        return self

    def unsafe_ask(self):
        return self.answer


@pytest.fixture
def questionary(monkeypatch):
    asked = {"select": Asked("b"), "text": Asked("typed"), "confirm": Asked(True)}
    for name, fake in asked.items():
        monkeypatch.setattr(prompts.questionary, name, fake)
    monkeypatch.setattr(prompts, "_stderr_output", lambda: "stderr")
    return asked


def test_select_takes_hints_and_draws_the_same_question_without_them(questionary):
    prompter = prompts.QuestionaryPrompter()

    plain = prompter.select("Pick", ["a", "b"], default="a")
    hinted = prompter.select("Pick", ["a", "b"], default="a", hint="why", hints={"a": "does a", "b": "does b"})

    assert plain == hinted == "b"
    first, second = questionary["select"].calls
    assert first == second == ("Pick", {"choices": ["a", "b"], "default": "a", "output": "stderr"})


def test_text_and_confirm_take_a_hint_and_draw_the_same_question_without_it(questionary):
    prompter = prompts.QuestionaryPrompter()

    assert prompter.text("Name", default="x", hint="why") == "typed"
    assert prompter.confirm("Sure?", default=False, hint="why") is True

    [(message, kwargs)] = questionary["text"].calls
    assert message == "Name" and "hint" not in kwargs and kwargs["default"] == "x"
    assert questionary["confirm"].calls == [("Sure?", {"default": False, "output": "stderr"})]


def test_a_type_to_confirm_question_is_a_plain_text_question_at_a_terminal(questionary):
    prompter = prompts.QuestionaryPrompter()

    plain = prompter.text("Type the sandbox name to confirm (x):")
    marked = prompter.text("Type the sandbox name to confirm (x):", type_to_confirm=("x",))

    assert plain == marked == "typed"
    first, second = questionary["text"].calls
    assert first == second
