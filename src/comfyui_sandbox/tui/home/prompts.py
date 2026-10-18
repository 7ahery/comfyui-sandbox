"""The questions: look-alikes of questionary's select, confirm, text and press-any-key, plus
typing a name to confirm. Each is a widget whose answer arrives through its `future`; Console
mounts it, waits for the answer and leaves the answered question behind as a line.

A question may carry display hints (the core Prompter's, prompts.Prompter): its own, shown faintly
after it, and one per choice of a select, shown beside the choice while the pointer is on it."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Any

from rich.text import Text
from textual import events
from textual.app import ComposeResult, RenderResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, Static

from ..gateway import Question, QuestionKind
from ..links import HoverLinks
from ..pacing import Pacer
from ..parts import segments
from ..theme import FAINT, INK, LINE, MINT, MUTED, PANEL, PINK, RAISED, RED, SKY, SOFT, TEXT, WHITE, breathe, mix, st
from .model import Cancel, Interrupt
from .widgets import HomeWidget, fade_in, shake

# questionary's parts, in the palette.
QMARK, QUESTION, POINTER = st(SKY, bold=True), st(TEXT, bold=True), st(SKY, bold=True)
DANGER, ANSWER = RED, SKY
VALIDATION = st(TEXT, "#5a1730", bold=True)

# What each choice does, said next to it while the pointer is on it: (icon, what it does).
CHOICE_META = {
    "open": ("↗", "opens it in your browser"),
    "stop": ("■", "stops the container, nothing is deleted"),
    "logs": ("≡", "its output, following as it arrives"),
    "start": ("▶", "opens its own run window, then comes back here"),
    "run": ("▷", "streams its output here until you stop it"),
    "resume": ("↻", "carries on from the step that stopped"),
    "update": ("↑", "ComfyUI, custom nodes, Docker or the sandbox's setup"),
    "backup": ("↓", "workflows, inputs, outputs and the node list"),
    "restore": ("↥", "puts a backup's files back and reinstalls its nodes"),
    "settings": ("◇", "port, models folder, launch flags and more"),
    "recreate": ("↻", "a fresh ComfyUI, models and backups stay"),
    "uninstall": ("✗", "deletes the distro and everything in it"),
    "back": ("←", ""),
    "install": ("+", "an isolated ComfyUI in its own WSL distro"),
    "check": ("◎", "WSL, virtualization, GPU and disk. Changes nothing"),
    "quit": ("×", "running sandboxes keep running"),
    "all": ("✦", "each target below, in this order"),
    "comfyui": ("◆", "ComfyUI itself"),
    "nodes": ("◆", "every custom node you installed"),
    "comfy-cli": ("◆", "the tool that installs ComfyUI and nodes"),
    "system": ("◆", "Docker and the NVIDIA container toolkit"),
    "setup": ("◆", "the sandbox's own setup files, your config stays"),
    "~/comfyui-models": ("⌂", "fastest, they live inside the distro"),
    "windows": ("▣", "share them with other tools on Windows"),
    "gpu": ("▲", "CUDA 13 · 24 GB VRAM"),
    "cpu": ("○", "runs anywhere, much slower"),
    "next": ("→", "the next free port"),
    "cancel": ("×", "your answers are kept"),
}


class Prompt(HomeWidget, HoverLinks, can_focus=True):
    """Base for the questions drawn as one widget: the answer arrives through `future`."""

    DEFAULT_CSS = "Prompt { height: auto; }"
    FADE = True

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__()
        self.message, self.hint = message, hint
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()

    def on_mount(self) -> None:
        if self.FADE:
            fade_in(self)
        # The pointer's glow and anything else that moves; slow while the question just waits.
        self.pacer = Pacer(self, self.refresh, self.animating, busy_hz=12)
        self.pacer.start()

    def counting_down(self) -> bool:
        """Whether a cancel key is armed: its time-left bar drains, which needs the busy rate."""
        return self.app.armed_key() is not None

    def animating(self) -> bool:
        """Whether to redraw at the busy rate: a Ctrl+C countdown, or a dot that breathes."""
        return self.counting_down() or self.breathing()

    def breathing(self) -> bool:
        """Whether something on this question needs the busy rate to look smooth. The pointer's slow
        glow doesn't (the idle rate does it); a subclass says if it has something that does."""
        return False

    def question(self) -> Text:
        return question_line(self.message, self.hint)

    def keys(self) -> list[tuple[str, str]]:
        """What the bottom bar shows while this question is up."""
        return []

    def status(self) -> Text | None:
        """A state the bottom bar shows after the keys, if this question has one."""
        return None

    def answer(self, value) -> None:
        """Gives the answer (once), which wakes whoever awaits `future`."""
        if not self.future.done():
            self.future.set_result(value)

    def interrupt(self) -> None:
        """Ctrl+C while this question is up."""
        if not self.future.done():
            self.future.set_exception(Interrupt())

    def escape(self) -> None:
        """Esc: abandons the flow that asked (Cancel), unless a subclass has a way back to give."""
        if not self.future.done():
            self.future.set_exception(Cancel())


def question_line(message: str, hint: str = "") -> Text:
    """A question's line: the mark, the question and, faintly after it, its hint. On the same line,
    so a hint never moves the choices or the field below it."""
    line = Text.assemble(("◆ ", QMARK), (message, QUESTION))
    if hint:
        line.append(f"  {hint}", st(FAINT, italic=True))
    return line


class Unavailable(Text):
    """A choice's title for a choice that can't be picked now: it is drawn greyed out, `why` (if
    any) beside it, and the pointer never stops on it (SelectPrompt)."""

    def __init__(self, label: str, why: str = "") -> None:
        super().__init__(label)
        self.why = why


def available(choice) -> bool:
    """Whether choice can be picked: not a separator, not an Unavailable one."""
    return choice is not None and not (isinstance(choice, tuple) and isinstance(choice[0], Unavailable))


class SelectPrompt(Prompt):
    """questionary.select. The answer is the picked choice's index. hints: what each choice does,
    by value, said beside it under the pointer in place of CHOICE_META's (a gateway's question
    brings its own). default: the value the pointer starts on."""

    BINDINGS = [
        Binding("up", "move(-1)", show=False),
        Binding("down", "move(1)", show=False),
        Binding("enter", "choose", show=False),
    ]

    def __init__(
        self, message: str, choices: list, *, hint: str = "", hints: Mapping[str, str] | None = None, default=None
    ) -> None:
        super().__init__(message, hint)
        self.choices = choices  # (title, value) pairs; None is a separator
        self.hints = dict(hints or {})
        self.index = next(
            (k for k, c in enumerate(choices) if available(c) and default is not None and c[1] == default),
            next(k for k, c in enumerate(choices) if available(c)),
        )
        # Esc picks the way out ("back", or "cancel" on a mid-run question); without one it cancels.
        self.back = next(
            (k for k, c in enumerate(choices) if isinstance(c, tuple) and c[1] in ("back", "cancel")), None
        )

    def keys(self) -> list[tuple[str, str]]:
        goes_back = self.back is not None and self.choices[self.back][1] == "back"
        return [("↑↓", "move"), ("enter", "choose"), ("esc", "back" if goes_back else "cancel"), ("ctrl+c", "quit")]

    def escape(self) -> None:
        if self.back is None:
            super().escape()
        else:
            self.answer(self.back)

    def choice_line(self, k: int) -> Text:
        """Choice k as a line: quiet, or under the pointer a pill that says what it does."""
        choice = self.choices[k]
        if choice is None:
            return Text("    " + "┄" * 16, st(LINE))
        title, value = choice
        if isinstance(title, Unavailable):  # greyed out: no pointer, no pill, and it says why if it has one
            return Text.assemble(
                "    ",
                ("•", st(LINE)),
                " ",
                (title.plain, st(FAINT)),
                (f"  ({title.why})" if title.why else "", st(FAINT, italic=True)),
            )
        danger = isinstance(title, Text) and str(title.style) == DANGER
        label = title.plain if isinstance(title, Text) else title
        icon, does = CHOICE_META.get(value, ("•", "")) if isinstance(value, str) else ("•", "")
        does = self.hints.get(value, does) if isinstance(value, str) else does
        accent = RED if danger else SKY
        if k != self.index:
            return Text.assemble(
                "    ",
                (icon, st(mix(RED, INK, 0.35) if danger else FAINT)),
                " ",
                (label, st(mix(RED, TEXT, 0.25) if danger else SOFT)),
            )
        # The pointer's choice is a pill, its pointer glows, and it says what it does.
        glow = mix(accent, WHITE, 0.35 * breathe(1.8))
        bg = "#3b1529" if danger else RAISED
        line = Text.assemble(
            (" ❯", st(glow, bold=True)),
            (" ", st(bg=bg)),
            (icon, st(glow, bg, bold=True)),
            (f" {label} ", st("#ffd6df" if danger else TEXT, bg, bold=True)),
        )
        if does:
            line.append(f"  {does}", st(FAINT, italic=True))
        return line

    def render(self) -> RenderResult:
        return Text("\n").join([self.question(), *(self.choice_line(k) for k in range(len(self.choices)))])

    def action_move(self, step: int) -> None:
        k = self.index
        while True:  # questionary wraps around and skips separators
            k = (k + step) % len(self.choices)
            if available(self.choices[k]):
                break
        self.index = k
        self.refresh()

    def action_choose(self) -> None:
        self.answer(self.index)

    def _choice_at(self, y: int) -> int | None:
        k = y - 1  # line 0 is the question
        return k if 0 <= k < len(self.choices) and available(self.choices[k]) else None

    def on_mouse_move(self, event: events.MouseMove) -> None:
        # The pointer follows the mouse, so what's highlighted is what a click picks.
        k = self._choice_at(event.y)
        if k is not None and k != self.index:
            self.index = k
            self.refresh()

    def on_click(self, event: events.Click) -> None:
        k = self._choice_at(event.y)
        if k is not None:
            self.index = k
            self.answer(k)


class ConfirmPrompt(Prompt):
    """questionary.confirm as two pills on the question's line: ←→ (or y/n, or a click) picks.
    The default starts lit, so Enter takes it."""

    BINDINGS = [
        Binding("left,right,up,down,tab", "flip", show=False),
        Binding("enter", "choose", show=False),
        Binding("y", "pick(True)", show=False),
        Binding("n", "pick(False)", show=False),
    ]

    def __init__(self, message: str, default: bool, hint: str = "") -> None:
        super().__init__(message, hint)
        self.value = default

    def keys(self) -> list[tuple[str, str]]:
        return [("←→", "switch"), ("y n", ""), ("enter", "choose"), ("esc", "cancel")]

    def pill(self, value: bool) -> Text:
        icon, label = ("✓", "Yes") if value else ("✗", "No")
        if value == self.value:
            fill = mix(SKY, MINT, breathe(2.4)) if value else mix(RED, PINK, breathe(2.4))
            return Text(f" {icon} {label} ", st(INK, fill, bold=True))
        return Text(f" {icon} {label} ", st(MUTED, PANEL))

    def render(self) -> Text:
        return self.geometry()[0]

    def geometry(self) -> tuple[Text, dict[bool, tuple[int, int, int]]]:
        """The question with its pills, and where each pill is: value: (line, first column, last column)."""
        line = self.question()
        yes, no = self.pill(True), self.pill(False)
        pills_w = yes.cell_len + 2 + no.cell_len
        if line.cell_len + 3 + pills_w <= self.app.cols():
            y, x = 0, line.cell_len + 3
            out = Text.assemble(line, "   ", yes, "  ", no)
        else:  # a long question: the pills go under it, where a select's choices would
            y, x = 1, 4
            out = Text.assemble(line, "\n    ", yes, "  ", no)
        return out, {True: (y, x, x + yes.cell_len), False: (y, x + yes.cell_len + 2, x + pills_w)}

    def _value_at(self, x: int, y: int) -> bool | None:
        return next((v for v, (line, a, b) in self.geometry()[1].items() if y == line and a <= x < b), None)

    def action_flip(self) -> None:
        self.value = not self.value
        self.refresh()

    def action_pick(self, value: bool) -> None:
        self.value = value
        self.answer(value)

    def action_choose(self) -> None:
        self.answer(self.value)

    def on_mouse_move(self, event: events.MouseMove) -> None:
        value = self._value_at(event.x, event.y)
        if value is not None and value != self.value:
            self.value = value
            self.refresh()

    def on_click(self, event: events.Click) -> None:
        value = self._value_at(event.x, event.y)
        if value is not None:
            self.action_pick(value)


class TypeToConfirm(Prompt):
    """A destructive action's type-the-name confirmation (the core's `type_to_confirm` question),
    with the name to type shown faintly in place: letters light up as they match, a wrong one
    turns red, and Enter only takes the whole name (anything else shakes and isn't sent). Why not
    a plain text field: the name is the whole answer, so showing it in place makes a typo visible
    letter by letter, before Enter, and the action can't be sent by a slip."""

    def __init__(self, message: str, target: str, hint: str = "") -> None:
        super().__init__(message, hint)
        self.target, self.typed = target, ""

    def keys(self) -> list[tuple[str, str]]:
        return [("type", f"“{self.target}”"), ("enter", "confirm"), ("esc", "cancel")]

    def render(self) -> Text:
        done = self.typed == self.target
        line = Text(" ❯ ", st(RED if done else SKY, bold=True))
        for k, ch in enumerate(self.target):
            if k < len(self.typed):
                ok = self.typed[k] == ch
                line.append(
                    self.typed[k], st(RED if done else TEXT, bold=True) if ok else st(TEXT, "#6b1a33", bold=True)
                )
            elif k == len(self.typed):  # the cursor sits on the next letter to type
                line.append(ch, st(INK, mix(SKY, PANEL, 0.6 * breathe(1.1)), bold=True))
            else:
                line.append(ch, st(LINE))
        if len(self.typed) > len(self.target):
            line.append(self.typed[len(self.target) :], st(TEXT, "#6b1a33", bold=True))
        if len(self.typed) >= len(self.target):
            line.append(" ", st(bg=mix(SKY, PANEL, 0.6 * breathe(1.1))))
        # How much of the name is right so far: up to the first wrong letter.
        prefix = next(
            (k for k, (a, b) in enumerate(zip(self.typed, self.target, strict=False)) if a != b),
            min(len(self.typed), len(self.target)),
        )
        status = (
            Text.assemble("   ", ("Enter", st(RED, bold=True)), (" confirms. This can't be undone.", st(RED)))
            if done
            else Text.assemble(
                "   ", segments(prefix, len(self.target), RED), (f"  {prefix}/{len(self.target)}", st(FAINT))
            )
        )
        return Text("\n").join([self.question(), line, status])

    def on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            if self.typed == self.target:
                self.answer(self.typed)
            else:
                shake(self)
                self.app.bell()
        elif event.key == "backspace":
            self.typed = self.typed[:-1]
        elif event.is_printable and event.character:
            self.typed = (self.typed + event.character)[: len(self.target) + 12]
        else:
            return
        event.stop()
        self.refresh()

    def on_paste(self, event: events.Paste) -> None:
        self.typed = (self.typed + event.text.strip())[: len(self.target) + 12]
        self.refresh()


class PausePrompt(Prompt):
    """questionary.press_any_key_to_continue. L reads the operation that just ended first."""

    def keys(self) -> list[tuple[str, str]]:
        return [("l", "full log"), ("any key", "back")] if self.app.has_op_log() else [("any key", "back")]

    def render(self) -> Text:
        return Text.assemble(("  ↵ ", st(mix(SKY, PANEL, 0.7 * breathe(2.0)), bold=True)), (self.message, st(MUTED)))

    def on_key(self, event: events.Key) -> None:
        event.stop()
        if event.key == "l" and self.app.has_op_log():
            self.app.action_toggle_logs()  # the operation that just ended: read its output first
        else:
            self.answer(None)

    def on_click(self) -> None:
        self.answer(None)

    def escape(self) -> None:
        self.answer(None)


class TextPrompt(Vertical):
    """questionary.text: the default is editable in place, in a field. It validates as you type
    (a ✓ or a quiet reason beside the field); Enter on an answer that doesn't pass shows the reason
    in a bar under it, shakes, and isn't accepted. The answer goes on its own line under the
    question, where a select's choices go."""

    DEFAULT_CSS = f"""
    TextPrompt {{ height: auto; }}
    TextPrompt > Horizontal {{ height: 1; }}
    TextPrompt .pointer {{ width: 3; }}
    TextPrompt Input, TextPrompt Input:focus {{ width: 1fr; max-width: 44; height: 1; border: none;
                                               padding: 0 1; background: {PANEL}; color: {TEXT}; }}
    TextPrompt Input > .input--cursor {{ background: {SKY}; color: {INK}; text-style: bold; }}
    TextPrompt .status {{ width: 1fr; padding: 0 0 0 2; }}
    TextPrompt .err {{ display: none; padding: 0 0 0 3; }}
    """

    def __init__(self, message: str, default: str, validate, hint: str = "") -> None:
        super().__init__()
        self.message, self.default, self.validate_fn, self.hint = message, default, validate, hint
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()

    def compose(self) -> ComposeResult:
        yield Static(question_line(self.message, self.hint))
        with Horizontal():
            yield Static(Text(" ❯ ", POINTER), classes="pointer")
            yield Input(self.default, compact=True, select_on_focus=False)
            yield Static(classes="status")
        yield Static(classes="err")

    def on_mount(self) -> None:
        fade_in(self)
        self.check(self.default)

    def keys(self) -> list[tuple[str, str]]:
        return [("enter", "accept"), ("esc", "cancel")]

    def check(self, value: str):
        """Validates value, shows the outcome beside the field, and returns it."""
        result = self.validate_fn(value) if self.validate_fn else True
        status = self.query_one(".status", Static)
        if not self.validate_fn:
            status.update("")
        elif result is True:
            status.update(Text("✓", st(MINT, bold=True)))
        else:
            status.update(Text.assemble(("✗ ", st(RED)), (str(result), st(FAINT, italic=True))))
        return result

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        result = self.check(event.value)
        if result is True:
            if not self.future.done():
                self.future.set_result(event.value)
            return
        err = self.query_one(".err", Static)
        err.update(Text(f" {result} ", VALIDATION))
        err.display = True
        shake(self.query_one(Horizontal))
        self.app.bell()

    def on_input_changed(self, event: Input.Changed) -> None:
        self.query_one(".err").display = False
        self.check(event.value)

    def escape(self) -> None:
        if not self.future.done():
            self.future.set_exception(Cancel())


def prompt_for(
    question: Question,
) -> tuple[Prompt | TextPrompt, Callable[[Any], str | bool]]:  # the answer is per kind: an index, text or bool
    """A gateway's question as the widget that asks it, as its kind asks it (a select, Yes/No, a
    text field, or a name to type), and how that widget's answer becomes the one the operation
    takes: a select answers with its choice's index, the operation wants the choice's value.
    For a screen that asks without home's Console (the run window); home's own flows ask through
    Console, which also leaves the answered line behind (operations.ask)."""
    if question.kind == QuestionKind.TEXT and len(question.confirms) == 1:
        return TypeToConfirm(question.message, question.confirms[0], question.hint), str
    if question.kind == QuestionKind.TEXT:
        check = question.validate
        return TextPrompt(
            question.message, str(question.default or ""), check and (lambda text: check(text) or True), question.hint
        ), str
    if question.kind == QuestionKind.CONFIRM:
        return ConfirmPrompt(question.message, question.default is not False, question.hint), bool
    choices = list(question.choices)
    return (
        SelectPrompt(question.message, choices, hint=question.hint, hints=question.hints, default=question.default),
        lambda k: choices[k][1],
    )
