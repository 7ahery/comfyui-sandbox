"""Questions to the user: an injected boundary (docs/adr/0013).

Commands never prompt under --non-interactive (they raise AnswerMissing instead).

Two things every Prompter shares, whichever front end draws it (docs/tui.md, "Questions"):

- **Display hints.** A question, and each choice of a select, may carry a one-line explanation
  (`hint`, `hints`). They're optional and keyword-only, so a caller that has none passes none. A
  front end with room for them (the TUI shows a choice's hint beside it while the pointer is on
  it) shows them; the terminal's questionary prompts ignore them, so the CLI's questions look as
  they always have. A hint explains; it never changes what's asked or what counts as an answer.
  A text question may also say it's a type-the-name confirmation (`type_to_confirm`), for a
  front end to draw as one; the terminal asks it as the plain text question it is.
- **A defined cancel.** Backing out of a question (Ctrl+C at a terminal, Esc in the TUI) raises
  KeyboardInterrupt, the same interruption a Ctrl+C anywhere else raises, so cli.main ends the
  command as interrupted (exit 130). Commands ask before they change anything, so a cancelled
  question changes nothing.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

import questionary


class Prompter(Protocol):
    def select(
        self,
        message: str,
        choices: Sequence[str],
        default: str | None = None,
        *,
        hint: str = "",
        hints: Mapping[str, str] | None = None,
    ) -> str:
        """One of `choices`. `hint` explains the question; `hints` explains choices, by choice
        (a choice may have none)."""
        ...

    def text(
        self,
        message: str,
        default: str = "",
        validate: Callable[[str], str | None] | None = None,
        *,
        hint: str = "",
        type_to_confirm: Sequence[str] = (),
    ) -> str:
        """Free text. `validate` returns an error message, or None when the text is fine;
        the question is asked again until it is. `hint` explains the question.

        `type_to_confirm`: given, the question is a destructive action's type-the-name confirmation
        and these are the names any of which confirms it (comfyui_sandbox.destructive). Like a hint,
        it's for display only (the TUI draws a field that lights up the name as it's typed); what
        counts as confirming stays the caller's check of the answer."""
        ...

    def confirm(self, message: str, default: bool = True, *, hint: str = "") -> bool:
        """Yes or no. `hint` explains the question."""
        ...


class QuestionaryPrompter:
    """The real prompts, on the terminal (stderr side; stdout stays the result channel).

    It takes the display hints and draws nothing of them: a terminal question stays the one line
    questionary draws, as scripts' transcripts and the CLI's users know it. Ctrl+C makes
    `unsafe_ask()` raise KeyboardInterrupt, which is the Prompter's cancel."""

    def select(
        self,
        message: str,
        choices: Sequence[str],
        default: str | None = None,
        *,
        hint: str = "",
        hints: Mapping[str, str] | None = None,
    ) -> str:
        return questionary.select(message, choices=list(choices), default=default, output=_stderr_output()).unsafe_ask()

    def text(
        self,
        message: str,
        default: str = "",
        validate: Callable[[str], str | None] | None = None,
        *,
        hint: str = "",
        type_to_confirm: Sequence[str] = (),
    ) -> str:
        # type_to_confirm is ignored too: the message already says what to type.
        check = (lambda value: validate(value) or True) if validate else None
        return questionary.text(message, default=default, validate=check, output=_stderr_output()).unsafe_ask()

    def confirm(self, message: str, default: bool = True, *, hint: str = "") -> bool:
        return questionary.confirm(message, default=default, output=_stderr_output()).unsafe_ask()


def _stderr_output():
    """Draw prompts on stderr, so stdout holds only the result (docs/scripting.md, "Streams")."""
    from prompt_toolkit.output import create_output

    return create_output(stdout=sys.stderr)
