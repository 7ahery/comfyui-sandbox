"""Back up, Reinstall ComfyUI… and Uninstall… for real (feat-tui-integration 12): `backup`, `recreate` and
`uninstall` run in-process under home's live view, like Update….

The gateway half runs the real gateway on the CLI's fake `Deps` (the spec's seam B): each command's
plan reaches the operation's progress. The screen half drives home on that gateway with Textual's
pilot: the command line shown is the one run, the steps are the command's own, L shows its full
log, Esc doesn't cancel, and Reinstall ComfyUI… and Uninstall… ask for the name typed, letter by letter,
before anything is touched. A Starting sandbox's menu is checked on the demo gateway (seam A).
"""

from __future__ import annotations

import asyncio
import re
import threading

from test_real_actions import ran_lines
from test_real_home_reads import in_distro
from test_real_update import deps, home_on, progress_of
from test_uninstall import Distros, remaining, unregisters

import scenarios
import wslcalls
from comfyui_sandbox.tui.gateway.demo import DemoGateway
from comfyui_sandbox.tui.gateway.records import Sandbox
from comfyui_sandbox.tui.home.app import SandboxApp
from comfyui_sandbox.tui.states import SandboxState, StepStatus

BACKUP_TAR = re.compile(r"comfyui-\d{8}-\d{6}\.tar")


def held(runner, word: str) -> threading.Event:
    """The in-distro call with `word` in its argv takes until the returned event is set, so what's on
    screen meanwhile can be looked at."""
    release = threading.Event()
    runner.on_match(lambda argv: word in argv, lambda argv: (release.wait(30), (0, ""))[1])
    return release


def wipes(runner) -> list:
    return [c for c in wslcalls.commands(runner.calls) if c.argv[:1] == ("rm",)]


# -- the gateway --------------------------------------------------------------------------------------


def test_a_backups_plan_is_its_progress_and_its_result_names_the_restore_command(deps):
    result, progress = progress_of(deps, ["backup", "--distro", "x"])

    assert result.ok, result.message
    titles = [step.title for step in progress.plan]
    assert titles[0] == "Save the custom-node snapshot" and BACKUP_TAR.search(titles[1])
    assert [step.status for step in progress.plan] == [StepStatus.DONE, StepStatus.DONE]
    assert result.data["restore"] == f"comfyui-sandbox restore --distro x --backup {result.data['name']}"


def test_a_recreates_plan_is_announced_once_its_name_is_typed(deps):
    result, progress = progress_of(deps, ["reinstall", "--distro", "x", "--confirm", "x"])

    assert result.ok, result.message
    assert progress.plan[0].title == "Stop ComfyUI" and len(progress.plan) == 10
    assert all(step.status == StepStatus.DONE for step in progress.plan)


# -- Back up --------------------------------------------------------------------------------------------


def test_back_up_runs_the_command_line_it_shows_under_the_live_view_and_says_how_to_restore(deps):
    async def test(r: scenarios.Rec) -> None:
        await r.row("x")
        await r.choose("Back up")
        await r.wait("Back up x now?")
        await r.press("enter")
        await r.wait("Save the custom-node snapshot")
        assert "comfyui-sandbox backup --distro x" in r.text
        await r.wait("comfyui-sandbox restore --distro x --backup comfyui-")  # the card, with the core's message
        assert re.search(r"comfyui-sandbox restore --distro x --backup " + BACKUP_TAR.pattern, r.text), r.text
        await r.wait("Press any key to go back")
        await r.press("enter", settle=0.3)

    home_on(deps, test)
    assert ran_lines(deps) == ["backup --distro x"]


def test_esc_doesnt_cancel_a_backup_and_l_shows_its_full_log(deps, runner):
    release = held(runner, "save-snapshot")

    async def test(r: scenarios.Rec) -> None:
        await r.row("x")
        await r.choose("Back up")
        await r.wait("Back up x now?")
        await r.press("enter")
        await r.wait("Save the custom-node snapshot")
        await r.press("escape", "escape")
        await r.settle(0.3)
        assert "again to cancel" not in r.text and "(cancelled)" not in r.text
        await r.press("l")
        await r.wait("==> [1/2] Save the custom-node snapshot")  # the CLI's own lines
        await r.press("escape")
        release.set()
        await r.wait("Press any key to go back")
        await r.press("enter", settle=0.3)

    home_on(deps, test)
    assert ran_lines(deps) == ["backup --distro x"]


# -- Reinstall ComfyUI… and Uninstall… ---------------------------------------------------------------------------


def test_recreate_takes_only_the_whole_name_then_runs_its_steps_under_the_live_view(deps, runner):
    release = held(runner, "build")

    async def test(r: scenarios.Rec) -> None:
        await r.row("x")
        await r.choose("Reinstall ComfyUI…")
        await r.wait("Type the sandbox name to confirm (x):")
        assert "comfyui-sandbox reinstall --distro x" in r.text
        await r.press("y", "enter", settle=0.3)  # a wrong name isn't taken: nothing goes ahead
        assert "Type the sandbox name to confirm (x):" in r.text and not wipes(runner)
        await r.press("backspace", "x", "enter")
        await r.wait("Rebuild the image without cache")
        assert "Delete data/" in r.text and "Wait for the first start" in r.text
        await r.press("escape", "escape")
        await r.settle(0.3)
        assert "again to cancel" not in r.text
        await r.press("l")
        await r.wait("==> [4/10] Rebuild the image without cache")  # its full log: the CLI's own lines
        await r.press("escape")
        release.set()
        await r.wait("Reinstalled ComfyUI in x")  # the core's message, on the outcome card
        await r.wait("Press any key to go back")
        await r.press("enter", settle=0.3)

    home_on(deps, test)
    assert ran_lines(deps) == ["reinstall --distro x"]
    assert len(wipes(runner)) == 1


def test_esc_on_the_typed_name_backs_out_and_touches_nothing(deps, runner):
    async def test(r: scenarios.Rec) -> None:
        await r.row("x")
        await r.choose("Reinstall ComfyUI…")
        await r.wait("Type the sandbox name to confirm (x):")
        await r.press("escape", settle=0.3)
        await r.wait("Cancelled. Nothing was changed.")
        assert "What do you want to do with x?" in r.text  # back on its menu, no outcome to dismiss

    home_on(deps, test)
    assert not wipes(runner)


def test_uninstall_runs_its_steps_under_the_live_view_and_the_sandbox_goes(deps, runner, environ, home):
    Distros(runner, environ, ["Ubuntu", "x"])

    async def test(r: scenarios.Rec) -> None:
        await r.row("x")
        await r.choose("Uninstall…")
        await r.wait("Type the sandbox name to confirm (x):")
        await r.press("x", "enter")
        await r.wait("Unregister x")
        await r.wait("Uninstalled x.")
        await r.wait("Press any key to go back")
        await r.press("enter", settle=0.5)
        await r.wait("Pick a sandbox or an action")  # its menu closes: it's gone

    home_on(deps, test)
    assert ran_lines(deps) == ["uninstall --distro x"]
    assert len(unregisters(runner)) == 1 and remaining(home) == []


# -- a Starting sandbox ----------------------------------------------------------------------------------


def test_a_starting_sandboxs_menu_offers_exactly_stop_and_show_logs():
    gateway = DemoGateway([Sandbox("warming", SandboxState.STARTING, 8188)])

    async def scenario() -> None:
        async with SandboxApp(gateway).run_test(size=(160, 45)) as pilot:
            r = scenarios.Rec(pilot)
            await r.wait("Pick a sandbox or an action")
            await r.row("warming")
            await r.wait("What do you want to do with warming?")
            menu = r.text.split("What do you want to do with warming?")[1]
            offered = [
                label
                for label in (
                    "Open in browser",
                    "Start (in its own window)",
                    "Run here",
                    "Stop",
                    "Show logs",
                    "Resume install",
                    "Update…",
                    "Back up",
                    "Restore…",
                    "Settings…",
                    "Reinstall ComfyUI…",
                    "Uninstall…",
                )
                if re.search(rf"\b{re.escape(label)}", menu)
            ]
            assert offered == ["Stop", "Show logs"]
            assert "Back" in menu

    asyncio.run(scenario())


# -- Restore… (fix/tui-restore) ---------------------------------------------------------------------------

OLD, NEW = "comfyui-20260101-100000.tar", "comfyui-20260301-090000.tar"
WHICH = "Which backup do you want to restore?"


def with_backups(runner, *names: str) -> None:
    runner.on(*in_distro("x", "ls", "-1", "/home/comfy/comfyui-backups"), stdout="".join(f"{n}\n" for n in names))


def extracted(runner) -> list[str]:
    return [
        c.argv[-1].rsplit("/", 1)[1]
        for c in wslcalls.commands(runner.calls)
        if c.argv[:1] == ("tar",) and "-xf" in c.argv
    ]


def test_a_restores_plan_is_its_progress_and_every_step_ends_done(deps, runner):
    with_backups(runner, OLD, NEW)

    result, progress = progress_of(deps, ["restore", "--distro", "x", "--backup", OLD, "--confirm", "x"])

    assert result.ok, result.message
    assert [step.title for step in progress.plan] == [
        "Stop ComfyUI",
        f"Restore {OLD}",
        "Start ComfyUI for this step",
        "Wait for ComfyUI to answer",
        "Reinstall the custom nodes from the snapshot",
        "Restart ComfyUI to load the nodes",
        "Wait for ComfyUI to answer again",
    ]
    assert all(step.status == StepStatus.DONE for step in progress.plan)
    assert extracted(runner) == [OLD]


def test_restore_says_what_it_replaces_asks_which_backup_and_the_name_then_runs_under_the_live_view(deps, runner):
    with_backups(runner, OLD, NEW)
    release = held(runner, "restore-snapshot")

    async def test(r: scenarios.Rec) -> None:
        await r.row("x")
        await r.choose("Restore…")
        await r.wait(WHICH)
        assert "comfyui-sandbox restore --distro x" in r.text  # no --backup, no --confirm: the command asks
        assert "Replaced from the backup" in r.text and "Files the backup doesn't have" in r.text
        assert "This can't be undone." in r.text
        menu = r.text.split(WHICH)[1]
        assert menu.index(NEW) < menu.index(OLD)  # newest first
        assert "made 1 Mar 2026, 09:00 UTC · the newest" in r.text  # the pointer starts on the newest
        await r.choose(OLD)
        await r.wait("Type the sandbox name to confirm (x):")
        assert "Replaced from the backup" in r.text  # the explanation stays for the second question
        assert not extracted(runner)
        await r.press("x", "enter")
        await r.wait("Reinstall the custom nodes from the snapshot")
        assert f"Restore {OLD}" in r.text
        assert "Replaced from the backup" not in r.text  # the steps have the screen to themselves
        await r.press("escape", "escape")
        await r.settle(0.3)
        assert "again to cancel" not in r.text  # no Esc-cancel
        release.set()
        await r.wait("Backup restored to x")
        assert f"Restored {OLD}." in r.text  # the core's message, on the card
        await r.wait("Press any key to go back")
        await r.press("enter", settle=0.3)

    home_on(deps, test)
    assert ran_lines(deps) == ["restore --distro x"]
    assert extracted(runner) == [OLD]


def test_restore_with_one_backup_still_lists_it_so_its_name_is_seen(deps, runner):
    with_backups(runner, OLD)

    async def test(r: scenarios.Rec) -> None:
        await r.row("x")
        await r.choose("Restore…")
        await r.wait(WHICH)
        assert OLD in r.text.split(WHICH)[1]
        await r.choose(OLD)
        await r.wait("Type the sandbox name to confirm (x):")
        await r.press("x", "enter")
        await r.wait("Backup restored to x")
        await r.wait("Press any key to go back")
        await r.press("enter", settle=0.3)

    home_on(deps, test)
    assert extracted(runner) == [OLD]


def test_esc_on_which_backup_backs_out_and_restores_nothing(deps, runner):
    with_backups(runner, OLD, NEW)

    async def test(r: scenarios.Rec) -> None:
        await r.row("x")
        await r.choose("Restore…")
        await r.wait(WHICH)
        await r.press("escape", settle=0.3)
        await r.wait("Cancelled. Nothing was changed.")
        assert "What do you want to do with x?" in r.text  # back on its menu, no outcome to dismiss
        assert "Replaced from the backup" not in r.text

    home_on(deps, test)
    assert not extracted(runner)
    assert not [c for c in wslcalls.commands(runner.calls) if c.argv[-1:] == ("stop",)]  # ComfyUI left running


def test_restore_with_no_backup_just_says_so_and_starts_no_restore(deps, runner):
    with_backups(runner)

    async def test(r: scenarios.Rec) -> None:
        await r.row("x")
        await r.choose("Restore…")
        await r.wait("x has no backup to restore yet")
        assert "What do you want to do with x?" in r.text  # back on its menu, no card to dismiss
        assert "Restoring x" not in r.text and "It didn't go through" not in r.text
        assert WHICH not in r.text and "Type the sandbox name" not in r.text
        assert "Replaced from the backup" not in r.text

    home_on(deps, test)
    assert not extracted(runner)
