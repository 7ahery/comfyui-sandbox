"""The console API the flows use: Rich's console and questionary, on Textual. The flows print
lines, ask questions and wait, as a plain CLI would; the screen is one column of those lines,
which each menu clears and redraws, so the table and the question stay in place and only their
contents change."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING

from rich.console import Group
from rich.text import Text
from textual.widget import Widget
from textual.widgets import Input

from ..gateway import OperationHandle, Result, Sandbox
from ..measure import rows
from ..parts import fmt, spinner
from ..theme import AMBER, FAINT, GREEN, MUTED, SOFT, TEXT, st
from .model import Interrupt
from .picker import HomePrompt
from .prompts import ANSWER, ConfirmPrompt, PausePrompt, SelectPrompt, TextPrompt, TypeToConfirm
from .table import sandboxes_table, spins
from .widgets import Live, Printed

if TYPE_CHECKING:
    from .app import SandboxApp

# Seconds between looks at a running operation: often enough that its lines and steps seem to
# arrive as they're printed, and that a cancel key is acted on at once.
WAIT_TICK = 0.03


class Console:
    """What a flow holds: print (say, show_live), ask (select, text, confirm...), wait (nap, spin,
    wait) and draw a new screen."""

    def __init__(self, app: SandboxApp) -> None:
        self.app = app
        self.notes: list[Text] = []  # the last action's result, shown under the table on the next draw
        self.last_picked = None  # home's last choice, so coming back keeps your place in a long list

    # -- printing --------------------------------------------------------------------------------

    async def say(self, *renderables) -> None:
        """console.print."""
        await self.app.out.mount_all([Printed(Text.from_markup(r) if isinstance(r, str) else r) for r in renderables])
        self.app.out.scroll_end(animate=False)

    async def show_live(self, source, moving=lambda: True) -> Live:
        """Prints a line that redraws itself. source: what to draw each frame. moving: whether it is
        changing fast (see Live)."""
        widget = Live(source, moving)
        await self.app.out.mount(widget)
        self.app.out.scroll_end(animate=False)
        return widget

    async def clear(self) -> None:
        """console.clear."""
        self.app.hovered = None  # what was under the mouse is gone
        await self.app.out.remove_children()

    async def echo(self, argv: Sequence[str]) -> None:
        """Every menu action shows the command line it runs (argv, as the gateway was given it),
        then a blank line: what's shown is what ran, so it can be repeated in a script."""
        await self.say(
            Text.assemble(("❯ ", st(FAINT)), ("comfyui-sandbox ", st(FAINT)), (" ".join(argv), st(MUTED))), Text("")
        )

    def flash(self, item) -> None:
        """Notes the last action's result, with a bar coloured by how it went."""
        item = Text.from_markup(item) if isinstance(item, str) else item
        plain = item.plain.strip().lower()
        color = (
            GREEN
            if plain.startswith("✓")
            else AMBER
            if "⚠" in plain or ("cancelled" in plain and "install" in plain)
            else FAINT
        )
        self.notes.append(Text.assemble(("▍ ", st(color)), item))

    def take_notes(self) -> list[Text]:
        """The notes flashed since the last screen, with a blank line under them, and forgets them."""
        notes = [*self.notes, Text("")] if self.notes else []
        self.notes.clear()
        return notes

    async def draw(self, sandboxes: list[Sandbox], *, table: bool = True, selected: Sandbox | None = None) -> None:
        """A new screen: the table and the notes. selected: the opened sandbox, the only row the
        table shows. table=False leaves the table to the caller (home's picker draws it). The
        table is live, so it follows state changes while a flow's screen is up."""
        app = self.app
        op = app.op
        if op is not None:
            # Operation.finish kept op_log and the sampler for this moment, the next screen: the
            # outcome has been on show, so L has nothing to open now and the logo may re-form.
            op.op_log = None  # L is for the operation on screen; a new screen has none yet
            if app.sampler is not None:
                app.sampler.release()  # its outcome has been read: the logo re-forms (the hero then drops op)
            else:
                app.op = None
        notes = self.take_notes()

        def screen() -> Group:
            # Once the selected sandbox is gone (uninstalled), there's no row left to show, and
            # the table's header alone would be an empty table: it goes too.
            shown = table and (selected is None or selected in sandboxes)
            top = [sandboxes_table(sandboxes, app.cols(), only=selected, highlight=selected), Text("")] if shown else []
            return Group(*top, *notes)

        await self.clear()
        if table or notes:
            await self.show_live(screen, lambda: spins(sandboxes))
            app.header_rows = rows(screen(), app.cols())
        else:
            app.header_rows = 0

    # -- waiting ---------------------------------------------------------------------------------

    async def nap(self, seconds: float) -> None:
        """Waits, raising Interrupt as soon as a key cancels what's running."""
        end = time.monotonic() + seconds
        while True:
            if self.app.interrupted:
                self.app.interrupted = False
                raise Interrupt
            left = end - time.monotonic()
            if left <= 0:
                return
            await asyncio.sleep(min(0.03, left))

    async def wait(
        self,
        handle: OperationHandle,
        each: Callable[[], object] = lambda: None,
        until: Callable[[], bool] = lambda: False,
    ) -> Result | None:
        """Waits for handle's operation to end, calling each() every tick (and once more at the
        end) to show its progress. A key that cancels raises Interrupt, as nap() does: the caller
        decides what cancelling means (cancel the operation, or quit). Returns None, still
        running, as soon as until() holds (it waits on a question, say)."""
        while handle.result is None:
            each()
            if until():
                return None
            await self.nap(WAIT_TICK)
        each()
        return handle.result

    async def spin(
        self,
        message: str,
        handle: OperationHandle,
        each: Callable[[], object] = lambda: None,
        until: Callable[[], bool] = lambda: False,
    ) -> Result | None:
        """with console.status(message): wait(handle, each, until). Under the spinner: what Ctrl+C
        would do now, once it has been pressed (SandboxApp.quit_notice)."""
        started = time.monotonic()

        def frame():
            line = Text.assemble(
                spinner(), " ", (message, st(TEXT)), "  ", (fmt(time.monotonic() - started), st(FAINT))
            )
            notice = self.app.quit_notice()
            return line if notice is None else Group(line, notice)

        widget = await self.show_live(frame)
        try:
            return await self.wait(handle, each, until)
        finally:
            await widget.remove()

    # -- asking ----------------------------------------------------------------------------------

    async def ask(self, widget, focus: Widget | None = None):
        """Mounts a question and waits for its answer."""
        app = self.app
        behind = app.overlay is not None  # the overlay keeps the keys; this waits until it closes
        if behind:
            app.overlay_prompt, app.overlay_focus = widget, focus or widget
        else:
            app.prompt = widget
        await app.out.mount(widget)
        app.out.scroll_end(animate=False)
        if not behind:
            (focus or widget).focus()
        try:
            return await widget.future
        finally:
            if app.overlay is not None:
                app.overlay_prompt = app.overlay_focus = None
            else:
                app.prompt = None

    async def answered(self, widget, message: str, answer: str) -> None:
        """Replaces an answered question with a line saying what was answered."""
        await widget.remove()
        await self.say(Text.assemble(("◆ ", st(FAINT)), (message, st(SOFT)), "  ", (answer, st(ANSWER, bold=True))))

    async def select(
        self,
        message: str,
        choices: list,
        *,
        crumb: str | None = None,
        hint: str = "",
        hints: Mapping[str, str] | None = None,
        default=None,
    ):
        """questionary.select. choices: (title, value) pairs; None is a separator. With crumb, the
        answer stays as a breadcrumb ("comfyui › Update") instead of the answered question, for a
        menu whose pick leads to another question. hint, hints and default: see SelectPrompt."""
        widget = SelectPrompt(message, choices, hint=hint, hints=hints, default=default)
        index = await self.ask(widget)
        title, value = choices[index]
        title = title.plain if isinstance(title, Text) else title
        if crumb is None:
            await self.answered(widget, message, title)
        else:
            await widget.remove()
            short = re.split(r"…| \(", title)[0]  # "Update… (update available)" -> "Update"
            await self.say(
                Text.assemble((crumb, st(TEXT, bold=True)), (" › ", st(FAINT)), (short, st(ANSWER, bold=True))), ""
            )  # a blank line under it
        return value

    async def pick_home(self, sandboxes: list[Sandbox], actions: list, notes: list):
        """Home's picker. Returns the picked sandbox, or the picked action's value. The next
        screen clears it."""
        widget = HomePrompt("Pick a sandbox or an action", sandboxes, actions, notes)
        widget.index = next(
            (
                k
                for k, c in enumerate(widget.choices)
                if c is self.last_picked or (isinstance(c, tuple) and c == self.last_picked)
            ),
            widget.index,
        )
        widget.follow()
        index = await self.ask(widget)
        # Looked up once answered, not before: a poll may have changed the rows meanwhile (resync).
        choice = widget.choices[index]
        self.last_picked = choice
        return choice if isinstance(choice, Sandbox) else choice[1]

    async def text(self, message: str, default: str = "", validate=None, *, hint: str = "") -> str:
        """questionary.text. validate(text) returns True, or why the text won't do."""
        app = self.app
        widget = TextPrompt(message, default, validate, hint)
        app.prompt = widget
        await app.out.mount(widget)
        app.out.scroll_end(animate=False)
        widget.query_one(Input).focus()
        try:
            value = await widget.future
        finally:
            app.prompt = None
        await self.answered(widget, message, value)
        return value

    async def confirm(self, message: str, default: bool = True, *, hint: str = "") -> bool:
        """questionary.confirm: Yes and No as pills; the default starts lit, so Enter takes it."""
        widget = ConfirmPrompt(message, default, hint)
        value = await self.ask(widget)
        await self.answered(widget, message, "Yes" if value else "No")
        return value

    async def type_to_confirm(self, message: str, name: str, *, hint: str = "") -> str:
        """A type-the-name confirmation: name has to be typed to confirm a destructive action.
        Returns what was typed (only the whole name gets through; Esc raises Cancel)."""
        widget = TypeToConfirm(message, name, hint)
        typed = await self.ask(widget)
        await self.answered(widget, message, typed)
        return typed

    async def pause(self) -> None:
        """questionary.press_any_key_to_continue: the line stays, a blank line under what it follows."""
        await self.say("")
        await self.ask(PausePrompt("Press any key to go back"))
