"""`comfyui-sandbox open`: opens a running sandbox's ComfyUI in the browser, and nothing else.

Home's Open in browser runs it. Unlike `start`, it never opens a run window: on a sandbox whose
ComfyUI isn't answering it fails, saying how to start it. Everything through the CLI entry point,
with fakes (the spec's seam B).
"""

import pytest
from test_check import Machine
from test_verify import own

from comfyui_sandbox.instance import instance_name

WINDOWS_COMFYUI = '{"system": {"os": "nt"}}'


@pytest.fixture(autouse=True)
def wsl_present(runner, environ, home):
    Machine().script(runner, environ)
    own(home)


def test_open_on_a_running_sandbox_opens_its_comfyui_in_the_browser_and_nothing_else(cli, winapi, http):
    result = cli("open", "--distro", "comfy-docker", "--json")  # the fake Http's ComfyUI answers

    assert result.exit_code == 0, result.stderr
    assert winapi.opened_urls == ["http://localhost:8188"]
    assert winapi.consoles == []  # no run window
    assert result.json["data"] == {"distro": "comfy-docker", "url": "http://localhost:8188"}
    assert result.json["message"] == "Opened http://localhost:8188 in the browser."


def test_open_works_with_or_without_a_run_window(cli, winapi):
    winapi.instances.add(instance_name("comfy-docker"))

    assert cli("open").exit_code == 0

    assert winapi.opened_urls == ["http://localhost:8188"] and winapi.consoles == []


def test_open_on_a_stopped_sandbox_fails_saying_how_to_start_it_and_opens_nothing(cli, winapi, http):
    http.refuse(8188)

    result = cli("open", "--distro", "comfy-docker", "--json")

    assert result.exit_code == 1
    assert result.json["message"].startswith("ComfyUI isn't running in comfy-docker")
    assert "comfyui-sandbox start --distro comfy-docker" in result.json["message"]
    assert result.json["data"]["windows_reach"]["ok"] is False
    assert winapi.opened_urls == [] and winapi.consoles == []


def test_a_comfyui_on_windows_on_the_port_isnt_the_sandboxs_own_and_isnt_opened(cli, winapi, http):
    http.answer(8188, WINDOWS_COMFYUI)

    result = cli("open", "--json")

    assert result.exit_code == 1
    assert winapi.opened_urls == []
    assert result.json["data"]["windows_reach"]["os"] in ("nt", "win32")


def test_open_writes_no_transcript(cli, home):
    assert cli("open").exit_code == 0

    logs = home / ".comfyui-sandbox" / "logs"
    assert not logs.exists() or list(logs.iterdir()) == []  # read-only, like `logs`


def test_open_is_listed_in_help(cli):
    result = cli("--help")

    assert " open " in result.stdout
