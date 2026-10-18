"""FAKE: what home's commands do in the demo. Their plans and output, this PC's checks, the
sandboxes the demo starts with, and the run window Start opens. Nothing here touches WSL, Docker
or a file. DemoGateway plays these; the screens see only what any gateway gives them: a plan's
titles and expected seconds, and the lines each step prints.

Two things the demo does are real: Start opens the run window's demo in a console of its own,
and Open in browser opens the browser. Both go through the core's WinApi, as the real `start` and
`open` do, so the TUI holds no platform code of its own (ADR 0017)."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from functools import cache
from typing import NamedTuple

from comfyui_sandbox.cli_command import PYTHON
from comfyui_sandbox.deps import Deps

from ...states import SandboxState
from ..records import CheckItem, Sandbox


class FakeStep(NamedTuple):
    """A step of a fake long operation: its title, how long it's expected to take (seconds, at
    speed 1), and the lines it prints over that time."""

    title: str
    seconds: float
    lines: list[str]


@dataclass
class Scenario:
    """The demo's knobs for home's commands: how they behave this run."""

    speed: float = 1.0  # run the fake steps this many times faster
    fail: bool = False  # the deploy (the image's build) fails the first time
    port_taken: bool = False  # a program holds the port a new install asks for, and preflight asks what to do
    failed: bool = False  # the build step has failed once already
    wsl_reboot: bool = True  # setting WSL up installs it, and Windows has to reboot (False: it only updates)

    def take_fail_at(self) -> int | None:
        """Whether install fails this time (not None), at its deploy: the first time only."""
        fail_at = BUILD_STEP if self.fail and not self.failed else None
        self.failed = self.failed or fail_at is not None
        return fail_at


# Seeded sandboxes whose distro isn't running, to try the greyed-out Show logs on.
SEEDED_DISTROS_DOWN = ("sandbox-scratch",)


def seed_sandboxes(empty: bool = False) -> list[Sandbox]:
    """The sandboxes the demo starts with (none with --empty)."""
    return (
        []
        if empty
        else [
            Sandbox("comfyui", SandboxState.RUNNING, 8188),
            Sandbox(
                "comfyui-flux",
                SandboxState.STOPPED,
                8189,
                models=r"D:\AI\models",
                updates=("comfyui",),
                update_versions={"comfyui": ("v0.3.9", "v0.3.10")},
            ),
            # Ten more, for trying the home table's scrolling and its columns with varied values.
            Sandbox(
                "sdxl",
                SandboxState.RUNNING,
                8190,
                updates=("comfyui", "nodes"),
                update_versions={"comfyui": ("v0.3.8", "v0.3.10")},
            ),
            Sandbox("sd15-legacy", SandboxState.STOPPED, 8191, models=r"E:\Models\Stable Diffusion 1.5"),
            Sandbox("wan22-video", SandboxState.INCOMPLETE, 8192),
            Sandbox("hunyuan", SandboxState.RUNNING, 8193, models=r"\\nas.local\media\models"),
            Sandbox("flux-dev-experiments-2026-autumn", SandboxState.STOPPED, 8194, models=r"D:\AI\models"),
            Sandbox("ltx-video", SandboxState.STOPPED, 8195, channel="0.3.40"),  # pinned to a release
            Sandbox("test", SandboxState.INCOMPLETE, 8196),
            Sandbox("upscalers", SandboxState.RUNNING, 8197, models=r"D:\AI\upscale"),
            Sandbox("controlnet", SandboxState.STOPPED, 8198, updates=("system",)),
            Sandbox("sandbox-scratch", SandboxState.STOPPED, 8199),
        ]
    )


# The seeded incomplete installs: how many of their steps had finished when they stopped.
SEEDED_DONE_STEPS = {"wan22-video": 3, "test": 5}


@cache
def machine() -> Deps:
    """This machine, as the CLI sees it: the demo has no Deps handed to it (it opens from its own
    module entry point), and uses only two calls of its WinApi."""
    return Deps.real()


def open_run_window(name: str, port: int, models: str, speed: float) -> None:
    """Opens the run window's demo for a sandbox in a console of its own, as `start` opens `run`
    (WinApi.open_console). It doesn't report back: closing it leaves the sandbox running here."""
    machine().winapi.open_console(
        [
            str(PYTHON),
            "-m",
            "comfyui_sandbox.tui.demo",
            "run",
            "--distro",
            name,
            "--port",
            str(port),
            "--models",
            models,
            "--speed",
            str(speed),
        ]
    )


def open_browser(url: str) -> None:
    """Opens url in the browser, as `open` does, and `start` for a sandbox whose run window is
    open (WinApi.open_url)."""
    machine().winapi.open_url(url)


# -- Check this PC ---------------------------------------------------------------------------

PC_CHECKS = [
    CheckItem("Windows", "11 Pro 10.0.26200"),
    CheckItem("WSL", "2.6.1.0"),
    CheckItem("Virtualization", "enabled"),
    CheckItem("GPU", "NVIDIA GeForce RTX 4090, driver 581.42"),
    CheckItem("Free disk on C:", "412 GB"),
    CheckItem("uv", "0.12.23"),
]


# -- what the commands print -----------------------------------------------------------------


def apt_lines(pkgs: list[str], repo: str) -> list[str]:
    """Roughly what apt prints installing pkgs from repo."""
    out = [
        "Hit:1 http://archive.ubuntu.com/ubuntu resolute InRelease",
        f"Get:2 {repo} InRelease [2,214 B]",
        "Reading package lists...",
        "Building dependency tree...",
        "Reading state information...",
    ]
    out += [f"Get:{k + 3} {repo} {p} amd64 [{random.randint(40, 9000):,} kB]" for k, p in enumerate(pkgs)]
    out.append(f"Fetched {random.randint(20, 120)}.{random.randint(0, 9)} MB in {random.randint(3, 9)}s")
    for p in pkgs:
        out += [f"Selecting previously unselected package {p}.", f"Unpacking {p} ..."]
    out += [f"Setting up {p} ..." for p in pkgs]
    return out


def build_lines() -> list[str]:
    """Roughly what `docker compose build` prints for the ComfyUI image."""
    out = [
        '#0 building with "default" instance using docker driver',
        "#1 [internal] load build definition from Dockerfile",
        "#2 [internal] load metadata for docker.io/nvidia/cuda:13.0.1-runtime-ubuntu24.04",
        "#3 [1/9] FROM docker.io/nvidia/cuda:13.0.1-runtime-ubuntu24.04",
    ]
    for _ in range(6):
        size = random.randint(5, 900)
        out.append(f"#3 sha256:{random.getrandbits(48):012x} {size}MB / {size}MB done")
    out += ["#4 [2/9] RUN useradd --uid 1000 --create-home comfy", "#5 [3/9] RUN pip install --no-cache-dir comfy-cli"]
    for pkg in [
        "torch-2.9.0+cu130",
        "torchvision-0.24.0",
        "torchaudio-2.9.0",
        "numpy-2.3.3",
        "safetensors-0.6.2",
        "transformers-4.57.0",
        "aiohttp-3.12.15",
        "comfyui_frontend_package-1.28.6",
    ]:
        out += [
            f"#6 Downloading {pkg}-cp312-cp312-manylinux_2_28_x86_64.whl",
            f"#6   {random.randint(2, 900)} MB  {random.randint(20, 90)} MB/s  0:00:0{random.randint(1, 9)}",
        ]
    out += [
        "#6 Successfully installed torch torchvision torchaudio numpy safetensors transformers",
        "#7 [5/9] RUN comfy --skip-prompt install --nvidia",
        "#7 ComfyUI v0.4.12 installed",
        "#8 [6/9] RUN comfy --skip-prompt tracking disable",
        "#9 exporting to image",
        "#9 naming to docker.io/library/comfyui-sandbox:latest done",
        " Network comfyui_default  Created",
        " Container comfyui  Started",
    ]
    return out


# What the build prints when it fails (--fail).
FAIL_LINES = [
    "#6 Downloading torch-2.9.0+cu130-cp312-cp312-manylinux_2_28_x86_64.whl",
    "#6 WARNING: Retrying (Retry(total=4)) after connection broken by 'ReadTimeoutError'",
    "#6 WARNING: Retrying (Retry(total=3)) after connection broken by 'ReadTimeoutError'",
    "#6 ERROR: Could not install packages due to an OSError: HTTPSConnectionPool("
    "host='download.pytorch.org', port=443): Read timed out.",
    '#6 ERROR: process "/bin/sh -c pip install --no-cache-dir -r requirements.txt" '
    "did not complete successfully: exit code: 1",
    "failed to solve: exit code: 1",
]


# Says that --fail fails install's deploy (install.py, which finds the step by its title; the
# scenario only says whether this time it fails).
BUILD_STEP = 5


def generic_steps(names: list[str]) -> list[FakeStep]:
    """A step for each name, all alike (1.5 s, two lines), for commands whose output isn't worth faking."""
    return [FakeStep(n, 1.5, [f"{n}...", "ok"]) for n in names]


def recreate_steps(running: bool) -> list[FakeStep]:
    """`recreate`'s steps, named as it names them. The stop at the end prints no step."""
    return [
        *generic_steps(["Stop ComfyUI"] if running else []),
        *generic_steps(["Stop and remove the container", "Delete data/"]),
        FakeStep("Rebuild the image without cache", 6, build_lines()),
        *generic_steps(["Start ComfyUI"]),
        FakeStep(
            "Wait for the first start (installs PyTorch and ComfyUI: 10-30 minutes)",
            5,
            [
                "Installing PyTorch (cu128)...",
                "Installing ComfyUI requirements...",
                "Starting ComfyUI...",
                "To see the GUI go to: http://0.0.0.0:8188",
            ],
        ),
        *generic_steps(
            [
                "Create the models structure",
                "Set the Manager security level",
                "Restart ComfyUI",
                "Wait for ComfyUI to answer again",
            ]
        ),
    ]


def compose_lines(name: str, port: int, *, up: bool) -> list[str]:
    """What start and stop print bringing name's ComfyUI up on port, or down, roughly as docker
    compose and the sandbox say it."""
    if up:
        return [
            "[+] Running 2/2",
            f" ✔ Network {name}_default        Created",
            f" ✔ Container {name}-comfyui-1  Started",
            f"Waiting for http://127.0.0.1:{port} ...",
            f"GET http://127.0.0.1:{port}/ -> 200",
        ]
    return [
        "[+] Running 2/2",
        f" ✔ Container {name}-comfyui-1  Stopped",
        f" ✔ Container {name}-comfyui-1  Removed",
        f" ✔ Network {name}_default        Removed",
    ]


# `config`'s interactive menu, as the real one asks it (commands/config.py): the question, its
# entries (each a setting, with the hint shown beside it) and what applying does. The real menu's
# labels carry the current value, as these do (config_menu).
CONFIG_QUESTION = "What do you want to change?"
CONFIG_HINT = "One at a time, nothing is saved until Apply changes."
CONFIG_APPLY = "Apply changes"
CONFIG_APPLY_HINT = "saves them, ComfyUI ends stopped, and they apply at its next start"
CONFIG_ENTRIES = {  # setting: (the .env key it changes, its hint)
    "Port": ("COMFY_PORT", "the Windows port ComfyUI answers on, at 127.0.0.1"),
    "Launch flags": ("COMFY_ARGS", "the flags ComfyUI starts with"),
    "Models folder": ("MODELS_DIR", "where ComfyUI finds its models, inside the distro"),
    "Manager UI": ("COMFY_ARGS", "the Manager's classic dialog, or its newer screens"),
    "Pinned memory": ("COMFY_ARGS", "on by default, off starts ComfyUI with --disable-pinned-memory"),
    "Security level": (
        "Manager security level",
        "applies inside ComfyUI only: the TUI and CLI install custom nodes at any level",
    ),
}
# The follow-up question for each setting: (message, the choices of a select, or None for text).
CONFIG_ASKS = {
    "Port": ("Port (1024-65535):", None),
    "Launch flags": ("ComfyUI launch flags (--enable-manager is kept):", None),
    "Models folder": ("Models folder in the distro (an absolute Linux path or ~/...):", None),
    "Manager UI": ("Manager UI", ("legacy (classic dialog)", "standard")),
    "Pinned memory": ("Pinned memory", ("on (default)", "off (--disable-pinned-memory)")),
    "Security level": (
        "ComfyUI Manager security level",
        (
            "Strict: Strictest, blocks even moderately risky actions like installing or updating nodes.",
            "Normal: Verified nodes only, risky actions blocked.",
            "Normal-: Like Normal on a network, but allows risky actions when running locally only.",
            "Weak: Anything allowed, including unverified nodes, risky.",
        ),
    ),
}
PORT_HINT = "ComfyUI answers on http://127.0.0.1:<port>. A port in use is caught before anything changes."


def config_menu(sandbox: Sandbox) -> dict[str, str]:
    """The menu's entries for sandbox, by setting: the labels the real menu shows."""
    return {
        setting: f"{setting} ({sandbox.port})"
        if setting == "Port"
        else f"{setting} ({sandbox.models})"
        if setting == "Models folder"
        else setting
        for setting in CONFIG_ENTRIES
    }


def valid_port(text: str) -> str | None:
    """`config`'s port check: why text isn't a port it takes, or None."""
    return None if text.strip().isdigit() and 1024 <= int(text) <= 65535 else "Enter a port between 1024 and 65535."


def config_lines(name: str, changed: list[str]) -> list[str]:
    """What `config` prints applying changed (.env keys): the steps, the file it writes."""
    return [
        *(["==> Check the port"] if "COMFY_PORT" in changed else []),
        "==> Update .env",
        f"Changed {', '.join(changed)} in /home/comfy/comfyui-docker/.env, the old file is .env.bak-{name}.",
    ]


CONFIRM_HINT = "This can't be undone."  # destructive.CONFIRM_HINT


# The seeded sandboxes' backups, oldest first, by sandbox (a backup made in the demo is added).
SEEDED_BACKUPS = {
    "comfyui": ["comfyui-20260412-081530.tar", "comfyui-20260903-194402.tar", "comfyui-20261011-221005.tar"],
    "comfyui-flux": ["comfyui-20260920-120000.tar"],
}
BACKUPS_DIR = "/home/comfy/comfyui-backups"
WHICH_BACKUP = "Which backup do you want to restore?"  # backup.WHICH_BACKUP, and its hints below
WHICH_BACKUP_HINT = "Newest first, they're kept in ~/comfyui-backups."


def backup_hints(names: list[str]) -> dict[str, str]:
    """`restore`'s hint for each backup (names oldest first): when it was made, from its UTC stamp."""
    hints = {}
    for name in names:
        when = time.strptime(name[len("comfyui-") :][:13], "%Y%m%d-%H%M")
        hints[name] = f"made {when.tm_mday} {time.strftime('%b %Y, %H:%M', when)} UTC" + (
            " · the newest" if name == names[-1] else ""
        )
    return hints


def restore_steps(name: str, running: bool) -> list[FakeStep]:
    """`restore`'s steps, named as it names them: the stop first when it's running (the one at the
    end prints no step), then the files, and the snapshot's nodes, which need the container."""
    return generic_steps(
        [
            *(["Stop ComfyUI"] if running else []),
            f"Restore {name}",
            "Start ComfyUI for this step",
            "Wait for ComfyUI to answer",
            "Reinstall the custom nodes from the snapshot",
            "Restart ComfyUI to load the nodes",
            "Wait for ComfyUI to answer again",
        ]
    )


# `update`'s targets, in the order `update all` runs them, each with its steps, named as the real
# targets name them. The ones that run in the container start it for that step
# (update_support.update_in_container); ComfyUI is stopped again afterwards, silently.
IN_CONTAINER = ["Start ComfyUI for this step"], ["Restart ComfyUI", "Wait for ComfyUI to answer"]
UPDATE_STEPS = {
    "comfyui": [*IN_CONTAINER[0], "Update ComfyUI (latest)", *IN_CONTAINER[1]],
    "nodes": [*IN_CONTAINER[0], "Update the custom nodes", *IN_CONTAINER[1]],
    "comfy-cli": [*IN_CONTAINER[0], "Update comfy-cli", *IN_CONTAINER[1]],
    "system": [
        "Refresh the package lists",
        "Upgrade docker-ce, docker-ce-cli, containerd.io, docker-buildx-plugin, docker-compose-plugin, "
        "nvidia-container-toolkit",
        "Restart Docker",
    ],
    "setup": ["Merge .env", "Redeploy the docker files and rebuild", "Wait for ComfyUI to answer"],
}


def comfyui_steps(channel: str, explicit: bool) -> list[str]:
    """`update comfyui`'s steps for the channel it updates to: none for a pinned sandbox left as it
    is (no --version), a switch for a release, else an update."""
    pinned = channel not in ("latest", "nightly")
    if pinned and not explicit:
        return []
    what = f"Switch ComfyUI to {channel}" if pinned else f"Update ComfyUI ({channel})"
    return [*IN_CONTAINER[0], what, *IN_CONTAINER[1]]


def update_steps(target: str, running: bool, channel: str = "latest", explicit: bool = False) -> list[FakeStep]:
    """`update <target>`'s steps (target "all": every one), as `update` prints them: the stop
    first when it's running (the one at the end prints no step), then each target's, under an
    "update <target>" step for all. channel: ComfyUI's (explicit: given as --version). A pinned
    ComfyUI with nothing else to update has no steps at all: nothing is stopped."""
    keys = list(UPDATE_STEPS) if target == "all" else [target]
    steps = {**UPDATE_STEPS, "comfyui": comfyui_steps(channel, explicit)}
    taken = [s for key in keys for s in ([f"update {key}"] if target == "all" else []) + steps[key]]
    return generic_steps([*(["Stop ComfyUI"] if running and taken else []), *taken])
