"""`comfyui-sandbox install`: set up a new sandboxed ComfyUI (resumable).

install is a step table (comfyui_sandbox.plans): `INTAKE` (check the machine, ask the questions,
preflight them and save the answers in installs.json) and then `STEPS` (create the distro, harden
it, Docker, the NVIDIA toolkit, deploy, the first start, ...). The questions and flags live in
`comfyui_sandbox.intake`, the preflight's in `comfyui_sandbox.preflight`.

The setup is announced as one plan before its first step, so a front end can show all of it with
a progress bar weighted by each step's expected seconds (the TUI's live view). A new install's
answers decide two of its steps (the WSL limits, the NVIDIA toolkit) but are only known after the
questions, so its plan is announced once, after preflight, with only the steps they take; the
intake before it runs unplanned (see `install()`).

Resume = rerun every step of `STEPS` with the saved answers; each step does nothing when already
done (docs/adr/0006). Nothing is saved before preflight passes, so a cancelled question (exit 130)
leaves nothing to resume. A resume may first change the answers the setup only writes into the
.env and the Manager's settings (`intake.AMENDABLE`: a release number that doesn't exist, say),
which its deploy writes again.
"""

from __future__ import annotations

import io
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

import typer

from comfyui_sandbox import envfile, intake, payload, plans, preflight, steps, wslconfig
from comfyui_sandbox.commands import check, startstop
from comfyui_sandbox.commands import launcher as launcher_command
from comfyui_sandbox.commands import verify as verify_command
from comfyui_sandbox.commands import wslconfig as wslconfig_command
from comfyui_sandbox.errors import AnswerMissing, CliError, ExitCode, UsageError, unregistered_message
from comfyui_sandbox.intake import Answers
from comfyui_sandbox.keepalive import Keepalive
from comfyui_sandbox.output import Outcome, Output, PlannedStep
from comfyui_sandbox.runner import CommandTimeout
from comfyui_sandbox.state import Gpu, Install, Installs, Models, now
from comfyui_sandbox.steps import LONG_STEP_TIMEOUT, ensure_ok, project_path
from comfyui_sandbox.wsl import ROOT, UBUNTU, Wsl


def new_record(a: Answers) -> Install:
    """A fresh in-progress installs.json entry for these answers."""
    return Install(
        distro=a.distro,
        linux_user=a.linux_user,
        port=a.port,
        models=Models(a.models, a.models_dir),
        gpu=Gpu(a.gpu, a.gpu_name if a.gpu == "nvidia" else None),
        cuda_version=a.cuda_version,
        location=a.location,
        deployed_by=package_version("comfyui-sandbox"),
        answers=a.to_dict(),
    )


def _current_memory_gb(home: Path) -> int | None:
    try:
        return wslconfig.read_memory_gb((home / ".wslconfig").read_text("utf-8", errors="replace"))
    except OSError:
        return None


# -- steps ----------------------------------------------------------------------


@dataclass
class Context:
    """What install's steps share: the session, and what the steps before have found out.

    A new install starts with no answers: its first three steps (the check, the questions and
    the preflight) find them, and the steps after use them. A resumed one starts with the
    answers it saved, and takes only the steps after those three."""

    wsl: Wsl
    out: Output
    answers: Answers | None  # None until the questions have been answered (a new install)
    keepalive: Keepalive
    session: Any = None  # the cli.Session, for steps that ask or gate (ticket 08)
    record: Install | None = None  # the installs.json entry being installed
    shutdown: str | None = None  # --shutdown now|later
    confirm: str | None = None  # --confirm <sandbox>
    data: dict[str, Any] = field(default_factory=dict)  # extra keys for the result's `data`
    flags: intake.Flags | None = None  # what the command line gave (a new install only)
    installs: Installs | None = None  # installs.json, which a new install's record is saved into
    facts: intake.Facts | None = None  # what the check found: the questions' defaults

    @property
    def resumed(self) -> bool:
        """Whether this install carries on from saved answers (it had them from the start)."""
        return self.flags is None

    # The fields above that start as None are filled in by the step that finds them, and the step
    # order guarantees every later step sees them set. These say so to the type checker, and a None
    # here is a bug in that order, not something the user did.

    @property
    def known_answers(self) -> Answers:
        return _known(self.answers, "the answers")

    @property
    def known_record(self) -> Install:
        return _known(self.record, "the installs.json entry")

    @property
    def known_installs(self) -> Installs:
        return _known(self.installs, "installs.json")

    @property
    def known_facts(self) -> intake.Facts:
        return _known(self.facts, "what the check found")

    @property
    def known_flags(self) -> intake.Flags:
        return _known(self.flags, "the command line's answers")


def _known[T](value: T | None, what: str) -> T:
    if value is None:
        raise AssertionError(f"A step needed {what} before the step that finds them ran.")
    return value


# -- the intake phases (a new install only) --------------------------------------------


def check_machine(ctx: Context) -> None:
    """`check`, quietly: stops on a machine that isn't ready; what it finds gives the questions
    their defaults."""
    session = ctx.session
    machine = _machine(session)
    taken = list(machine.get("wsl", {}).get("distros") or []) + [i.distro for i in ctx.known_installs.all()]
    ctx.facts = intake.Facts.from_check(machine, taken, _current_memory_gb(session.deps.home))


def ask_questions(ctx: Context) -> None:
    """The intake: flags, defaults (--yes) or questions, then the summary to confirm."""
    session, out = ctx.session, ctx.out

    def show(lines: list[str]) -> None:
        for line in lines:
            out.info(line)

    ctx.answers = intake.gather(
        ctx.known_facts,
        ctx.known_flags,
        session.deps.prompter,
        yes=session.yes,
        interactive=not session.non_interactive,
        show=show,
    )


def run_preflight(ctx: Context) -> None:
    """Checks the answers against this machine (a taken port or name is asked again), makes the
    Windows models folders, and saves the record: from here on the install is resumable."""
    answers = ctx.answers = preflight.run(ctx.session, ctx.known_answers)
    if answers.models == "windows":
        made = intake.create_models_folders(Path(answers.models_dir))
        if made:
            ctx.out.info(f"Created {len(made)} model folder(s) in {answers.models_dir}.")
    ctx.record = new_record(answers)
    ctx.known_installs.put(ctx.record)
    ctx.known_installs.save()
    ctx.out.info(f"Saved the answers for {answers.distro} in {ctx.known_installs.path}.")


def apply_wslconfig(ctx: Context) -> None:
    """Merge the chosen limits into .wslconfig before the distro exists, then restart WSL if wanted."""
    a = ctx.known_answers
    limits = _known(a.limits, "the WSL limits")  # the step runs only when memory is tuned
    applied = wslconfig_command.apply_limits(
        ctx.session, limits, shutdown=ctx.shutdown, confirm=ctx.confirm, confirm_names=(a.distro,)
    )
    ctx.data["wsl_restart_pending"] = applied.restart == "later"


def create_distro(ctx: Context) -> None:
    a = ctx.known_answers
    if ctx.wsl.exists(a.distro):
        ctx.out.info(f"Distro {a.distro} already exists.")
    else:
        done = ctx.wsl.install(a.distro, image=UBUNTU, location=a.location, on_line=lambda _line: None)
        ensure_ok(done, f"Creating the distro ({UBUNTU}), if WSL rejected it, run 'wsl --update' and retry")
    # From here on a missing distro means it was removed by hand (ticket 22).
    record = ctx.record
    if record is not None and record.distro_created_at is None:
        record.distro_created_at = now()
        installs = ctx.session.installs()
        installs.put(record)
        installs.save()


def create_user(ctx: Context) -> None:
    a = ctx.known_answers
    ensure_ok(
        ctx.wsl.run_script(a.distro, ROOT, "create_user.sh", {"LINUX_USER": a.linux_user}), "Creating the Linux user"
    )


def harden(ctx: Context) -> None:
    a = ctx.known_answers
    env = {"LINUX_USER": a.linux_user, "MODELS_MODE": a.models}
    env["MODELS_DIR" if a.models == "wsl" else "MODELS_WIN"] = a.models_dir
    ensure_ok(ctx.wsl.run_script(a.distro, ROOT, "harden.sh", env), "Hardening the distro")


def terminate(ctx: Context) -> None:
    ensure_ok(ctx.wsl.terminate(ctx.known_answers.distro), "Stopping the distro")


# The daemon defaults install_docker.sh writes to /etc/docker/daemon.json.
DAEMON_DEFAULTS = {"no-new-privileges": True, "log-driver": "local", "log-opts": {"max-size": "20m", "max-file": "3"}}


def _docker_done(ctx: Context) -> bool:
    """Docker runs and its daemon.json has the hardened defaults."""
    a = ctx.known_answers
    config = ctx.wsl.run_command(a.distro, ROOT, ["cat", "/etc/docker/daemon.json"])
    try:
        found = json.loads(config.out())
    except ValueError:
        return False
    if config.returncode != 0 or not isinstance(found, dict):
        return False
    if any(found.get(key) != value for key, value in DAEMON_DEFAULTS.items()):
        return False
    return ctx.wsl.run_command(a.distro, ROOT, ["systemctl", "is-active", "docker"]).returncode == 0


def _toolkit_done(ctx: Context) -> bool:
    """Docker knows the `nvidia` runtime."""
    done = ctx.wsl.run_command(ctx.known_answers.distro, ROOT, ["docker", "info", "--format", "{{json .Runtimes}}"])
    try:
        runtimes = json.loads(done.out())
    except ValueError:
        return False
    return done.returncode == 0 and isinstance(runtimes, dict) and "nvidia" in runtimes


def install_docker(ctx: Context) -> None:
    a = ctx.known_answers
    if _docker_done(ctx):
        ctx.out.info("Docker Engine is already installed with the hardened defaults.")
        return
    ensure_ok(
        ctx.wsl.run_script(
            a.distro,
            ROOT,
            "install_docker.sh",
            {"LINUX_USER": a.linux_user},
            on_line=ctx.out.progress,
            timeout=LONG_STEP_TIMEOUT,
        ),
        "Installing Docker Engine",
    )


def install_toolkit(ctx: Context) -> None:
    a = ctx.known_answers
    if _toolkit_done(ctx):
        ctx.out.info("The NVIDIA Container Toolkit is already installed.")
        return
    ensure_ok(
        ctx.wsl.run_script(
            a.distro, ROOT, "install_nvidia_toolkit.sh", on_line=ctx.out.progress, timeout=LONG_STEP_TIMEOUT
        ),
        "Installing the NVIDIA Container Toolkit",
    )


def prepare_data(ctx: Context) -> None:
    """Hold the distro up (the keepalive lasts until install ends), then create the project
    and data folders owned by the Linux user, so Docker never creates them as root."""
    a = ctx.known_answers
    ctx.keepalive.start(a.distro)
    ensure_ok(
        ctx.wsl.run_script(a.distro, ROOT, "prepare_data.sh", {"LINUX_USER": a.linux_user}, on_line=ctx.out.progress),
        "Preparing the data folder",
    )


def deploy(ctx: Context) -> None:
    """Send the docker assets and the `.env` made from the answers; build and start the
    container. Returns before ComfyUI is ready."""
    a = ctx.known_answers
    env_text = envfile.render(a, *steps.linux_ids(ctx.wsl, a))
    env = {"PAYLOAD": payload.build(env_text), "MODELS_DIR": envfile.models_dir(a)}
    ensure_ok(
        ctx.wsl.run_script(
            a.distro, a.linux_user, "deploy_comfyui.sh", env, on_line=ctx.out.progress, timeout=LONG_STEP_TIMEOUT
        ),
        "Deploying ComfyUI",
    )


def wait_first_start(ctx: Context) -> None:
    steps.wait_first_start(
        ctx.wsl,
        ctx.out,
        ctx.known_answers,
        retry=steps.keep_waiting_hint("install", ctx.session, ctx.known_answers.distro),
    )


def wait_after_restart(ctx: Context) -> None:
    steps.wait_after_restart(ctx.wsl, ctx.out, ctx.known_answers)


def models_structure(ctx: Context) -> None:
    a = ctx.known_answers
    steps.models_structure(ctx.wsl, ctx.out, a, envfile.models_dir(a), create=a.models == "wsl")


def manager_security(ctx: Context) -> None:
    steps.manager_security(ctx.wsl, ctx.out, ctx.known_answers, ctx.known_answers.security)


def restart_container(ctx: Context) -> None:
    steps.restart_container(ctx.wsl, ctx.out, ctx.known_answers)


def verify(ctx: Context) -> None:
    """The security checklist and the Windows reach check; any failure stops install (resumable)."""
    ctx.data["verify"] = verify_command.run_verify(ctx.session, new_record(ctx.known_answers), ctx.wsl)


def create_launcher(ctx: Context) -> None:
    """Desktop and Start menu shortcuts. A packaged-app context refuses (the shortcut would point at
    a private copy): install says so in its result and carries on."""
    winapi = ctx.session.deps.winapi
    distro = ctx.known_answers.distro
    try:
        paths = launcher_command.install_shortcuts(winapi, distro)
    except CliError as error:
        if not winapi.is_packaged_app():
            raise
        ctx.out.warn(error.message)
        ctx.data["launcher"] = {"shortcuts": [], "refused": error.message}
        return
    for path in paths:
        ctx.out.info(f"Shortcut {path}")
    ctx.data["launcher"] = {"shortcuts": [str(path) for path in paths], "refused": None}


def stop_container_step(ctx: Context) -> None:
    """Install leaves ComfyUI stopped: `start` brings it up in its own run window."""
    a = ctx.known_answers
    ensure_ok(
        ctx.wsl.run_command(
            a.distro, a.linux_user, steps.stop_argv(a), on_line=ctx.out.progress, timeout=steps.STOP_TIMEOUT
        ),
        "Stopping ComfyUI",
    )


def release_keepalive(ctx: Context) -> None:
    ctx.keepalive.stop()


def amend_answers(ctx: Context, flags: intake.Flags, ask: bool) -> None:
    """A resume's changes to its saved answers (intake.amend), saved before its steps run: a new
    port is checked as preflight checks it. Says what changed."""
    session, before = ctx.session, ctx.known_answers
    if ask and session.non_interactive:
        raise AnswerMissing(
            "--change asks the questions again. Re-run with --channel, --manager-ui, --security or --port instead.",
            "--channel",
        )
    answers = intake.amend(before, flags, session.deps.prompter, ask=ask)
    if answers.port != before.port:
        answers = replace(answers, port=preflight.check_port(session, answers.port, other_than=answers.distro))
    if answers == before:
        return
    ctx.answers = answers
    record = ctx.known_record
    record.port = answers.port
    record.answers = answers.to_dict()
    ctx.known_installs.put(record)
    ctx.known_installs.save()
    ctx.out.info("Changed the saved answers:")
    for now_line, was in zip(
        intake.summary(answers, intake.Facts()), intake.summary(before, intake.Facts()), strict=False
    ):
        if now_line != was:
            ctx.out.info(f"  {now_line}")


def resume_hint(session, distro: str) -> str:
    """How a failed install carries on, and how to change the answers that may have caused it."""
    return (
        f"The answers are saved: '{steps.command_for('install', session, distro)}' carries on. "
        "Add --change to pick another ComfyUI version, Manager UI or security level first "
        "(or --port <n> for another port)."
    )


def windows_path(distro: str, path: str) -> str:
    """How Explorer reaches a path inside the distro."""
    return rf"\\wsl.localhost\{distro}" + path.replace("/", "\\")


def _wsl_ram_gb(verify_data: dict[str, Any]) -> int | None:
    """The RAM WSL sees, from verify.sh's `WSL sees N GB RAM` line."""
    for item in verify_data.get("checks", []):
        found = re.search(r"WSL sees (\d+) GB RAM", item["message"])
        if found:
            return int(found.group(1))
    return None


def summary(a: Answers, data: dict[str, Any]) -> dict[str, Any]:
    wsl_models = a.models == "wsl"
    return {
        "url": f"http://localhost:{a.port}",
        "gpu": {"mode": a.gpu, "name": a.gpu_name if a.gpu == "nvidia" else None},
        "wsl_ram_gb": _wsl_ram_gb(data.get("verify", {})),
        "pinned_memory": "off" if a.disable_pinned_memory else "on",
        "project": project_path(a.linux_user),
        "models": {
            "mode": a.models,
            "path": a.models_dir,
            "windows_path": windows_path(a.distro, a.models_dir) if wsl_models else None,
        },
        "runs_only_while_window_open": True,
    }


def show_summary(out: Output, a: Answers, found: dict[str, Any], restart_pending: bool) -> None:
    gpu = found["gpu"]
    out.step("Summary")
    out.info(f"ComfyUI: {found['url']}")
    out.info("GPU: " + (f"NVIDIA ({gpu['name']})" if gpu["mode"] == "nvidia" else "none (CPU-only, slow)"))
    if found["wsl_ram_gb"] is not None:
        out.info(f"RAM WSL sees: {found['wsl_ram_gb']} GB")
    out.info(
        "Pinned memory is off (--disable-pinned-memory: less RAM used, generation ~10% slower)."
        if found["pinned_memory"] == "off"
        else "Pinned memory is on (the default): WSL's RAM can look nearly full. "
        "'comfyui-sandbox config --pinned-memory off' trades ~10% speed for RAM."
    )
    out.info(f"Project (in the distro): {found['project']}")
    models = found["models"]
    out.info(f"Models: {models['path']}" + (" (a Windows folder)" if models["mode"] == "windows" else ""))
    if models["windows_path"]:
        out.info(f"Add models from Windows by copying them into {models['windows_path']} (open it in Explorer).")
    out.info(
        "ComfyUI runs only while its window is open, close the window to stop it. "
        "'comfyui-sandbox start' (or the shortcut) opens it again."
    )
    if restart_pending:
        out.warn(
            "The new .wslconfig limits are not active yet: run 'wsl --shutdown' when you can "
            "(it stops every WSL distro), then start ComfyUI again."
        )


def new_install(ctx: Context) -> bool:
    """The `when` of the intake phases: a resumed install has its answers already."""
    return not ctx.resumed


def answered(test: Callable[[Answers], bool]) -> Callable[[Context], bool]:
    """The `when` of a step the answers decide. `STEPS` are only selected once the answers are
    known (after the intake, or from a resume's saved ones), so they say."""
    return lambda ctx: test(ctx.known_answers)


# install's step table (comfyui_sandbox.plans). The expected seconds are hand-set, for a typical PC
# on a typical connection: the first start (PyTorch and ComfyUI downloading) is most of it. The
# questions' seconds are the person's, reading and answering them.
INTAKE: list[plans.Step[Context]] = [
    plans.Step("Check the machine", 5, check_machine, when=new_install),
    plans.Step("Questions", 60, ask_questions, when=new_install),
    plans.Step("Preflight", 5, run_preflight, when=new_install),
]
STEPS: list[plans.Step[Context]] = [
    plans.Step("Set the WSL limits in .wslconfig", 5, apply_wslconfig, when=answered(lambda a: a.tune_wslconfig)),
    plans.Step("Create the distro", 90, create_distro),
    plans.Step("Create the Linux user (no password, no sudo)", 5, create_user),
    plans.Step("Harden the distro (no Windows drives, no interop)", 5, harden),
    plans.Step("Restart the distro so the hardening applies", 10, terminate),
    plans.Step("Install Docker Engine", 120, install_docker),
    plans.Step(
        "Install the NVIDIA Container Toolkit and test GPU access",
        90,
        install_toolkit,
        when=answered(lambda a: a.gpu == "nvidia"),
    ),
    plans.Step("Stop the distro", 10, terminate),
    plans.Step("Prepare the data folder", 10, prepare_data),
    plans.Step("Deploy ComfyUI and start the container", 240, deploy),
    plans.Step("Wait for the first start (installs PyTorch and ComfyUI: 10-30 minutes)", 900, wait_first_start),
    plans.Step("Create the models structure", 5, models_structure),
    plans.Step("Set the Manager security level", 5, manager_security),
    plans.Step("Restart ComfyUI", 15, restart_container),
    plans.Step("Wait for ComfyUI to answer again", 45, wait_after_restart),
    plans.Step("Verify the sandbox (security checks and Windows access)", 30, verify),
    plans.Step("Create the launcher shortcuts", 2, create_launcher),
    plans.Step("Stop ComfyUI (it runs from its own window)", 15, stop_container_step),
    plans.Step("Release the keepalive", 1, release_keepalive),
]
# The hand-off to `start` once setup is done (the command's end, not a row of the table: its
# failure doesn't undo the install). Planned, so the wait for ComfyUI is a step of its own.
HAND_OFF = PlannedStep(startstop.START_STEP, 30)


def _take(out: Output, steps: list[plans.Step[Context]], ctx: Context) -> None:
    """plans.run, naming the step a timeout ended: a long call's own timeout message doesn't say
    which step it was."""
    for step in steps:
        out.step(step.title)
        try:
            step.run(ctx)
        except CommandTimeout as timed_out:
            raise CommandTimeout(f"{step.title} timed out: {timed_out.message}", data=timed_out.data) from timed_out


# -- the command ----------------------------------------------------------------


def require_distro_present(session, found: Install) -> Install:
    """An entry whose distro WSL no longer has is cleaned up first, except an in-progress one
    that never got as far as creating the distro (an ordinary retry)."""
    if session.wsl().exists(found.distro):
        return found
    if found.state == "complete" or found.distro_created_at is not None:
        raise CliError(unregistered_message(found.distro))
    return found


def _pick_resume(session, installs: Installs, new: bool = False) -> Install | None:
    """The in-progress install to resume, if any. With new (--new), none: a new install, even
    while another is unfinished."""
    if new:
        found = installs.find(session.distro) if session.distro is not None else None
        if found is not None and found.state != "complete":
            raise CliError(
                f"{found.distro} has an unfinished install. Run 'comfyui-sandbox install "
                f"--distro {found.distro}' (without --new) to resume it."
            )
        return None
    if session.distro is not None:
        found = installs.find(session.distro)
        if found is None:
            return None
        if found.state == "complete":
            if session.wsl().exists(found.distro):
                raise CliError(f"{found.distro} is already installed.")
            raise CliError(unregistered_message(found.distro))
        return require_distro_present(session, found)
    pending = [i for i in installs.all() if i.state == "in_progress"]
    if len(pending) <= 1:
        return require_distro_present(session, pending[0]) if pending else None
    names = [i.distro for i in pending]
    if session.non_interactive:
        raise AnswerMissing(
            f"Several sandboxes have an unfinished install ({', '.join(names)}). "
            "Re-run with --distro <name> to resume one.",
            "--distro",
        )
    return require_distro_present(
        session, installs.require(session.deps.prompter.select("Which sandbox's install should be resumed?", names))
    )


def _machine(session) -> dict[str, Any]:
    """check, with its report in the transcript only. Stops on anything but a ready machine
    or one without an NVIDIA GPU (CPU-only)."""
    quiet = Output(io.StringIO(), io.StringIO(), json_mode=False)
    quiet.transcript = session.log
    report = check.inspect(session.deps, quiet)
    code = report.exit_code()
    if code not in (ExitCode.OK, ExitCode.NO_NVIDIA):
        raise CliError(check.SUMMARY[code], code, data={"check": report.data})
    if code == ExitCode.NO_NVIDIA:
        session.out.warn("No NVIDIA GPU detected: installing in CPU-only mode (slow).")
    return report.data


def register(app: typer.Typer) -> None:
    @app.command()
    def install(
        ctx: typer.Context,
        models: str | None = typer.Option(None, "--models", help="Where models live: wsl (default) or windows."),
        models_path: str | None = typer.Option(
            None, "--models-path", help="The models folder: an absolute Linux path (wsl) or Windows path (windows)."
        ),
        memory: str | None = typer.Option(
            None,
            "--memory",
            help="tune, keep, pinned, both, keep-pinned or neither (.wslconfig and --disable-pinned-memory).",
        ),
        manager_ui: str | None = typer.Option(None, "--manager-ui", help="legacy or standard."),
        security: str | None = typer.Option(
            None, "--security", help="Manager security level: strict, normal, normal- or weak."
        ),
        user: str | None = typer.Option(None, "--user", help="The Linux username."),
        location: str | None = typer.Option(
            None, "--location", help="Windows folder for the distro's disk, or 'default'."
        ),
        channel: str | None = typer.Option(
            None, "--channel", help="ComfyUI version: latest, nightly or a release number such as 0.3.40."
        ),
        wsl_limits: str | None = typer.Option(
            None, "--wsl-limits", help="suggested, low, or e.g. 'memory=48 processors=16 swap=16' (GB)."
        ),
        port: int | None = typer.Option(None, "--port", help="The Windows port for ComfyUI (default 8188)."),
        create_models_folders: bool = typer.Option(
            False,
            "--create-models-folders",
            help="Create the missing model subfolders in an existing Windows models folder.",
        ),
        shutdown: str | None = typer.Option(
            None, "--shutdown", help="Restart WSL so new .wslconfig limits apply: now or later."
        ),
        confirm: str | None = typer.Option(
            None, "--confirm", help="Confirms stopping other WSL distros: the new distro's name or a running one."
        ),
        new: bool = typer.Option(
            False,
            "--new",
            help="Install a new sandbox even when another's install is unfinished "
            "(without it, that install is resumed).",
        ),
        change: bool = typer.Option(
            False,
            "--change",
            help="On a resume: ask the ComfyUI version, Manager UI and security level "
            "again (--channel, --manager-ui, --security and --port change them too).",
        ),
    ) -> Outcome:
        """Install a new sandboxed ComfyUI, or resume an unfinished install."""
        session = ctx.obj
        out = session.out
        session.transcript()
        installs = session.installs()
        record = _pick_resume(session, installs, new)
        resumed = record is not None
        flags = None
        if record is not None:
            fixed = [
                flag
                for flag, given in (
                    ("--models", models),
                    ("--models-path", models_path),
                    ("--memory", memory),
                    ("--user", user),
                    ("--location", location),
                    ("--wsl-limits", wsl_limits),
                    ("--create-models-folders", create_models_folders),
                )
                if given
            ]
            if fixed:
                raise UsageError(
                    f"{', '.join(fixed)} can't change on a resume: {record.distro} is installed "
                    f"with its saved answers. Only {', '.join(intake.AMENDABLE)} can change."
                )
            out.info(f"Resuming the install of {record.distro} with the saved answers.")
        elif change:
            raise UsageError("--change changes an unfinished install's answers, and there is none to resume.")
        else:
            flags = intake.Flags(
                models=models,
                models_path=models_path,
                memory=memory,
                manager_ui=manager_ui,
                security=security,
                distro=session.distro,
                user=user,
                location=location,
                channel=channel,
                wsl_limits=wsl_limits,
                port=port,
                create_models_folders=create_models_folders,
            )
        wsl = session.wsl()
        run_ctx = Context(
            wsl,
            out,
            Answers.from_dict(record.answers) if record else None,
            Keepalive(wsl, session.deps.winapi),
            session=session,
            record=record,
            shutdown=shutdown,
            confirm=confirm,
            data={"wsl_restart_pending": False},
            flags=flags,
            installs=installs,
        )
        # The plan is announced once, when the steps are known. A new install's steps depend on
        # its answers, so the intake (check, questions, preflight) runs first, unplanned, like a
        # short command's steps. Announcing a guess before it and correcting it after made the
        # CLI's counter go [3/22] then [4/21] and needed re-announcing support in Output.plan and
        # the TUI. Unplanned, the TUI's live view has no step list while the questions are asked
        # in place and shows the plan the moment it exists; the intake is quick next to the setup,
        # so the bar loses nothing. A resumed install has its answers already and plans at once.
        if not resumed:
            _take(out, plans.select(INTAKE, run_ctx), run_ctx)
        else:
            amend_answers(
                run_ctx, intake.Flags(channel=channel, manager_ui=manager_ui, security=security, port=port), ask=change
            )
        out.plan([*plans.planned(plans.select(STEPS, run_ctx)), HAND_OFF])
        answers, record = run_ctx.known_answers, run_ctx.known_record
        try:
            _take(out, plans.select(STEPS, run_ctx), run_ctx)
        except CliError as error:
            if error.code == ExitCode.FAILED:
                error.message += " " + resume_hint(session, answers.distro)
            raise
        finally:
            run_ctx.keepalive.stop()
        record.state = "complete"
        installs.put(record)
        installs.save()
        # The hand-off: `start` opens ComfyUI in its own run window. Setup is done by now (the
        # install is `complete`), so the summary and the .wslconfig reminder are shown whatever
        # `start` does. A failure keeps its exit code (10 = not reachable yet, 1 = failed) and is
        # retried with `comfyui-sandbox start`, not by running install again.
        found = summary(answers, run_ctx.data)
        restart_pending = run_ctx.data["wsl_restart_pending"]
        try:
            started = startstop.start_comfyui(session, record, from_env=False)
        except Exception as caught:
            error = caught if isinstance(caught, CliError) else CliError(f"{type(caught).__name__}: {caught}")
            show_summary(out, answers, found, restart_pending)
            error.message += (
                f" {answers.distro} is installed: once that is sorted out, run "
                "'comfyui-sandbox start' (the install does not need to be repeated)."
            )
            error.data = {**error.data, "distro": answers.distro, "resumed": resumed, **run_ctx.data, "summary": found}
            if error is caught:
                raise
            raise error from caught
        show_summary(out, answers, found, restart_pending)
        return Outcome(
            f"{answers.distro} is installed. ComfyUI opens at http://localhost:{answers.port}.",
            {
                "distro": answers.distro,
                "resumed": resumed,
                "install": record.to_json(),
                **run_ctx.data,
                "start": started.data,
                "summary": found,
            },
        )
