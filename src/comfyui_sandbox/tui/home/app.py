"""Home: the screen comfyui-sandbox opens on with no command. SandboxApp is the screen (the
hero, the content column, the bottom bar) and its keys; the flows (flows.py) drive what's on it
through a Console. Everything it shows or does goes through the Gateway it's given."""

from __future__ import annotations

import dataclasses
import time

from rich.console import Console as RichConsole
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widget import Widget

from ..crash import crash_of
from ..gateway import Gateway, GpuReading, Sandbox
from ..links import TracksHover, file_uri_path
from ..pacing import Pacer, WakesOnInput
from ..press_twice import armed_line
from ..states import SandboxState
from ..theme import AMBER, INK, MUTED, PANEL, SKY, TERMINAL, TEXT, blend, st
from .chrome import HERO_H, BottomBar, Hero, Sampler
from .console import Console
from .flows import home
from .log_views import OpLog
from .model import Interrupt, Operation, Tracked
from .picker import HomePrompt
from .prompts import Prompt, TextPrompt
from .splash import SHOW_SPLASH, Splash, play_splash

PULSE_IDLE_HZ = 1.0  # the GPU reading and layout check, when no operation runs: the GPU meter changes slowly
# How often home reads the sandboxes again on its own (spec: "every few seconds while idle"), so a
# sandbox started, stopped, installed or removed from another terminal, or by another home, shows
# without a key. Often enough that a Start elsewhere is seen within moments; seldom enough that
# the gateway's reads (a file, a look at each port) cost nothing noticeable.
POLL_SECONDS = 3.0

# The warning Ctrl+C's first press gives while an operation runs: a second press quits home, and
# the operation is stopped where it is (spec: Ctrl+C twice quits, during any operation). One that
# Esc cancels (an install) says so, since that's the way to stop it and stay.
QUIT_WARNING = "Press Ctrl+C again to quit. This can't be cancelled, so it's left unfinished."
QUIT_WARNING_ESC = "Press Ctrl+C again to quit, leaving it unfinished. Esc twice cancels it instead."


class SandboxApp(WakesOnInput, TracksHover, App):
    """The screen: one column of printed lines that the flows clear and redraw, like a console
    on the alternate screen, framed by the bar and the hero."""

    CSS = f"""
    Screen {{ background: {INK}; color: {TEXT}; padding: 0; }}
    #bottom {{ height: 1; background: {PANEL}; dock: bottom; }}
    #hero {{ height: {HERO_H}; }}
    #body {{ height: 1fr; padding: 0 0 1 0; }}  /* a blank row above the bar, always */
    #out {{ height: 1fr; width: 1fr; scrollbar-size: 0 0; padding: 1 2 0 2; }}
    #out > Static {{ height: auto; }}
    #fullscreen {{ height: 1fr; width: 1fr; padding: 1 2 0 2; }}
    """
    TITLE = "comfyui-sandbox"
    BINDINGS = [
        Binding("ctrl+c", "interrupt", show=False, priority=True),
        Binding("escape", "escape", show=False, priority=True),
        Binding("l", "toggle_logs", show=False),
    ]

    def __init__(self, gateway: Gateway, intro: bool = False) -> None:
        super().__init__(ansi_color=False)  # truecolor: the palette, not the terminal's colours
        self.gateway = Tracked(gateway)  # notes what it runs: quitting stops it (stop_running)
        # The TUI's own records of the sandboxes, one per sandbox for the whole session (refresh).
        self.sandboxes: list[Sandbox] = []
        # The states shown in place of what the gateway says while the TUI's own command acts on a
        # sandbox (installing, stopping...), by name (mark).
        self.marks: dict[str, SandboxState] = {}
        self.gpu: GpuReading | None = None  # the latest GPU reading; None: no meters
        self.intro = intro  # play the splash first
        self.splashing = False  # the splash is up, in the hero's and the screen's place
        self.op: Operation | None = None  # the running operation's state, until the next screen
        self.prompt: Prompt | TextPrompt | None = None  # the question up now, which the keys go to
        self.interrupted = False  # a key cancelled what's running: the next Console.nap() raises it
        self.hovered: str | None = None
        self.started = time.monotonic()
        self.fullscreen = False  # a full-screen view is up, over the hero and the screen's lines
        self.overlay: Prompt | None = None  # its view, when it's up (open_overlay)
        self.overlay_prompt: Prompt | TextPrompt | None = None  # the question behind it
        self.overlay_focus: Widget | None = None  # what had focus behind it
        self.keys: list[tuple[str, str]] = []  # the bottom bar's keys when no question is up
        self.header_rows = 0  # rows the last Console.draw() used; the log views size themselves to fit below it
        self.reads_started = 0  # read_sandboxes() calls begun, and the latest one whose listing was used:
        self.read_applied = 0  # an older listing that arrives late mustn't undo a newer one
        self.polling = False  # a poll's read is under way (poll)
        self.quitting = False  # home is quitting, once what's running has stopped (quit_unfinished)

    def compose(self) -> ComposeResult:
        yield BottomBar(id="bottom")
        yield Hero(id="hero")
        with Horizontal(id="body"):
            yield VerticalScroll(id="out", can_focus=False)

    def on_mount(self) -> None:
        self.ansi_theme_dark = TERMINAL  # the flows' [green], [red]... markup lands on the palette
        # Held, not looked up on each tick: the app's own timers (pulse) can fire while Textual is
        # taking the screen down on exit, when a lookup finds nothing and would crash home.
        # (Inferred from the code as the cause of the flaky NoMatches 'Hero', never reproduced; if
        # it recurs this may be wrong and can be refactored freely: CONTRIBUTING.md, "Tests that can
        # fail under load".)
        self.hero, self.body = self.query_one(Hero), self.query_one("#body", Horizontal)
        self.out = self.query_one("#out", VerticalScroll)
        self.gpu = self.gateway.gpu()
        self.chrome()
        # The pulse runs at 5 Hz while an operation does, and at PULSE_IDLE_HZ otherwise.
        Pacer(self, self.pulse, lambda: self.op is not None, busy_hz=5, idle_hz=PULSE_IDLE_HZ).start()
        self.set_interval(POLL_SECONDS, self.poll)
        self.run_worker(self.main_flow(), exit_on_error=True)

    def on_unmount(self) -> None:
        # However home ends (Quit, Ctrl+C, a crash), the gateway stops what it runs for home's
        # sake: the real one's GPU readings come from a process that would otherwise outlive it.
        self.gateway.close()

    async def main_flow(self) -> None:
        """The whole session: the splash if wanted, then home until it returns. Either way, an
        operation still running is stopped first (stop_running).

        Quit exits 0, and so does Ctrl+C (Interrupt) with nothing running: quitting an idle home
        is no failure, and wrappers and shortcuts mustn't report one (spec: quitting home exits
        0). Ctrl+C that leaves an operation unfinished exits 130, as an interrupted command does."""
        try:
            if self.intro:
                await play_splash(self)
            await self.read_sandboxes()
            await home(Console(self), self.sandboxes)
            code = 0
        except Interrupt:
            code = 130 if self.gateway.running() else 0
        await self.stop_running()
        self.exit(return_code=code)

    async def stop_running(self) -> None:
        """Cancels every operation still running, and waits until each has unwound: its cleanup
        (releasing the keepalive, stopping the container) runs before home is gone, and nothing
        it started is left running unseen (spec: quitting stops its wsl.exe processes)."""
        running = self.gateway.running()
        if running:
            self.quitting = True
        for handle in running:
            handle.cancel()
        for handle in running:
            await handle.wait()

    async def quit_unfinished(self) -> None:
        """Ctrl+C's second press while an operation runs: home quits (130), once it has stopped."""
        await self.stop_running()
        self.exit(return_code=130)

    def quit_notice(self) -> Text | None:
        """The line a running operation's screen shows under it about quitting, if any: the
        warning after Ctrl+C's first press, or, once quitting, that home waits for it to stop."""
        if self.quitting:
            return Text.assemble(
                ("▲ ", st(AMBER, bold=True)),
                ("Quitting once it has stopped…", st(AMBER, bold=True)),
                ("  Ctrl+C quits now, without waiting.", st(MUTED)),
            )
        if self.op is not None and self.op.armed.key() == "Ctrl+C":
            return armed_line(QUIT_WARNING_ESC if self.op.cancel_keys else QUIT_WARNING, self.op.armed)
        return None

    def poll(self) -> None:
        """The poll timer: reads the sandboxes again, in the background, unless a poll's read is
        still under way (a slow gateway gets no pile of reads behind it). Nothing is polled
        before main_flow's first read, which the splash may delay.

        It polls whatever the flows are doing: the TUI's own operation shows its sandbox's
        marked state whatever is read meanwhile (mark), and the other sandboxes may change
        under it like any time. A failing read crashes home like the first one would, so a
        broken installs.json is reported, not hidden behind a stale table."""
        if self.polling or not self.reads_started:
            return
        self.polling = True

        async def read() -> None:
            try:
                await self.read_sandboxes()
            finally:
                self.polling = False

        self.run_worker(read(), exit_on_error=True)

    async def read_sandboxes(self) -> None:
        """Reads the sandboxes again, into the same records: a sandbox keeps its record (so the
        pointer, an open menu and the table, which go by identity, stay on it), a new one gets
        one, and one that's gone loses its own. The list itself stays the same object, so the
        screens holding it see the change. Marked states are put back on top.

        Reads may overlap (a poll, and a flow's read once its operation ends): a listing that
        comes back after a newer one has been used is dropped, so the table never steps back."""
        self.reads_started += 1
        mine_started = self.reads_started
        listing = await self.gateway.sandboxes()
        if mine_started < self.read_applied:
            return
        self.read_applied = mine_started
        names_before = [s.name for s in self.sandboxes]
        mine = {s.name: s for s in self.sandboxes}
        fresh = []
        for listed in listing:
            record = mine.get(listed.name)
            if record is None:
                record = listed
            else:
                for f in dataclasses.fields(Sandbox):
                    setattr(record, f.name, getattr(listed, f.name))
            if listed.name in self.marks:
                record.state = self.marks[listed.name]
            fresh.append(record)
        self.sandboxes[:] = fresh
        if [s.name for s in fresh] != names_before and isinstance(self.prompt, HomePrompt):
            self.prompt.resync()  # home's picker has a row per sandbox: it gains or loses one

    def mark(self, sandbox: Sandbox, state: SandboxState) -> None:
        """Shows sandbox as state (installing, stopping...) while the TUI's own command acts on
        it, whatever the gateway says meanwhile, until unmark: the gateway reports only what it
        can see (spec: the state rule), not what the TUI is in the middle of."""
        self.marks[sandbox.name] = sandbox.state = state

    def unmark(self, sandbox: Sandbox) -> None:
        """The TUI's command on sandbox is over: the next refresh shows what the gateway says."""
        self.marks.pop(sandbox.name, None)

    def begin_operation(self) -> Operation:
        """Starts an operation's state, replacing the last one's."""
        self.op = Operation()
        return self.op

    @property
    def sampler(self) -> Sampler | None:
        """The running (or just ended) operation's sampler, if it has one."""
        return self.op.sampler if self.op is not None else None

    def armed_key(self) -> str | None:
        """The cancel key pressed once and waiting for its second press, if any."""
        return self.op.armed.key() if self.op is not None else None

    def cancels_with(self, key: str) -> bool:
        """Whether key cancels the running operation."""
        return self.op is not None and key in self.op.cancel_keys

    def has_op_log(self) -> bool:
        """Whether there is an operation's output to read (L)."""
        return self.op is not None and self.op.op_log is not None

    def pulse(self) -> None:
        """The app's own timer: reads the GPU and re-checks the layout (the window may have changed size)."""
        self.gpu = self.gateway.gpu()
        self.chrome()

    # -- layout ----------------------------------------------------------------------------------

    def chrome(self) -> None:
        """Shows the hero when the window has room for it and nothing is full screen; the splash
        takes the place of both it and the screen's lines."""
        self.hero.display = self.tall() and not self.fullscreen and not self.splashing
        self.body.display = not self.splashing

    def on_resize(self) -> None:
        if hasattr(self, "out"):  # the first Resize arrives before on_mount
            self.chrome()

    def tall(self) -> bool:
        """Whether the hero fits, leaving the screen's content its rows."""
        return self.size.height >= 24 and self.size.width >= 64

    def cols(self) -> int:
        """The content column's width (inside its padding)."""
        return max(20, self.size.width - 4)

    def lines(self) -> int:
        """The content column's height: the window less the bar, the blank row above it, the hero and
        the top padding."""
        return max(8, self.size.height - 2 - (HERO_H if self.tall() else 0) - 1)

    async def full_screen(self, widget: Prompt):
        """Asks widget over everything: the hero and the screen's lines are hidden until it answers."""
        widget.id = "fullscreen"
        self.fullscreen = True
        self.chrome()
        self.out.display = False
        self.prompt = widget
        await self.body.mount(widget)
        widget.focus()
        try:
            return await widget.future
        finally:
            self.prompt = None
            await widget.remove()
            self.fullscreen = False
            self.out.display = True
            self.chrome()

    def open_overlay(self, view: Prompt) -> None:
        """Puts view over everything without waiting on it: whatever was running carries on, and
        questions it asks meanwhile wait behind the view (see Console.ask) until close_overlay."""
        if self.overlay is not None:
            return
        self.overlay, self.overlay_prompt, self.overlay_focus = view, self.prompt, self.focused
        view.id = "fullscreen"
        self.fullscreen = True
        self.chrome()
        self.out.display = False
        self.prompt = view
        self.body.mount(view)
        self.call_after_refresh(view.focus)

    def close_overlay(self) -> None:
        view = self.overlay
        if view is None:
            return
        self.overlay = None
        view.remove()
        self.prompt = self.overlay_prompt
        self.out.display = True
        self.fullscreen = False
        self.chrome()
        focus = self.overlay_focus
        if focus is not None and focus.is_attached:
            focus.focus()
        else:
            self.set_focus(None)  # so L reaches the app

    # -- keys and the mouse ----------------------------------------------------------------------

    def action_interrupt(self) -> None:
        if self.quitting:  # waiting for an operation to stop: never trapped, a press quits now
            self.exit(return_code=130)
        elif self.splashing:  # nothing's started: Ctrl+C quits, as Quit does (main_flow)
            self.exit(return_code=0)
        elif isinstance(self.prompt, Prompt):
            self.prompt.interrupt()
        elif self.prompt is not None and not self.prompt.future.done():
            self.prompt.future.set_exception(Interrupt())
        else:
            self.interrupt_running()

    def interrupt_running(self) -> None:
        """Ctrl+C with no question up: quits on the second press while an operation runs."""
        op = self.op
        if op is not None and self.gateway.running():
            # An operation is running (a short action under its spinner, or steps under the live
            # view). Ctrl+C quits, as in any terminal program, but on the second press, since
            # quitting leaves it unfinished; the first one warns. It never cancels: Esc twice does
            # that for an operation that can be cancelled (an install), and home stays.
            if op.armed.press("Ctrl+C"):
                self.run_worker(self.quit_unfinished(), exit_on_error=True)
        else:
            self.interrupted = True  # the next nap() raises it

    def action_escape(self) -> None:
        """Back out one level: a question takes it as its way out; running steps are cancelled
        as with Ctrl+C. The splash takes it as any key. Anything else (a spinner, home, an
        uninstall) ignores it."""
        if self.splashing:
            self.query_one(Splash).finish()
        elif self.prompt is not None and not self.prompt.future.done():
            self.prompt.escape()
        elif self.cancels_with("Esc"):
            self.press_to_cancel("Esc")

    def press_to_cancel(self, key: str) -> None:
        """A running operation cancels on the second press of the same key, so a stray key
        doesn't throw away work. The first press only asks (see PressTwice)."""
        if self.op is not None and self.op.armed.press(key):
            self.interrupted = True  # the next nap() raises it

    def action_toggle_logs(self) -> None:
        """L: the last operation's full output, over everything (OpLog closes it again)."""
        entry = self.op.op_log if self.op is not None else None  # what has_op_log() looks at
        if entry is not None and self.overlay is None:
            self.open_overlay(OpLog(*entry))

    def on_click(self, event: events.Click) -> None:
        self.open_link(event)  # clicks on printed lines (a result, a log path) bubble up to here

    def open_link(self, event: events.Click) -> bool:
        """Opens the link under the click, if there is one, and says whether there was."""
        target = event.style.link
        if not target:
            return False
        event.prevent_default()
        event.stop()
        if target.startswith("file:") and not file_uri_path(target).exists():
            self.bell()  # the demo writes no files, so its logs and backups aren't there
        else:
            self.open_url(target)
        return True


def run_home(gateway: Gateway, intro: bool = False, *, headless: bool = False) -> int:
    """Runs home on gateway in this terminal, and returns its exit code: 0 on Quit, 130 on Ctrl+C.
    Back on the normal screen, it leaves one line behind instead of the whole session.

    A crash inside home is raised, once Textual has closed and restored the terminal, so the CLI
    reports it like any other crash (a transcript with the traceback, exit 1). Left to itself,
    Textual would only print the traceback and end with exit code 1. `headless` runs it with no
    terminal at all, for tests.
    """
    app = SandboxApp(gateway, intro=SHOW_SPLASH or intro)
    app.run(headless=headless)
    crash = crash_of(app)
    if crash is not None:
        raise crash
    sandboxes = app.sandboxes
    running = sum(s.state == SandboxState.RUNNING for s in sandboxes)
    RichConsole(highlight=False).print(
        Text.assemble(
            ("◆ ", st(SKY, bold=True)),
            blend("comfyui-sandbox", bold=True),
            (f"  {len(sandboxes)} sandbox{'es' * (len(sandboxes) != 1)}, {running} running", st(MUTED)),
        )
    )
    return app.return_code or 0
