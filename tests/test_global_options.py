"""Global options work anywhere on the command line (docs/scripting.md, "Global options and choosing a sandbox")."""

from pathlib import Path

import pytest
import typer

from comfyui_sandbox.cli import Session, build_app
from comfyui_sandbox.output import Outcome


def probe_app() -> typer.Typer:
    """The real app plus `probe` (reports the global options) and an `exec` passthrough."""
    app = build_app()

    @app.command("probe")
    def probe(ctx: typer.Context) -> Outcome:
        s: Session = ctx.obj
        s.out.info("probing...")
        return Outcome(
            "probed",
            {"json": s.json, "yes": s.yes, "non_interactive": s.non_interactive, "distro": s.distro},
            log=s.deps.app_dir / "logs" / "x.log",
        )

    @app.command("exec", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
    def exec_(ctx: typer.Context) -> Outcome:
        s: Session = ctx.obj
        return Outcome("ran", {"args": list(ctx.args), "json": s.json})

    return app


@pytest.mark.parametrize(
    "argv",
    [
        ("probe", "--json", "--yes", "--non-interactive", "--distro", "comfy-docker"),
        ("--json", "--distro=comfy-docker", "--yes", "probe", "--non-interactive"),
    ],
)
def test_global_options_reach_the_command_wherever_they_are(cli, home, argv):
    result = cli(*argv, app=probe_app())

    assert result.exit_code == 0
    doc = result.json
    assert doc["command"] == "probe"
    assert doc["data"] == {"json": True, "yes": True, "non_interactive": True, "distro": "comfy-docker"}
    assert Path(doc["log"]) == home / ".comfyui-sandbox" / "logs" / "x.log"
    assert "probing..." in result.stderr


def test_without_json_the_result_message_is_the_only_stdout(cli):
    result = cli("probe", app=probe_app())

    assert result.exit_code == 0
    assert result.stdout == "probed\n"
    assert "probing..." in result.stderr


def test_passthrough_arguments_are_not_taken_as_global_options(cli):
    result = cli("--json", "exec", "--yes", "--json", "--distro", "x", app=probe_app())

    assert result.exit_code == 0
    assert result.json["data"] == {"args": ["--yes", "--json", "--distro", "x"], "json": True}
