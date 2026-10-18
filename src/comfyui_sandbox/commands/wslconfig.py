"""`comfyui-sandbox wslconfig show|apply`, and the `.wslconfig` step of `install` (ticket 08).

Ports wslconfig.sh. `.wslconfig` is global (every WSL2 distro) and only takes effect once WSL
restarts, so restarting is asked separately from writing the file: "now" or "later". Stopping
distros that are not ours (`wsl --shutdown`) is a destructive action and goes through the shared
gate (`comfyui_sandbox.destructive`); our own distros are stopped with `wsl --terminate`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import typer

from comfyui_sandbox import hostinfo, intake, wslconfig
from comfyui_sandbox.destructive import confirm_destructive
from comfyui_sandbox.errors import AnswerMissing, CliError, UsageError
from comfyui_sandbox.intake import InvalidAnswer
from comfyui_sandbox.output import Outcome
from comfyui_sandbox.wslconfig import Limits

RESTART_CHOICES = ("now", "later")
NOW = "Shut down now (Recommended)"
LATER = "Later (I'll restart WSL myself)"


def config_path(home: Path) -> Path:
    return home / ".wslconfig"


def read_raw(home: Path) -> bytes | None:
    try:
        return config_path(home).read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CliError(f"Could not read {config_path(home)}: {exc}") from None


def suggested_limits(session) -> Limits:
    facts = hostinfo.probe(session.deps.runner)
    if not (facts.ram_gb and facts.cpus):
        raise CliError(
            "Could not read this machine's RAM and CPU count from Windows, so there are no limits to suggest."
        )
    return wslconfig.suggest(facts.ram_gb, facts.cpus)


def check_restart_flag(value: str | None) -> str | None:
    if value is None:
        return None
    if value.strip().lower() not in RESTART_CHOICES:
        raise UsageError("--shutdown: use now or later.")
    return value.strip().lower()


# -- applying -----------------------------------------------------------------------------


@dataclass
class Applied:
    path: Path
    limits: tuple[int, int, int]  # memory GB, processors, swap GB
    changed: bool
    backup: Path | None
    restart: str  # "not_needed" | "now" | "later"

    def data(self) -> dict[str, Any]:
        memory, processors, swap = self.limits
        return {
            "path": str(self.path),
            "limits": {"memory_gb": memory, "processors": processors, "swap_gb": swap},
            "changed": self.changed,
            "backup": str(self.backup) if self.backup else None,
            "restart": self.restart,
            "restart_pending": self.restart == "later",
        }


def _backup(path: Path, raw: bytes) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup, n = path.with_name(f"{path.name}.bak-{stamp}"), 1
    while backup.exists():
        n += 1
        backup = path.with_name(f"{path.name}.bak-{stamp}-{n}")
    backup.write_bytes(raw)
    return backup


def _write(path: Path, data: bytes) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_bytes(data)
    os.replace(temp, path)


def _choose_restart(session, running: list[str], foreign: list[str], flag: str | None) -> str:
    if flag is not None:
        return flag
    if session.yes:
        # --yes never stops distros that aren't ours.
        return "later" if foreign else "now"
    names = ", ".join(running)
    if session.non_interactive:
        raise AnswerMissing(
            f"WSL is running ({names}), and the new limits apply after it restarts. "
            "Re-run with `--shutdown now` (stopping distros that aren't ours also needs "
            "`--confirm <distro>`) or `--shutdown later`.",
            "--shutdown",
        )
    picked = session.deps.prompter.select(
        f"The new limits apply after WSL restarts. Running now: {names}. Restart WSL?", [NOW, LATER], default=NOW
    )
    return "now" if picked == NOW else "later"


def apply_limits(
    session,
    limits: tuple[int, int, int],
    *,
    shutdown: str | None,
    confirm: str | None,
    confirm_names: tuple[str, ...] = (),
) -> Applied:
    """Merge `limits` (memory GB, processors, swap GB) into the .wslconfig, with a backup, then
    restart WSL if the user wants it.

    Everything that can refuse or ask (restart now or later, the destructive gate) happens
    before the file is written. `confirm_names` are extra distro names that confirm a shutdown
    (install passes the distro it is creating). Call `session.transcript()` first.
    """
    out, home = session.out, session.deps.home
    shutdown = check_restart_flag(shutdown)
    path, raw = config_path(home), read_raw(home)
    merged = wslconfig.merge(raw, *limits)
    changed = raw is None or wslconfig.decode(merged) != wslconfig.decode(raw)
    if not changed:
        out.info(f"{path} already has these limits.")
        return Applied(path, limits, False, None, "not_needed")

    wsl = session.wsl()
    running = wsl.list_running() if wsl.installed() else []
    ours = {i.distro.lower() for i in session.installs().all()}
    foreign = [name for name in running if name.lower() not in ours]
    restart = "not_needed"
    if running:
        restart = _choose_restart(session, running, foreign, shutdown)
        if restart == "now" and foreign:
            confirm_destructive(
                session,
                [*confirm_names, *foreign],
                confirm=confirm,
                warning=f"`wsl --shutdown` stops every running WSL distro, including {', '.join(foreign)}. "
                "Unsaved work in them is lost.",
            )

    backup = _backup(path, raw) if raw is not None else None
    if backup:
        out.info(f"Backup: {backup}")
    _write(path, merged)
    memory, processors, swap = limits
    out.info(f"Wrote {path}: memory={memory}GB processors={processors} swap={swap}GB")

    if restart == "now":
        if foreign:
            out.info("Shutting down WSL (wsl --shutdown).")
            done = wsl.shutdown()
            if done.returncode != 0:
                raise CliError(f"wsl --shutdown failed (exit {done.returncode}).")
        else:
            for name in running:
                out.info(f"Stopping {name} (wsl --terminate).")
                if wsl.terminate(name).returncode != 0:
                    raise CliError(f"wsl --terminate {name} failed.")
    elif restart == "later":
        out.warn(
            "The new limits are not active until WSL restarts: run 'wsl --shutdown' (stops all distros) "
            "or reboot Windows."
        )
    else:
        out.info("The new limits apply the next time WSL starts.")
    return Applied(path, limits, True, backup, restart)


# -- the commands ----------------------------------------------------------------------------


def _limit_text(limits: Limits) -> str:
    return (
        f"Host: {limits.host_ram_gb} GB RAM, {limits.host_cpus} CPUs. Suggested limits: "
        f"memory {limits.memory_gb} GB (or {limits.memory_low_gb} GB for more room for Windows), "
        f"{limits.processors} processors, swap {limits.swap_gb} GB"
    )


def _resolve_limits(session, flag: str | None, suggested: Limits) -> tuple[int, int, int]:
    def parse(text: str) -> tuple[int, int, int]:
        return intake.parse_limits(text, suggested)

    if flag is not None:
        try:
            return parse(flag)
        except InvalidAnswer as exc:
            raise UsageError(f"--limits: {exc}") from None
    if session.yes:
        return parse("suggested")
    if session.non_interactive:
        raise AnswerMissing(
            "Missing answer. Re-run with --limits suggested|low|"
            "'memory=<GB> processors=<n> swap=<GB>', or --yes for the suggested limits.",
            "--limits",
        )
    prompter = session.deps.prompter
    suggested_label = (
        f"Suggested: {suggested.memory_gb} GB RAM, {suggested.processors} CPUs, {suggested.swap_gb} GB swap"
    )
    low_label = (
        f"More room for Windows: {suggested.memory_low_gb} GB RAM, {suggested.processors} CPUs, "
        f"{suggested.swap_gb} GB swap"
    )
    other = "Other (type values)"
    picked = prompter.select("WSL limits?", [f"{suggested_label} (Recommended)", low_label, other])
    if picked == other:

        def problem(text: str) -> str | None:
            try:
                parse(text)
            except InvalidAnswer as exc:
                return str(exc)
            return None

        return parse(prompter.text("Limits (GB), e.g. memory=48 processors=16 swap=16:", validate=problem))
    return parse("low" if picked == low_label else "suggested")


def register(app: typer.Typer) -> None:
    group = typer.Typer(help="Show or change the WSL memory, CPU and swap limits in .wslconfig.", no_args_is_help=True)
    app.add_typer(group, name="wslconfig")

    @group.command()
    def show(ctx: typer.Context) -> Outcome:
        """Print the suggested limits and the current .wslconfig (changes nothing)."""
        session = ctx.obj
        limits = suggested_limits(session)
        path, raw = config_path(session.deps.home), read_raw(session.deps.home)
        content = wslconfig.decode(raw) if raw is not None else None
        session.out.info(_limit_text(limits))
        session.out.info(
            f"Without a .wslconfig WSL gets about 50% of the RAM ({limits.host_ram_gb // 2} GB), "
            "all CPUs, and swap of 25% of its memory."
        )
        session.out.info(f"== current {path} ==")
        session.out.info(content.rstrip("\n") if content is not None else "(file does not exist)")
        return Outcome(
            f"Suggested WSL limits: {limits.memory_gb} GB RAM, {limits.processors} CPUs, {limits.swap_gb} GB swap.",
            {"path": str(path), "exists": raw is not None, "content": content, "limits": limits.as_dict()},
        )

    @group.command()
    def apply(
        ctx: typer.Context,
        limits: str | None = typer.Option(
            None, "--limits", help="suggested, low, or e.g. 'memory=48 processors=16 swap=16' (GB)."
        ),
        shutdown: str | None = typer.Option(None, "--shutdown", help="Restart WSL so the limits apply: now or later."),
        confirm: str | None = typer.Option(
            None, "--confirm", help="Confirms stopping other WSL distros: the name of a running one."
        ),
    ) -> Outcome:
        """Merge memory, processors and swap into .wslconfig (other settings kept, backup written)."""
        session = ctx.obj
        check_restart_flag(shutdown)
        suggested = suggested_limits(session)
        chosen = _resolve_limits(session, limits, suggested)
        session.transcript()
        applied = apply_limits(session, chosen, shutdown=shutdown, confirm=confirm)
        memory, processors, swap = chosen
        message = f"Set memory={memory}GB processors={processors} swap={swap}GB in {applied.path}."
        if not applied.changed:
            message = f"{applied.path} already has these limits."
        return Outcome(message, applied.data())
