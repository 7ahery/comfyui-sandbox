"""The TUI's snapshot scenarios: drives home and the run window headlessly (Textual's Pilot) on the demo
gateway, through every flow, and records the screen's text at fixed points. test_snapshots.py compares
those screens with the ones recorded in snapshots/.

    python scenarios.py <scenario> <out.json>

Each scenario runs in its own process, so one flow's module-level state (the fakes' random stream,
memoised measurements, what was opened) can't leak into the next. The fakes' randomness is seeded; telemetry is frozen
(its numbers are drawn at timer ticks, which would reorder the other draws), the run window isn't
spawned, and no browser opens. The screen text is normalised where it moves on its own: spinners,
the logo's noise, durations, timestamps and how far a bar is filled.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import sys
import time
from pathlib import Path

import textual.app

from comfyui_sandbox.tui.gateway import Sandbox
from comfyui_sandbox.tui.gateway.demo import DemoGateway, LaunchScenario, Scenario, home, seed_sandboxes, telemetry
from comfyui_sandbox.tui.home.app import SandboxApp
from comfyui_sandbox.tui.run_window.app import LaunchApp
from comfyui_sandbox.tui.states import SandboxState

OPENED: list[str] = []  # what would have opened: URLs, run windows
textual.app.App.open_url = lambda self, url, new_tab=True: OPENED.append(url)
telemetry.Telemetry.tick = lambda self, sandboxes: None
# Recorders in place of the real openers: they return what OPENED.append does (None), not a window.
home.open_run_window = lambda name, port, models, speed: OPENED.append(f"run-window {name} {port}")  # ty: ignore[invalid-assignment]
home.open_browser = lambda url: OPENED.append(url)  # ty: ignore[invalid-assignment]


def home_app(*, empty=False, fail=False, port_taken=False, speed=1.0, intro=False) -> SandboxApp:
    return SandboxApp(
        DemoGateway(seed_sandboxes(empty), scenario=Scenario(speed=speed, fail=fail, port_taken=port_taken)),
        intro=intro,
    )


def run_app(*, distro="comfyui", port=8188, models="~/comfyui-models", **scenario) -> LaunchApp:
    sandbox = Sandbox(distro, SandboxState.STOPPED, port, models)
    return LaunchApp(DemoGateway([sandbox], launch=LaunchScenario(**scenario)), sandbox, replay=True)


# -- reading the screen --------------------------------------------------------------------------

SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def screen_text(app) -> str:
    # The compositor is private, but Textual 8.2.8 has no public way to read the screen as plain
    # text: export_screenshot() renders the same strips through it into SVG, whose text would need
    # unescaping and loses the line structure the snapshots compare. This is what the screen draws.
    strips = app.screen._compositor.render_strips()
    return "\n".join("".join(seg.text for seg in strip).rstrip() for strip in strips)


def normalize(s: str) -> str:
    s = re.sub(f"[{SPIN}]", "*", s)
    s = re.sub(r"[█▀▄▌▐]", "#", s)  # the logo, as noise resolves it
    s = re.sub(r"\d{8}-\d{6}", "<stamp>", s)
    s = re.sub(r"\b\d+m \d{2}s\b|\b\d+(?:\.\d)?s\b", "<t>", s)
    s = re.sub(r"(GPU [━╸]*) +\d+%", r"\1 <pct>", s)
    s = re.sub(r"[━╸]+ <t>", "<bar> <t>", s)  # a draining "press again" bar
    s = re.sub(r"[━╸]{5,}", lambda m: f"<bar:{len(m.group())}>", s)  # how far a bar is filled
    s = re.sub(r"(?m)^[ #]*#{3,}[ #]*$", "<banner>", s)  # the run window's logo, resolving or glitching
    # The hero's logo previews operations as noise: only the wordmark beside it is stable.
    s = re.sub(r"(?m)^ +[# ]+?(?= {3}(?:S A N D B O X|v1\.0\.0)$)", "<logo>", s)
    return s


class Rec:
    def __init__(self, pilot) -> None:
        self.pilot, self.snaps = pilot, []

    @property
    def text(self) -> str:
        return screen_text(self.pilot.app)

    async def settle(self, seconds: float = 0.25) -> None:
        await self.pilot.pause(seconds)

    async def snap(self, name: str, settle: float = 0.25) -> None:
        await self.settle(settle)
        self.snaps.append([name, normalize(self.text)])

    async def wait(self, needle: str, timeout: float = 60) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if needle in self.text:
                return
            await self.pilot.pause(0.05)
        raise TimeoutError(f"{needle!r} never showed:\n{self.text}")

    async def gone(self, needle: str, timeout: float = 60) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if needle not in self.text:
                return
            await self.pilot.pause(0.05)
        raise TimeoutError(f"{needle!r} never went:\n{self.text}")

    async def press(self, *keys: str, settle: float = 0.08) -> None:
        for key in keys:
            await self.pilot.press(key)
            await self.pilot.pause(settle)

    async def choose(self, label: str) -> None:
        """Moves a select's pointer to label and picks it."""
        pattern = re.compile(r"❯ . " + re.escape(label) + r"(?: {2,}|\s*$)")
        for _ in range(40):
            if any(pattern.search(line) for line in self.text.splitlines()):
                await self.press("enter", settle=0.15)
                return
            await self.press("down")
        raise LookupError(f"no choice {label!r}:\n{self.text}")

    async def row(self, name: str) -> None:
        """Moves home's pointer to the sandbox row name and opens it."""
        pattern = re.compile(r"▌ " + re.escape(name) + r"\s")
        for _ in range(40):
            if any(pattern.search(line) for line in self.text.splitlines()):
                await self.press("enter", settle=0.15)
                return
            await self.press("down")
        raise LookupError(f"no row {name!r}:\n{self.text}")

    async def action(self, label: str) -> None:
        """Picks one of home's actions (Install a new sandbox, Check this PC, Quit)."""
        for _ in range(40):
            if any(re.search(r"❯ . " + re.escape(label) + r"(?: {2,}|\s*$)", line) for line in self.text.splitlines()):
                await self.press("enter", settle=0.15)
                return
            await self.press("up")
        raise LookupError(label)


# -- home scenarios ------------------------------------------------------------------------------


async def home_browse(r: Rec) -> None:
    await r.wait("Pick a sandbox or an action")
    await r.snap("home", settle=1.2)
    await r.press("down", "down", "down")
    await r.snap("down x3")
    await r.press("pagedown")
    await r.snap("pagedown")
    await r.press("end")
    await r.snap("end")
    await r.press("home")
    await r.snap("home key")
    await r.press("up")
    await r.snap("up wraps to Quit")
    await r.pilot.click(offset=(10, 10))
    await r.snap("click a row opens it")
    await r.press("escape", settle=0.3)
    await r.snap("esc goes back")
    await r.action("Quit")


async def home_logs(r: Rec) -> None:
    await r.wait("Pick a sandbox or an action")
    await r.row("comfyui")
    await r.wait("What do you want to do with comfyui?")
    await r.snap("menu running")
    await r.choose("Show logs")
    await r.wait("ComfyUI output")
    await r.snap("logs page", settle=0.1)
    await r.press("l", settle=0.2)
    await r.snap("logs full", settle=0.1)
    await r.press("up", "up", "up", "pageup")
    await r.snap("logs full scrolled", settle=0.1)
    await r.press("home")
    await r.snap("logs full top", settle=0.1)
    await r.press("end", "l", settle=0.2)
    await r.snap("logs page again", settle=0.1)
    await r.press("escape", settle=0.3)
    await r.snap("menu after logs")
    await r.choose("Back")
    await r.row("comfyui-flux")
    await r.choose("Show logs")
    await r.wait("ComfyUI output")
    await r.snap("logs of a stopped one")
    await r.press("escape", "escape", settle=0.3)
    await r.snap("home after logs")
    await r.action("Quit")


async def home_start_stop_run(r: Rec) -> None:
    await r.wait("Pick a sandbox or an action")
    await r.row("comfyui")
    await r.choose("Stop")
    await r.wait("Stopping the container")
    await r.snap("stopping")
    await r.press("l", settle=0.2)
    await r.wait("✔ Container comfyui-comfyui-1  Stopped")
    await r.snap("stop's full log", settle=0.05)
    await r.press("escape", settle=0.2)
    await r.wait("comfyui stopped")
    await r.snap("stopped flash")
    await r.choose("Run here")
    await r.wait("LIVE")
    await r.wait("To see the GUI")
    await r.snap("run here", settle=0.1)
    await r.press("escape")
    await r.snap("run here armed", settle=0.05)
    await r.press("l", settle=0.2)
    await r.press("escape", settle=0.2)
    await r.press("escape")
    await r.press("escape")
    await r.wait("Stopping")
    await r.snap("run here stopping", settle=0.05)
    await r.wait("What do you want to do with comfyui?")
    await r.snap("after run here")
    await r.choose("Start (in its own window)")
    await r.wait("Opening the run window")
    await r.snap("starting")
    await r.wait("What do you want to do with comfyui?")
    await r.snap("after start")
    await r.choose("Open in browser")
    await r.wait("Opened http")
    await r.snap("after open")
    await r.choose("Back")
    await r.press("ctrl+c")


async def home_update(r: Rec) -> None:
    await r.wait("Pick a sandbox or an action")
    await r.row("sdxl")
    await r.choose("Update… (update available)")
    await r.wait("Update what?")
    await r.snap("update what")
    await r.choose("Back")
    await r.choose("Update… (update available)")
    await r.choose("ComfyUI (v0.3.8 → v0.3.10 available)")
    await r.wait("Which ComfyUI?")
    await r.snap("which comfyui")
    await r.choose("Newest release (now)")
    await r.wait("Update ComfyUI now?")
    await r.snap("update comfyui confirm")
    await r.press("n", settle=0.3)
    await r.snap("update not started")
    await r.choose("Update… (update available)")
    await r.choose("Custom nodes (update available)")
    await r.wait("Update Custom nodes now?")
    await r.snap("update nodes confirm")
    await r.press("enter")
    await r.wait("Updating sdxl")
    await r.press("escape")
    await r.press("ctrl+c")
    await r.wait("Press any key to go back")
    await r.snap("update done")
    await r.press("l", settle=0.2)
    await r.snap("update full log")
    await r.press("l", settle=0.2)
    await r.press("space", settle=0.3)
    await r.snap("after update")
    await r.choose("Back")
    await r.action("Quit")


async def home_pinned(r: Rec) -> None:
    """A sandbox pinned to a release: its note, Update what?'s label, and switching it to another
    release (the text starts on its pin), then back to the newest release."""
    await r.wait("Pick a sandbox or an action")
    await r.wait("pinned to 0.3.40")
    await r.row("ltx-video")
    await r.choose("Update…")
    await r.wait("Update what?")
    await r.snap("pinned update what")
    await r.choose("ComfyUI (pinned to 0.3.40)")
    await r.wait("Which ComfyUI?")
    await r.snap("pinned which comfyui")
    await r.choose("A release you pick… (now)")
    await r.wait("Release number")
    await r.press(*["backspace"] * 6, "0", ".", "3", ".", "x")
    await r.snap("release number invalid", settle=0.3)
    await r.press("backspace", "6", "2", "enter")
    await r.wait("Switch ComfyUI to 0.3.62 now?")
    await r.snap("switch confirm")
    await r.press("enter")
    await r.wait("Press any key to go back")
    await r.snap("switch done")
    await r.press("enter", settle=0.3)
    await r.choose("Back")
    await r.wait("pinned to 0.3.62")
    await r.snap("home pinned to the new release", settle=0.5)
    await r.row("ltx-video")
    await r.choose("Update…")
    await r.choose("ComfyUI (pinned to 0.3.62)")
    await r.choose("Newest release")
    await r.wait("Update ComfyUI now?")
    await r.press("enter")
    await r.wait("Press any key to go back")
    await r.press("enter", settle=0.3)
    await r.choose("Back")
    await r.gone("pinned to")
    await r.snap("home unpinned", settle=0.5)
    await r.action("Quit")


async def home_backup_settings(r: Rec) -> None:
    await r.wait("Pick a sandbox or an action")
    await r.row("comfyui-flux")
    await r.choose("Back up")
    await r.wait("Back up comfyui-flux now?")
    await r.snap("backup confirm")
    await r.press("left", settle=0.1)
    await r.snap("backup confirm flipped")
    await r.press("right", "enter", settle=0.2)
    await r.wait("Press any key to go back")
    await r.snap("backup done")
    await r.press("enter", settle=0.3)
    await r.choose("Settings…")
    await r.wait("What do you want to change?")
    await r.snap("settings")
    await r.choose("Port (8189)")
    await r.wait("Port (1024-65535):")
    await r.press("8", "0")
    await r.snap("settings invalid")
    await r.press("enter")
    await r.snap("settings invalid enter")
    await r.press("backspace", "backspace", "8", "1", "8", "8")
    await r.press("enter")
    await r.wait("What do you want to change?")
    await r.choose("Apply changes")
    await r.wait("Press any key to go back")
    await r.snap("settings clash")
    await r.press("enter", settle=0.3)
    await r.choose("Settings…")
    await r.wait("What do you want to change?")
    await r.choose("Port (8189)")
    await r.press("9", "0", "0", "0", "enter")
    await r.wait("What do you want to change?")
    await r.choose("Apply changes")
    await r.wait("Press any key to go back")
    await r.snap("settings ok")
    await r.press("enter", settle=0.3)
    await r.choose("Settings…")
    await r.wait("What do you want to change?")
    await r.press("escape", settle=0.3)
    await r.wait("Cancelled. Nothing was changed.")
    await r.snap("settings cancelled")
    await r.choose("Back")
    await r.row("comfyui")
    await r.choose("Settings…")
    await r.wait("What do you want to change?")
    await r.choose("Pinned memory")
    await r.wait("on (default)")
    await r.choose("off (--disable-pinned-memory)")
    await r.wait("What do you want to change?")
    await r.choose("Apply changes")
    await r.wait("Press any key to go back")
    await r.snap("settings ok running")
    await r.press("enter", settle=0.3)
    await r.choose("Back")
    await r.action("Quit")


async def home_restore(r: Rec) -> None:
    await r.wait("Pick a sandbox or an action")
    await r.row("comfyui")
    await r.wait("What do you want to do with comfyui?")
    await r.choose("Restore…")
    await r.wait("Which backup do you want to restore?")
    await r.snap("restore which")
    await r.press("down", settle=0.1)
    await r.snap("restore which older")
    await r.press("enter")
    await r.wait("Type the sandbox name to confirm (comfyui):")
    await r.snap("restore confirm")
    for ch in "comfyui":
        await r.press(ch, settle=0.02)
    await r.press("enter")
    await r.wait("Press any key to go back", timeout=120)
    await r.snap("restore done")
    await r.press("enter", settle=0.3)
    await r.choose("Back")
    await r.row("sdxl")
    await r.choose("Restore…")
    await r.wait("sdxl has no backup to restore yet")
    await r.snap("restore no backup")
    await r.choose("Back")
    await r.action("Quit")


async def home_recreate_uninstall(r: Rec) -> None:
    await r.wait("Pick a sandbox or an action")
    await r.row("comfyui")
    await r.choose("Reinstall ComfyUI…")
    await r.wait("Type the sandbox name to confirm (comfyui):")
    await r.snap("recreate confirm")
    await r.press("c", "o", "m", "x")
    await r.snap("recreate typing wrong")
    await r.press("enter")
    await r.snap("recreate wrong enter")
    await r.press("escape", settle=0.3)
    await r.wait("Cancelled. Nothing was changed.")
    await r.snap("recreate cancelled")
    await r.choose("Reinstall ComfyUI…")
    await r.wait("Type the sandbox name to confirm (comfyui):")
    for ch in "comfyui":
        await r.press(ch, settle=0.02)
    await r.snap("recreate typed")
    await r.press("enter")
    await r.wait("Press any key to go back", timeout=120)
    await r.snap("recreate done")
    await r.press("enter", settle=0.3)
    await r.snap("after recreate")
    await r.choose("Back")
    await r.row("comfyui-flux")
    await r.choose("Uninstall…")
    await r.wait("Type the sandbox name to confirm (comfyui-flux):")
    await r.snap("uninstall confirm windows models")
    await r.press("escape", settle=0.3)
    await r.wait("Cancelled. Nothing was changed.")
    await r.choose("Back")
    await r.row("sdxl")
    await r.choose("Uninstall…")
    await r.wait("Type the sandbox name to confirm (sdxl):")
    await r.snap("uninstall confirm")
    for ch in "sdxl":
        await r.press(ch, settle=0.02)
    await r.press("enter")
    await r.wait("Press any key to go back", timeout=120)
    await r.snap("uninstall done")
    await r.press("enter", settle=0.5)
    await r.snap("home after uninstall", settle=0.5)
    await r.action("Quit")


async def through(r: Rec, needle: str) -> None:
    """Takes each question's default (Enter) until needle shows: an install's questions, say."""
    for _ in range(40):
        if needle in r.text:
            return
        await r.press("enter", settle=0.15)
    await r.wait(needle, timeout=5)


async def home_install(r: Rec) -> None:
    await r.wait("Pick a sandbox or an action")
    await r.action("Install a new sandbox")
    await r.wait("Where should your models live?")
    await r.snap("install models")
    await through(r, "Name of the new sandbox")
    await r.snap("install name")
    await r.choose("Other (type a value)")
    await r.wait("Sandbox name:")
    await r.press("c", "o", "m", "f", "y", "u", "i")
    await r.snap("install name taken")
    await r.press("!", settle=0.1)
    await r.snap("install name invalid")
    await r.press(*["backspace"] * 8, "n", "e", "w", "enter")
    await through(r, "nothing has changed yet")
    await r.snap("install review")
    await r.choose("Change something")
    await r.wait("Where should your models live?")
    await through(r, "Name of the new sandbox")
    await r.snap("install name again")  # the name typed before is an option now, and the default
    await through(r, "nothing has changed yet")
    await r.press("enter")
    await r.wait("is the port of the sandbox comfyui")
    await r.snap("install port taken")
    await r.press("enter")
    await r.wait("Installing new")
    await r.wait("Press any key to go back", timeout=180)
    await r.snap("install done")
    await r.press("enter", settle=0.4)
    await r.snap("home after install", settle=0.5)
    await r.action("Install a new sandbox")
    await r.wait("Where should your models live?")
    await r.press("escape", settle=0.3)
    await r.wait("Pick a sandbox or an action")
    await r.snap("install escaped")
    await r.action("Install a new sandbox")
    await through(r, "nothing has changed yet")
    await r.press("enter")
    await r.wait("is the port of the sandbox comfyui")
    await r.press("enter")
    await r.wait("Installing comfy-docker")
    await r.press("escape")
    await r.press("escape")
    await r.wait("cancelled")
    await r.wait("Pick a sandbox or an action")
    await r.snap("install cancelled", settle=0.3)
    await r.row("comfy-docker")  # Incomplete: its menu offers Resume install
    await r.snap("cancelled install's menu")
    await r.press("escape", settle=0.3)
    await r.action("Quit")


async def home_install_fail(r: Rec) -> None:
    await r.wait("Pick a sandbox or an action")
    await r.action("Install a new sandbox")
    await through(r, "nothing has changed yet")
    await r.press("enter")
    await r.wait("is the port of the sandbox comfyui")
    await r.press("enter")
    await r.wait("Press any key to go back", timeout=180)
    await r.snap("install failed")
    await r.press("l", settle=0.2)
    await r.snap("install failed full log")
    await r.press("escape", settle=0.2)
    await r.press("enter", settle=0.4)
    await r.snap("home after failure", settle=0.5)
    await r.row("comfy-docker")
    await r.snap("incomplete menu")
    await r.choose("Resume install")
    await r.wait("Installing comfy-docker")
    await r.wait("Press any key to go back", timeout=180)
    await r.snap("resume done")
    await r.press("enter", settle=0.4)
    await r.press("ctrl+c")


async def home_port_taken(r: Rec) -> None:
    """On a PC with no sandbox yet, a program holds the port a new install asks for."""
    await r.wait("Pick a sandbox or an action")
    await r.action("Install a new sandbox")
    await through(r, "nothing has changed yet")
    await r.press("enter")
    await r.wait("is used by python.exe")
    await r.snap("port question")
    await r.choose("Keep 8188, I'll close python.exe first")
    await r.wait("Close python.exe now")
    await r.snap("port closed?")
    await r.press("enter")
    await r.wait("Press any key to go back", timeout=180)
    await r.snap("port kept done")
    await r.press("enter", settle=0.4)
    await r.action("Install a new sandbox")
    await through(r, "nothing has changed yet")
    await r.press("enter")
    await r.wait("is the port of the sandbox comfy-docker")  # now it's the new sandbox's
    await r.press("escape")
    await r.wait("Install not started")
    await r.snap("port question escaped", settle=0.3)
    await r.action("Quit")


async def home_check(r: Rec) -> None:
    await r.wait("Pick a sandbox or an action")
    await r.action("Check this PC")
    await r.wait("Press any key to go back")
    await r.snap("check done")
    await r.press("enter", settle=0.4)
    await r.snap("home after check", settle=0.5)
    await r.action("Quit")


async def home_empty(r: Rec) -> None:
    await r.wait("Pick a sandbox or an action")
    await r.snap("empty home", settle=1.0)
    await r.action("Quit")


async def home_splash(r: Rec) -> None:
    await r.wait("S A N D B O X")
    await r.snap("splash", settle=2.6)
    await r.press("x")
    await r.wait("Pick a sandbox or an action")
    await r.snap("home after splash", settle=1.0)
    await r.action("Quit")


async def home_narrow(r: Rec) -> None:
    await r.wait("Pick a sandbox or an action")
    await r.snap("narrow home", settle=1.2)
    await r.row("flux-dev…nts-2026-autumn")
    await r.snap("narrow menu")
    await r.choose("Back up")
    await r.snap("narrow backup confirm")
    await r.press("enter")
    await r.wait("Press any key to go back")
    await r.snap("narrow backup done")
    await r.press("enter", settle=0.3)
    await r.choose("Back")
    await r.action("Quit")


async def home_tall(r: Rec) -> None:
    await r.wait("Pick a sandbox or an action")
    await r.snap("tall home", settle=1.2)
    await r.row("sd15-legacy")
    await r.choose("Update…")
    await r.choose("ComfyUI")
    await r.choose("Newest release (now)")
    await r.press("enter")
    await r.wait("Press any key to go back")
    await r.snap("tall update done")
    await r.press("enter", settle=0.3)
    await r.choose("Back")
    await r.action("Quit")


async def home_resize(r: Rec) -> None:
    await r.wait("Pick a sandbox or an action")
    await r.snap("before resize", settle=1.2)
    await r.pilot.resize_terminal(70, 20)
    await r.snap("small", settle=0.5)
    await r.pilot.resize_terminal(160, 45)
    await r.snap("big", settle=0.5)
    await r.action("Quit")


async def home_sized(r: Rec) -> None:
    """Home and a sandbox's menu at one window size: the extra sizes (see HOME) only check the
    layout, so they take the one path that draws the table, the hero and a menu."""
    await r.wait("Pick a sandbox or an action")
    await r.snap("home", settle=1.2)
    await r.row("comfyui")
    await r.wait("What do you want to do with comfyui?")
    await r.snap("menu")
    await r.press("escape", settle=0.3)
    await r.action("Quit")


HOME = {
    "home_browse": (home_browse, {}, (120, 30)),
    "home_logs": (home_logs, {"speed": 0.05}, (120, 30)),
    "home_start_stop_run": (home_start_stop_run, {"speed": 1}, (120, 30)),
    "home_update": (home_update, {"speed": 4}, (120, 30)),
    "home_pinned": (home_pinned, {"speed": 8}, (120, 30)),
    "home_backup_settings": (home_backup_settings, {"speed": 4}, (120, 30)),
    "home_recreate_uninstall": (home_recreate_uninstall, {"speed": 8}, (120, 30)),
    "home_restore": (home_restore, {"speed": 8}, (120, 30)),
    "home_install": (home_install, {"speed": 8}, (120, 30)),
    "home_install_fail": (home_install_fail, {"speed": 8, "fail": True}, (120, 30)),
    "home_port_taken": (home_port_taken, {"speed": 8, "port_taken": True, "empty": True}, (120, 30)),
    "home_check": (home_check, {}, (120, 30)),
    "home_empty": (home_empty, {"empty": True}, (120, 30)),
    "home_splash": (home_splash, {"intro": True}, (120, 30)),
    "home_narrow": (home_narrow, {"speed": 4}, (80, 24)),
    "home_tall": (home_tall, {"speed": 8}, (200, 60)),
    "home_resize": (home_resize, {}, (120, 30)),
    # Extra sizes: smaller than the narrow one, a laptop's, and a big monitor's.
    "home_size_60x20": (home_sized, {}, (60, 20)),
    "home_size_100x30": (home_sized, {}, (100, 30)),
    "home_size_160x45": (home_sized, {}, (160, 45)),
}


# -- run window scenarios ------------------------------------------------------------------------


async def run_default(r: Rec) -> None:
    await r.wait("Running at")
    await r.snap("running", settle=0.6)
    await r.press("l", settle=0.2)
    await r.snap("full log")
    await r.press("up", "up", "pageup", settle=0.05)
    await r.snap("full log scrolled")
    await r.press("home")
    await r.snap("full log top")
    await r.press("end", "pagedown")
    await r.press("escape", settle=0.2)
    await r.snap("back from log")
    await r.press("ctrl+c")
    await r.snap("armed", settle=0.05)
    await r.press("ctrl+c")
    await r.wait("Stopping ComfyUI")
    await r.snap("stopping", settle=0.05)


async def run_fail(r: Rec) -> None:
    await r.wait("ComfyUI stopped before it was ready")
    await r.snap("failed", settle=1.5)
    await r.press("l", settle=0.2)
    await r.snap("failed log")
    await r.press("l", settle=0.2)
    await r.press("enter")


async def run_broken_node(r: Rec) -> None:
    await r.wait("failed to import")
    await r.snap("broken node", settle=0.6)
    await r.press("r", settle=0.3)
    await r.snap("replayed")
    await r.wait("failed to import")
    await r.press("ctrl+c", "ctrl+c")


async def run_port_taken(r: Rec) -> None:
    await r.wait("Use port 8189 instead")
    await r.snap("port question", settle=0.4)
    await r.press("enter")
    await r.wait("Running at")
    await r.snap("port taken", settle=0.6)
    await r.press("ctrl+c", "ctrl+c")


async def run_already(r: Rec) -> None:
    await r.wait("Already running in another window")
    await r.snap("elsewhere", settle=0.6)
    await r.press("enter")


async def run_update(r: Rec) -> None:
    await r.wait("is available")
    await r.snap("update", settle=0.6)
    await r.press("ctrl+c", "ctrl+c")


async def run_pinned(r: Rec) -> None:
    await r.wait("Pinned to ComfyUI 0.3.40")
    await r.snap("pinned", settle=0.6)
    await r.press("ctrl+c", "ctrl+c")


async def run_narrow(r: Rec) -> None:
    await r.wait("Running at")
    await r.snap("narrow", settle=0.6)
    await r.pilot.resize_terminal(140, 40)
    await r.snap("resized", settle=0.5)
    await r.press("ctrl+c", "ctrl+c")


async def run_loading(r: Rec) -> None:
    await r.wait("Loading custom nodes")
    await r.snap("loading", settle=0.05)
    await r.press("ctrl+c", "ctrl+c")
    await r.wait("ComfyUI has been stopped", timeout=30)
    await r.snap("stopped", settle=0.05)


async def run_sized(r: Rec) -> None:
    """The run window, running, at one window size (see RUN)."""
    await r.wait("Running at")
    await r.snap("running", settle=0.6)
    await r.press("ctrl+c", "ctrl+c")


LONG_MODELS = r"\\nas.local\media\models\stable-diffusion\checkpoints-and-loras-2026"
RUN = {
    "run_default": (run_default, {"speed": 4}, (120, 30)),
    "run_fail": (run_fail, {"speed": 4, "fail": True}, (120, 30)),
    "run_broken_node": (run_broken_node, {"speed": 4, "broken_node": True}, (120, 30)),
    "run_port_taken": (run_port_taken, {"speed": 4, "port_taken": True}, (120, 30)),
    "run_already": (run_already, {"already_running": True}, (120, 30)),
    "run_update_release": (run_update, {"speed": 4, "update": "release"}, (120, 30)),
    "run_update_nightly": (run_update, {"speed": 4, "update": "nightly"}, (120, 30)),
    "run_pinned": (run_pinned, {"speed": 4, "update": "pinned"}, (120, 30)),
    "run_narrow": (run_narrow, {"speed": 4, "models": LONG_MODELS}, (80, 24)),
    "run_loading": (run_loading, {"speed": 1}, (120, 30)),
    # Extra sizes, as for home.
    "run_size_60x20": (run_sized, {"speed": 4}, (60, 20)),
    "run_size_100x30": (run_sized, {"speed": 4}, (100, 30)),
    "run_size_200x60": (run_sized, {"speed": 4, "models": LONG_MODELS}, (200, 60)),
}


SCENARIOS = {**HOME, **RUN}


async def main(scenario: str, out: str) -> None:
    random.seed(1234)
    fn, kwargs, size = SCENARIOS[scenario]
    app = home_app(**kwargs) if scenario in HOME else run_app(**kwargs)
    error = None
    async with app.run_test(size=size) as pilot:
        rec = Rec(pilot)
        try:
            await fn(rec)
            await pilot.pause(0.5)
        except Exception as e:
            error = normalize(f"{type(e).__name__}: {e}")
    result = {
        "snapshots": [[name, text.splitlines()] for name, text in rec.snaps],
        "opened": OPENED,
        "return_code": app.return_code,
        "error": error,
    }
    Path(out).write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:3]))
