"""`comfyui-sandbox rebuild`: `docker compose up -d --build`, then wait until ComfyUI answers.

Rebuilds the image after the Dockerfile or entrypoint changed; `data/` and the models folder are
host folders, so nothing of the user's is lost (section 6 of the old operations notes). The build
may take long, so it gets the long step timeout. It ends with ComfyUI stopped, like every command that changes the sandbox
(`steps.ending_stopped`): the build starts it, the wait proves it answers, then it is stopped.
Its two steps are a step table (comfyui_sandbox.plans), announced as its plan before the first.
"""

from __future__ import annotations

import typer

from comfyui_sandbox import plans, steps
from comfyui_sandbox.output import Outcome
from comfyui_sandbox.steps import LONG_STEP_TIMEOUT, Context, compose_argv, ensure_ok


def _build(context: Context) -> None:
    context.keep_distro_up()  # until ComfyUI has answered: a long build mustn't let the distro idle out
    install = context.install
    ensure_ok(
        context.wsl().run_command(
            install.distro,
            install.linux_user,
            compose_argv(install, "up", "-d", "--build"),
            on_line=context.out.progress,
            timeout=LONG_STEP_TIMEOUT,
        ),
        "docker compose up -d --build",
    )


def _wait(context: Context) -> None:
    steps.wait_after_restart(context.wsl(), context.out, context.install)


TABLE = [
    plans.Step("Rebuild the image (docker compose up -d --build)", 300, _build),  # an image build: minutes
    plans.Step("Wait for ComfyUI to answer", 45, _wait),
]


def register(app: typer.Typer) -> None:
    @app.command()
    def rebuild(ctx: typer.Context) -> Outcome:
        """Rebuild the ComfyUI image (docker compose up -d --build) without losing data and check it answers. ComfyUI ends stopped."""
        session = ctx.obj
        session.transcript()
        install = session.resolve_install()
        context = Context(session, install)
        chosen = plans.select(TABLE, context)

        def rebuild_image() -> Outcome:
            context.run(chosen)
            return Outcome(
                f"Rebuilt the image of {install.distro}, ComfyUI answered after the rebuild.",
                {"distro": install.distro},
            )

        return steps.ending_stopped(session, install, rebuild_image, plans.planned(chosen))
