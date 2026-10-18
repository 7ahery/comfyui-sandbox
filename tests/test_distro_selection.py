"""--distro resolution and "is this distro ours?" (docs/scripting.md, "Global options and choosing a sandbox", and "installs.json").

Every command that acts on an install gets it from `session.resolve_install()`; the
`probe` command here stands in for them.
"""

import json

import pytest
import typer
from test_check import Machine

from comfyui_sandbox.cli import build_app
from comfyui_sandbox.output import Outcome


def probe_app() -> typer.Typer:
    app = build_app()

    @app.command("probe")
    def probe(ctx: typer.Context) -> Outcome:
        install = ctx.obj.resolve_install()
        return Outcome("resolved", {"distro": install.distro})

    return app


@pytest.fixture(autouse=True)
def wsl_present(runner, environ):
    Machine(distros=["Ubuntu", "comfy-docker", "other", "comfy-docker-2", "COMFY-DOCKER"]).script(runner, environ)


def write_installs(home, *names: str, **extra) -> None:
    folder = home / ".comfyui-sandbox"
    folder.mkdir(exist_ok=True)
    installs = [
        {
            "distro": n,
            "linux_user": "comfy",
            "state": "complete",
            "port": 8188,
            "models": {"mode": "wsl", "path": "/home/comfy/comfyui-models"},
            "gpu": {"mode": "nvidia", "name": "RTX"},
            "cuda_version": None,
            "location": None,
            "deployed_by": "1.0.0",
            "created_at": "2026-10-06T16:48:00Z",
            "updated_at": "2026-10-06T16:48:00Z",
            "answers": {},
        }
        for n in names
    ]
    (folder / "installs.json").write_text(json.dumps({"schema": 1, "installs": installs, **extra}), "utf-8")


def test_missing_installs_json_tells_the_user_to_install(cli):
    result = cli("probe", "--json", app=probe_app())

    assert result.exit_code == 1
    assert "comfyui-sandbox install" in result.json["message"]


def test_missing_installs_json_refuses_a_named_distro_too(cli):
    result = cli("probe", "--json", "--distro", "comfy-docker", app=probe_app())

    assert result.exit_code == 1
    assert "comfyui-sandbox install" in result.json["message"]


def test_a_distro_not_in_installs_json_is_refused(cli, home):
    write_installs(home, "comfy-docker")

    result = cli("probe", "--json", "--distro", "Ubuntu", app=probe_app())

    assert result.exit_code == 1
    assert "Ubuntu" in result.json["message"]
    assert "never touched" in result.json["message"]


def test_one_sandbox_resolves_on_its_own(cli, home):
    write_installs(home, "comfy-docker")

    result = cli("probe", "--json", app=probe_app())

    assert result.exit_code == 0
    assert result.json["data"] == {"distro": "comfy-docker"}


def test_distro_names_match_case_insensitively(cli, home):
    write_installs(home, "comfy-docker", "comfy-docker-2")

    result = cli("probe", "--json", "--distro", "COMFY-Docker-2", app=probe_app())

    assert result.json["data"] == {"distro": "comfy-docker-2"}


def test_several_sandboxes_need_distro_under_non_interactive(cli, home):
    write_installs(home, "comfy-docker", "comfy-docker-2")

    result = cli("probe", "--json", "--non-interactive", app=probe_app())

    assert result.exit_code == 9
    assert result.json["flag"] == "--distro"
    assert "several sandboxes (comfy-docker, comfy-docker-2)" in result.json["message"]


def test_several_sandboxes_are_asked_for_interactively(cli, home, prompter):
    write_installs(home, "comfy-docker", "comfy-docker-2")
    prompter.answers.append("comfy-docker-2")

    result = cli("probe", "--json", app=probe_app())

    assert result.json["data"] == {"distro": "comfy-docker-2"}
    [(question, choices)] = prompter.asked
    assert question == "Which sandbox?"
    assert choices == ("comfy-docker", "comfy-docker-2")


def test_installs_json_that_cannot_be_read_is_an_error_and_is_left_alone(cli, home):
    folder = home / ".comfyui-sandbox"
    folder.mkdir()
    (folder / "installs.json").write_text("{not json", "utf-8")

    result = cli("probe", "--json", app=probe_app())

    assert result.exit_code == 1
    assert (folder / "installs.json").read_text("utf-8") == "{not json"


def test_installs_json_from_a_newer_cli_is_refused(cli, home):
    write_installs(home, "comfy-docker")
    path = home / ".comfyui-sandbox" / "installs.json"
    doc = json.loads(path.read_text("utf-8"))
    doc["schema"] = 2
    path.write_text(json.dumps(doc), "utf-8")

    result = cli("probe", "--json", app=probe_app())

    assert result.exit_code == 1
    assert "newer" in result.json["message"]
