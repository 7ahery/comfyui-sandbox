"""`comfyui-sandbox reinstall` (ticket 23, first named `recreate`, still a hidden alias): a clean start when ComfyUI is broken. Ports section 5 of
the old operations notes.

Destructive (the shared gate): stops the container, wipes `data/` (ComfyUI, venv, custom nodes,
workflows, inputs, outputs), rebuilds the image without cache and waits through ComfyUI's first
start, then leaves ComfyUI stopped like every command that changes the sandbox (`steps.ending_stopped`).
The models folder, `.env` and `~/comfyui-backups` live outside `data/` and are not touched.
It refuses, before the gate, when the models folder is inside `data/`, so it can never delete models.

Its steps are a step table (comfyui_sandbox.plans): the plan is announced once the name has been
confirmed, so a refused confirmation has announced nothing.
"""

from __future__ import annotations

import configparser
import posixpath
from dataclasses import dataclass

import typer

from comfyui_sandbox import envfile, plans, steps
from comfyui_sandbox.destructive import confirm_destructive
from comfyui_sandbox.errors import CliError
from comfyui_sandbox.output import Outcome
from comfyui_sandbox.state import Install
from comfyui_sandbox.steps import LONG_STEP_TIMEOUT, ensure_ok, project_path

MANAGER_CONFIGS = ("__manager/config.ini", "default/ComfyUI-Manager/config.ini")  # under data/ComfyUI/user/
SECURITY_LEVELS = ("weak", "normal-", "normal", "strict")


def models_folder(wsl, install: Install) -> tuple[str, bool]:
    """(the models folder as the distro sees it, whether recreate may create its subfolders). `.env` is
    the source of truth; the install record is only the fallback when `.env` is missing or has no
    MODELS_DIR."""
    project = project_path(install.linux_user)
    try:
        value = envfile.parse(envfile.read(wsl, install)).get("MODELS_DIR")
    except envfile.EnvMissing:
        value = None
    if value:
        path = posixpath.normpath(posixpath.join(project, value))
        return path, path != steps.WINDOWS_MODELS_MOUNT
    models = install.models
    return steps.models_in_distro(models.mode, models.path), models.mode == "wsl"


def models_inside_data(install: Install, models_dir: str) -> bool:
    """A models folder at or below `<project>/data` (the Windows mount is elsewhere)."""
    data = posixpath.normpath(f"{project_path(install.linux_user)}/data")
    models = posixpath.normpath(posixpath.join("/", models_dir))
    return models == data or models.startswith(data + "/")


def current_security_level(wsl, install: Install) -> str | None:
    """The level the Manager is set to now, read from its config in data/ (None when unreadable)."""
    user_dir = f"{project_path(install.linux_user)}/data/ComfyUI/user"
    for name in MANAGER_CONFIGS:
        done = wsl.run_command(install.distro, install.linux_user, ["cat", f"{user_dir}/{name}"])
        if done.returncode != 0:
            continue
        parser = configparser.ConfigParser(strict=False, interpolation=None)
        try:
            parser.read_string(done.out())
        except configparser.Error:
            continue
        level = parser.get("default", "security_level", fallback="").strip().lower()
        if level in SECURITY_LEVELS:
            return level
    return None


# -- recreate's step table (comfyui_sandbox.plans) ------------------------------------------------------


@dataclass
class _Recreate(steps.Context):
    models_dir: str = ""  # the models folder as the distro sees it (models_folder)
    create_models: bool = False  # whether its missing subfolders may be created
    level: str = "normal"  # the Manager security level to set back, read before the wipe


def _run(context: _Recreate, what: str, argv: list[str], timeout: int = 600) -> None:
    install = context.install
    ensure_ok(
        context.wsl().run_command(
            install.distro, install.linux_user, argv, on_line=context.out.progress, timeout=timeout
        ),
        what,
    )


def _down(context: _Recreate) -> None:
    context.keep_distro_up()  # until the last step: a long build mustn't let the distro idle out
    _run(context, "docker compose down", steps.compose_argv(context.install, "down"))


def _wipe(context: _Recreate) -> None:
    data = f"{project_path(context.install.linux_user)}/data"  # the one folder the wipe may touch
    _run(context, "Deleting data/", ["rm", "-rf", data])
    _run(context, "Creating data/", ["mkdir", data])
    _run(context, "Setting the permissions of data/", ["chmod", "750", data])


def _build(context: _Recreate) -> None:
    _run(
        context,
        "docker compose build",
        steps.compose_argv(context.install, "build", "--pull", "--no-cache"),
        LONG_STEP_TIMEOUT,
    )


def _up(context: _Recreate) -> None:
    _run(context, "docker compose up -d", steps.compose_argv(context.install, "up", "-d"))


def _first_start(context: _Recreate) -> None:
    steps.wait_first_start(
        context.wsl(),
        context.out,
        context.install,
        retry=steps.keep_waiting_hint("start", context.session, context.install.distro),
    )


# data/ held the Manager's config and the models folder's extra subfolders: the next two steps put
# back what install had set up after its first start.
def _models(context: _Recreate) -> None:
    steps.models_structure(
        context.wsl(), context.out, context.install, context.models_dir, create=context.create_models
    )


def _security(context: _Recreate) -> None:
    steps.manager_security(context.wsl(), context.out, context.install, context.level)


def _restart(context: _Recreate) -> None:
    steps.restart_container(context.wsl(), context.out, context.install)


def _wait(context: _Recreate) -> None:
    steps.wait_after_restart(context.wsl(), context.out, context.install)


# Hand-set seconds (comfyui_sandbox.plans): the first start installs PyTorch and ComfyUI (10-30
# minutes), the no-cache build takes minutes, the rest seconds.
TABLE = [
    plans.Step("Stop and remove the container", 15, _down),
    plans.Step("Delete data/", 30, _wipe),
    plans.Step("Rebuild the image without cache", 420, _build),
    plans.Step("Start ComfyUI", 20, _up),
    plans.Step("Wait for the first start (installs PyTorch and ComfyUI: 10-30 minutes)", 1200, _first_start),
    plans.Step("Create the models structure", 5, _models),
    plans.Step("Set the Manager security level", 5, _security),
    plans.Step("Restart ComfyUI", 20, _restart),
    plans.Step("Wait for ComfyUI to answer again", 45, _wait),
]


def register(app: typer.Typer) -> None:
    # `recreate` was its first name: still accepted, no longer listed.
    @app.command("recreate", hidden=True)
    @app.command("reinstall")
    def reinstall(
        ctx: typer.Context,
        confirm: str | None = typer.Option(
            None, "--confirm", help="Type the sandbox name to allow the reinstall (needed with --non-interactive)."
        ),
    ) -> Outcome:
        """Wipe data/, rebuild without cache and run a fresh first install. Keeps the models and backups."""
        session = ctx.obj
        out = session.out
        session.transcript()
        install = session.resolve_install()
        distro = install.distro
        if install.state != "complete":
            raise CliError(
                f"Refusing to reinstall: the install of {distro} is unfinished. Run 'comfyui-sandbox "
                f"install --distro {distro}' to resume it, or 'comfyui-sandbox uninstall --distro "
                f"{distro}' to remove it. Nothing was changed."
            )
        models_dir, create_models = models_folder(session.wsl(), install)
        if models_inside_data(install, models_dir):
            raise CliError(
                f"Refusing to reinstall:the models folder ({models_dir}) is inside data/, which this "
                "would delete. Move the models out of data/ and set the models folder in .env, then try again. "
                "Nothing was changed."
            )
        confirm_destructive(
            session,
            [distro],
            confirm=confirm,
            warning=f"Reinstalling ComfyUI in {distro} deletes everything in data/ (ComfyUI, its Python environment, custom "
            "nodes, workflows, input and output files) and reinstalls from scratch. The models folder "
            "and ~/comfyui-backups are kept. This cannot be undone: run 'comfyui-sandbox backup' first.",
        )
        context = _Recreate(session, install, models_dir=models_dir, create_models=create_models)
        chosen = plans.select(TABLE, context)

        def wipe_and_reinstall() -> Outcome:
            level = current_security_level(session.wsl(), install)  # read before the wipe: it's in data/
            if level is None:
                level = str((install.answers or {}).get("security", "normal"))
                out.info(
                    f"Could not read the Manager security level from data/, so the install answer ({level}) is used."
                )
            context.level = level
            context.run(chosen)
            return Outcome(
                f"Reinstalled ComfyUI in {distro}: a fresh ComfyUI installed and answered. "
                "Restore a backup with 'comfyui-sandbox restore' if you want your work back.",
                {"distro": distro},
            )

        return steps.ending_stopped(session, install, wipe_and_reinstall, plans.planned(chosen))
