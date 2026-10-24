"""`comfyui-sandbox run`: the foreground run window.

Starts the container (`compose up -d`), holds the distro open with the keepalive, waits until
Windows reaches the container, opens the browser and stays in the foreground. The window ending
(Ctrl+C, closing the console, the container stopping) runs `compose stop` and releases the
keepalive. Only one run window exists per distro (a run-window marker,
docs/adr/0010); a second `run` only opens the browser.

It has two front ends (docs/scripting.md, "`run`, `start`, `stop` and `open`"). At a terminal it opens the
Textual run window (`front_end.RunWindow`), which runs this same command line in-process and shows
what it says; `--plain`, no terminal, `--json` or `--non-interactive` give the plain window, its
steps and lines printed. The lifecycle above is this module's in both: the TUI only shows it. What
the Textual window shows beyond the plain lines comes through `Output` too: the steps are the plan
(`PLAN`), the stop notice is `Output.notice`, and when the Output shows ComfyUI's own output
(`Output.shows_comfyui`) this command follows ComfyUI's log into it and says whether a newer ComfyUI
is out (`update comfyui`'s read-only pending check).
"""

from __future__ import annotations

import contextlib
import re
import threading

import typer

from comfyui_sandbox import ports, reach, steps
from comfyui_sandbox.cli_command import PROG
from comfyui_sandbox.commands import update_comfyui, verify
from comfyui_sandbox.container import CONTAINER
from comfyui_sandbox.errors import CliError, NotReady
from comfyui_sandbox.front_end import Closed
from comfyui_sandbox.front_end import RunWindow as RunWindowScreen
from comfyui_sandbox.instance import instance_name, stopping_name
from comfyui_sandbox.keepalive import Keepalive
from comfyui_sandbox.output import Outcome, PlannedStep
from comfyui_sandbox.runner import Process
from comfyui_sandbox.state import Install
from comfyui_sandbox.steps import compose_argv, project_path
from comfyui_sandbox.wsl import CALL_TIMEOUT, ROOT

READY_MINUTES = 10
READY_INTERVAL = 10
REACH_TRIES = 10  # WSL's localhost forwarding can lag a little behind the container
REACH_PAUSE = 3
# watch.sh exit codes
WATCH_STOPPED, WATCH_CRASHED = 0, 3

# The plan: run's steps, with hand-set estimates. "Waiting for ComfyUI to answer" is ComfyUI's own
# start-up (the Textual window reads its parts from ComfyUI's lines). A first start after an
# install, which installs ComfyUI in the container, takes far longer; the window's bar waits it out.
STARTING = PlannedStep("Starting ComfyUI", 8)
WAITING = PlannedStep("Waiting for ComfyUI to answer", 40)
REACHING = PlannedStep("Checking that Windows reaches the container", 2)
PLAN = (STARTING, WAITING, REACHING)

# The stop notice (Output.notice): true from the moment the console-close handler is set until the
# cleanup starts. The plain window says it in its own words once ComfyUI is up (CLOSE_TO_STOP).
STOP_NOTICE = "Closing this window stops ComfyUI."
ISOLATION_NOTICE = f"Isolation warning. {STOP_NOTICE}"
ISOLATION_TIMEOUT = 10
CLOSE_TO_STOP = "Close this window to stop ComfyUI."

# ComfyUI's log since this start of its container, stdout and stderr together (ComfyUI logs mostly
# to stderr, and Runner.follow reads stdout only). `--since` its start time: the container keeps
# its earlier runs' logs, which would replay a previous start's custom nodes and failures.
FOLLOW_LOG = (
    "exec docker logs --follow --since "
    f"\"$(docker inspect -f '{{{{.State.StartedAt}}}}' {CONTAINER})\" {CONTAINER} 2>&1"
)
# The same log so far, read once: a second window has no start-up of its own to show, so it shows
# the first one's (ComfyUI's versions and custom nodes).
READ_LOG = FOLLOW_LOG.replace(" --follow", "")
COLOURS = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def versions_line(versions: dict[str, str]) -> str:
    """What ComfyUI runs, from its /system_stats (reach.versions_of), as the Textual window reads it
    (its VERSIONS_SAID; tests/tui/test_real_run_window.py holds the two together). The window's
    grid fills from ComfyUI's log as it loads; this line settles it, whatever the log's wording."""
    return "Versions: " + ", ".join(f"{name} {value}" for name, value in versions.items())


def _report(lines: list[str]) -> str:
    """The text of an `[error] ...` report from an in-distro script (its last lines included)."""
    start = next((i for i, line in enumerate(lines) if line.startswith("[error]")), 0)
    return "\n".join(lines[start:]).removeprefix("[error] ")


class RunWindow:
    def __init__(self, session, install: Install) -> None:
        self.session, self.install = session, install
        self.deps, self.out = session.deps, session.out
        self.wsl = session.wsl()
        self.project = project_path(install.linux_user)
        self.port = install.port  # until run() reads `.env`
        self.keepalive = Keepalive(self.wsl, self.deps.winapi)
        self.log_reader: Process | None = None  # following ComfyUI's log, for an Output that shows it
        self._cleanup = threading.Lock()
        self._cleaned = False

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}"

    def _compose(self, *args: str, timeout: float | None = CALL_TIMEOUT, on_line=None):
        return self.wsl.run_command(
            self.install.distro,
            self.install.linux_user,
            compose_argv(self.install, *args),
            on_line=on_line,
            timeout=timeout,
        )

    def cleanup(self) -> None:
        """`compose stop`, then let the distro idle out. Runs once, whichever of the normal exit,
        Ctrl+C and the console-close handler (another thread) gets here first; the others wait."""
        with self._cleanup:
            if self._cleaned:
                return
            self._cleaned = True
            self.out.notice("")  # from here on, closing the window stops nothing
            # Says "stopping" to whoever looks (home's row) until the cleanup is done; free if taken.
            stopping = self.deps.winapi.acquire_instance(stopping_name(self.install.distro))
            try:
                self.out.info("Stopping ComfyUI ...")
                done = self._compose("stop", timeout=steps.STOP_TIMEOUT)
                if done.returncode != 0:
                    self.out.warn(
                        f"docker compose stop exited {done.returncode}: {done.err().strip() or done.out().strip()}"
                    )
            finally:
                try:
                    if self.log_reader is not None:
                        self.log_reader.terminate()  # it ends with the container anyway; this makes sure
                    self.keepalive.stop()
                finally:
                    if stopping is not None:
                        stopping.release()

    def start_container(self) -> None:
        self.keepalive.start(self.install.distro)
        self.out.step(STARTING.title)
        self.warn_about_isolation()
        done = self._compose("up", "-d", on_line=self.out.progress)
        if done.returncode != 0:
            raise CliError(f"docker compose up -d failed (exit {done.returncode}), the messages above say why.")
        if self.out.shows_comfyui:
            self.log_reader = self.wsl.follow_command(
                self.install.distro, self.install.linux_user, ["sh", "-c", FOLLOW_LOG], self._comfyui_line
            )

    def warn_about_isolation(self) -> None:
        """Advisory at every new launch, with the same read-only checks as verify.

        Startup deliberately continues on failed or inconclusive isolation, per the
        startup warning policy in docs/scripting.md, "`run`, `start`, `stop` and `open`".
        Full verify/install retain their failure behavior.
        """
        try:
            done = self.wsl.run_script(
                self.install.distro, ROOT, "verify.sh", {"ISOLATION_ONLY": "1"}, timeout=ISOLATION_TIMEOUT
            )
        except CliError as exc:
            reasons = [str(exc)]
        else:
            checks = [check for check in verify.parse(done.out()) if check.section == "WSL isolation"]
            reasons = [check.message for check in checks if check.status in ("fail", "warn")]
            complete = any(
                check.status == "info" and check.message == "WSL isolation check complete" for check in checks
            )
            if done.returncode == 0 and complete and not reasons:
                return
            if not reasons:
                reasons = ["The isolation check did not complete successfully."]
        self.out.notice(ISOLATION_NOTICE)
        self.out.warn("Sandbox isolation failed or could not be verified. ComfyUI will still start.")
        for reason in reasons:
            self.out.info(reason)
        self.out.info(f"Run '{steps.command_for('verify', self.session, self.install.distro)}' for details.")

    def _comfyui_line(self, line: str) -> None:
        """A line of ComfyUI's log, on the reader's thread, without its colours: the window draws
        its own."""
        self.out.comfyui(COLOURS.sub("", line))

    def wait_ready(self) -> None:
        self.out.step(WAITING.title)
        env = {"PROJECT_DIR": self.project, "TIMEOUT_MIN": str(READY_MINUTES), "INTERVAL": str(READY_INTERVAL)}
        # Its progress lines summarise ComfyUI's log and its report repeats the log's last lines: an
        # Output that already shows the log would get them twice (custom nodes counted twice).
        on_line = None if self.log_reader is not None else self.out.progress
        done = self.wsl.run_script(
            self.install.distro,
            self.install.linux_user,
            "wait_ready.sh",
            env,
            on_line=on_line,
            timeout=READY_MINUTES * 60 + 600,
        )
        if done.returncode == 0:
            return
        lines = [line for line in done.out().splitlines() if line.strip()]
        if done.returncode == 2:
            raise NotReady(
                f"ComfyUI was not ready after {READY_MINUTES} minutes. It may still be installing. "
                + steps.keep_waiting_hint("run", self.session, self.install.distro)
            )
        raise CliError(f"ComfyUI stopped before it was ready: {_report(lines)}")

    def check_windows_reach(self) -> None:
        self.out.step(REACHING.title)
        for attempt in range(REACH_TRIES):
            found = reach.check_windows_reach(self.deps, self.port)
            if found.ok:
                ports.record_port(self.session, self.install, self.port)
                if self.out.shows_comfyui and found.versions:
                    self.out.info(versions_line(found.versions))
                return
            if found.os in reach.WINDOWS_OS or attempt == REACH_TRIES - 1:
                hint = f" {found.hint}" if found.hint else ""
                raise CliError(
                    f"{found.message[0].upper()}{found.message[1:]}.{hint}".replace("..", "."),
                    data={"windows_reach": found.to_json()},
                )
            self.deps.sleep(REACH_PAUSE)

    def report_update(self) -> None:
        """Says whether a newer ComfyUI is out, from `update comfyui`'s read-only pending check, for
        an Output that shows ComfyUI's own output (the Textual window marks it), or, when it's pinned,
        the release it's pinned to. Nothing when it's current or the check can't tell: it's advice,
        never a reason for the window to fail."""
        try:
            found = update_comfyui.pending(self.session, self.install)
        except CliError:
            return
        if found.pinned:
            unpin = steps.command_for(update_comfyui.UNPIN, self.session, self.install.distro)
            self.out.info(f"ComfyUI is pinned to {found.pinned}: '{unpin}' unpins it.")
            return
        if not found.pending or not found.available:
            return
        how = f"'{PROG} update comfyui --distro {self.install.distro}' updates it."
        if found.detail.startswith("nightly"):
            self.out.info(f"A newer ComfyUI nightly is available ({found.available}): {how}")
        else:
            self.out.info(f"ComfyUI {found.available} is available: {how}")

    def hold(self) -> Outcome:
        """Block until the container stops; say why."""
        install = self.install
        done = self.wsl.run_script(
            install.distro,
            install.linux_user,
            "watch.sh",
            {"PROJECT_DIR": self.project},
            on_line=self.out.progress,
            timeout=None,
        )  # blocks until the container stops
        data = {"distro": install.distro, "url": self.url}
        if done.returncode == WATCH_STOPPED:
            self.out.info("ComfyUI has been stopped.")
            return Outcome("ComfyUI has been stopped.", {**data, "ended": "stopped"})
        lines = [line for line in done.out().splitlines() if line.strip()]
        if done.returncode == WATCH_CRASHED:
            raise CliError(f"ComfyUI stopped on its own: {_report(lines)}")
        raise CliError("The WSL session ended (distro terminated or shut down?). ComfyUI has stopped.")

    def run(self) -> Outcome:
        self.deps.winapi.on_console_close(self.cleanup)
        self.out.plan(PLAN)
        self.out.notice(STOP_NOTICE)  # true from here: the handler above stops ComfyUI on a close
        try:
            self.port = ports.port_to_start(self.session, self.install, fix=ports.PORT_FIX)
            self.start_container()
            self.wait_ready()
            self.check_windows_reach()
            self.deps.winapi.open_url(self.url)
            self.out.plan_done()
            self.out.info(f"ComfyUI is running at {self.url}")
            self.out.info("Keep this window open while you use ComfyUI (you can minimize it).")
            self.out.info(CLOSE_TO_STOP)
            if self.out.shows_comfyui:
                self.report_update()
            return self.hold()
        finally:
            self.cleanup()


def _show_log_so_far(session, install: Install) -> None:
    """For an Output that shows ComfyUI's output (the Textual window): the running ComfyUI's log
    since its start, then its versions from /system_stats, so a second window has its details too.
    Advice only, so a failure shows nothing rather than failing the run."""
    with contextlib.suppress(CliError):
        session.wsl().run_command(
            install.distro,
            install.linux_user,
            ["sh", "-c", READ_LOG],
            on_line=lambda line: session.out.comfyui(COLOURS.sub("", line)),
        )
    found = reach.check_windows_reach(session.deps, install.port)
    if found.ok and found.versions:
        session.out.info(versions_line(found.versions))


def _wait_for_key(session) -> None:
    session.out.info("Press any key to close this window.")
    session.deps.winapi.wait_for_key()


def _open_run_window(session, install: Install) -> Closed:
    """Hands the command line to the Textual run window, which runs `run --distro <distro>`
    in-process and shows it, and returns once the window has closed."""
    try:
        return Closed(session.deps.launch(RunWindowScreen(install.distro), session.deps))
    except Exception:
        # The window's own `run` writes its transcript; a crash of the window itself needs one for
        # its traceback (main()'s crash path writes it), as home's does (cli._open_home).
        session.transcript()
        raise


def register(app: typer.Typer) -> None:
    @app.command()
    def run(
        ctx: typer.Context,
        plain: bool = typer.Option(
            False,
            "--plain",
            help="Print the steps and lines instead of opening the full-screen "
            "run window (as without a terminal, --json or --non-interactive).",
        ),
    ) -> Outcome | Closed:
        """Start ComfyUI and keep it running while this window is open (close the window to stop it)."""
        session = ctx.obj
        install = session.resolve_install()
        if not plain and session.can_open_tui():
            return _open_run_window(session, install)
        winapi = session.deps.winapi
        url = f"http://localhost:{install.port}"
        lock = winapi.acquire_instance(instance_name(install.distro))
        if lock is None:
            winapi.open_url(url)
            if session.out.shows_comfyui:
                _show_log_so_far(session, install)
            session.out.info(f"ComfyUI is already running from another run window: opened {url}.")
            return Outcome(
                f"ComfyUI is already running, opened {url}.",
                {"distro": install.distro, "url": url, "already_running": True},
            )
        try:
            session.transcript()
            return RunWindow(session, install).run()
        except KeyboardInterrupt, typer.Abort:
            raise  # Ctrl+C is the user asking to stop, not an error: the window closes
        except Exception:
            # In a window of its own the message would vanish with the window: keep it open,
            # whatever went wrong (cli.main shows and logs the message before the wait).
            if winapi.owns_console():
                session.after_failure = lambda: _wait_for_key(session)
            raise
        finally:
            lock.release()
