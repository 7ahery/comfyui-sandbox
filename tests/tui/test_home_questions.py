"""Home asking a command's questions in place: Settings (`config`'s own menu), and the typed
confirmation of Reinstall ComfyUI… and Uninstall….

Screen tests drive home with Textual's pilot on the demo gateway (the spec's seam A), whose
commands ask what the real ones ask, with the same display hints; one runs on the real gateway over
the CLI's fake `Deps`, to show a setting changed in home is saved as the CLI saves it. What's
asserted is what's on screen and which command lines ran.
"""

from __future__ import annotations

import asyncio
import re

import pytest
from test_real_actions import deps, ran_lines
from test_real_gateway import installed
from test_real_questions import ENV, PROJECT, port_saved

import scenarios
from comfyui_sandbox.tui.gateway.demo import DemoGateway, Scenario, seed_sandboxes
from comfyui_sandbox.tui.gateway.real import RealGateway
from comfyui_sandbox.tui.gateway.records import Sandbox
from comfyui_sandbox.tui.home.app import SandboxApp
from comfyui_sandbox.tui.home.flows import menu_choices
from comfyui_sandbox.tui.states import SandboxState


def on_demo(test, *, size=(160, 45)) -> DemoGateway:
    """Runs home on the demo gateway and hands test a Rec once its picker is up. Returns the
    gateway, whose `ran` lists the command lines run."""
    return on_gateway(DemoGateway(seed_sandboxes(), scenario=Scenario(speed=8)), test, size=size)


def on_gateway(gateway: DemoGateway, test, *, size=(160, 45)) -> DemoGateway:
    """on_demo, on a demo gateway of the test's own."""

    async def scenario() -> None:
        async with SandboxApp(gateway).run_test(size=size) as pilot:
            r = scenarios.Rec(pilot)
            await r.wait("Pick a sandbox or an action")
            await r.settle(0.4)
            await test(r)

    asyncio.run(scenario())
    return gateway


def pointer_line(r: scenarios.Rec) -> str:
    """The line the select's pointer is on (not the echoed command line, which starts with ❯ too)."""
    return next(line for line in r.text.splitlines() if re.search(r"❯ . ", line) and "comfyui-sandbox " not in line)


def sandbox(gateway: DemoGateway, name: str):
    return next((s for s in asyncio.run(gateway.sandboxes()) if s.name == name), None)


# -- Settings ---------------------------------------------------------------------------------------


def test_settings_shows_configs_menu_with_each_settings_hint_under_the_pointer():
    async def test(r: scenarios.Rec) -> None:
        await r.row("comfyui-flux")
        await r.choose("Settings…")
        await r.wait("What do you want to change?")
        assert "nothing is saved until Apply changes" in r.text  # the question's own hint
        for label in ("Launch flags", "Manager UI", "Pinned memory", "Security level"):
            assert label in r.text
        assert "Port (8189)" in pointer_line(r) and "the Windows port ComfyUI answers on" in pointer_line(r)
        await r.press("down")
        assert "Launch flags" in pointer_line(r) and "the flags ComfyUI starts with" in pointer_line(r)
        assert "the Windows port" not in r.text  # a hint shows only beside the choice under the pointer
        await r.press("escape", settle=0.3)

    on_demo(test)


def test_a_setting_changed_in_settings_is_applied_with_the_commands_own_message():
    async def test(r: scenarios.Rec) -> None:
        await r.row("comfyui-flux")
        await r.choose("Settings…")
        await r.wait("What do you want to change?")
        await r.choose("Port (8189)")
        await r.wait("Port (1024-65535):")
        assert "A port in use is caught before anything changes" in r.text
        await r.press(*"9100", "enter")
        await r.wait("What do you want to change?")
        await r.choose("Apply changes")
        await r.wait("Press any key to go back")
        assert "Changed COMFY_PORT in comfyui-flux" in r.text
        await r.press("enter", settle=0.3)

    gateway = on_demo(test)
    assert gateway.ran == [("config", "--distro", "comfyui-flux")]
    assert sandbox(gateway, "comfyui-flux").port == 9100


def test_esc_on_a_settings_question_backs_out_and_changes_nothing():
    async def test(r: scenarios.Rec) -> None:
        await r.row("comfyui-flux")
        await r.choose("Settings…")
        await r.wait("What do you want to change?")
        await r.choose("Port (8189)")
        await r.wait("Port (1024-65535):")
        await r.press(*"9100", "escape", settle=0.3)
        await r.wait("Cancelled. Nothing was changed.")
        assert "What do you want to do with comfyui-flux?" in r.text  # back on its menu

    gateway = on_demo(test)
    assert sandbox(gateway, "comfyui-flux").port == 8189


def test_settings_on_the_real_gateway_saves_the_change_as_the_cli_would(deps, home, runner):
    installed(home, "x", 8188)
    runner.on("wsl.exe", "-d", "x", "-u", "comfy", "--cd", "/", "--exec", "env", "cat", f"{PROJECT}/.env", stdout=ENV)

    async def scenario() -> None:
        async with SandboxApp(RealGateway(deps)).run_test(size=(160, 45)) as pilot:
            r = scenarios.Rec(pilot)
            await r.wait("Pick a sandbox or an action")
            await r.settle(0.6)
            await r.row("x")
            await r.choose("Settings…")
            await r.wait("What do you want to change?")
            assert "comfyui-sandbox config --distro x" in r.text
            await r.choose("Port (8188)")
            await r.wait("Port (1024-65535):")
            await r.press(*"9000", "enter")
            await r.wait("What do you want to change?")
            await r.choose("Apply changes")
            await r.wait("Press any key to go back")
            assert "Changed COMFY_PORT in x" in r.text

    asyncio.run(scenario())
    assert ran_lines(deps) == ["config --distro x"]
    assert port_saved(home, "x") == 9000


# -- Reinstall ComfyUI… and Uninstall… ---------------------------------------------------------------------------


def test_recreate_says_what_goes_then_asks_its_typed_confirmation_with_its_hint():
    async def test(r: scenarios.Rec) -> None:
        await r.row("comfyui")
        await r.choose("Reinstall ComfyUI…")
        await r.wait("Type the sandbox name to confirm (comfyui):")
        assert "comfyui-sandbox reinstall --distro comfyui" in r.text  # no --confirm: the command asks
        assert "Custom nodes" in r.text and "Kept" in r.text  # what goes and what stays, still shown
        assert "This can't be undone." in r.text
        await r.press("escape", settle=0.3)

    on_demo(test)


def test_esc_on_the_typed_confirmation_backs_out_and_deletes_nothing():
    async def test(r: scenarios.Rec) -> None:
        await r.row("sdxl")
        await r.choose("Uninstall…")
        await r.wait("Type the sandbox name to confirm (sdxl):")
        await r.press("s", "escape", settle=0.3)
        await r.wait("Cancelled. Nothing was changed.")

    gateway = on_demo(test)
    assert gateway.ran == [("uninstall", "--distro", "sdxl")]
    assert sandbox(gateway, "sdxl") is not None


def test_the_name_is_typed_letter_by_letter_and_only_the_whole_name_is_taken():
    """The typed confirmation is drawn as the name to type: its letters light up as they match, and
    Enter on anything else isn't sent (the command would refuse it; here it can't even be sent)."""

    async def test(r: scenarios.Rec) -> None:
        await r.row("sdxl")
        await r.choose("Uninstall…")
        await r.wait("Type the sandbox name to confirm (sdxl):")
        assert "0/4" in r.text  # how much of the name is typed right so far
        await r.press(*"sdx")
        assert "3/4" in r.text
        await r.press("enter", settle=0.3)
        assert "Type the sandbox name to confirm (sdxl):" in r.text and "Press any key" not in r.text
        await r.press("l")
        assert "Enter confirms. This can't be undone." in r.text
        await r.press("escape", settle=0.3)
        await r.wait("Cancelled. Nothing was changed.")

    gateway = on_demo(test)
    assert sandbox(gateway, "sdxl") is not None


def test_the_typed_name_goes_ahead_and_the_outcome_shows_the_commands_message():
    async def test(r: scenarios.Rec) -> None:
        await r.row("sdxl")
        await r.choose("Uninstall…")
        await r.wait("Type the sandbox name to confirm (sdxl):")
        await r.press(*"sdxl", "enter")
        await r.wait("Uninstalled sdxl.")  # the core's message, on the outcome card under its steps
        await r.wait("Press any key to go back")
        await r.press("enter", settle=0.5)
        await r.wait("Pick a sandbox or an action")  # its menu closes: it's gone

    gateway = on_demo(test)
    assert sandbox(gateway, "sdxl") is None


# -- Restore… (fix/tui-restore) ---------------------------------------------------------------------------

WHICH = "Which backup do you want to restore?"


def labels(sandbox_: Sandbox) -> list[str]:
    return [str(choice[0]) for choice in menu_choices(sandbox_) if choice is not None]


@pytest.mark.parametrize("state", [SandboxState.RUNNING, SandboxState.STOPPED])
def test_restore_is_offered_right_after_back_up_on_an_installed_sandbox(state):
    offered = labels(Sandbox("x", state, 8188))
    assert offered[offered.index("Back up") + 1] == "Restore…"


@pytest.mark.parametrize("state", [SandboxState.INCOMPLETE, SandboxState.STARTING])
def test_restore_is_not_offered_on_an_incomplete_or_starting_sandbox(state):
    assert "Restore…" not in labels(Sandbox("x", state, 8188))


def test_restore_asks_which_backup_newest_first_then_the_name_and_ends_stopped():
    async def test(r: scenarios.Rec) -> None:
        await r.row("comfyui")  # running, with three seeded backups
        await r.choose("Restore…")
        await r.wait(WHICH)
        menu = r.text.split(WHICH)[1]
        assert menu.index("comfyui-20261011") < menu.index("comfyui-20260903") < menu.index("comfyui-20260412")
        assert "made 11 Oct 2026, 22:10 UTC · the newest" in r.text
        assert r.pilot.app.sampler is None  # the logo previews the steps, not the questions before them
        await r.choose("comfyui-20260903-194402.tar")
        await r.wait("Type the sandbox name to confirm (comfyui):")
        assert "Replaced from the backup" in r.text
        assert r.pilot.app.sampler is None
        await r.press(*"comfyui", "enter")
        await r.wait("Restore comfyui-20260903-194402.tar")
        assert r.pilot.app.sampler is not None
        await r.wait("Backup restored to comfyui")
        assert "Restored comfyui-20260903-194402.tar." in r.text
        await r.wait("Press any key to go back")
        await r.press("enter", settle=0.3)

    gateway = on_demo(test)
    assert gateway.ran == [("restore", "--distro", "comfyui")]
    assert sandbox(gateway, "comfyui").state == SandboxState.STOPPED


def test_a_backup_made_in_home_is_offered_by_restore_as_the_newest():
    gateway = DemoGateway(seed_sandboxes(), scenario=Scenario(speed=8))

    async def test(r: scenarios.Rec) -> None:
        await r.row("comfyui-flux")  # one seeded backup: Restore… wouldn't ask
        await r.choose("Back up")
        await r.wait("Back up comfyui-flux now?")
        await r.press("enter")
        await r.wait("Press any key to go back")
        await r.press("enter", settle=0.3)
        made = gateway.backups["comfyui-flux"][-1]
        await r.choose("Restore…")
        await r.wait(WHICH)  # now there's a choice
        menu = r.text.split(WHICH)[1]
        assert menu.index(made) < menu.index("comfyui-20260920-120000.tar")
        await r.press("escape", settle=0.3)
        await r.wait("Cancelled. Nothing was changed.")

    on_gateway(gateway, test)
    assert gateway.ran == [("backup", "--distro", "comfyui-flux"), ("restore", "--distro", "comfyui-flux")]
    assert len(gateway.backups["comfyui-flux"]) == 2


def test_restore_with_no_backup_just_says_so_and_starts_no_restore():
    async def test(r: scenarios.Rec) -> None:
        await r.row("sdxl")  # running, no backups
        await r.choose("Restore…")
        await r.wait("sdxl has no backup to restore yet")
        assert "What do you want to do with sdxl?" in r.text  # back on its menu, no card to dismiss
        assert "Restoring sdxl" not in r.text and "Replaced from the backup" not in r.text
        assert WHICH not in r.text and "Type the sandbox name" not in r.text

    gateway = on_demo(test)
    assert sandbox(gateway, "sdxl").state == SandboxState.RUNNING


def test_restore_runs_through_in_a_small_window():
    """The explanation, the two questions and a seven-step plan in 80x24 (a short page once crashed
    Recreate's live view)."""

    async def test(r: scenarios.Rec) -> None:
        await r.row("comfyui")
        await r.choose("Restore…")
        await r.wait(WHICH)
        await r.press("enter")  # the newest
        await r.wait("Type the sandbox name to confirm (comfyui):")
        await r.press(*"comfyui", "enter")
        await r.wait("Backup restored to comfyui")
        await r.wait("Press any key to go back")
        await r.press("enter", settle=0.3)
        await r.wait("What do you want to do with comfyui?")

    gateway = on_demo(test, size=(80, 24))
    assert gateway.ran == [("restore", "--distro", "comfyui")]
