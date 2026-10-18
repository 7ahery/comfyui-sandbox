"""FAKE: the run window's `run`, scripted. run.py's steps and what ComfyUI prints while it starts,
serves and stops, with the demo's scenarios (a failure, a broken node, a taken port, a window
already open, an update out). It plays into an operation handle like any command, so the run
window reads it the way it will read the real `run`: the plan, the lines, the load step's
sub-progress, and the result."""

from __future__ import annotations

import random
from dataclasses import dataclass

from ...states import StepStatus
from ..records import Result
from .comfyui import comfy_job
from .install import Intake, free_port
from .operation import Cancelled, DemoOperation


@dataclass(frozen=True)
class LaunchScenario:
    """The demo's knobs for the run window: how the scripted launch behaves this run."""

    speed: float = 1.0  # run the fake steps this many times faster
    fail: bool = False  # ComfyUI dies while loading PyTorch (no NVIDIA driver)
    broken_node: bool = False  # one custom node fails to import (ComfyUI still starts)
    port_taken: bool = False  # a program took the port: run asks what to do, as preflight does
    already_running: bool = False  # a second window for a running sandbox: it opens the browser and closes
    update: str | None = None  # None (ComfyUI is the newest) | "release" | "nightly" | "pinned"


# run.py's steps, with the seconds each is expected to take. The run window finds ComfyUI's own
# start-up among them by the title the real `run` gives it (Launch.LOAD_STAGE), and reads the parts of it
# from sub-progress.
STEPS = [
    ("Checking the port", 0.3),
    ("Waking the WSL distro", 1.2),
    ("Starting the container", 1.5),
    ("Waiting for ComfyUI to answer", 7.0),
    ("Checking that Windows reaches it", 0.8),
]
LOAD = 3  # the index of ComfyUI's start-up
STOP_NOTICE = "Closing this window stops ComfyUI."  # the real `run`'s stop notice (commands/run.py)
PARTS = 4  # the parts of ComfyUI's start-up its sub-progress counts (Launch.LOAD_PARTS)


class Failed(Exception):
    """A step failed: the launch ends in failure, with this message."""


class Script:
    """Plays a launch into op: the steps, then ComfyUI serving until op is cancelled, which stops
    it (what closing the window does too, run.py's cleanup, but on screen)."""

    def __init__(self, op: DemoOperation, name: str, port: int, models: str, scenario: LaunchScenario) -> None:
        self.op, self.name, self.port, self.models, self.scenario = op, name, port, models, scenario
        if not scenario.already_running:
            op.plan(STEPS)
            op.progress.notice = STOP_NOTICE  # as the real `run` says it: from the start until it stops

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def out(self, line: str, delay: float = 0.05) -> None:
        """Prints line after delay seconds (at speed 1)."""
        await self.op.nap(delay)
        self.op.say(line)

    async def run(self) -> Result:
        """The launch: the steps, then ComfyUI's running output until it is cancelled. A failure
        ends it early, its window kept open."""
        op = self.op
        if self.scenario.already_running:
            # As the real `run` does: the running ComfyUI's log so far, read at once, for its details.
            for line in [
                "pytorch version: 2.9.0+cu130",
                "Python version: 3.12.3 (main, Aug 14 2026, 17:47:21) [GCC 13.3.0]",
                "ComfyUI version: 0.3.66",
                "ComfyUI frontend version: 1.28.7",
                "Import times for custom nodes:",
                "   0.1 seconds: /app/ComfyUI/custom_nodes/rgthree-comfy",
                "   0.6 seconds: /app/ComfyUI/custom_nodes/ComfyUI-Manager",
            ]:
                await self.out(line, 0.0)
            await self.out(f"ComfyUI is already running from another run window: opened {self.url}.", 0.2)
            return Result(
                0,
                f"ComfyUI is already running: opened {self.url}.",
                {"distro": self.name, "url": self.url, "already_running": True},
            )
        # Before anything starts: a cancel here (its question backed out of) ends `run` as
        # interrupted, as the real one ends, with nothing to stop (DemoOperation._run says so).
        await self.check_port()
        try:
            await self.wake_distro()
            await self.start_container()
            await self.load_comfyui()
            await self.check_reach()
            for line in [
                f"Opened {self.url} in your browser",
                f"ComfyUI is running at {self.url}",
                "Keep this window open while you use ComfyUI (you can minimize it).",
                "Close this window to stop ComfyUI.",
            ]:
                await self.out(line, 0.02)
            await self.check_update()
            await self.serve()
        except Failed as e:
            op.progress.notice = ""  # stopping: closing the window stops nothing any more
            for line, delay in [
                ("Stopping ComfyUI ...", 0.3),
                (" Container comfyui  Stopped", 0.5),
                (f"[error] {e}", 0.1),
                ("Press Enter to close this window.", 0.05),
            ]:
                await op.sleep(delay)
                op.say(line)
            return Result(1, str(e), {"distro": self.name, "url": self.url})
        except Cancelled:
            return await self.stop()
        raise AssertionError("serve() returns only when cancelled")

    async def check_port(self) -> None:
        self.op.progress.begin(0)
        await self.out(f"Checking port {self.port} on 127.0.0.1 ...", 0.1)
        if self.scenario.port_taken:
            # The real `run`'s question (preflight.check_port, through the Prompter): the port
            # its .env asks for is held, so it asks what to do before anything starts.
            await self.op.nap(0.2)
            port = await free_port(Intake(self.op, [], yes=False, distro=None), [], self.port, {self.port})
            if port != self.port:
                self.port = port
                await self.out(f"Using port {self.port} instead, saved to .env", 0.1)
        else:
            await self.out(f"Port {self.port} is free", 0.2)
        self.op.progress.end(0)

    async def wake_distro(self) -> None:
        self.op.progress.begin(1)
        await self.out(f"wsl --distribution {self.name} --user comfy -- true", 0.2)
        await self.out("Holding the distro open while this window is open (keepalive)", 0.6)
        await self.out(f"{self.name} is running · WSL 2.6.1 · kernel 6.6.87.2-microsoft-standard-WSL2", 0.4)
        self.op.progress.end(1)

    async def start_container(self) -> None:
        self.op.progress.begin(2)
        await self.out("docker compose up -d", 0.1)
        await self.out(f"Models folder: {self.models} -> /app/ComfyUI/models", 0.05)
        for line, delay in [
            (" Network comfyui_default  Creating", 0.3),
            (" Network comfyui_default  Created", 0.2),
            (" Container comfyui  Creating", 0.2),
            (" Container comfyui  Created", 0.2),
            (" Container comfyui  Starting", 0.2),
            (" Container comfyui  Started", 0.4),
        ]:
            await self.out(line, delay)
        self.op.progress.end(2)

    def part(self, k: int) -> None:
        """ComfyUI's start-up has reached its part k (of PARTS): the load step's sub-progress."""
        self.op.progress.sub = k / PARTS

    async def load_comfyui(self) -> None:
        progress = self.op.progress
        progress.begin(LOAD)
        self.part(0)
        for line in [
            "[START] Security scan",
            "[DONE] Security scan",
            "## ComfyUI-Manager: installing dependencies done.",
            "** ComfyUI startup time: 2026-10-11 12:04:31.512",
            "** Platform: Linux",
            "** Python version: 3.12.3 (main, Aug 14 2026, 17:47:21) [GCC 13.3.0]",
            "** Python executable: /app/venv/bin/python",
            "** ComfyUI Path: /app/ComfyUI",
            "** Log path: /app/ComfyUI/user/comfyui.log",
            "",
            "Prestartup times for custom nodes:",
            "   0.0 seconds: /app/ComfyUI/custom_nodes/rgthree-comfy",
            "   1.1 seconds: /app/ComfyUI/custom_nodes/ComfyUI-Manager",
            "",
        ]:
            await self.out(line, 0.09)
        self.part(1)
        await self.out("Checkpoint files will always be loaded safely.", 0.3)
        if self.scenario.fail:
            for line in [
                "Traceback (most recent call last):",
                '  File "/app/ComfyUI/main.py", line 147, in <module>',
                "    import comfy.model_management",
                '  File "/app/ComfyUI/comfy/model_management.py", line 229, in <module>',
                "    total_vram = get_total_memory(get_torch_device()) / (1024 * 1024)",
                "RuntimeError: Found no NVIDIA driver on your system. Please check that you have an "
                "NVIDIA GPU and installed a driver from http://www.nvidia.com/Download/index.aspx",
            ]:
                await self.out(line, 0.15)
            progress.end(LOAD, StepStatus.FAILED)
            raise Failed("RuntimeError: Found no NVIDIA driver on your system.")
        for line in [
            "Total VRAM 24564 MB, total RAM 65304 MB",
            "pytorch version: 2.9.0+cu130",
            "Set vram state to: NORMAL_VRAM",
            "Device: cuda:0 NVIDIA GeForce RTX 4090 : cudaMallocAsync",
            "Using pytorch attention",
            "Python version: 3.12.3 (main, Aug 14 2026, 17:47:21) [GCC 13.3.0]",
            f"ComfyUI version: {'0.3.40' if self.scenario.update == 'pinned' else '0.3.66'}",
            "ComfyUI frontend version: 1.28.7",
            "[Prompt Server] web root: /app/venv/lib/python3.12/site-packages/comfyui_frontend_package/static",
        ]:
            await self.out(line, 0.25)
        self.part(2)
        for line in [
            "### Loading: ComfyUI-Manager (V3.37)",
            "[ComfyUI-Manager] network_mode: public",
            "### ComfyUI Revision: 4012 [f4b9a3c1] *DETACHED | Released on '2026-10-02'",
            "[rgthree-comfy] Loaded 48 exciting nodes.",
            "[comfyui_controlnet_aux] | INFO -> Using ckpts path: "
            "/app/ComfyUI/custom_nodes/comfyui_controlnet_aux/ckpts",
            "[comfyui_controlnet_aux] | INFO -> Using symlinks: False",
            "[KJNodes] Loaded 212 nodes",
            "[VideoHelperSuite] ffmpeg found: /usr/bin/ffmpeg",
        ]:
            await self.out(line, 0.35)
        if self.scenario.broken_node:
            for line in [
                "Traceback (most recent call last):",
                '  File "/app/ComfyUI/nodes.py", line 2131, in load_custom_node',
                "ModuleNotFoundError: No module named 'insightface'",
                "Cannot import /app/ComfyUI/custom_nodes/ComfyUI_IPAdapter_plus module for custom nodes: "
                "No module named 'insightface'",
            ]:
                await self.out(line, 0.12)
        await self.out("", 0.2)
        await self.out("Import times for custom nodes:", 0.0)
        for name, secs in [
            ("websocket_image_save.py", 0.0),
            ("rgthree-comfy", 0.1),
            ("ComfyUI-KJNodes", 0.3),
            ("comfyui_controlnet_aux", 0.4),
            ("ComfyUI-VideoHelperSuite", 0.5),
            ("ComfyUI-Manager", 0.6),
        ]:
            await self.out(f"   {secs:.1f} seconds: /app/ComfyUI/custom_nodes/{name}", 0.0)
        if self.scenario.broken_node:
            await self.out("   0.0 seconds (IMPORT FAILED): /app/ComfyUI/custom_nodes/ComfyUI_IPAdapter_plus", 0.0)
        self.part(3)
        for line in [
            "",
            "Context impl SQLiteImpl.",
            "Will assume non-transactional DDL.",
            "Starting server",
            "",
            f"To see the GUI go to: http://0.0.0.0:{self.port}",
        ]:
            await self.out(line, 0.2)
        progress.end(LOAD)

    async def check_reach(self) -> None:
        self.op.progress.begin(4)
        await self.out(f"GET http://127.0.0.1:{self.port}/ -> 200", 0.8)
        self.op.progress.end(4)

    async def check_update(self) -> None:
        """`update comfyui`'s pending check, once ComfyUI answers (it reads the checkout in the
        container): the newest release tag, or on nightly the remote master. It only tells; a
        pinned sandbox is only told what it's pinned to."""
        update = self.scenario.update
        channel = "nightly" if update == "nightly" else "latest"
        await self.out(f"Checking for a newer ComfyUI ({channel}) ...", 1.0)
        if update == "pinned":
            await self.out(
                "ComfyUI is pinned to 0.3.40: 'comfyui-sandbox update comfyui --version latest' unpins it.", 0.8
            )
        elif update == "nightly":
            await self.out("A newer ComfyUI nightly is available (9e21d07)", 0.8)
        elif update == "release":
            await self.out("ComfyUI v0.3.70 is available", 0.8)
        else:
            await self.out("ComfyUI v0.3.66 is the newest release", 0.8)

    async def serve(self) -> None:
        """What a running ComfyUI prints: a prompt now and then, as someone queues them."""
        while True:
            await self.op.nap(random.uniform(4, 9))
            for delay, line in comfy_job():
                await self.out(line, delay)

    async def stop(self) -> Result:
        """Cancelled (Ctrl+C twice, or the window closing): stops ComfyUI, which can't be cut short."""
        self.op.progress.notice = ""
        for line, delay in [
            ("Stopping ComfyUI ...", 0.05),
            (" Container comfyui  Stopping", 0.4),
            (" Container comfyui  Stopped", 0.9),
            ("Released the distro. It idles out in a few seconds", 0.2),
            ("ComfyUI has been stopped.", 0.1),
        ]:
            await self.op.sleep(delay)
            self.op.say(line)
        return Result(0, "ComfyUI has been stopped.", {"distro": self.name, "url": self.url, "ended": "stopped"})
