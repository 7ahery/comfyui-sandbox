"""The Typer app and the single place that turns outcomes and exceptions into exit codes.

It implements the public interface in docs/scripting.md: exit codes, streams, the --json document and the
global options.

Writing a command:
- Register it in `build_app()` (or add a Typer sub-app there for a command group). Take `ctx: typer.Context`;
  `ctx.obj` is the `Session`.
- Return an `output.Outcome(message, data={...})` on success; raise a `CliError` (errors.py) on failure. Never call
  `sys.exit`, never `print`, never write to `sys.stdout`: stdout is the result channel. Talk to people with
  `session.out.step/info/warn/error`.
- A command that changes something calls `session.transcript()` first.
- A command that acts on an install calls `session.resolve_install()` before anything else, and never takes a
  distro name from anywhere else (`launcher remove` is the one exception).
- Reach the distro only through `session.wsl()`, and everything else outside the process through `session.deps`
  (docs/adr/0013).
- A command that changes the sandbox wraps its work in `steps.ending_stopped` (docs/adr/0015); a destructive one
  calls `destructive.confirm_destructive` before any destructive call (docs/adr/0009).
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.metadata import version as package_version

import typer
import typer.main

from comfyui_sandbox.cli_command import PROG
from comfyui_sandbox.commands import backup as backup_command
from comfyui_sandbox.commands import (
    check,
    install,
    launcher,
    passthroughs,
    prereqs,
    rebuild,
    recreate,
    startstop,
    status,
    uninstall,
    update,
    verify,
)
from comfyui_sandbox.commands import config as config_command
from comfyui_sandbox.commands import logs as logs_command
from comfyui_sandbox.commands import node as node_command
from comfyui_sandbox.commands import run as run_command
from comfyui_sandbox.commands import wslconfig as wslconfig_command
from comfyui_sandbox.deps import Deps
from comfyui_sandbox.errors import CliError, ExitCode, UsageError, unregistered_message
from comfyui_sandbox.front_end import Closed, Home
from comfyui_sandbox.logs import Transcript
from comfyui_sandbox.output import CommandResult, Outcome, Output
from comfyui_sandbox.state import Install, Installs, resolve
from comfyui_sandbox.wsl import Wsl

# Commands whose arguments are passed through untouched: global options are only
# recognized before their name.
PASSTHROUGH = frozenset({"comfy", "cm-cli", "exec"})


@dataclass
class Session:
    """Per-invocation state handed to commands as ctx.obj."""

    deps: Deps
    out: Output
    json: bool = False
    yes: bool = False
    non_interactive: bool = False
    distro: str | None = None
    command: str = ""  # the command words, e.g. "update comfyui"
    argv: list[str] = field(default_factory=list)  # the original argv, for the transcript header
    log: Transcript | None = None  # set by transcript()
    after_failure: Callable[[], None] | None = None  # runs once main() has shown a failure
    # False for a command line a front end runs in-process (call()): it's already inside one, so
    # nothing may open another (a TUI inside the TUI would fight it for the terminal).
    front_ends: bool = True

    def transcript(self) -> Transcript:
        """Start this command's transcript (once; later calls return the same one).

        Every command that changes something calls this first. From then on, out.step/info/
        warn/error and the wsl calls made through `wsl()` are written to it, and main()
        closes it and reports its path ("log") on success and on failure.
        """
        if self.log is None:
            self.log = Transcript.create(self.deps.app_dir, self.command, self.argv, package_version(PROG))
            self.out.transcript = self.log
        return self.log

    def wsl(self) -> Wsl:
        """The wsl.exe wrapper, logging to the transcript when there is one."""
        return Wsl(self.deps.runner, self.deps.environ, log=self.log)

    def installs(self) -> Installs:
        return Installs.load(self.deps.app_dir)

    def resolve_install(self, *, require_registered: bool = True) -> Install:
        """The install this command acts on (--distro resolution, docs/scripting.md, "Global options and choosing a sandbox").

        Refuses a distro not in installs.json; a missing installs.json means reinstall.
        Then one `wsl --list --quiet` for the resolved install: a distro WSL no longer has is
        refused, pointing to `uninstall` (`uninstall` passes require_registered=False).
        """
        install = resolve(
            self.installs(), self.distro, non_interactive=self.non_interactive, prompter=self.deps.prompter
        )
        if require_registered and not self.wsl().exists(install.distro):
            raise CliError(unregistered_message(install.distro))
        return install

    def can_open_tui(self) -> bool:
        """Whether this invocation may take over the terminal with a full-screen TUI.

        Both stdin and stdout must be a terminal, so a script or a pipe (either end) never hangs
        on a screen nobody sees; --json and --non-interactive ask for machine-readable, prompt-free
        output, which a TUI isn't (docs/scripting.md, "The TUI").
        """
        return (
            self.front_ends
            and self.deps.stdin_is_terminal
            and self.deps.stdout_is_terminal
            and not self.json
            and not self.non_interactive
        )


def _extract_globals(argv: list[str], session: Session) -> list[str]:
    """Pull the global options out of argv, wherever they appear (docs/scripting.md, "Global options and choosing a sandbox")."""
    rest: list[str] = []
    i = 0
    seen_command = False
    while i < len(argv):
        token = argv[i]
        if token == "--" or (not seen_command and token in PASSTHROUGH):
            rest.extend(argv[i:])
            break
        if token == "--json":
            session.json = True
        elif token == "--yes":
            session.yes = True
        elif token == "--non-interactive":
            session.non_interactive = True
        elif token == "--distro" and i + 1 < len(argv):
            session.distro = argv[i + 1]
            i += 1
        elif token.startswith("--distro="):
            session.distro = token.removeprefix("--distro=")
        else:
            if not token.startswith("-"):
                seen_command = True
            rest.append(token)
        i += 1
    return rest


def _command_path(root, argv: list[str]) -> str:
    """The command words in argv ("update comfyui"), as far as they name real commands."""
    words: list[str] = []
    group = root
    for token in argv:
        if token == "--":
            break
        if token.startswith("-"):
            continue
        commands = getattr(group, "commands", None)
        if not commands or token not in commands:
            break
        words.append(token)
        group = commands[token]
    return " ".join(words)


class _Finished(Exception):
    """Raised by the root callback when it produced the result itself (e.g. --version)."""

    def __init__(self, command: str, outcome: Outcome) -> None:
        self.command, self.outcome = command, outcome


def build_app() -> typer.Typer:
    app = typer.Typer(
        add_completion=False,
        pretty_exceptions_enable=False,
        help="Isolated ComfyUI on Windows: a hardened WSL2 distro with Docker.",
    )

    @app.callback(invoke_without_command=True)
    def root(
        ctx: typer.Context,
        show_version: bool = typer.Option(False, "--version", help="Print the version and exit."),
        json_: bool = typer.Option(False, "--json", help="Print one JSON result document on stdout."),
        yes: bool = typer.Option(False, "--yes", help="Accept every default (never destructive actions)."),
        non_interactive: bool = typer.Option(
            False, "--non-interactive", help="Never prompt, a missing answer exits 9."
        ),
        distro: str | None = typer.Option(None, "--distro", help="The sandbox to act on."),
    ) -> None:
        # --json, --yes, --non-interactive and --distro are taken out of argv by main()
        # so they work anywhere on the command line; they are declared here for --help.
        if show_version:
            v = package_version(PROG)
            raise _Finished("version", Outcome(f"{PROG} {v}", {"version": v}))
        if ctx.invoked_subcommand is None:
            # main() handles a truly bare command line before Typer; this is what's left (`--`).
            raise UsageError(_missing_command())

    @app.command("home")
    def home(ctx: typer.Context) -> int:
        """Open home: your sandboxes and the everyday actions, full screen. Needs a terminal."""
        # main() opens home itself before Typer runs, so it can pick the front end outside the
        # Typer machinery. The command is here so --help lists it; reaching this body (only via
        # an odd command line like `home --`) gets the same rule.
        return _open_home(ctx.obj, bare=False)

    check.register(app)
    prereqs.register(app)
    install.register(app)
    wslconfig_command.register(app)
    launcher.register(app)
    verify.register(app)
    run_command.register(app)
    status.register(app)
    startstop.register(app)
    uninstall.register(app)
    config_command.register(app)
    rebuild.register(app)
    recreate.register(app)
    update.register(app)
    passthroughs.register(app)
    logs_command.register(app)
    node_command.register(app)
    backup_command.register(app)
    return app


def _missing_command() -> str:
    """The usage error for a bare command where home can't open: it says what a terminal would do,
    so someone who piped it, or a script, learns why nothing opened."""
    return (
        f"Missing command. At a terminal, without --json or --non-interactive, '{PROG}' on its own "
        f"opens home. Run '{PROG} --help' to see the commands."
    )


def _open_home(session: Session, *, bare: bool) -> int:
    """Opens home through the launcher, or refuses with a usage error (exit 2) when it can't open here."""
    if session.yes or session.distro is not None:
        # Both only mean something to a command. Home has no defaults to accept, and opening it
        # straight on one sandbox is left for later, so neither is quietly ignored.
        if bare:
            raise UsageError(f"Missing command. Run '{PROG} --help' to see the commands.")
        raise UsageError(f"'{PROG} home' takes no --yes or --distro. Run '{PROG} --help' to see the commands.")
    if not session.front_ends:
        raise UsageError("Missing command: a front end runs command lines in-process, never another front end.")
    if not session.can_open_tui():
        if bare:
            raise UsageError(_missing_command())
        raise UsageError(
            f"'{PROG} home' needs a terminal: it opens home only with stdin and stdout both a "
            f"terminal, and without --json or --non-interactive."
        )
    session.command = "home"  # names the transcript, should there be one
    try:
        return session.deps.launch(Home(), session.deps)
    except Exception:
        # Home writes no transcript of its own (one per session would push the commands' out of
        # the newest 20), but a crash needs one for its traceback: start it now and let main()'s
        # crash path write it. The launcher has restored the terminal before raising.
        session.transcript()
        raise


def main(argv: list[str], deps: Deps | None = None, *, app: typer.Typer | None = None) -> int:
    """Run the CLI. Returns the exit code; never raises.

    It's also the one place that picks the front end (ADR 0017; docs/scripting.md, "The TUI"): a
    bare command line or `home` goes to home through `deps.launch` when it can open, and is a
    usage error when it can't; `run` opens the run window under the same rule (`can_open_tui`)
    unless it's given --plain, which its own command decides once it has resolved the sandbox;
    everything else is the plain CLI. Deciding here, before Typer, keeps the rule testable with
    fake `Deps` and keeps `entry()` a thin wrapper.
    """
    deps = deps or Deps.real()
    return _run(argv, deps, Output(deps.stdout, deps.stderr, json_mode=False), app, front_ends=True).exit_code


def call(argv: list[str], deps: Deps, out: Output, *, app: typer.Typer | None = None) -> CommandResult:
    """`main()` for a front end in this process: runs argv through `out`, and hands back how it ended.

    It's how the TUI does anything (ADR 0017): every action is the command line it shows, run
    exactly as `main()` runs it, so the TUI can't do what the CLI can't, and each action writes the
    transcript, maps errors to exit codes and runs `after_failure` as the CLI would. What differs:

    - the command talks through `out`, the front end's own `Output` (its json_mode still comes from
      argv's --json), so the front end sees each step and line as it's said;
    - the result comes back as a value, the same content as the `--json` document, as well as being
      printed through `out` (`CommandResult`);
    - it never opens a front end: a bare command line or `home` is a usage error (2) here;
    - sys.stdout isn't redirected for Typer's --help, as `main()` does: that swap is process-wide,
      and a call runs on a worker thread while the front end owns the real stdout.

    Never raises. Blocks until the command ends, so a front end runs it off its UI thread.
    """
    return _run(argv, deps, out, app, front_ends=False)


def _run(argv: list[str], deps: Deps, out: Output, app: typer.Typer | None, *, front_ends: bool) -> CommandResult:
    """main() and call(): runs argv through out, and turns how it ended into a result."""
    session = Session(deps, out, front_ends=front_ends)
    rest = _extract_globals(argv, session)
    out.json_mode = session.json
    root = typer.main.get_command(app or build_app())
    command = _command_path(root, rest)
    session.command, session.argv = command, list(argv)

    def failure(code: int, message: str, *, data=None, log=None, flag=None, crash=None) -> CommandResult:
        transcript = session.log
        if transcript is not None:
            log = log or transcript.path
            transcript.error(message)
            if crash is not None:
                transcript.crash(crash)
            transcript.close(code)
            out.transcript = None  # closed: out.failure's messages stay out of the file
        result = out.failure(command, code, message, data=data, log=log, flag=flag)
        if session.after_failure is not None:
            session.after_failure()  # e.g. `run` keeps its own window open once the message is on screen
        return result

    try:
        if rest in ([], ["home"]):
            # A TUI screen draws on the terminal itself and has shown whatever it had to say, so
            # its exit code is the whole result: no message, no document (there's no --json here).
            return CommandResult(session.command, _open_home(session, bare=not rest), "")
        if front_ends:
            # Typer writes --help to sys.stdout; route it to the injected stream.
            with contextlib.redirect_stdout(deps.stdout):
                result = root.main(args=rest, prog_name=PROG, standalone_mode=False, obj=session)
        else:
            result = root.main(args=rest, prog_name=PROG, standalone_mode=False, obj=session)
    except _Finished as done:
        return out.result(done.command, done.outcome)
    except CliError as exc:
        return failure(exc.code, exc.message, data=exc.data, log=exc.log, flag=getattr(exc, "flag", None))
    except KeyboardInterrupt, typer.Abort:
        return failure(ExitCode.INTERRUPTED, "Interrupted.")
    except typer.TyperException as exc:
        code = ExitCode.USAGE if exc.exit_code == ExitCode.USAGE else ExitCode.FAILED
        return failure(code, exc.format_message())
    except Exception as exc:
        return failure(ExitCode.FAILED, f"{type(exc).__name__}: {exc}", crash=exc)
    if isinstance(result, Closed):
        # A screen ran the command line (`run`'s window) and has shown how it went: as for home,
        # its exit code is the whole result. Any transcript is the screen's own command's.
        return CommandResult(command, result.exit_code, "")
    if isinstance(result, int) and result == ExitCode.INTERRUPTED:
        # Typer catches a KeyboardInterrupt raised inside a command and returns 130 rather than
        # letting it reach the except above: it's the same interruption, so it ends the same way.
        return failure(ExitCode.INTERRUPTED, "Interrupted.")
    if session.log is not None:
        session.log.close(0)
        if isinstance(result, Outcome) and result.log is None:
            result.log = session.log.path
    if isinstance(result, Outcome):
        return out.result(command, result)
    if isinstance(result, int):
        # --help (or a stray typer.Exit): Click returns the exit code instead of raising.
        if result == 0:
            return CommandResult(command, 0, "")
        return out.failure(command, result, f"Exited with code {result}.")
    return out.result(command, Outcome(""))


def entry() -> None:
    """The console script. JSON and messages are UTF-8 whatever the console code page."""
    import sys

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
