"""The run window: what a "ComfyUI (<distro>)" shortcut and home's "Start (in its own window)"
open (`comfyui-sandbox run` at a terminal). The banner and loader while ComfyUI starts and runs, the
full log on L, and a standing note that closing the window stops ComfyUI. It runs `run` through the
Gateway it's given and shows what its operation handle reports, and asks the questions `run` asks
(a port another program took) with home's question widgets, under the loader.

It's only a front end: the run's lifecycle (the run-window marker, the keepalive, `compose stop` when
the window ends, closing the console) is the core `run` command's, which the real gateway runs
in-process. The window's own part is to show it, to ask `run` to stop (Ctrl+C twice, the handle's
cancel), and never to leave it running behind it (`on_unmount`, `run_window`)."""

from __future__ import annotations

import asyncio
import sys
import time

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.geometry import Size
from textual.widget import Widget
from textual.widgets import Input, Static
from textual.worker import Worker

from ..crash import crash_of
from ..gateway import Gateway, OperationHandle, Question, Result, Sandbox
from ..home.model import Cancel, Interrupt
from ..home.prompts import Prompt, TextPrompt, prompt_for
from ..links import TracksHover
from ..pacing import Pacer, WakesOnInput
from ..press_twice import PressTwice
from ..states import LaunchPhase
from ..theme import AMBER, INK, PANEL, TERMINAL, TEXT, st
from .launch import Launch
from .view import Banner, BottomBar, LogPane, Summary, banner_rows, banner_scale, banner_top

DOT_HZ = 10.0  # the live dot's line while it shows: its 1.6 s breath gets ~16 steps
IDLE_HZ = 2.0  # ComfyUI is up and nothing changes: only the live dot breathes
FOLLOW_TICK = 0.02  # seconds between looks at `run`'s progress: under a 20 Hz frame
ELSEWHERE_CLOSE = 4.0  # seconds a second window stays up, saying the first one has it, before it closes
# How long run_window waits, once the window has gone without `run` having ended (a crash), for the
# cancelled `run` to stop ComfyUI: `compose stop` takes up to two minutes (the core's STOP_TIMEOUT).
UNWIND_SECONDS = 150.0


class Unasked(Exception):
    """A question taken down unanswered: `run` no longer waits on it (it was cancelled, or ended)."""


class LaunchApp(WakesOnInput, TracksHover, App):
    """The run window: one launch of sandbox (a Launch, following `run`'s operation handle) shown
    by the banner, the loader under it, or on L the full log."""

    CSS = f"""
    Screen {{ background: {INK}; color: {TEXT}; }}
    #bottom {{ dock: bottom; height: 1; background: {PANEL}; }}
    #summary {{ height: 1fr; padding: 0 0 1 0; }}
    #logs {{ height: 1fr; padding: 1 2 1 2; display: none; }}
    #ask {{ height: auto; padding: 0 2 1 2; display: none; }}
    #ask > Static {{ height: auto; }}
    """
    BINDINGS = [
        Binding("ctrl+c", "interrupt", show=False, priority=True),
        Binding("l", "toggle_logs", show=False),
        Binding("escape", "escape", show=False),
        Binding("enter", "close", show=False),
        Binding("r", "replay", show=False),
        Binding("up", "scroll(-1)", show=False),
        Binding("down", "scroll(1)", show=False),
        Binding("pageup", "page(-1)", show=False),
        Binding("pagedown", "page(1)", show=False),
        Binding("home", "top", show=False),
        Binding("end", "follow", show=False),
    ]

    def __init__(self, gateway: Gateway, sandbox: Sandbox, *, replay: bool = False) -> None:
        """replay: R starts the launch over (the demo's key, not the design's: on a real machine
        it would race a second `run` against the first one's cleanup and its marker)."""
        super().__init__(ansi_color=False)
        self.gateway, self.sandbox, self.replay = gateway, sandbox, replay
        self.logs = False  # the full log is up
        self.closing = False
        self.seen: tuple | None = None  # what moving() saw last
        self.armed = PressTwice()  # Ctrl+C pressed once
        self.hovered: str | None = None
        self.launch = self.new_launch()
        self.handle: OperationHandle | None = None  # `run`, as the gateway runs it
        self.follow_task: Worker | None = None  # what keeps the launch up with it
        self.ended: Result | None = None  # how the last `run` ended: the window's exit code is its
        self.asked: Question | None = None  # the question of `run`'s being asked now (ask)
        self.prompt: Prompt | TextPrompt | None = None  # its widget, which the keys go to

    def compose(self) -> ComposeResult:
        yield BottomBar(id="bottom")
        yield Banner()
        yield Summary(id="summary")
        yield LogPane(id="logs")
        yield Vertical(id="ask")

    def on_mount(self) -> None:
        self.ansi_theme_dark = TERMINAL
        # Held, not looked up on each tick: the Pacers' ticks can fire while Textual is taking the
        # window down on exit, when a lookup finds nothing and would crash the window.
        # (Inferred from the code as the cause of the flaky NoMatches 'Banner', never reproduced; if
        # it recurs this may be wrong and can be refactored freely: CONTRIBUTING.md, "Tests that can
        # fail under load".)
        self.banner, self.summary = self.query_one(Banner), self.query_one(Summary)
        self.log_pane, self.bottom = self.query_one(LogPane), self.query_one(BottomBar)
        self.layout_banner()
        # The window's frame: the banner, the loader, the log and the bar, 20 Hz while ComfyUI loads
        # or anything changes, IDLE_HZ once it is up and quiet (it can sit that way for hours).
        self.pacer = Pacer(self, self.frame, self.moving, busy_hz=20, idle_hz=IDLE_HZ)
        self.pacer.start()
        # The dot breathes (1.6 s), so its line is drawn ten times a second whether or not the
        # rest of the window moves; the banner and the log keep the idle rate. A second Pacer, so
        # the dot's smoothness costs the loader's redraw, not the whole window's frame at 10 Hz.
        self.dot_pacer = Pacer(self, self.draw_dot, self.dot_shown, busy_hz=DOT_HZ, idle_hz=IDLE_HZ)
        self.dot_pacer.start()
        self.start()

    def new_launch(self) -> Launch:
        return Launch(self.sandbox.name, self.sandbox.port, self.sandbox.models)

    def start(self) -> None:
        """Runs `run` for the sandbox (again, after R), and follows it into the launch."""
        # Interactive, as at a terminal: a question `run` asks (a port another program took) is
        # asked here (ask), rather than ending the window with exit 9 and a flag to type.
        self.handle = self.gateway.run(["run", "--distro", self.sandbox.name])
        self.launch.follow(self.handle.progress, None)  # its plan is drawn from the first frame
        # A worker, not a bare task: an error in it is the window's crash (run_window raises it),
        # where a bare task's would be lost and leave the window up, no longer following `run`.
        self.follow_task = self.run_worker(self.follow(self.launch, self.handle), exit_on_error=True)

    async def follow(self, launch: Launch, handle: OperationHandle) -> None:
        """Keeps launch up with handle until `run` ends. A second window, which only opened the
        browser, closes on its own a few seconds later."""
        while handle.result is None:
            launch.follow(handle.progress, None)
            self.keep_asking(handle)
            await asyncio.sleep(FOLLOW_TICK)
        self.keep_asking(handle)
        launch.follow(handle.progress, handle.result)
        self.ended = handle.result
        if launch.phase == LaunchPhase.ELSEWHERE:
            self.set_timer(ELSEWHERE_CLOSE, self.exit)

    def keep_asking(self, handle: OperationHandle) -> None:
        """Keeps the question on screen the one `run` waits on: a new one is asked (ask), and one
        it no longer waits on (it was cancelled, Ctrl+C twice, or it ended) goes."""
        question = handle.progress.question
        if question is not self.asked:
            self.drop_question()
        if question is not None and question is not self.asked and handle.result is None:
            self.asked = question
            self.run_worker(self.ask(handle, question), exit_on_error=True)

    def drop_question(self) -> None:
        """Takes down the question on screen without answering it: `run` no longer waits on it."""
        if self.prompt is not None and not self.prompt.future.done():
            self.prompt.future.set_exception(Unasked())  # ask() then takes it down

    async def ask(self, handle: OperationHandle, question: Question) -> None:
        """Asks `run`'s question under the loader, as home asks one (its widgets, hints and keys),
        and answers it. Backing out (Esc) cancels `run`, which ends as interrupted before anything
        has started, as a cancelled prompt ends it at a terminal."""
        if self.logs:
            self.action_toggle_logs()  # the full log gives way, so the question is seen
        box = self.query_one("#ask", Vertical)
        widget, value_of = prompt_for(question)
        parts: list[Widget] = (
            [Static(Text.assemble(("▲ ", st(AMBER, bold=True)), question.lead))] if question.lead else []
        )
        parts.append(widget)
        await box.mount_all(parts)
        box.display = True
        self.prompt = widget
        (widget.query_one(Input) if isinstance(widget, TextPrompt) else widget).focus()
        try:
            handle.answer(value_of(await widget.future))
        except Cancel, Interrupt:
            handle.cancel()
        except Unasked:
            pass
        finally:
            # Only its own parts: `run`'s next question may be up already.
            if self.prompt is widget:
                self.prompt = None
                self.set_focus(None)
            for part in parts:
                await part.remove()
            box.display = self.prompt is not None

    def armed_key(self) -> str | None:
        """The key pressed once (Ctrl+C), for a question's time-left bar (Prompt.counting_down)."""
        return self.armed.key()

    def cols(self) -> int:
        """The question's column (inside its padding): how wide a question may lay itself out."""
        return max(20, self.size.width - 4)

    def moving(self) -> bool:
        """Whether the screen is changing: ComfyUI is loading, failing or stopping, or what
        shows differs from the last look (new output, a value arriving, a hover, a resize).
        The Pacer asks once a tick, so `seen` is a memory of the last tick's look."""
        launch = self.launch
        seen = (
            launch.phase,
            len(launch.facts),
            len(launch.log.lines),
            launch.log.dropped,
            self.logs,
            self.armed.key(),
            self.hovered,
            self.size,
            self.prompt,
        )
        changed, self.seen = seen != self.seen, seen
        return changed or launch.phase != LaunchPhase.RUNNING

    def dot_shown(self) -> bool:
        """Whether the loader is showing the breathing dot: the dot Pacer's moving()."""
        return self.summary.display and self.launch.phase in (LaunchPhase.RUNNING, LaunchPhase.ELSEWHERE)

    def draw_dot(self) -> None:
        """Redraws the loader (Summary) alone, for the dot's breath."""
        summary = self.summary
        if summary.display:
            summary.refresh()

    def frame(self) -> None:
        """The main Pacer's tick: closes the window a moment after ComfyUI has stopped, moves the
        banner on by elapsed time, and redraws the widgets on show."""
        if self.launch.phase == LaunchPhase.STOPPED and not self.closing:
            self.closing = True
            self.set_timer(1.2, self.exit)  # the window closes once ComfyUI has stopped
        self.banner.advance(time.monotonic())
        for widget in (self.banner, self.summary, self.log_pane, self.bottom):
            if widget.display:
                widget.refresh()

    def layout_banner(self, size: Size | None = None) -> None:
        """size: the window's new size, on a resize (self.size is still the old one then)."""
        width, height = size or self.size
        scale = banner_scale(width, height)
        banner = self.banner
        banner.styles.height = banner_rows(scale) + banner_top(height, scale)
        banner.display = not self.logs
        self.summary.display = not self.logs
        self.log_pane.display = self.logs

    def on_resize(self, event: events.Resize) -> None:
        if hasattr(self, "banner"):  # the first Resize may arrive before on_mount
            self.layout_banner(event.size)

    def action_interrupt(self) -> None:
        """Ctrl+C: twice to stop ComfyUI (and close the window); once when there's nothing to stop."""
        if not self.launch.alive():
            if self.launch.phase != LaunchPhase.STOPPING:
                self.exit()
            return
        if self.armed.press("Ctrl+C") and self.handle is not None:
            self.launch.stopping()
            self.handle.cancel()  # `run` stops ComfyUI, as closing the window does

    def action_toggle_logs(self) -> None:
        self.logs = not self.logs
        self.layout_banner()

    def action_escape(self) -> None:
        if self.prompt is not None and not self.prompt.future.done():
            self.prompt.escape()  # a question takes Esc as its way out
        elif self.logs:
            self.action_toggle_logs()
        elif self.launch.phase in (LaunchPhase.FAILED, LaunchPhase.STOPPED):
            self.exit()

    def action_close(self) -> None:
        if self.launch.phase in (LaunchPhase.FAILED, LaunchPhase.STOPPED, LaunchPhase.ELSEWHERE):
            self.exit()

    def on_click(self, event: events.Click) -> None:
        if event.style.link:
            event.stop()
            self.open_url(event.style.link)

    def action_replay(self) -> None:
        """R: starts the launch over (the demo's key, not the design's): the last one is stopped
        and left behind."""
        if not self.replay:
            return
        if self.follow_task:
            self.follow_task.cancel()
        if self.handle is not None:
            self.handle.cancel()
        self.drop_question()  # the last launch's question goes with it
        self.asked = None
        self.armed.clear()
        self.closing = False
        self.launch = self.new_launch()
        self.banner.shown = 0.0
        self.start()

    def on_unmount(self) -> None:
        """However the window goes, `run` mustn't outlive it: it would hold ComfyUI with no window
        to close. Normally it has ended already (the window closes only once ComfyUI has stopped,
        failed, or was running elsewhere); after a crash it's cancelled here, which stops ComfyUI
        through `run`'s own cleanup, and run_window waits for that."""
        if self.handle is not None and self.handle.result is None:
            self.handle.cancel()

    def action_scroll(self, step: int) -> None:
        if self.logs:
            self.log_pane.scroll_lines(step)

    def action_page(self, step: int) -> None:
        if self.logs:
            self.log_pane.page_lines(step)

    def action_top(self) -> None:
        if self.logs:
            self.log_pane.to_top()

    def action_follow(self) -> None:
        if self.logs:
            self.log_pane.follow()


def run_window(gateway: Gateway, sandbox: Sandbox, *, replay: bool = False, headless: bool = False) -> int:
    """Runs the run window for sandbox on gateway in this terminal, and returns its exit code:
    `run`'s (0 once ComfyUI has stopped or was running elsewhere, 130 after Ctrl+C twice, the
    failure's otherwise), so a script that started the window learns how it went.

    A crash inside the window is raised once Textual has restored the terminal, after the `run` it
    cancelled has stopped ComfyUI, so the CLI reports it like any other (as `run_home` does).
    `headless` runs it with no terminal at all, for tests."""
    if not headless:
        sys.stdout.write(f"\x1b]0;ComfyUI ({sandbox.name})\x07")  # the window's title, as the shortcut names it
        sys.stdout.flush()
    app = LaunchApp(gateway, sandbox, replay=replay)
    try:
        app.run(headless=headless)
        crash = crash_of(app)
        handle = app.handle
        if crash is not None and handle is not None and handle.result is None:
            # Gone before `run` ended: on_unmount cancelled it. A real `run` ends on a thread of its
            # own, which sets its result, so it can be waited for here with the event loop gone.
            # (Only after a crash: the window otherwise closes once `run` has stopped ComfyUI.)
            deadline = time.monotonic() + UNWIND_SECONDS
            while handle.result is None and time.monotonic() < deadline:
                time.sleep(0.05)
    finally:
        gateway.close()
    if crash is not None:
        raise crash
    ended = app.ended or (app.handle.result if app.handle is not None else None)
    return ended.exit_code if ended is not None else (app.return_code or 0)
