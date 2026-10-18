"""The rest of home's reads from the real machine: the GPU meters, the update marks and Check this PC.

Like test_real_gateway.py, they run the real gateway on the CLI's fake `Deps` (the spec's seam
B): `nvidia-smi` and the in-distro checks are scripted on the fake runner, and what's asserted is
what the `Gateway` answers (a reading, a mark, a list of checks) and what home shows of it.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from importlib.metadata import version
from pathlib import Path

import pytest
from test_check import Machine
from test_real_gateway import deps, installed, window_open

import scenarios
import wslcalls
from comfyui_sandbox.runner import CommandNotFound
from comfyui_sandbox.tui.gateway.real import RealGateway
from comfyui_sandbox.tui.gateway.records import CheckItem, GpuReading
from comfyui_sandbox.tui.home.app import SandboxApp

NVIDIA_SMI = "nvidia-smi.exe"


def missing(program: str):
    """A `then=` for the fake runner: the program isn't installed."""

    def then() -> None:
        raise CommandNotFound(f"Program not found: {program}")

    return then


def gpu_calls(runner) -> list:
    return [call for call in runner.calls if call.argv[0] == NVIDIA_SMI]


# -- the GPU meters -------------------------------------------------------------------------------


def test_the_gpu_reading_is_the_latest_line_nvidia_smi_reports(deps, runner):
    # index, utilisation %, memory used MiB, memory total MiB: one line a second
    runner.on(NVIDIA_SMI, stdout="0, 3, 1024, 24576\n0, 87, 18432, 24576\n")

    assert RealGateway(deps).gpu() == GpuReading(0.87, 18.0, 24.0)


def test_several_gpus_read_as_one_machine_their_memory_added_up(deps, runner):
    runner.on(NVIDIA_SMI, stdout="0, 80, 8192, 24576\n1, 20, 2048, 16384\n")

    assert RealGateway(deps).gpu() == GpuReading(0.5, 10.0, 40.0)


def test_with_no_nvidia_smi_there_are_no_readings(deps, runner):
    runner.on(NVIDIA_SMI, then=missing(NVIDIA_SMI))

    assert RealGateway(deps).gpu() is None


def test_when_nvidia_smi_finds_no_gpu_there_are_no_readings(deps, runner):
    runner.on(
        NVIDIA_SMI,
        returncode=9,
        stdout="NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver.\n",
    )

    assert RealGateway(deps).gpu() is None


def test_a_gpu_that_cant_report_its_load_is_left_out(deps, runner):
    runner.on(NVIDIA_SMI, stdout="0, [N/A], 512, 4096\n1, 40, 1024, 8192\n")

    assert RealGateway(deps).gpu() == GpuReading(0.4, 1.0, 8.0)


def test_nvidia_smi_is_started_once_and_keeps_reporting(deps, runner):
    runner.on(NVIDIA_SMI, stdout="0, 10, 1024, 8192\n")
    gateway = RealGateway(deps)

    for _ in range(5):  # home reads the meters about once a second
        gateway.gpu()

    assert len(gpu_calls(runner)) == 1
    assert runner.started[0].call.argv[0] == NVIDIA_SMI and not runner.started[0].terminated


def test_closing_the_gateway_stops_nvidia_smi(deps, runner):
    runner.on(NVIDIA_SMI, stdout="0, 10, 1024, 8192\n")
    gateway = RealGateway(deps)
    gateway.gpu()

    gateway.close()

    assert runner.started[0].terminated
    assert gateway.gpu() is None  # and nothing starts it again


def test_a_missing_nvidia_smi_is_looked_for_once(deps, runner):
    runner.on(NVIDIA_SMI, then=missing(NVIDIA_SMI))
    gateway = RealGateway(deps)

    for _ in range(3):
        gateway.gpu()

    assert len(gpu_calls(runner)) == 1


def home_text(deps, until: str, *, then=None) -> str:
    """Opens home on the real gateway, waits until `until` shows, runs `await then(rec)` if given,
    and returns the screen's text. Leaving it closes home."""

    async def scenario() -> str:
        app = SandboxApp(RealGateway(deps))
        async with app.run_test(size=(160, 40)) as pilot:
            rec = scenarios.Rec(pilot)
            await rec.wait(until)
            await rec.settle(0.3)
            if then is not None:
                await then(rec)
            return rec.text

    return asyncio.run(scenario())


def test_home_shows_the_machines_gpu_in_the_bottom_bar(deps, runner):
    runner.on(NVIDIA_SMI, stdout="0, 87, 18432, 24576\n")

    text = home_text(deps, "VRAM")

    assert "87%" in text and "VRAM 18.0/24 GB" in text


def test_home_shows_no_meters_without_nvidia_smi(deps, runner):
    runner.on(NVIDIA_SMI, then=missing(NVIDIA_SMI))

    text = home_text(deps, "No sandboxes yet")

    bottom_bar = text.splitlines()[-1]
    assert "quit" in bottom_bar and "GPU" not in bottom_bar and "VRAM" not in bottom_bar


def test_closing_home_stops_nvidia_smi(deps, runner):
    runner.on(NVIDIA_SMI, stdout="0, 10, 1024, 8192\n")

    home_text(deps, "VRAM")

    assert len(runner.started) == 1 and runner.started[0].terminated


# -- update marks ---------------------------------------------------------------------------------


def in_distro(distro: str, *argv: str) -> tuple[str, ...]:
    """The start of a command run in distro as its user (Wsl.run_command)."""
    return ("wsl.exe", "-d", distro, "-u", "comfy", "--cd", "/", "--exec", "env", *argv)


APT = ("apt", "list", "--upgradable")
DOCKER_UPGRADE = "Listing...\ndocker-ce/noble 5:28.1 amd64 [upgradable from: 5:28.0]\n"


@pytest.fixture
def wsl_installed(environ) -> None:
    wsl = Path(environ["ProgramFiles"]) / "WSL" / "wsl.exe"
    wsl.parent.mkdir(parents=True)
    wsl.touch()


def current(home: Path, distro: str, port: int) -> None:
    """A sandbox whose setup this CLI deployed: `update setup` has nothing to do for it."""
    installed(home, distro, port)
    path = home / ".comfyui-sandbox" / "installs.json"
    doc = json.loads(path.read_text("utf-8"))
    doc["installs"][-1]["deployed_by"] = version("comfyui-sandbox")
    path.write_text(json.dumps(doc), "utf-8")


def marks(gateway: RealGateway, name: str, *, until=bool, timeout: float = 10) -> tuple[str, ...]:
    """name's update marks, polled the way home polls, until `until(marks)` holds (they're found in
    the background, so the first polls may come back without them)."""
    deadline = time.monotonic() + timeout
    while True:
        found = {s.name: s.updates for s in asyncio.run(gateway.sandboxes())}[name]
        if until(found) or time.monotonic() > deadline:
            return found
        time.sleep(0.02)


def checks_ended() -> None:
    """Waits for every update check the gateway has started to end. They run on daemon threads
    named "update check: <sandbox>" (real/updates.py); one started earlier either runs still and
    is joined, or has ended, its calls recorded. So what's asserted next is final, with no pause
    long enough "for the check to end", and no pause that asserting an absence would rely on."""
    for thread in threading.enumerate():
        if thread.name.startswith("update check: "):
            thread.join(10)
            assert not thread.is_alive(), f"{thread.name} never ended"


def apt_calls(runner, distro: str) -> list:
    return [call for call in runner.calls if call.argv[: len(in_distro(distro, *APT))] == in_distro(distro, *APT)]


def test_a_running_sandbox_is_marked_with_the_updates_its_check_finds(deps, home, runner, wsl_installed):
    installed(home, "up", 8188)  # its setup is from an older CLI, and Docker has an upgrade
    runner.on(*in_distro("up", *APT), stdout=DOCKER_UPGRADE)

    assert marks(RealGateway(deps), "up") == ("system", "setup")  # in `update all`'s order


def test_a_marked_comfyui_update_carries_the_installed_and_the_newest_version(deps, home, runner, wsl_installed):
    current(home, "up", 8188)
    box = in_distro("up", "docker", "exec", "comfyui", "git", "-C", "/workspace/ComfyUI")
    runner.on(*in_distro("up", *APT), stdout="Listing...\n")
    runner.on(
        *in_distro(
            "up",
            "env",
            "--chdir",
            "/home/comfy/comfyui-docker",
            "docker",
            "compose",
            "ps",
            "--status",
            "running",
            "--quiet",
        ),
        stdout="3f2a9c1d7e44\n",
    )
    runner.on(
        *in_distro("up", "env", "cat", "/home/comfy/comfyui-docker/.env"),
        stdout='COMFY_INSTALL_ARGS="--nvidia --version latest"\n',
    )
    runner.on(*box, "describe", "--tags", "--exact-match", stdout="v0.3.9\n")
    runner.on(
        *box, "ls-remote", "--tags", "--refs", "origin", stdout="1111\trefs/tags/v0.3.9\n2222\trefs/tags/v0.3.10\n"
    )
    gateway = RealGateway(deps)

    assert marks(gateway, "up") == ("comfyui",)
    checks_ended()

    assert {s.name: s.update_versions for s in asyncio.run(gateway.sandboxes())}["up"] == {
        "comfyui": ("v0.3.9", "v0.3.10")
    }


def test_a_sandbox_with_nothing_to_update_has_no_mark(deps, home, runner, wsl_installed):
    current(home, "up", 8188)
    runner.on(*in_distro("up", *APT), stdout="Listing...\n")
    gateway = RealGateway(deps)
    asyncio.run(gateway.sandboxes())  # home's first read: it's running, so its check starts

    checks_ended()

    assert apt_calls(runner, "up") and marks(gateway, "up") == ()


def test_each_sandbox_is_checked_once_a_session_however_often_home_polls(deps, home, runner, wsl_installed):
    installed(home, "up", 8188)
    runner.on(*in_distro("up", *APT), stdout=DOCKER_UPGRADE)
    gateway = RealGateway(deps)
    marks(gateway, "up")

    for _ in range(5):
        assert marks(gateway, "up") == ("system", "setup")  # cached

    assert len(apt_calls(runner, "up")) == 1


def test_a_sandbox_that_isnt_running_isnt_checked_so_its_distro_isnt_woken(deps, home, runner, http, wsl_installed):
    installed(home, "down", 8190)
    http.refuse(8190)
    gateway = RealGateway(deps)

    for _ in range(3):
        asyncio.run(gateway.sandboxes())
    checks_ended()

    assert not any(wslcalls.is_wsl(call) for call in runner.calls)


def test_a_sandbox_is_checked_once_it_is_seen_running(deps, home, runner, http, wsl_installed):
    installed(home, "later", 8188)
    runner.on(*in_distro("later", *APT), stdout=DOCKER_UPGRADE)
    http.refuse(8188)
    gateway = RealGateway(deps)
    asyncio.run(gateway.sandboxes())

    http.answer(8188, '{"system": {"os": "posix"}}')  # started from another terminal

    assert marks(gateway, "later") == ("system", "setup")


def test_a_check_that_fails_leaves_no_mark(deps, home, runner, wsl_installed):
    installed(home, "up", 8188)

    def broken() -> None:
        raise OSError("the pipe broke")

    runner.on(*in_distro("up", *APT), then=broken)
    gateway = RealGateway(deps)

    marks(gateway, "up", until=lambda found: False, timeout=0.5)

    assert apt_calls(runner, "up") and marks(gateway, "up") == ()


def test_reading_the_sandboxes_doesnt_wait_for_their_update_checks(deps, home, runner, wsl_installed):
    installed(home, "up", 8188)
    gate = threading.Event()
    runner.on(*in_distro("up", *APT), stdout=DOCKER_UPGRADE, then=lambda: gate.wait(10))
    gateway = RealGateway(deps)

    try:
        assert marks(gateway, "up", until=lambda found: False, timeout=0.3) == ()  # still checking
    finally:
        gate.set()

    assert marks(gateway, "up") == ("system", "setup")


def test_home_stays_responsive_while_an_update_check_runs_and_then_shows_its_mark(
    deps, home, runner, winapi, wsl_installed
):
    installed(home, "up", 8188)
    window_open(winapi, "up")  # so its note is free for the update mark
    gate = threading.Event()
    runner.on(*in_distro("up", *APT), stdout=DOCKER_UPGRADE, then=lambda: gate.wait(30))

    async def meanwhile(rec) -> None:
        try:
            await rec.row("up")  # the check is still under way: home answers keys all the same
            await rec.wait("What do you want to do with up?")
            assert "update ready" not in rec.text
            await rec.press("escape")
            await rec.wait("Pick a sandbox or an action")
        finally:
            gate.set()
        await rec.wait("update ready")  # the next poll brings the mark

    text = home_text(deps, "Pick a sandbox or an action", then=meanwhile)

    row = next(line for line in text.splitlines() if re.match(r"\s+(?:▌ )?up\s", line))
    assert "update ready" in row


# -- Check this PC --------------------------------------------------------------------------------


def checked(deps) -> list[CheckItem]:
    return asyncio.run(RealGateway(deps).check_this_pc())


def test_check_this_pc_is_the_cores_check_item_by_item(deps, runner, environ):
    Machine().script(runner, environ)  # a ready RTX 4090 box

    items = checked(deps)

    assert items[:3] == [
        CheckItem("Host", "Windows", ok=True),
        CheckItem("GPU", "NVIDIA GPU: NVIDIA GeForce RTX 4090 | driver 576.80 | VRAM 24564 MiB | compute 8.9"),
        CheckItem("GPU", "Driver supports CUDA up to 12.9"),
    ]
    assert CheckItem("WSL", "WSL version: 2.6.1.0") in items
    assert all(item.ok for item in items)


def test_what_the_check_finds_wrong_is_a_failed_item(deps, runner, environ):
    Machine(hypervisor=False, firmware=False).script(runner, environ)

    items = checked(deps)

    assert (
        CheckItem(
            "Virtualization",
            "Virtualization is off in the BIOS/UEFI (Intel VT-x / AMD SVM). Turn it on in the firmware settings.",
            ok=False,
        )
        in items
    )


def test_check_this_pc_shows_whatever_the_platforms_check_reports(deps):
    deps.platform = "linux"  # the core's check stops at the host: its one item is all there is

    assert checked(deps) == [CheckItem("Host", "Unsupported host (linux): runs on Windows only", ok=False)]


def test_the_gpus_windows_lists_are_part_of_the_gpu_item(deps, runner, environ):
    Machine(nvidia_smi=None, video_controllers=["Intel UHD 770", "AMD Radeon"]).script(runner, environ)

    items = checked(deps)

    assert CheckItem("GPU", "No NVIDIA GPU detected.", ok=False) in items
    assert CheckItem("GPU", "GPUs Windows reports: Intel UHD 770, AMD Radeon") in items


def test_home_offers_check_this_pc_and_lists_the_cores_items(deps, runner, environ):
    Machine(hypervisor=False, firmware=False).script(runner, environ)

    async def check(rec) -> None:
        await rec.choose("Check this PC")
        await rec.wait("isn't ready for an install")

    text = home_text(deps, "Check this PC", then=check)

    assert "Virtualization is off in the BIOS/UEFI" in text and "WSL version: 2.6.1.0" in text
