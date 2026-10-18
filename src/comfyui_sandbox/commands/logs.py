"""`comfyui-sandbox logs`: the container log, or (`--cli`) the CLI's own transcripts.

Read-only: it writes no transcript of its own (so reading a failed command's transcript never
pushes it out of the 20 kept), and it never starts anything: a distro that isn't running is
refused, because `wsl -d` would boot it (and Docker with it). `--follow` streams until Ctrl+C,
which ends it normally.
"""

from __future__ import annotations

import typer

from comfyui_sandbox import logs
from comfyui_sandbox.errors import CliError, NotReady, UsageError
from comfyui_sandbox.output import Outcome
from comfyui_sandbox.steps import compose_argv


def _transcripts(session, last: bool) -> Outcome:
    paths = logs.transcripts(session.deps.app_dir)
    shown = [str(p) for p in paths]
    if not last:
        return Outcome("\n".join(shown), {"transcripts": shown})
    if not paths:
        return Outcome("", {"transcripts": [], "path": None, "content": None})
    content = paths[0].read_text(encoding="utf-8", errors="replace")
    return Outcome(content.rstrip("\n"), {"transcripts": shown, "path": shown[0], "content": content})


def register(app: typer.Typer) -> None:
    @app.command("logs")
    def logs_command(
        ctx: typer.Context,
        tail: int | None = typer.Option(None, "--tail", min=0, help="Show only the last N lines."),
        follow: bool = typer.Option(False, "--follow", help="Keep showing new lines until Ctrl+C."),
        cli: bool = typer.Option(False, "--cli", help="List the CLI's own transcripts, newest first."),
        last: bool = typer.Option(False, "--last", help="With --cli: print the newest transcript."),
    ) -> Outcome:
        """Show the container log, or with --cli the transcripts of this tool's commands."""
        session = ctx.obj
        if last and not cli:
            raise UsageError("--last only works together with --cli.")
        if cli:
            if tail is not None or follow:
                raise UsageError("--tail and --follow are for the container log, not --cli.")
            return _transcripts(session, last)

        install = session.resolve_install()
        argv = compose_argv(install, "logs", "--no-color")
        if tail is not None:
            argv += ["--tail", str(tail)]
        wsl = session.wsl()
        if not any(name.lower() == install.distro.lower() for name in wsl.list_running()):
            raise NotReady(
                f"{install.distro} isn't running, so there is no container log to show, and showing it "
                "would start WSL and Docker. Start the sandbox first, or read the CLI's own "
                "transcripts with 'comfyui-sandbox logs --cli'."
            )
        data = {"distro": install.distro, "tail": tail, "follow": follow}
        if not follow:
            done = wsl.run_command(install.distro, install.linux_user, argv)
            if done.returncode != 0:
                raise CliError(f"docker compose logs exited {done.returncode}: {done.err().strip()}")
            text = done.out()
            return Outcome(text.rstrip("\n"), {**data, "log": text})

        out = session.out

        def show(line: str) -> None:
            # The lines are the result, so they go to stdout; under --json stdout holds only
            # the final document, so they go to stderr there.
            if out.json_mode:
                out.progress(line)
            else:
                out.stdout.write(line + "\n")
                out.stdout.flush()

        try:
            done = wsl.run_command(install.distro, install.linux_user, [*argv, "--follow"], on_line=show, timeout=None)
        except KeyboardInterrupt:
            return Outcome("", data)
        if done.returncode != 0:
            raise CliError(f"docker compose logs exited {done.returncode}.")
        return Outcome("", data)
