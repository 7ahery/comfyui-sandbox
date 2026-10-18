"""`comfyui-sandbox update setup`: bring a sandbox's setup up to this CLI's version.

The target is named `setup`, not `sandbox`, so it doesn't sound like "update everything" (`update all` is that).
It was renamed before any release, so `update sandbox` has no alias. The module keeps its old file name: the
rename is user-facing only (the feat-tui-integration spec, "Naming").

Redeploys the docker assets (Dockerfile, compose files, entrypoint) and rebuilds through
`deploy_comfyui.sh`, merges `.env` instead of replacing it (the user's edits stay; the old file is
kept once, by the deploy script, as `.env.bak-<stamp>`; only keys the new CLI writes and the file lacks are added), waits until
ComfyUI answers (the deploy starts it), records the CLI version in installs.json so `status` stops warning, and then
leaves ComfyUI stopped like every command that changes the sandbox (`steps.ending_stopped`). The
in-distro scripts need no deploy: the CLI streams them from its own package on every call.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import version as package_version

import typer

from comfyui_sandbox import envfile, payload, plans, steps
from comfyui_sandbox.commands.update_support import Context, Pending, Prepared, prepared, run_ending_stopped
from comfyui_sandbox.errors import CliError, UsageError
from comfyui_sandbox.intake import Answers
from comfyui_sandbox.output import Outcome
from comfyui_sandbox.steps import LONG_STEP_TIMEOUT, backup_stamp, ensure_ok, project_path


def _answers(install) -> Answers:
    try:
        return Answers.from_dict(install.answers)
    except TypeError:
        raise CliError(
            f"{install.distro} has no saved install answers (it was set up by an older tool). "
            "Run 'comfyui-sandbox install' again to set it up."
        ) from None


NAME = "setup"


def pending(session, install) -> Pending:
    """a `Pending`: the sandbox's setup is current when the CLI that deployed it is this one."""
    current = package_version("comfyui-sandbox")
    deployed = install.deployed_by or None
    if install.deployed_by == current:
        return Pending(False, f"up to date (deployed by CLI {current})", deployed, current)
    return Pending(
        True, f"deployed by CLI {install.deployed_by or 'unknown'}, this CLI is {current}", deployed, current
    )


@dataclass
class _Context(Context):
    merged: envfile.Merged | None = None  # the .env as "Merge .env" merged it, for the deploy to write
    stamp: str = ""  # the old .env's backup name


def _merge(context: _Context) -> None:
    install, out, wsl = context.install, context.out, context.wsl()
    answers = _answers(install)
    current = envfile.read(wsl, install)
    fresh = envfile.parse(envfile.render(answers, *steps.linux_ids(wsl, install)))
    known = envfile.parse(current)
    added = {key: value for key, value in fresh.items() if key not in known}
    context.stamp = backup_stamp()
    try:
        merged = context.merged = envfile.merge(current, added, stamp=context.stamp)
    except ValueError as exc:
        raise UsageError(f"{exc} Nothing was changed.") from None
    if merged.changed:
        out.info(
            f"Adding {', '.join(merged.changes)} to {project_path(install.linux_user)}/.env, "
            f"the old file will be {merged.backup}."
        )
    else:
        out.info(".env already has every setting this version writes.")


def _redeploy(context: _Context) -> None:
    install, merged = context.install, context.merged
    assert merged is not None  # "Merge .env" ran first
    context.keep_distro_up()  # until ComfyUI has answered
    # The deploy script writes the merged .env (it is in the payload) and makes the one backup.
    env = {
        "PAYLOAD": payload.build(merged.text),
        "MODELS_DIR": envfile.parse(merged.text).get("MODELS_DIR", ""),
        **({"ENV_BACKUP_STAMP": context.stamp} if merged.changed else {"BACKUP_ENV": "no"}),
    }
    ensure_ok(
        context.wsl().run_script(
            install.distro,
            install.linux_user,
            "deploy_comfyui.sh",
            env,
            on_line=context.out.progress,
            timeout=LONG_STEP_TIMEOUT,
        ),
        "Deploying ComfyUI",
    )
    # Recorded as soon as the files are the new ones, so a wait that fails after doesn't leave a
    # warning about a setup that is in fact current.
    installs = context.session.installs()
    record = installs.require(install.distro)
    record.deployed_by = package_version("comfyui-sandbox")
    installs.put(record)
    installs.save()


def _wait(context: _Context) -> None:
    steps.wait_after_restart(context.wsl(), context.out, context.install)


TABLE = [
    plans.Step("Merge .env", 2, _merge),
    plans.Step("Redeploy the docker files and rebuild", 300, _redeploy),  # an image rebuild: minutes
    plans.Step("Wait for ComfyUI to answer", 60, _wait),
]


def _updated(context: _Context) -> Outcome:
    distro, merged, cli_version = context.install.distro, context.merged, package_version("comfyui-sandbox")
    assert merged is not None
    return Outcome(
        f"Updated the setup of {distro} to CLI {cli_version}, ComfyUI answered after the rebuild.",
        {
            "distro": distro,
            "deployed_by": cli_version,
            "env_added": list(merged.changes),
            "backup": merged.backup or None,
        },
    )


def prepare(session, install) -> Prepared:
    return prepared(NAME, TABLE, _Context(session, install), _updated)


def register(group: typer.Typer) -> None:
    @group.command(NAME)
    def update_setup(ctx: typer.Context) -> Outcome:
        """Redeploy the docker files, merge .env (keeping your edits and a backup) and rebuild. ComfyUI ends stopped."""
        return run_ending_stopped(ctx.obj, prepare)
