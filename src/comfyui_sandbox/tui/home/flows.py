"""The flows: home, a sandbox's menu and each of its actions, an install, Check this PC. They
read like a plain CLI's: print, ask, run, print the outcome (see Console). Each action is a
command line the app's Gateway runs, echoed as it was given; what it prints and how it ends come
back through its operation handle, and home reads the sandboxes again once it's over."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence

from rich import box
from rich.constrain import Constrain
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..gateway import OperationHandle, Question, Result, Sandbox
from ..gateway.records import WSL_NOT_READY
from ..links import folder_link, host_folder_link, url_link, wsl_link
from ..logs import LOG_TAIL, LogSession
from ..parts import card, heading, spinner
from ..states import Outcome, SamplerKind, SandboxState, StepStatus
from ..theme import AMBER, FAINT, GREEN, INK, LINE, MINT, MUTED, RED, SKY, SOFT, TEXT, VIOLET, clamp, mix, st
from .chrome import Sampler
from .console import Console
from .log_views import LogView, OpLogEntry, RunView
from .model import (
    BACKUPS_DIR,
    COMFYUI_DIR,
    COMFYUI_VERSION_HINTS,
    LINUX_USER,
    UPDATE_TARGETS,
    Cancel,
    Interrupt,
    comfyui_does,
    release_number,
)
from .operations import ask, busy, follow, outcome_card, outcome_of, run_steps, update_card
from .prompts import DANGER, Prompt, Unavailable
from .splash import SPLASH_SEED

KEPT, DELETED, REPLACED = ("✓", GREEN), ("✗", RED), ("↺", AMBER)


def checklist(mark: tuple[str, str], items: Sequence[tuple[str, str | Text]]) -> Table:
    """The lists the confirmations explain with: a mark, what (in ComfyUI's words), and a note,
    usually the folder: dim text, or a Text of its own (folder(): a link that opens it)."""
    grid = Table.grid(padding=(0, 2))
    for what, note in items:
        grid.add_row(
            Text(f"  {mark[0]}", st(mark[1], bold=True)),
            Text(what, st(SOFT)),
            note if isinstance(note, Text) else Text(note, st(FAINT)),
        )
    return grid


def folder(distro: str, name: str) -> Text:
    """One of ComfyUI's folders in distro: a folder glyph and its name, a click opening it."""
    return Text.from_markup(folder_link(distro, f"{COMFYUI_DIR}/{name}", name))


def backups_folder(distro: str) -> Text:
    """Where distro's backups are, shown as `backup` says it (~/comfyui-backups), a click opening it."""
    return Text.from_markup(folder_link(distro, BACKUPS_DIR, "~/comfyui-backups"))


def models_folder(distro: str, models: str) -> Text:
    """The models folder as the config has it: a Linux path inside the distro (absolute, or ~/),
    else a Windows folder (a drive or a share). A click opens it."""
    if models.startswith("~"):
        return Text.from_markup(folder_link(distro, f"/home/{LINUX_USER}{models[1:]}", models))
    if models.startswith("/"):
        return Text.from_markup(folder_link(distro, models, models))
    return Text.from_markup(host_folder_link(models))


def cannot_undo() -> Text:
    return Text.assemble(("▲ ", st(RED, bold=True)), ("This can't be undone.", st(RED, bold=True)))


# -- home ------------------------------------------------------------------------------------


async def home(con: Console, sandboxes: list[Sandbox]) -> None:
    """Home: pick a sandbox or an action, run it, come back, until Quit."""
    while True:
        notes = con.take_notes()  # shown under the table, as on the sandbox menu
        actions = [("Install a new sandbox", "install"), ("Check this PC", "check"), ("Quit", "quit")]
        await con.draw(sandboxes, table=False)
        picked = await con.pick_home(sandboxes, actions, notes)
        if picked == "quit":
            return
        if picked == "install":
            try:
                await install_flow(con, sandboxes)
            except Cancel:  # Esc on one of the install questions
                con.flash("[dim]Install not started.[/]")
        elif picked == "check":
            await check_pc(con, sandboxes)
        else:
            await sandbox_menu(con, sandboxes, picked)


# The widest Check this PC's card gets: a default PowerShell window's columns.
CHECK_MAX_WIDTH = 130


async def check_pc(con: Console, sandboxes: list[Sandbox]) -> None:
    """Check this PC: each check spins, then ticks (or crosses), in a live card, with the hero's
    logo scanning. The checks are whatever the gateway's check finds, in its order."""
    await con.draw(sandboxes, table=False)  # about this PC, not the sandboxes
    await con.echo(["check"])  # shown first: the real check takes a few seconds to answer
    checks: list = []  # empty until the gateway answers; the card is up meanwhile
    found = [False]
    done = [0]

    def result() -> Panel:
        # Each check spins while it runs, then shows whether it passed.
        passed = all(item.ok for item in checks)
        t = Table(box=None, show_header=False, padding=(0, 1), expand=True)
        t.add_column(no_wrap=True)
        t.add_column(no_wrap=True)
        t.add_column(ratio=1, overflow="ellipsis", no_wrap=True)
        if not found[0]:
            t.add_row(spinner(SKY), Text("Asking this PC…", st(TEXT)), Text(""))
        for k, item in enumerate(checks):
            if k < done[0]:
                mark = Text("✓", st(MINT, bold=True)) if item.ok else Text("✗", st(RED, bold=True))
                t.add_row(mark, Text(item.label, st(MUTED)), Text(item.value, st(TEXT if item.ok else RED)))
            elif k == done[0]:
                t.add_row(spinner(SKY), Text(item.label, st(TEXT)), Text("checking…", st(FAINT, italic=True)))
            else:
                t.add_row(Text("·", st(LINE)), Text(item.label, st(FAINT)), Text(""))
        if not found[0] or done[0] < len(checks):
            title, color, border = " Checking this PC… ", SKY, st(LINE)
        elif passed:
            title, color, border = " ✓ This PC is ready for an install ", GREEN, st(mix(GREEN, INK, 0.4))
        else:
            title, color, border = " ✗ This PC isn't ready for an install ", RED, st(mix(RED, INK, 0.4))
        return Panel(
            t,
            title=Text(title, st(color, bold=True)),
            title_align="left",
            box=box.ROUNDED,
            border_style=border,
            width=min(con.app.cols(), CHECK_MAX_WIDTH),
            padding=(1, 2),
        )

    await con.show_live(result)
    checks.extend(await con.app.gateway.check_this_pc())
    found[0] = True
    passed = all(item.ok for item in checks)
    began = time.monotonic()  # it scans the logo, smoothly, over the checks' two seconds
    sampler = con.app.begin_operation().sampler = Sampler(
        SamplerKind.CHECK, SPLASH_SEED, lambda: clamp((time.monotonic() - began) / 2)
    )
    for _ in checks:
        await con.nap(2 / len(checks))
        done[0] += 1
    sampler.end(Outcome.OK if passed else Outcome.FAILED)
    # The result keeps its own screen; home stays the place to pick the next thing.
    if not passed and any(item.fixable for item in checks):
        if await set_up_wsl(con, "WSL needs setting up."):
            await check_pc(con, sandboxes)
        return
    await con.pause()


async def set_up_wsl(con: Console, why: str) -> bool:
    """Offers to set WSL up now, running `prereqs`, which asks its own question (what it will run as
    administrator, and why) and raises the Windows prompt. Returns whether the caller can carry on:
    True once WSL is ready, False when declined, failed, or Windows has to reboot first (said here)."""
    await con.say(Text(why, st(MUTED)), "")
    if not await con.confirm("Set WSL up now?"):
        con.flash("[dim]WSL not set up. Run 'comfyui-sandbox prereqs' whenever you're ready.[/]")
        return False
    ended = await busy(con, None, None, ["prereqs"], "Setting up WSL…")
    if outcome_of(ended) == Outcome.CANCELLED:
        con.flash("[dim]WSL not changed.[/]")
    elif not ended.ok:
        con.flash(failed(ended))
    elif ended.data.get("reboot_needed"):
        await show_outcome(con, ended, "✓ WSL is installed. Reboot Windows to finish")
    else:
        con.flash(f"[green]✓[/] {escape(ended.message)}")
        return True
    return False


# -- install ---------------------------------------------------------------------------------

# Seconds between reads of the sandboxes while an install's questions are up: how soon the form
# gives way to the live view once the install has recorded its sandbox.
READ_EVERY = 0.5

# The install summary fills the screen up to this many columns.
SUMMARY_WIDTH = 120


async def install_flow(con: Console, sandboxes: list[Sandbox]) -> None:
    """Install a new sandbox: `install`, as at a terminal. Its own questions (intake's: the same
    questions, order, defaults and checks, the review, and preflight's questions about a taken port
    or name) are asked here as a form; then, once it has recorded its sandbox, its steps run under
    the live view. Esc on a question backs out of the whole install, and nothing has been created:
    install saves nothing before its preflight has passed."""
    app = con.app
    before = {s.name for s in sandboxes}
    await con.draw(sandboxes, table=False)  # the new sandbox isn't in it yet
    handle = app.gateway.run(["install", "--new"])  # --new: not the resume of an Incomplete one
    await con.echo(handle.argv)
    await con.say(heading("New sandbox"), "")

    def recorded() -> Sandbox | None:
        return next((s for s in sandboxes if s.name not in before), None)

    ended = await intake_form(con, handle, recorded)
    sandbox = recorded()
    if sandbox is None:  # it ended before it got that far: nothing was created
        assert ended is not None  # the form returns without a result only once the sandbox is recorded
        if outcome_of(ended) == Outcome.CANCELLED:
            con.flash("[dim]Install not started.[/]")
        elif ended.exit_code == WSL_NOT_READY:
            # A fresh PC: set WSL up right here, then start the install over if no reboot is needed.
            if await set_up_wsl(con, "This PC needs WSL set up before a sandbox can be installed."):
                await install_flow(con, sandboxes)
        else:
            await show_outcome(con, ended, "")  # the check refused this PC, say: the core's message
        return
    await run_install(con, sandboxes, sandbox, handle)


async def intake_form(con: Console, handle: OperationHandle, recorded: Callable[[], Sandbox | None]) -> Result | None:
    """Asks the questions handle's install asks one under another, as a form, until it has recorded
    its sandbox (None: the live view takes over) or has ended (its result).

    What it prints between two questions comes before the second, as at a terminal, but for its
    step lines (the live view's): the summary of the answers before "Ready to install?", a warning.
    Several lines (the summary) take the place of the answers before them, which they repeat, so
    the screen stays short. Meanwhile a spinner names the step it's on, and L shows its output."""
    app = con.app
    session = LogSession()
    app.begin_operation().op_log = OpLogEntry(session, "Installing", " ".join(handle.argv))
    app.keys = [("l", "full log")]
    form = len(app.out.children)  # the form's lines start here
    shown = len(handle.progress.lines)  # the output before this was already on screen (or is no question's)

    async def read() -> None:
        # The sandbox shows up in the listing once install has saved it: read it often.
        while recorded() is None:
            await asyncio.sleep(READ_EVERY)
            await app.read_sandboxes()

    reading = asyncio.create_task(read())
    asked: Question | None = None

    def waiting() -> bool:
        question = handle.progress.question
        return recorded() is not None or (question is not None and question is not asked)

    try:
        while True:
            step = handle.progress.current
            doing = handle.progress.plan[step].title if step is not None else "Starting"
            ended = await con.spin(f"{doing}…", handle, lambda: session.catch_up(handle.progress.lines), until=waiting)
            if ended is not None:
                await app.read_sandboxes()  # the last word on whether it got as far as saving its sandbox
                return ended
            if recorded() is not None:
                return None
            asked = handle.progress.question
            said = [line for line in handle.progress.lines[shown:] if line.strip() and not line.startswith("==> ")]
            shown = len(handle.progress.lines)
            if len(said) > 1:
                for widget in list(app.out.children)[form:]:
                    await widget.remove()
                await con.say(
                    Constrain(card(Text("\n".join(said), st(TEXT)), "Install summary", expand=True), SUMMARY_WIDTH)
                )
            elif said:
                await con.say(Text(said[0], st(MUTED)))
            assert asked is not None  # waiting() was true with no sandbox recorded, so a question is up
            await ask(con, handle, asked, keep=True)
    finally:
        reading.cancel()
        app.keys = []


def install_card(name: str) -> Callable[[Outcome, Result], list]:
    """An install's outcome card (operations.outcome_card): the core's message and the transcript;
    and, when it didn't finish, that it can be picked up again."""
    lines = outcome_card(f"{name} is installed")

    def card_lines(outcome: Outcome, ended: Result) -> list:
        body = lines(outcome, ended)
        if outcome != Outcome.OK:
            body.append(
                Text.from_markup("Pick it on home: [bold]Resume install[/], or [bold]Change answers and resume…[/]")
            )
        return body

    return card_lines


async def run_install(con: Console, sandboxes: list[Sandbox], sandbox: Sandbox, handle: OperationHandle) -> None:
    """Shows handle's install of sandbox (new, or resumed) under the live view, its row Installing,
    and leaves a note on how it went. Esc twice cancels it: the running `wsl.exe` call stops at
    once. One that fails or is cancelled stays Incomplete, to resume."""
    app = con.app
    app.mark(sandbox, SandboxState.INSTALLING)
    await con.draw(sandboxes, selected=sandbox)
    ended = await run_steps(
        con,
        f"Installing {sandbox.name}",
        handle,
        kind=SamplerKind.INSTALL,
        seed=sandbox.port,
        on=sandbox,
        result=install_card(sandbox.name),
    )
    outcome = outcome_of(ended)
    if outcome == Outcome.CANCELLED:
        con.flash(f"[yellow]Install of {sandbox.name} cancelled.[/] Pick it to resume.")
        return
    await con.pause()  # the outcome stays on screen until the user has read it
    if outcome == Outcome.FAILED:
        where = next((f" at “{step.title}”" for step in handle.progress.plan if step.status == StepStatus.FAILED), "")
        con.flash(f"[yellow]⚠ {sandbox.name}: install stopped{escape(where)}. Pick it to resume.[/]")


# -- a sandbox's menu ----------------------------------------------------------------------


async def sandbox_menu(con: Console, sandboxes: list[Sandbox], sandbox: Sandbox) -> None:
    """sandbox's menu: asks what to do with it, does that, and asks again, until Back (or sandbox is gone)."""
    while sandbox in sandboxes:
        await con.draw(sandboxes, selected=sandbox)
        if sandbox.state == SandboxState.RUNNING and sandbox.run_window is False:
            await con.say(no_run_window(sandbox), "")
        # The highlighted row above shows its state; the question names it in full (the row may cut it).
        # A Stopped sandbox's distro may be down, and its log can't be read without booting it.
        # (A Running or Starting one has its ComfyUI or its run window up, so its distro is.)
        distro_up = sandbox.state != SandboxState.STOPPED or await con.app.gateway.distro_running(sandbox.name)
        action = await con.select(
            f"What do you want to do with {sandbox.name}?", menu_choices(sandbox, distro_up), crumb=sandbox.name
        )
        if action == "back":
            return
        try:
            await SANDBOX_ACTIONS[action](con, sandboxes, sandbox)
        except Cancel:  # Esc on a follow-up question of the flow's own (Update what?)
            con.flash("[dim]Cancelled.[/]")


def menu_choices(sandbox: Sandbox, distro_up: bool = True) -> list:
    """What a sandbox's menu offers in its state. distro_up: whether its distro is running; if not,
    Show logs is greyed out (reading the log would start the distro and Docker)."""
    if sandbox.state == SandboxState.STARTING:
        # Its run window is bringing ComfyUI up: stopping it or watching it start is all that makes
        # sense now (opening it would find nothing yet; anything that changes it would race the start).
        return [("Stop", "stop"), ("Show logs", "logs"), ("Back", "back")]
    acts: list = []
    if sandbox.state == SandboxState.RUNNING:
        acts += [("Open in browser", "open"), ("Stop", "stop"), ("Show logs", "logs")]
    elif sandbox.state == SandboxState.STOPPED:
        acts += [
            ("Start (in its own window)", "start"),
            ("Run here", "run"),
            ("Show logs", "logs") if distro_up else (Unavailable("Show logs"), "logs"),
        ]
    elif sandbox.state == SandboxState.INCOMPLETE:
        acts += [("Resume install", "resume"), ("Change answers and resume…", "change")]
    if sandbox.state != SandboxState.INCOMPLETE:
        acts += [
            ("Update… (update available)" if sandbox.update_ready else "Update…", "update"),
            ("Back up", "backup"),
            ("Restore…", "restore"),
            ("Settings…", "settings"),
        ]
    acts.append(None)
    if sandbox.state != SandboxState.INCOMPLETE:  # an unfinished install is resumed, not reinstalled
        acts.append((Text("Reinstall ComfyUI…", DANGER), "recreate"))
    acts += [(Text("Uninstall…", DANGER), "uninstall"), ("Back", "back")]
    return acts


def no_run_window(sandbox: Sandbox) -> Text:
    """What a Running sandbox with no run window open needs said: closing a window won't stop it
    (there's none to close; it was left running by a command or a window that ended without
    stopping it), so here's what does."""
    return Text.assemble(
        ("◌ ", st(AMBER, bold=True)),
        (f"No run window is open for {sandbox.name}. ", st(TEXT)),
        ("Closing a window won't stop it. Stop, below, stops it.", st(MUTED)),
    )


def failed(ended: Result) -> Text:
    """A short action's note when it didn't succeed: the core's own message, and, when it failed
    (not cancelled), the transcript that says what happened, as the CLI prints them."""
    if outcome_of(ended) == Outcome.CANCELLED:
        return Text.assemble(("✗ ", st(MUTED)), ("Cancelled.", st(MUTED)))
    note = Text.assemble(("✗ ", st(RED, bold=True)), (ended.message, st(TEXT)))
    if ended.log:
        note.append_text(Text.assemble(("  Log: ", st(FAINT)), (ended.log, st(MUTED))))
    return note


async def open_in_browser(con: Console, sandboxes: list[Sandbox], sandbox: Sandbox) -> None:
    """Open in browser: `open`, which only opens the browser, and fails, saying how to start it,
    if the sandbox stopped since its menu was drawn."""
    ended = await busy(con, sandbox, None, ["open", "--distro", sandbox.name], "Opening ComfyUI in your browser…")
    if ended.ok:
        con.flash(f"[green]✓[/] Opened {escape(str(ended.data.get('url', '')))} in your browser.")
    else:
        con.flash(failed(ended))


async def start(con: Console, sandboxes: list[Sandbox], sandbox: Sandbox) -> None:
    """Start (in its own window): opens the run window, and waits under a spinner until ComfyUI is up."""
    ended = await busy(
        con,
        sandbox,
        SandboxState.STARTING,
        ["start", "--distro", sandbox.name],
        "Opening the run window and waiting until Windows reaches ComfyUI…",
    )
    con.flash(f"[green]✓[/] {sandbox.name} is up at {url_link(sandbox.port)}" if ended.ok else failed(ended))


async def stop(con: Console, sandboxes: list[Sandbox], sandbox: Sandbox) -> None:
    """Stop: the container stops, and a run window open for it sees that and closes."""
    ended = await busy(
        con, sandbox, SandboxState.STOPPING, ["stop", "--distro", sandbox.name], "Stopping the container…"
    )
    con.flash(f"[green]✓[/] {sandbox.name} stopped. Its run window closes on its own." if ended.ok else failed(ended))


ENDED = object()  # what a page "answers" when the command it shows ends by itself


async def unless_ended(handle: OperationHandle, view: Prompt, show: Callable[[Prompt], Awaitable]):
    """show(view)'s answer, or ENDED if handle's command ends first: then the view is answered
    for the user, so a page over a command that has ended (Run here's `run`, after ComfyUI stopped
    on its own) doesn't wait for a key that would change nothing."""
    asking = asyncio.ensure_future(show(view))
    ending = asyncio.ensure_future(handle.wait())
    try:
        await asyncio.wait({asking, ending}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        ending.cancel()  # only this waiter: the handle's own end is untouched
    if not asking.done():
        view.answer(ENDED)
    return await asking


async def run_here(con: Console, sandboxes: list[Sandbox], sandbox: Sandbox) -> None:
    """Run here: `run`, in home. ComfyUI's output is on the page, under the live line, until a
    stop key is pressed twice. Leaving ends `run` the way closing its window would: its own
    cleanup stops the container and releases the distro, and the row reads Stopping until it has.
    If `run` ends by itself (ComfyUI stopped elsewhere, or failed), the page goes and says why."""
    app = con.app
    handle = app.gateway.run(["run", "--distro", sandbox.name])
    await app.read_sandboxes()  # its run window (this page) is open now
    session = LogSession(sandbox)
    session.live = True  # whatever the row said a moment ago, ComfyUI's output is on its way
    printing = follow(session, handle)
    op = app.begin_operation()  # for the stop keys' second press, kept across the redraws
    stopping = False
    try:
        while handle.result is None:
            await con.draw(sandboxes, selected=sandbox)  # drops the app's op: put it back
            # Or a stop key pressed once (armed on op) would be forgotten at each redraw, and the
            # second press would only arm it again.
            app.op = op
            await con.echo(handle.argv)
            # The page answers only to go full screen; full screen, only to come back.
            if await unless_ended(handle, RunView(session, handle.progress), con.ask) is ENDED:
                break
            await unless_ended(handle, RunView(session, handle.progress, full=True), app.full_screen)
    except Interrupt:  # the stop key's second press
        stopping = True
        handle.cancel()
    finally:
        app.op = None
    if stopping:
        app.mark(sandbox, SandboxState.STOPPING)
        await con.draw(sandboxes, selected=sandbox)
        try:
            ended = await con.spin("Stopping ComfyUI…", handle)
        finally:
            app.unmark(sandbox)
    else:
        ended = handle.result
    assert ended is not None  # spin() without `until` waits for the result, and the loop ends only once it is in
    printing.cancel()
    await app.read_sandboxes()
    if stopping and (ended.ok or ended.interrupted):
        con.flash(f"[green]✓[/] {sandbox.name} stopped.")
    elif ended.ok and ended.data.get("already_running"):
        con.flash(f"[green]✓[/] {escape(ended.message)}")  # its run window is elsewhere: only the browser opened
    elif ended.ok:
        con.flash(f"[green]✓[/] {sandbox.name}: {escape(ended.message)}")  # stopped from elsewhere
    else:
        con.flash(failed(ended))


async def show_logs(con: Console, sandboxes: list[Sandbox], sandbox: Sandbox) -> None:
    """Show logs: `logs --follow`, its lines on the page (the sandbox's row, the command, the log
    in a box) and, with L, the same log full screen over everything. Leaving ends the follow."""
    handle = con.app.gateway.run(["logs", "--follow", "--tail", str(LOG_TAIL), "--distro", sandbox.name])
    command = " ".join(handle.argv)
    session = LogSession(sandbox)
    feed = follow(session, handle)
    try:
        while True:
            await con.draw(sandboxes, selected=sandbox)
            await con.echo(handle.argv)
            if await con.ask(LogView(session, command=command)) != "full":
                return
            await con.app.full_screen(LogView(session, full=True, command=command))
    finally:
        feed.cancel()
        handle.cancel()


async def long_action(
    con: Console,
    sandboxes: list[Sandbox],
    sandbox: Sandbox,
    kind: SamplerKind,
    title: str,
    argv: list[str],
    result: Callable[[Outcome, Result], list | None],
    explain: Sequence = (),
    handle: OperationHandle | None = None,
) -> Outcome:
    """A multi-step action on sandbox (update, backup, recreate, uninstall): the gateway's plan for
    argv draws as a live view under the table and its row, and the hero previews it (kind: see
    Sampler). result(outcome, result) is the card under the steps once they've ended
    (operations.outcome_card: the core's message and the transcript's path); like an install's, it
    stays on screen until a key is pressed. explain: what to say under the command line before its
    question (what a destructive one deletes; see run_steps). handle: argv already started (Restore…
    starts it to see whether there's a backup to ask about).

    It offers no Esc-cancel (the spec): stopping an update, backup, recreate or uninstall halfway
    would leave the sandbox half-changed. Ctrl+C twice still quits home, which cancels it. Esc on
    a question it asks first (the typed name) backs out of it before anything has changed: then
    there's no outcome to read, only a note."""
    await con.draw(sandboxes, selected=sandbox)
    handle = handle or con.app.gateway.run(argv)
    ended = await run_steps(
        con, title, handle, kind=kind, seed=sandbox.port, cancellable=False, result=result, explain=explain
    )
    if outcome_of(ended) == Outcome.CANCELLED and not handle.progress.plan:
        con.flash(Text.assemble(("✗ ", st(MUTED)), ("Cancelled. Nothing was changed.", st(MUTED))))
        return Outcome.CANCELLED
    await con.pause()
    return outcome_of(ended)


def _available(sandbox: Sandbox, key: str) -> str:
    """What is waiting for target key: "v0.3.9 → v0.3.10 available" when the check read both versions."""
    versions = sandbox.update_versions.get(key)
    return f"{versions[0]} → {versions[1]} available" if versions else "update available"


def _target_label(sandbox: Sandbox, key: str, label: str) -> str:
    """A target as Update what? offers it: with what's waiting, or, for a pinned ComfyUI, its pin."""
    if key in sandbox.updates:
        return f"{label} ({_available(sandbox, key)})"
    if key == "comfyui" and sandbox.pinned:
        return f"{label} (pinned to {sandbox.pinned})"
    return label


async def comfyui_version(con: Console, sandbox: Sandbox) -> str | None:
    """Which ComfyUI?: the channel to update to, or a release to switch to and pin, starting on the
    one it follows. Returns `update comfyui`'s --version (None: keep its channel), or "back"."""
    channel = sandbox.channel or "latest"
    current = "release" if sandbox.pinned else channel
    now = {"latest": "Newest release", "nightly": "Nightly", "release": "A release you pick…"}
    picked = await con.select(
        "Which ComfyUI?",
        [*((f"{title} (now)" if value == current else title, value) for value, title in now.items()), ("Back", "back")],
        default=current,
        hints=COMFYUI_VERSION_HINTS,
    )
    if picked == "back":
        return "back"
    if picked == "release":
        typed = await con.text(
            "Release number (e.g. 0.3.40):",
            sandbox.pinned or "",
            release_number,
            hint="the sandbox stays on it until you pick another version here",
        )
        return typed.strip().lower().removeprefix("v")
    return None if picked == channel else picked


async def update(con: Console, sandboxes: list[Sandbox], sandbox: Sandbox) -> None:
    """Update…: asks what to update (for ComfyUI, also which version), says what that does, and on
    a Yes runs it."""
    n = sandbox.name
    # Everything ("all") isn't offered for now; the "all" branches below are kept for when it is again.
    target = await con.select(
        "Update what?",
        [*((_target_label(sandbox, key, label), key) for key, (label, _) in UPDATE_TARGETS.items()), ("Back", "back")],
    )
    if target == "back":
        return
    version = await comfyui_version(con, sandbox) if target == "comfyui" else None
    if version == "back":
        return
    # Like Back up: say what it does, then ask. The targets and their order are `update`'s.
    keys = list(UPDATE_TARGETS) if target == "all" else [target]
    what = Table.grid(padding=(0, 2))
    for key in keys:
        label, does = UPDATE_TARGETS[key]
        if key == "comfyui":
            does = comfyui_does(version or sandbox.channel or "latest")
        what.add_row(
            Text("  •", st(VIOLET)),
            Text(label, st(TEXT, bold=True)),
            Text(does, st(MUTED)),
            Text(_available(sandbox, key) if key in sandbox.updates else "", st(GREEN)),
        )
    await con.say(
        "",
        heading("Updates, in this order", "stops at the first that fails", VIOLET)
        if target == "all"
        else heading("Updates", accent=VIOLET),
        what,
        "",
        Text("Your workflows, input and output files and models aren't touched.", st(MUTED)),
        "",
    )
    asked = (
        f"Switch ComfyUI to {version} now?"
        if version not in (None, "latest", "nightly")
        else f"Update {'everything' if target == 'all' else UPDATE_TARGETS[target][0]} now?"
    )
    if not await con.confirm(asked):
        con.flash("[dim]Update not started.[/]")
        return
    # Exactly `update <target>`: its plan is the live view's steps, its message (which says it's
    # stopped, and how to start it) the card's. The gateway checks the sandbox for updates again
    # afterwards, so the mark goes once what's out has been updated.
    argv = ["update", target, *(["--version", version] if version else []), "--distro", n]
    await long_action(con, sandboxes, sandbox, SamplerKind.UPDATE, f"Updating {n}", argv, result=update_card(n, keys))


async def back_up(con: Console, sandboxes: list[Sandbox], sandbox: Sandbox) -> None:
    """Back up: says what a backup holds and what it leaves out, and on a Yes runs it."""
    n = sandbox.name
    # Say what a backup is before asking, in ComfyUI's terms (its folders), not the
    # sandbox's. What's in it matches `backup`: user/, input/, output/ and a node snapshot.
    saved = checklist(
        KEPT,
        [
            ("Workflows and ComfyUI settings", folder(n, "user")),
            ("Input files (images you load into workflows)", folder(n, "input")),
            ("Output files (what you've generated)", folder(n, "output")),
            ("The list of custom nodes you installed", ""),
        ],
    )
    skipped = checklist(
        ("–", FAINT),
        [
            ("Models", "large, they stay where they are"),
            ("The custom nodes' own files", "restore downloads them again"),
            ("ComfyUI Sandbox config files", "port, models folder"),
        ],
    )
    await con.say(
        heading(f"A backup of {n} saves", accent=GREEN),
        saved,
        "",
        heading("Not included", accent=FAINT),
        skipped,
        "",
        Text(f"It's saved inside {n} in ~/comfyui-backups, which Windows opens at", st(MUTED)),
        "  " + wsl_link(n, BACKUPS_DIR),
        *(
            [Text(f"{n} is stopped, so it starts for a moment to list its custom nodes.", st(MUTED))]
            if sandbox.state != SandboxState.RUNNING
            else []
        ),
        "",
    )
    if not await con.confirm(f"Back up {n} now?"):
        con.flash("[dim]Backup not started.[/]")
        return
    # Exactly `backup`: its message names the backup and the `restore` command line that brings it
    # back (Restore… asks which), which the card shows; where Windows opens it goes under.
    card_lines = outcome_card(f"Backup of {n} saved")

    def result(outcome: Outcome, ended: Result) -> list:
        lines = card_lines(outcome, ended)
        if outcome == Outcome.OK:
            lines.append(Text.from_markup(f"Open it from Windows: {wsl_link(n, BACKUPS_DIR)}", style=st(MUTED)))
        return lines

    await long_action(
        con, sandboxes, sandbox, SamplerKind.BACKUP, f"Backing up {n}", ["backup", "--distro", n], result=result
    )


async def settings(con: Console, sandboxes: list[Sandbox], sandbox: Sandbox) -> None:
    """Settings…: `config`'s own menu, asked by `config` itself, so every setting it can change is
    here, checked as it checks them. It ends with ComfyUI stopped, as `config` does; its message
    says when the change takes effect. Backing out of a question changes nothing.

    `config` reads the sandbox's `.env` before it asks, which boots a stopped distro (never the
    container), so the spinner says so rather than looking like a bare menu load."""
    ended = await busy(
        con,
        sandbox,
        None,
        ["config", "--distro", sandbox.name],
        f"Reading {sandbox.name}'s settings (starts its WSL distro if stopped)…",
    )
    await show_outcome(con, ended, f"✓ {sandbox.name}'s settings changed")


async def show_outcome(con: Console, ended: Result, done: str) -> None:
    """How an action that changes a sandbox ended, as a card that stays until a key is pressed (L
    reads its output meanwhile): done as the title and the core's message, or what went wrong
    with the transcript's path. Backed out of (a question cancelled), it's only a note: nothing
    happened to read about."""
    if outcome_of(ended) == Outcome.CANCELLED:
        con.flash(Text.assemble(("✗ ", st(MUTED)), ("Cancelled. Nothing was changed.", st(MUTED))))
        return
    if ended.ok:
        result = card(Text(ended.message, st(MUTED)), done, GREEN, mix(GREEN, INK, 0.4))
    else:
        body = Text(ended.message, st(TEXT))
        if ended.log:
            body.append_text(Text.assemble(("\nLog: ", st(FAINT)), (ended.log, st(MUTED))))
        result = card(body, f"✗ It didn't go ahead (exit {ended.exit_code})", RED, mix(RED, INK, 0.4))
    await con.say(result)
    await con.pause()


async def resume_install(con: Console, sandboxes: list[Sandbox], sandbox: Sandbox) -> None:
    """Resume install: `install --distro x`, which carries on from x's saved answers (no questions)
    and reruns every step, each doing nothing when it's done already."""
    await run_install(con, sandboxes, sandbox, con.app.gateway.run(["install", "--distro", sandbox.name]))


async def change_and_resume(con: Console, sandboxes: list[Sandbox], sandbox: Sandbox) -> None:
    """Change answers and resume…: `install --distro x --change`, which asks the ComfyUI version, Manager
    UI and security again (the saved answers preselected; the others built the distro), saves them and
    resumes. For an install that failed on one of them: a release number that doesn't exist, say."""
    await run_install(con, sandboxes, sandbox, con.app.gateway.run(["install", "--distro", sandbox.name, "--change"]))


# Recreate and uninstall say what goes and what stays, like Back up, in ComfyUI's terms. It
# matches the real commands: recreate wipes data/ only; uninstall deletes the whole distro, so the
# backups in it go too, and the models with them when they live in WSL.
def your_work(n: str) -> list[tuple[str, Text]]:
    """What a sandbox n holds of the user's own, as ComfyUI's folders."""
    return [
        ("Workflows and ComfyUI settings", folder(n, "user")),
        ("Input files", folder(n, "input")),
        ("Output files", folder(n, "output")),
    ]


def removal(lead: Text, deleted: list, kept: list, after: list, mark: tuple[str, str] = DELETED) -> list:
    """What a destructive action deletes (or, with mark REPLACED, replaces) and keeps, said before
    it asks for the name to be typed."""
    return [
        lead,
        checklist(mark, deleted),
        *(["", heading("Kept", accent=GREEN), checklist(KEPT, kept)] if kept else []),
        "",
        *after,
        "",
    ]


async def recreate(con: Console, sandboxes: list[Sandbox], sandbox: Sandbox) -> None:
    """Reinstall ComfyUI…: what goes and what stays, then `reinstall`, which asks for the name to be typed
    (drawn letter by letter) and then runs its steps under the live view."""
    n = sandbox.name
    deleted = [("ComfyUI and its Python environment", ""), ("Custom nodes", folder(n, "custom_nodes")), *your_work(n)]
    kept = [
        ("Models", models_folder(n, sandbox.models)),
        ("Backups", backups_folder(n)),
        ("ComfyUI Sandbox config files", "port, models folder"),
    ]
    # Only data/ and the image are redone (`recreate`): the distro and Docker in it stay.
    # Said in the heading's note, so the page fits a 120x30 window.
    lead = heading(
        "Removed", "ComfyUI is reinstalled from scratch. The WSL distro and Docker inside it stay as they are", RED
    )
    # `reinstall` asks for the name itself, as at a terminal: no --confirm, so the command shown is
    # the one that asks. It ends with ComfyUI stopped, as every changing command does (its message,
    # on the card, says how to start it again).
    await long_action(
        con,
        sandboxes,
        sandbox,
        SamplerKind.RECREATE,
        f"Reinstalling ComfyUI in {n}",
        ["reinstall", "--distro", n],
        result=outcome_card(f"ComfyUI reinstalled in {n}"),
        explain=removal(lead, deleted, kept, [cannot_undo()]),
    )


async def restore(con: Console, sandboxes: list[Sandbox], sandbox: Sandbox) -> None:
    """Restore…: `restore`, which first asks which backup (newest first, even when there's one),
    then what a restore replaces and keeps and the name to be typed (see recreate), then runs its
    steps under the live view. Until it asks, a spinner says it's looking for the backups: with
    none, it ends there and home only says so, on the sandbox's menu, as no restore began. Any
    other way it ends before asking is said on its card (show_outcome)."""
    n = sandbox.name
    argv = ["restore", "--distro", n]
    await con.draw(sandboxes, selected=sandbox)
    handle = con.app.gateway.run(argv)
    con.app.begin_operation()  # so Ctrl+C under the spinner warns before it quits, as under busy's
    try:
        ended = await con.spin(
            f"Looking for {n}'s backups…", handle, until=lambda: handle.progress.question is not None
        )
    except Interrupt:
        handle.cancel()
        raise
    if ended is not None:
        if ended.data.get("backups") == []:
            con.flash(
                Text.assemble(("– ", st(MUTED)), (f"{n} has no backup to restore yet. Back up makes one.", st(MUTED)))
            )
        else:
            await show_outcome(con, ended, f"Backup restored to {n}")
        return
    # `restore` extracts the tar over data/ (same-named files overwritten, nothing deleted), then
    # reinstalls the snapshot's custom nodes.
    kept = [
        ("Files the backup doesn't have", "nothing is deleted"),
        ("Models", sandbox.models),
        ("Your backups", "~/comfyui-backups"),
    ]
    lead = heading(
        "Replaced from the backup", "the files it has, by name, then its custom nodes are installed again", AMBER
    )
    # No --backup and no --confirm: the command asks both, as at a terminal. It ends with ComfyUI
    # stopped (its message, on the card, says how to start it again).
    await long_action(
        con,
        sandboxes,
        sandbox,
        SamplerKind.RESTORE,
        f"Restoring {n}",
        argv,
        result=outcome_card(f"Backup restored to {n}"),
        explain=removal(lead, your_work(n), kept, [cannot_undo()], mark=REPLACED),
        handle=handle,
    )


async def uninstall(con: Console, sandboxes: list[Sandbox], sandbox: Sandbox) -> None:
    """Uninstall…: what goes and what stays, then `uninstall`, which asks for the name to be typed
    (see recreate)."""
    n = sandbox.name
    in_wsl = sandbox.models.startswith("~")
    deleted = [
        ("WSL distro", n),
        ("ComfyUI and its custom nodes", ""),
        *your_work(n),
        ("Backups", backups_folder(n)),
        *([("Models", models_folder(n, sandbox.models))] if in_wsl else []),
    ]
    kept = [] if in_wsl else [("Models", models_folder(n, sandbox.models))]
    lead = heading(f"Uninstall deletes the whole {n} sandbox, with everything in it", accent=RED)
    after = [cannot_undo()]
    # `uninstall` asks for the name itself (see recreate). Once it's gone, the next listing no
    # longer has it, so its menu closes (sandbox_menu).
    await long_action(
        con,
        sandboxes,
        sandbox,
        SamplerKind.UNINSTALL,
        f"Uninstalling {n}",
        ["uninstall", "--distro", n],
        result=outcome_card(f"{n} uninstalled" + ("" if in_wsl else f". Models in {sandbox.models} were kept.")),
        explain=removal(lead, deleted, kept, after),
    )


# The sandbox menu's choices, by value.
SANDBOX_ACTIONS = {
    "open": open_in_browser,
    "start": start,
    "stop": stop,
    "run": run_here,
    "logs": show_logs,
    "update": update,
    "backup": back_up,
    "restore": restore,
    "settings": settings,
    "resume": resume_install,
    "change": change_and_resume,
    "recreate": recreate,
    "uninstall": uninstall,
}
