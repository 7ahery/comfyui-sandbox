"""Helpers the install steps and the day-2 commands share (recreate, rebuild, update, node, backup).

They take only what they use: a `Wsl`, an `Output` and the install (anything with `distro` and
`linux_user`: an `Install` record or the install's `Answers`).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from comfyui_sandbox import plans
from comfyui_sandbox.cli_command import PROG
from comfyui_sandbox.errors import CliError, NotReady
from comfyui_sandbox.instance import instance_name
from comfyui_sandbox.keepalive import Keepalive
from comfyui_sandbox.output import Outcome, Output, PlannedStep
from comfyui_sandbox.runner import Completed
from comfyui_sandbox.wsl import CALL_TIMEOUT, Wsl


class Subject(Protocol):
    """What the helpers read from an install."""

    distro: str
    linux_user: str


# apt, image pulls, image builds and updates can be slow on a bad connection: an hour.
LONG_STEP_TIMEOUT = 3600
# `docker compose stop`: ComfyUI gets its grace period to shut down.
STOP_TIMEOUT = 120

# The steps these helpers say themselves, as a step table plans them (comfyui_sandbox.plans), with
# their expected seconds: `ending_stopped`'s stop first, and `container_up`'s start for one step.
STOP_COMFYUI = PlannedStep("Stop ComfyUI", 10)
START_FOR_STEP = PlannedStep("Start ComfyUI for this step", 20)


# wsl.exe died before the distro ran the command. Exit 4294967295 is -1. Blaming the step
# (for example "Creating the Linux user") hides that, and `prereqs` is what updates WSL.
WSL_UNEXPECTED = "Wsl/Service/E_UNEXPECTED"


def ensure_ok(done: Completed, what: str) -> None:
    """Raise a CliError naming `what` and the last output line when the command failed."""
    if done.returncode != 0:
        text = done.out() + "\n" + done.err()
        if WSL_UNEXPECTED in text:
            raise CliError(
                f"{what} failed because WSL could not start the distro ({WSL_UNEXPECTED}). "
                "Run 'comfyui-sandbox prereqs' to update WSL, then retry."
            )
        lines = [line for line in text.splitlines() if line.strip()]
        said = f": {lines[-1].strip()}" if lines else ""
        raise CliError(f"{what} failed (exit {done.returncode}){said}")


def project_path(user: str) -> str:
    """The project folder of a Linux user (where deploy_comfyui.sh puts the docker assets)."""
    return f"/home/{user}/comfyui-docker"


def compose_argv(who: Subject, *args: str) -> list[str]:
    """`docker compose <args>` run inside the project folder: the argv every command runs.

    It changes into the folder rather than passing --project-directory: compose resolves the
    relative COMPOSE_FILE in `.env` against the working directory, and wsl.exe starts in `/`.
    """
    return ["env", "--chdir", project_path(who.linux_user), "docker", "compose", *args]


# The sign, in the project folder, that ComfyUI was stopped on purpose: an open run window's
# watch.sh ends at once on it instead of waiting to see whether the container restarts.
STOP_ASKED = ".stop-asked"


def stop_argv(who: Subject) -> list[str]:
    """`docker compose stop`, as every stop comfyui-sandbox asks for runs it: leaving STOP_ASKED
    first, so an open run window closes with the container rather than a minute later (its
    run-window marker held meanwhile, home would show the sandbox as starting)."""
    stop = compose_argv(who, "stop")
    return [*stop[:3], "sh", "-c", f'touch {STOP_ASKED}; exec "$@"', "sh", *stop[3:]]


def container_running(wsl: Wsl, who: Subject, *, timeout: float | None = CALL_TIMEOUT) -> bool:
    """Whether the ComfyUI container is up (a read only probe: it never starts anything)."""
    up = wsl.run_command(
        who.distro, who.linux_user, compose_argv(who, "ps", "--status", "running", "--quiet"), timeout=timeout
    )
    return up.returncode == 0 and bool(up.out().strip())


def linux_ids(wsl: Wsl, who: Subject) -> tuple[int, int]:
    """The (uid, gid) of the install's Linux user."""
    ids = []
    for option in ("-u", "-g"):
        done = wsl.run_command(who.distro, who.linux_user, ["id", option])
        try:
            ids.append(int(done.out().strip()))
        except ValueError:
            raise CliError(f"Could not read the id of {who.linux_user} (id {option} exit {done.returncode}).") from None
    return ids[0], ids[1]


@contextmanager
def keep_distro_up(session, who: Subject):
    """Hold the distro up while the command works, so it can't idle out mid-build."""
    keepalive = Keepalive(session.wsl(), session.deps.winapi)
    keepalive.start(who.distro)
    try:
        yield
    finally:
        keepalive.stop()


def backup_stamp() -> str:
    """`YYYYMMDD-HHMMSS` in UTC: the suffix of backup files and `.env` / compose backups."""
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


# Where the Windows models folder is mounted in the distro (harden.sh writes the fstab line).
WINDOWS_MODELS_MOUNT = "/mnt/models"


def models_in_distro(mode: str, path: str) -> str:
    """The models folder as the distro sees it: the Windows mount, or the WSL path itself."""
    return WINDOWS_MODELS_MOUNT if mode == "windows" else path


# ComfyUI's first start installs PyTorch (several GB): up to an hour on a slow line.
FIRST_START_MINUTES = 60
RESTART_MINUTES = 10
WAIT_INTERVAL = 30
# Lines of the container log that explain a first start that is not a plain fresh install.
NOTABLE = ("[setup] Previous install did not finish", "[setup] Previous install is broken")
REPORT_START = "[error] the container is"


def wait_for_comfyui(
    wsl: Wsl, out: Output, who: Subject, minutes: int, what: str, retry: str = "Safe to retry."
) -> None:
    """Run wait_ready.sh: it prints the phase, elapsed time and data growth as it goes.

    Exit 0 is ready. Exit 1 means the container stopped: the error carries its last log
    lines ("not retrying" included). Exit 2 means not ready in time (exit 10, safe to retry).
    """
    warned: set[str] = set()

    def show(line: str) -> None:
        if line.startswith(NOTABLE) and line not in warned:
            warned.add(line)
            out.warn(line)
        elif not line.startswith(NOTABLE):
            out.progress(line)

    env = {"PROJECT_DIR": project_path(who.linux_user), "TIMEOUT_MIN": str(minutes), "INTERVAL": str(WAIT_INTERVAL)}
    done = wsl.run_script(who.distro, who.linux_user, "wait_ready.sh", env, on_line=show, timeout=minutes * 60 + 600)
    if done.returncode == 0:
        return
    lines = [line for line in done.out().splitlines() if line.strip()]
    if done.returncode == 2:
        raise NotReady(f"ComfyUI was not ready {what} after {minutes} minutes. It may still be installing. {retry}")
    start = next((i for i, line in enumerate(lines) if line.startswith(REPORT_START)), max(len(lines) - 40, 0))
    report = "\n".join(lines[start:]).removeprefix("[error] ")
    raise CliError(f"ComfyUI stopped {what}: {report}")


def models_structure(wsl: Wsl, out: Output, who: Subject, models_dir: str, *, create: bool) -> None:
    """Compare the models folder with the folders this ComfyUI version expects. A WSL folder
    gets the missing ones (`create`); a Windows folder is only checked (the person agreed to
    the folders at intake, and later changes need their consent)."""
    env = {"MODELS_DIR": models_dir, "PROJECT_DIR": project_path(who.linux_user)}
    if create:
        env["CREATE"] = "1"
    ensure_ok(
        wsl.run_script(who.distro, who.linux_user, "models_structure.sh", env, on_line=out.progress),
        "Creating the models structure",
    )


def manager_security(wsl: Wsl, out: Output, who: Subject, level: str) -> None:
    """Write the Manager security level explicitly (`normal` or `weak`, ...)."""
    env = {"SECURITY_LEVEL": level, "PROJECT_DIR": project_path(who.linux_user)}
    ensure_ok(
        wsl.run_script(who.distro, who.linux_user, "manager_settings.sh", env, on_line=out.progress),
        "Writing the Manager security level",
    )


def restart_container(wsl: Wsl, out: Output, who: Subject) -> None:
    ensure_ok(
        wsl.run_command(who.distro, who.linux_user, compose_argv(who, "restart"), on_line=out.progress, timeout=300),
        "Restarting ComfyUI",
    )


@dataclass
class Context:
    """What the steps of a day-2 command's step table (comfyui_sandbox.plans) share while they run: the
    command's session and install, and an ExitStack for what a step sets up for the steps after it (the
    distro held up, the container started for these steps), which is closed, in reverse, once the last
    step has run or one has failed. A command with more to share (what an earlier step found, a name
    chosen up front) subclasses it.

    Why a stack rather than `with` blocks around the steps: a table runs its steps one by one, so what
    spans several of them can't be a block in one function; a step enters it here instead."""

    session: Any
    install: Any  # an Install record (Subject is all the helpers read)
    stack: ExitStack = field(default_factory=ExitStack)

    @property
    def out(self) -> Output:
        return self.session.out

    def wsl(self) -> Wsl:
        return self.session.wsl()

    def keep_distro_up(self) -> None:
        """Holds the distro up from now until the last step has run."""
        self.stack.enter_context(keep_distro_up(self.session, self.install))

    def start_for_these_steps(self) -> None:
        """Starts ComfyUI (if it's stopped) until the last step has run: the step that calls this says
        so in its title (START_FOR_STEP), so the start isn't announced again here."""
        self.stack.enter_context(container_up(self.wsl(), self.out, self.install, for_step=True, announce=False))

    def run(self, chosen: Sequence[plans.Step]) -> None:
        """Takes the chosen steps (already announced) in order, then closes what they set up."""
        with self.stack:
            plans.run(self.out, chosen, self)


@contextmanager
def container_up(wsl: Wsl, out: Output, who: Subject, *, for_step: bool = False, announce: bool = True):
    """Make sure the container is up while the block runs (ComfyUI is stopped by default).

    Yields a dict `{"started_comfyui": bool}`: True when this had to start it, in which case it is
    stopped again afterwards (also when the block failed). By default (`backup`) that leaves ComfyUI as
    it was found. With `for_step` the block is one step of a command that ends stopped
    (`ending_stopped`): it starts ComfyUI just for that step and stops it again after.

    announce: say the start as a step of its own, when it has to start it. A step table that plans
    the start as a step (START_FOR_STEP, comfyui_sandbox.plans) has said it already, needed or not,
    so it passes False.
    """
    state = {"started_comfyui": False}
    if not container_running(wsl, who):
        if announce:
            out.step(START_FOR_STEP.title if for_step else "ComfyUI is stopped: starting it for this command")
        ensure_ok(
            wsl.run_command(
                who.distro, who.linux_user, compose_argv(who, "up", "-d"), on_line=out.progress, timeout=300
            ),
            "docker compose up -d",
        )
        state["started_comfyui"] = True
    try:
        yield state
    finally:
        if state["started_comfyui"]:
            done = _compose_stop(wsl, who)
            if done.returncode == 0:
                out.info(
                    "ComfyUI is stopped again."
                    if for_step
                    else "ComfyUI was stopped before this command, so it is stopped again."
                )
            else:
                out.warn(
                    f"docker compose stop exited {done.returncode}: ComfyUI is still running. "
                    f"Stop it with '{PROG} stop'."
                )


def _compose_stop(wsl: Wsl, who: Subject) -> Completed:
    return wsl.run_command(who.distro, who.linux_user, stop_argv(who), timeout=STOP_TIMEOUT)


def stop_container(wsl: Wsl, who: Subject, out: Output | None = None, *, running: bool | None = None) -> bool:
    """`docker compose stop` when ComfyUI is running (naming the step on `out`, if given).
    True when it is stopped now. running: whether it runs, when the caller has just asked."""
    if not (container_running(wsl, who) if running is None else running):
        return True
    if out is not None:
        out.step(STOP_COMFYUI.title)
    return _compose_stop(wsl, who).returncode == 0


def ending_stopped(
    session, who: Subject, work: Callable[[], Outcome], plan: Sequence[PlannedStep] | None = None
) -> Outcome:
    """The rule of docs/adr/0015: stop ComfyUI if it is running, run `work`, and end with
    ComfyUI stopped (also when `work` failed). A step of `work` that needs the container starts it just for
    itself (`container_up(for_step=True)`). Nothing is asked: an open run window closes with the container,
    and the output says so and how to reopen it.

    Adds `container` ("stopped") and `run_window_closed` to the result's (or the error's) data, and says
    that ComfyUI is stopped and to run `comfyui-sandbox start`.

    plan: the steps `work` will take (comfyui_sandbox.plans). Given, the whole command's plan is
    announced here, once it's known whether ComfyUI has to be stopped first, which is then the
    plan's first step. The stop at the end is no step of its own: it's quick, and usually finds
    ComfyUI stopped already (a step that started it stopped it again).
    """
    out, wsl = session.out, session.wsl()
    window = session.deps.winapi.instance_exists(instance_name(who.distro))
    start = command_for("start", session, who.distro)
    running = container_running(wsl, who)
    if plan is not None:
        out.plan([*([STOP_COMFYUI] if running else []), *plan])
    if not stop_container(wsl, who, out, running=running):
        raise CliError(
            "docker compose stop failed: ComfyUI is still running. Stop it with "
            f"'{command_for('stop', session, who.distro)}' and try again. Nothing was changed.",
            data={"container": "running", "run_window_closed": False},
        )
    outcome, failure = None, None
    try:
        outcome = work()
    except BaseException as exc:
        failure = exc
    stopped = stop_container(wsl, who)

    stopped_text = f"ComfyUI is stopped: run '{start}' to start it again."
    closed_text = f"The run window closed when ComfyUI was stopped, '{start}' reopens it."
    data = {"container": "stopped" if stopped else "running", "run_window_closed": window}
    if stopped:
        out.info(stopped_text)
        if window:
            out.info(closed_text)
    else:
        out.warn(f"ComfyUI is still running, stop it with '{command_for('stop', session, who.distro)}'.")
    if failure is not None:
        if isinstance(failure, CliError):
            failure.data = {**failure.data, **data}
        raise failure
    assert outcome is not None
    if not stopped:
        raise CliError(
            "The command finished, but ComfyUI could not be stopped afterwards.", data={**outcome.data, **data}
        )
    outcome.message = " ".join(part for part in (outcome.message, stopped_text, closed_text if window else "") if part)
    outcome.data = {**outcome.data, **data}
    return outcome


def command_for(verb: str, session, distro: str) -> str:
    """`comfyui-sandbox <verb>`, naming the distro when `session` records more than one install."""
    flag = f" --distro {distro}" if len(session.installs().all()) > 1 else ""
    return f"{PROG} {verb}{flag}"


def keep_waiting_hint(verb: str, session, distro: str) -> str:
    """The retry advice for a first start that timed out: the exact command that continues it
    (the distro is named when `session` records more than one install). The setup runs again, but the
    pip/uv caches persist in data/, so the packages already downloaded are not fetched twice."""
    return (
        f"Wait a while, then run '{command_for(verb, session, distro)}' again: "
        "the setup runs again, but the downloaded packages are kept, so it does not fetch them twice."
    )


def wait_first_start(wsl: Wsl, out: Output, who: Subject, retry: str) -> None:
    wait_for_comfyui(wsl, out, who, FIRST_START_MINUTES, "during its first start", retry=retry)


def wait_after_restart(wsl: Wsl, out: Output, who: Subject) -> None:
    wait_for_comfyui(wsl, out, who, RESTART_MINUTES, "after the restart")
