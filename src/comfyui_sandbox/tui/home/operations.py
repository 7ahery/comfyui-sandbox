"""Showing a running command: a long operation's steps under a live view (run_steps), or a short
action under a spinner (busy). Both run the command line through the gateway, show what its
operation handle reports as it goes, keep its output for its full log (L), and read the
sandboxes again once it has ended.

They don't know what the command does: the gateway's plan says which steps it has, its output
says what they printed, and its result says how it ended."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence

from rich.table import Table
from rich.text import Text

from ..gateway import OperationHandle, Question, QuestionKind, Result, Sandbox
from ..logs import LogSession
from ..states import Outcome, SamplerKind, SandboxState
from ..theme import AMBER, FAINT, GREEN, MUTED, RED, TEXT, st
from .chrome import Sampler
from .console import WAIT_TICK, Console
from .live_view import LiveView
from .log_views import OpLogEntry
from .model import UPDATE_TARGETS, Cancel, Interrupt


def outcome_of(result: Result) -> Outcome:
    """How the screen tells a result: OK, cancelled (Esc, a cancelled question) or failed."""
    if result.ok:
        return Outcome.OK
    return Outcome.CANCELLED if result.interrupted else Outcome.FAILED


async def run_steps(
    con: Console,
    title: str,
    handle: OperationHandle,
    *,
    kind: SamplerKind,
    seed: int,
    on: Sandbox | None = None,
    cancellable: bool = True,
    result: Callable[[Outcome, Result], list | None] | None = None,
    explain: Sequence = (),
) -> Result:
    """Shows the operation handle runs under a live view, the hero's logo previewing it (kind and
    seed: see Sampler), and returns how it ended. The caller starts it (Gateway.run), so it can
    first draw what starting it changed (a new sandbox's row). Pressing Esc twice cancels it,
    unless cancellable is off; Ctrl+C twice quits home either way (SandboxApp.interrupt_running),
    stopping it where it is. A question
    it asks meanwhile is asked in the live view's place. result(outcome, result) gives the lines
    the view shows under the steps once they've ended. on: the sandbox it acts on, whose marked
    state (SandboxApp.mark) ends with it.

    explain: what to say under the command line before its first question (what a destructive one
    deletes and keeps, read before the name is typed). It's about the questions, so it stays while
    they're asked (Restore… asks which backup, then for the name) and goes once the plan is
    announced, or the command ends without one: the steps then have the screen to themselves.
    The logo's preview waits for the plan too: it's the steps' preview, so the questions before
    them leave it as it is, and a command that ends without steps (backed out of) never starts one."""
    app = con.app
    await con.echo(handle.argv)
    explained_from = len(app.out.children)
    if explain:
        await con.say(*explain)
    explained = list(app.out.children)[explained_from:]
    view = LiveView(app, title, handle.progress)
    live = view.widget = await con.show_live(view.__rich__, view.moving)
    op = app.begin_operation()
    op.live_view = view
    sampler: Sampler | None = None

    def preview() -> Sampler | None:
        """The logo's preview, started once the plan is announced (see above)."""
        nonlocal sampler
        if sampler is None and handle.progress.plan:
            sampler = op.sampler = Sampler(kind, seed, view.progress)
        return sampler

    op.op_log = OpLogEntry(view.session, title, " ".join(handle.argv))
    op.cancellable({"Esc"} if cancellable else set())
    app.keys = [
        ("l", "full log"),
        ("esc ×2", "cancel") if cancellable else ("ctrl+c ×2", "quit, leaving it unfinished"),
    ]
    app.set_focus(None)  # so L reaches the app
    asked: Question | None = None
    while handle.result is None:
        view.follow()
        question = handle.progress.question
        if question is not None and question is not asked:
            asked = question
            live = await ask_in_place(con, view, live, handle, question)
            continue
        preview()
        if explained and handle.progress.plan:
            explained = await unsay(explained)
        try:
            await con.nap(WAIT_TICK)
        except Interrupt:  # a cancel key pressed twice: the operation stops, and is shown stopping
            handle.cancel()
    view.follow()
    await unsay(explained)
    ended = handle.result
    outcome = outcome_of(ended)
    if not live.is_attached:
        live = view.widget = await con.show_live(view.__rich__, view.moving)
    if on is not None:
        app.unmark(on)
    await app.read_sandboxes()  # before the outcome's lines, which may read what changed (a new port)
    view.result = (result(outcome, ended) if result is not None else None) or []
    view.ended_as = outcome
    view.ended = time.monotonic()
    if (previewing := preview()) is not None:
        previewing.end(outcome)
    live.refresh(layout=True)  # now, not at its next tick: it makes room for what follows (pause)
    view.session.live, view.session.outcome = False, outcome  # the full log (L) now reads as ended
    op.finish()  # the view and the cancel keys go; the op_log and the sampler stay for the pause that follows
    app.keys = []
    return ended


def outcome_card(done: str) -> Callable[[Outcome, Result], list]:
    """The outcome card of a long operation, for run_steps' `result`: how it ended, the core's own
    message (what the CLI would print) and the transcript's path, whichever way it ended. done is
    the title of a success ("x updated"); a failure or a cancel gets its own."""

    def lines(outcome: Outcome, ended: Result) -> list:
        if outcome == Outcome.OK:
            head = Text(f"✓ {done}", st(GREEN, bold=True))
        elif outcome == Outcome.CANCELLED:
            head = Text("■ Cancelled", st(AMBER, bold=True))
        else:
            head = Text(f"✗ It didn't go through (exit {ended.exit_code})", st(RED, bold=True))
        body = [head, Text(ended.message, st(MUTED))]
        if ended.log:
            body.append(Text.assemble(("Log: ", st(FAINT)), (ended.log, st(MUTED))))
        return body

    return lines


def update_card(n: str, keys: Sequence[str]) -> Callable[[Outcome, Result], list]:
    """The outcome card of `update` on sandbox n for the targets `keys`: on success what each one
    ended up as (ComfyUI: the version it moved from and to), that nothing of the user's was touched,
    and that ComfyUI is stopped, with how to start it. A failure or a cancel gets outcome_card's."""

    def lines(outcome: Outcome, ended: Result) -> list:
        if outcome != Outcome.OK:
            return outcome_card(f"{n} updated")(outcome, ended)
        data = ended.data.get("targets", dict.fromkeys(keys, ended.data))
        body: list[Text | Table] = [Text(f"✓ {n} updated", st(GREEN, bold=True))]
        results = Table.grid(padding=(0, 2))
        for key in keys:
            results.add_row(
                Text("  •", st(MUTED)),
                Text(UPDATE_TARGETS[key][0], st(TEXT, bold=True)),
                Text(_update_result(key, data.get(key, {})), st(MUTED)),
            )
        body += [
            results,
            Text(""),
            Text(
                "ComfyUI is stopped. Choose Start (or Run here) on this sandbox to use the updated version.", st(MUTED)
            ),
            Text("Your workflows, input and output files and models weren't touched.", st(MUTED)),
        ]
        if ended.log:
            body.append(Text.assemble(("Log: ", st(FAINT)), (ended.log, st(MUTED))))
        return body

    return lines


def _update_result(key: str, data: dict) -> str:
    """What one updated target ended as, in a few words."""
    if key == "comfyui":
        before, now, pin = data.get("previous"), data.get("installed"), data.get("pinned")
        if data.get("left_pinned"):
            return f"pinned to {pin}, left as it is"
        pinned = ", pinned there" if pin else ""
        if now and before and now != before:
            return f"{before} → {now}{pinned}"
        if now:
            return f"on {now}{pinned}" if pin else f"already on {now}, the newest, nothing changed"
        return f"switched to {pin}, pinned there" if pin else "updated (its version couldn't be read)"
    return {
        "nodes": "every custom node is on its newest version",
        "comfy-cli": "on its newest version",
        "system": "Docker and its tools are up to date",
        "setup": "set up files are up to date",
    }.get(key, "updated")


async def unsay(widgets: Sequence) -> list:
    """Takes widgets that were said off the screen (those still on it); nothing is left of them."""
    for widget in widgets:
        if widget.is_attached:
            await widget.remove()
    return []


async def ask_in_place(con: Console, view: LiveView, live, handle: OperationHandle, question: Question):
    """Asks the operation's question where its live view was, and brings the view back. The view
    and the question can't share the screen: hide the view, ask, then show it again in the
    question's place (the table row shows the answer's effect, and the log records it). Returns
    the view's new widget."""
    app = con.app
    await live.remove()
    await ask(con, handle, question)
    live = view.widget = await con.show_live(view.__rich__, view.moving)
    app.set_focus(None)
    return live


async def ask(con: Console, handle: OperationHandle, question: Question, *, keep: bool = False) -> None:
    """Asks the question handle's operation waits on, as the kind of question it is (a select, a
    text field, Yes/No) with its hints, answers it, and waits until the operation has taken the
    answer (it's no longer waiting). Backing out of it (Esc, or Ctrl+C on it) cancels the
    operation, which then ends as interrupted, as a cancelled prompt ends a command at a terminal.
    The full log, if it's up, gives way so the question is seen. What was asked goes once it's
    answered: the screen shows the operation, and its log records what it did with the answer.
    keep: it stays instead, as the answered line, for questions asked as a form (an install's)."""
    app = con.app
    app.close_overlay()
    asked_from = len(app.out.children)
    if question.lead:
        await con.say(Text.assemble(("▲ ", st(AMBER, bold=True)), question.lead))
    try:
        if question.kind == QuestionKind.TEXT and len(question.confirms) == 1:
            # A destructive action's typed confirmation: drawn as typing that name, letter by letter.
            # (Several names, any of which confirms, are asked as the plain text question below.)
            handle.answer(await con.type_to_confirm(question.message, question.confirms[0], hint=question.hint))
        elif question.kind == QuestionKind.TEXT:
            check = question.validate
            handle.answer(
                await con.text(
                    question.message,
                    str(question.default or ""),
                    hint=question.hint,
                    validate=check and (lambda text: check(text) or True),
                )
            )
        elif question.kind == QuestionKind.CONFIRM:
            handle.answer(await con.confirm(question.message, question.default is not False, hint=question.hint))
        else:
            handle.answer(
                await con.select(
                    question.message,
                    list(question.choices),
                    hint=question.hint,
                    hints=question.hints,
                    default=question.default,
                )
            )
    except Interrupt, Cancel:
        handle.cancel()
    # The operation acts on the answer before it stops waiting: once it has, what it changed
    # (a sandbox's port, say) is there to be read.
    while handle.progress.question is question and handle.result is None:
        await asyncio.sleep(WAIT_TICK)
    await app.read_sandboxes()
    if keep:
        return
    for widget in list(app.out.children)[asked_from:]:
        await widget.remove()


async def busy(
    con: Console, sandbox: Sandbox | None, state: SandboxState | None, argv: Sequence[str], message: str
) -> Result:
    """A short action, shown in place (sandbox None: on no sandbox, like `prereqs`): sandbox's row shows state (None: as it is) while argv
    runs, and a spinner replaces the menu. What the command prints goes to a full log, as a long
    operation's does (L). It offers no cancel: Ctrl+C warns under the spinner, and a second
    press quits home once the command has stopped (SandboxApp.interrupt_running).

    A question it asks (config's menu) is asked in the spinner's place, and the spinner comes
    back once it's answered; backing out of it cancels the command (ask)."""
    app = con.app
    if sandbox is not None and state is not None:
        app.mark(sandbox, state)
    await con.draw(app.sandboxes, selected=sandbox)
    handle = app.gateway.run(argv)
    await con.echo(handle.argv)
    session = LogSession()
    # Only the output for L: a short action has no sampler, so the hero's logo carries on as it was.
    app.begin_operation().op_log = OpLogEntry(session, message.rstrip("…"), " ".join(handle.argv))
    app.keys = [("l", "full log")]
    asked: list[Question] = []

    def new_question() -> bool:
        question = handle.progress.question
        return question is not None and (not asked or question is not asked[-1])

    try:
        while (
            ended := await con.spin(
                message, handle, lambda: session.catch_up(handle.progress.lines), until=new_question
            )
        ) is None:
            question = handle.progress.question
            assert question is not None  # new_question() was true
            asked.append(question)
            await ask(con, handle, question)
        session.outcome = outcome_of(ended)
    except Interrupt:
        handle.cancel()
        session.outcome = Outcome.CANCELLED
        raise
    finally:
        session.live = False
        app.keys = []
        if sandbox is not None:
            app.unmark(sandbox)
    await app.read_sandboxes()
    return ended


def follow(session: LogSession, handle: OperationHandle) -> asyncio.Task:
    """Keeps session fed with what handle's operation prints, until the task is cancelled: for a
    page that waits on a question meanwhile (Show logs, Run here). Once the operation has ended
    (a follow whose container stopped), nothing more is coming, and the log says so."""

    async def feed() -> None:
        while True:
            session.catch_up(handle.progress.lines)
            if handle.result is not None:
                session.live = False
                return
            await asyncio.sleep(WAIT_TICK)

    session.catch_up(handle.progress.lines)  # what's there already shows on the first frame
    return asyncio.create_task(feed())
