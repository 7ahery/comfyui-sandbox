"""The TUI on fake data: home, or the run window, with no WSL, Docker, GPU or files written.

It's a module entry point, not a CLI command, because it's for whoever shows or redesigns the
screens, not for users. The screens run on the demo gateway (`tui.gateway.demo`): every command
they run is faked, and state lives in memory. This module is the one place that picks it, so the
screens themselves only know they were given a Gateway. Run it in a real terminal with truecolor
(Windows Terminal):

    python -m comfyui_sandbox.tui.demo [home] [options]    home (the default)
    python -m comfyui_sandbox.tui.demo run [options]       the run window

Home's options:

    --empty        start with no sandboxes (the first-run experience)
    --fail         the "Build and start ComfyUI" step fails the first time (then Resume install)
    --port-taken   a mid-run question pauses the live view during "Check this PC"
    --no-gpu       a machine with no GPU readings: the meters are hidden
    --speed 3      run the fake steps 3x faster
    --intro        play the splash first (it's off for now: home/splash.py's SHOW_SPLASH)

Home's "Start (in its own window)" opens the run window's demo in a new console, as `start` opens
`run`; home doesn't hear back when it closes.

The run window's options:

    --distro NAME      the sandbox's name (default comfyui); --port 8188; --models FOLDER
    --fail             ComfyUI dies while loading PyTorch (no NVIDIA driver)
    --broken-node      one custom node fails to import (ComfyUI still starts)
    --port-taken       a program took the port: the window asks what to do, as `run` does
    --already-running  a second window for a running sandbox: it opens the browser and closes
    --update           a newer ComfyUI release is out; --update nightly: a newer nightly commit;
                       --update pinned: the sandbox is pinned to a release (nothing newer is shown)
    --speed 3          run the fake steps 3x faster

Its keys: L the full log (arrows, PgUp/PgDn or the mouse wheel scroll it); a click on the URL
opens it (nothing answers: no ComfyUI runs); Ctrl+C twice stops ComfyUI and closes the window;
R replays the launch (the demo's key, not the design's).
"""

from __future__ import annotations

import argparse
import sys

PROG = "python -m comfyui_sandbox.tui.demo"


def main(argv: list[str] | None = None) -> int:
    """Opens home, or with a leading `run` the run window, on fake data; returns the exit code.

    The first word picks the screen and the rest is that screen's demo options. Home is the
    default because it's the screen the CLI opens on with no command.
    """
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["run"]:
        return run_window_demo(args[1:])
    if args[:1] == ["home"]:
        args = args[1:]
    return home_demo(args)


def terminal() -> bool:
    """Whether a full-screen app can open here: both ends are a terminal."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def home_demo(argv: list[str]) -> int:
    """Home on the demo gateway, with the command line's scenarios."""
    parser = argparse.ArgumentParser(
        prog=PROG,
        epilog=f"The run window has its own demo: {PROG} run --help",
        description="The comfyui-sandbox home screen, on the demo's fake data.",
    )
    parser.add_argument("--empty", action="store_true", help="start with no sandboxes")
    parser.add_argument("--fail", action="store_true", help="fail the build step the first time")
    parser.add_argument("--port-taken", action="store_true", help="ask a question mid-install")
    parser.add_argument("--no-gpu", action="store_true", help="no GPU readings: the meters are hidden")
    parser.add_argument("--speed", type=float, default=1.0, help="fake step speed multiplier")
    parser.add_argument("--intro", action="store_true", help="play the splash first (off for now)")
    args = parser.parse_args(argv)
    if not terminal():
        print("Not a terminal: the real CLI would print a usage error here. Run the demo in Windows Terminal.")
        return 0
    return open_demo_home(
        empty=args.empty,
        gpu=not args.no_gpu,
        speed=args.speed,
        fail=args.fail,
        port_taken=args.port_taken,
        intro=args.intro,
    )


def open_demo_home(
    *,
    empty: bool = False,
    gpu: bool = True,
    speed: float = 1.0,
    fail: bool = False,
    port_taken: bool = False,
    intro: bool = False,
) -> int:
    """Runs home on the demo gateway in this terminal and returns its exit code (see `run_home`).

    The defaults are the plain demo: seeded sandboxes, GPU meters, nothing failing.
    """
    # Imported here, not at the top: each pulls in Textual and its whole screen, and only one opens.
    from .gateway.demo import DemoGateway, Scenario, seed_sandboxes
    from .home.app import run_home

    gateway = DemoGateway(
        seed_sandboxes(empty), gpu=gpu, scenario=Scenario(speed=speed, fail=fail, port_taken=port_taken)
    )
    return run_home(gateway, intro=intro)


def run_window_demo(argv: list[str]) -> int:
    """The run window on the demo gateway, for one sandbox, with the command line's scenarios."""
    parser = argparse.ArgumentParser(
        prog=f"{PROG} run", description="The comfyui-sandbox run window, on the demo's fake data."
    )
    parser.add_argument("--distro", default="comfyui")
    parser.add_argument("--port", type=int, default=8188)
    parser.add_argument("--models", default="~/comfyui-models", help="the sandbox's models folder")
    parser.add_argument("--fail", action="store_true", help="ComfyUI dies while loading PyTorch")
    parser.add_argument("--broken-node", action="store_true", help="one custom node fails to import")
    parser.add_argument("--port-taken", action="store_true", help="a program took the port: the window asks what to do")
    parser.add_argument("--already-running", action="store_true", help="a second window for a running sandbox")
    parser.add_argument(
        "--update",
        nargs="?",
        const="release",
        choices=["release", "nightly", "pinned"],
        help="a newer ComfyUI is out (a release, or with 'nightly' a nightly commit), "
        "'pinned' means it's pinned to a release",
    )
    parser.add_argument("--speed", type=float, default=1.0)
    args = parser.parse_args(argv)
    if not terminal():
        print("Not a terminal. Run the demo in Windows Terminal.")
        return 0
    from .gateway import Sandbox
    from .gateway.demo import DemoGateway, LaunchScenario
    from .run_window.app import run_window
    from .states import SandboxState

    sandbox = Sandbox(args.distro, SandboxState.STOPPED, args.port, args.models)
    scenario = LaunchScenario(
        speed=args.speed,
        fail=args.fail,
        broken_node=args.broken_node,
        port_taken=args.port_taken,
        already_running=args.already_running,
        update=args.update,
    )
    return run_window(DemoGateway([sandbox], launch=scenario), sandbox, replay=True)


if __name__ == "__main__":
    sys.exit(main())
