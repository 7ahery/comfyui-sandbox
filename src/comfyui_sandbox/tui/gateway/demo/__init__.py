"""FAKE: the demo gateway. The prototypes' fakes behind the `Gateway` protocol, so the TUI can be
run, redesigned and snapshot-tested with no WSL, Docker or GPU (`python -m comfyui_sandbox.tui.demo`).

It keeps the sandboxes in memory and plays each command line it's asked to run: the plan, the
output and the effect a real command would have (a stop stops it, an uninstall removes it). It
knows only the command lines home and the run window send; anything else is a usage error (2).

    home        home's commands: their plans and output, the seeded sandboxes, Check this PC
    launch      the run window's `run`, scripted
    comfyui     what a running ComfyUI prints
    telemetry   GPU load for the running sandboxes
    operation   DemoOperation, the demo's operation handle
"""

from __future__ import annotations

import copy
import time
from collections.abc import Callable, Sequence
from typing import Any

from ...states import SandboxState, StepStatus
from ..records import CheckItem, GpuReading, Question, QuestionKind, Result, Sandbox
from . import comfyui, home, install
from .home import BUILD_STEP, FAIL_LINES, PC_CHECKS, FakeStep, Scenario, seed_sandboxes
from .launch import LOAD, STEPS, LaunchScenario, Script
from .operation import DemoOperation
from .telemetry import GPU_GB, Telemetry

__all__ = ["BUILD_STEP", "DemoGateway", "DemoOperation", "LaunchScenario", "Scenario", "seed_sandboxes"]

USAGE = 2  # the CLI's ExitCode.USAGE: an unknown command, or a refused `config` value
DESTRUCTIVE_REFUSED = 8  # the CLI's ExitCode.DESTRUCTIVE_REFUSED: the typed name didn't match


def options(argv: Sequence[str]) -> dict[str, str]:
    """argv's --flag value pairs, by flag name ("--distro x" -> {"distro": "x"}); a flag with no
    value (--follow, --cpu) maps to ""."""
    out, k = {}, 0
    while k < len(argv):
        if argv[k].startswith("--"):
            has_value = k + 1 < len(argv) and not argv[k + 1].startswith("--")
            out[argv[k][2:]] = argv[k + 1] if has_value else ""
            k += 2 if has_value else 1
        else:
            k += 1
    return out


class DemoGateway:
    """A Gateway on fake data. sandboxes: what it starts with (default: the seeded dozen; [] for
    the first-run experience). scenario: how home's commands behave; launch: how the run window's
    `run` behaves (with it, `run` plays the scripted launch; without, the ComfyUI output Run here
    shows). checks: what Check this PC finds (default: a ready Windows PC). gpu: False for a
    machine with no readings, so the meters hide. distros_down: sandboxes whose distro isn't
    running (default: the seeded sandbox-scratch's), so Show logs is greyed out for them.

    `ran` lists every command line it was asked to run, in order: what the screens did."""

    def __init__(
        self,
        sandboxes: list[Sandbox] | None = None,
        *,
        scenario: Scenario | None = None,
        launch: LaunchScenario | None = None,
        checks: list[CheckItem] | None = None,
        gpu: bool = True,
        distros_down: Sequence[str] | None = None,
    ) -> None:
        self._sandboxes = seed_sandboxes() if sandboxes is None else list(sandboxes)
        # Incomplete installs: how many of their steps had finished, for Resume install. The
        # seeded ones say; any other starts from its first step.
        self._done_steps = {
            s.name: home.SEEDED_DONE_STEPS[s.name]
            for s in self._sandboxes
            if s.state == SandboxState.INCOMPLETE and s.name in home.SEEDED_DONE_STEPS
        }
        self._answers: dict[str, dict] = {}  # an Incomplete install's answers, once --change has asked them
        self.scenario = scenario or Scenario()
        self.launch = launch
        self.checks = PC_CHECKS if checks is None else checks
        self.has_gpu = gpu
        self.telemetry = Telemetry()
        self.ran: list[tuple[str, ...]] = []
        self.distros_down = set(
            home.SEEDED_DISTROS_DOWN if distros_down is None else distros_down
        )  # sandboxes whose distro isn't running (WSL shut it down)
        self.distro_checks: list[str] = []  # the sandboxes distro_running was asked about
        # Each sandbox's backups, oldest first: the seeded ones', and any Back up makes.
        self.backups = {s.name: list(home.SEEDED_BACKUPS.get(s.name, [])) for s in self._sandboxes}

    # -- queries -------------------------------------------------------------------------------

    async def sandboxes(self) -> list[Sandbox]:
        # Copies: the TUI copies listings into records of its own and marks its own states on
        # them, which mustn't change what the demo "really" has.
        return [copy.copy(s) for s in self._sandboxes]

    async def distro_running(self, name: str) -> bool:
        self.distro_checks.append(name)
        return name not in self.distros_down

    async def check_this_pc(self) -> list[CheckItem]:
        return list(self.checks)

    def gpu(self) -> GpuReading | None:
        if not self.has_gpu:
            return None
        self.telemetry.tick(self._sandboxes)
        return GpuReading(self.telemetry.gpu(self._sandboxes), self.telemetry.vram_total(self._sandboxes), GPU_GB)

    def close(self) -> None:
        pass  # nothing runs on its own: the telemetry ticks only when gpu() is asked

    # -- commands ------------------------------------------------------------------------------

    def run(self, argv: Sequence[str]) -> DemoOperation:
        self.ran.append(tuple(argv))
        speed = self.launch.speed if self.launch is not None and argv[0] == "run" else self.scenario.speed
        op = DemoOperation(argv, speed)
        command = COMMANDS.get(argv[0])
        play = command(self, op, options(argv[1:])) if command else self._unknown(argv)
        return op.start(play)

    def _find(self, name: str) -> Sandbox:
        return next(s for s in self._sandboxes if s.name == name)

    async def _unknown(self, argv: Sequence[str]) -> Result:
        return Result(USAGE, f"The demo gateway doesn't know {' '.join(argv)!r}.")

    # Each command below does what happens before its first wait now (in run()), and returns the
    # rest as a coroutine for the operation to play. That's where the demo's random output is
    # drawn, so it's drawn in the same order whatever the screen does meanwhile.

    def _install(self, op: DemoOperation, opts: dict[str, str]):
        return install.install(self, op, opts)  # install's check, questions, preflight and steps: install.py

    async def _step(self, op: DemoOperation, k: int, step: FakeStep, *, fail_at: int | None = None) -> bool:
        """Plays step k: its lines over its expected seconds, its sub-progress following them.
        Fails it partway when k is fail_at (and says so: True)."""
        progress, lines = op.progress, step.lines
        progress.begin(k)
        for j, line in enumerate(lines):
            if fail_at == k and j == len(lines) * 3 // 5:
                for bad in FAIL_LINES:
                    op.say(bad)
                    await op.nap(0.25)
                progress.end(k, StepStatus.FAILED)
                return True
            op.say(line)
            await op.nap(step.seconds / len(lines))
            progress.sub = (j + 1) / len(lines)
        progress.end(k)
        return False

    def _steps(self, op: DemoOperation, steps: list[FakeStep], result: Callable[[], Result]):
        """Announces steps as the plan, and returns their play: each in turn, then result() says
        how it went (and makes the change the command makes). A cancel ends it as interrupted
        with nothing changed."""
        op.plan([(s.title, s.seconds) for s in steps])

        async def play() -> Result:
            for k, step in enumerate(steps):
                await self._step(op, k, step)
            return result()

        return play()

    def _short(
        self,
        op: DemoOperation,
        seconds: float,
        lines: list[str],
        result: Callable[[], Result],
        before: Callable[[], None] | None = None,
    ):
        """A short action with no plan: lines printed evenly over seconds, the last as it ends.
        before() makes the change that happens as it starts (a stop stops the container first)."""
        if before is not None:
            before()

        async def play() -> Result:
            for line in lines[:-1]:
                await op.nap(seconds / len(lines))
                op.say(line)
            await op.nap(seconds - (len(lines) - 1) * seconds / len(lines))
            op.say(lines[-1])
            return result()

        return play()

    def _open(self, op: DemoOperation, opts: dict[str, str]):
        """`open`: only the browser opens, on a running sandbox; any other fails as the real one
        does, saying how to start it. It's what Open in browser runs."""
        sandbox = self._find(opts["distro"])
        url = f"http://localhost:{sandbox.port}"
        if sandbox.state != SandboxState.RUNNING:
            message = (
                f"ComfyUI isn't running in {sandbox.name}: nothing answers on http://127.0.0.1:{sandbox.port} "
                f"from Windows. Start it with 'comfyui-sandbox start --distro {sandbox.name}'."
            )
            return self._short(op, 0.3, [f"Error: {message}"], lambda: Result(1, message, {"distro": sandbox.name}))
        home.open_browser(url)
        message = f"Opened {url} in the browser."
        return self._short(op, 0.5, [message], lambda: Result(0, message, {"distro": sandbox.name, "url": url}))

    def _start(self, op: DemoOperation, opts: dict[str, str]):
        sandbox = self._find(opts["distro"])
        url = f"http://localhost:{sandbox.port}"
        if sandbox.state == SandboxState.RUNNING:
            # As the real `start` does when the run window is open (the demo's running sandboxes
            # all have one): only the browser opens.
            home.open_browser(url)
            message = f"ComfyUI is already running, opened {url}."
            return self._short(
                op,
                0.5,
                [f"ComfyUI is already running: opened {url}.", message],
                lambda: Result(0, message, {"distro": sandbox.name, "url": url, "already_running": True}),
            )
        home.open_run_window(sandbox.name, sandbox.port, sandbox.models, self.scenario.speed)

        def started() -> Result:
            sandbox.state = SandboxState.RUNNING
            return Result(
                0,
                f"{sandbox.name} is up at http://127.0.0.1:{sandbox.port}.",
                {"distro": sandbox.name, "url": f"http://127.0.0.1:{sandbox.port}", "already_running": False},
            )

        return self._short(op, 3, home.compose_lines(sandbox.name, sandbox.port, up=True), started)

    def _stop(self, op: DemoOperation, opts: dict[str, str]):
        sandbox = self._find(opts["distro"])

        def stopping() -> None:
            sandbox.state = SandboxState.STOPPED  # its GPU use stops with the container, at once

        return self._short(
            op,
            2,
            home.compose_lines(sandbox.name, sandbox.port, up=False),
            lambda: Result(0, f"{sandbox.name} stopped.", {"distro": sandbox.name}),
            before=stopping,
        )

    def _prereqs(self, op: DemoOperation, opts: dict[str, str]):
        """`prereqs`: asks whether to run the WSL install (or update) as administrator, then plays
        the UAC wait. FAKE: no Windows prompt, nothing installed; it ends as the real one does,
        saying whether a reboot is needed."""
        reboot = self.scenario.wsl_reboot
        argv = "wsl.exe --install --no-distribution" if reboot else "wsl.exe --update"
        action = "install" if reboot else "update"

        async def play() -> Result:
            op.say("WSL is not installed." if reboot else "WSL 2.3.26.0 is older than 2.4.4.")
            op.say(
                f"The sandbox runs ComfyUI inside WSL (Windows Subsystem for Linux), and {'installing' if reboot else 'updating'} WSL needs administrator rights."
            )
            answer = await op.ask(
                Question(
                    f"Run '{argv}' as administrator? (Windows will ask you to approve it)",
                    kind=QuestionKind.CONFIRM,
                    default=True,
                )
            )
            op.asked()
            data = {"action": action, "reboot_needed": False}
            if not answer:
                return Result(1, "WSL was not changed. Run prereqs again and answer yes to continue.", data)
            op.say(
                f"Running '{argv}' as administrator: choose Yes in the Windows (UAC) prompt, then wait for its window to close."
            )
            await op.nap(2)
            data["reboot_needed"] = reboot
            if reboot:
                return Result(0, "WSL installed. Reboot Windows, then run 'comfyui-sandbox check'.", data)
            return Result(0, "WSL updated. Next, run 'comfyui-sandbox check'.", data)

        return play()

    def _config(self, op: DemoOperation, opts: dict[str, str]):
        """`config --distro x`: its menu, asked through the operation, until Apply changes; then
        the changes, refused when the port is another sandbox's. It ends with ComfyUI stopped."""
        sandbox = self._find(opts["distro"])

        async def ask_setting(setting: str) -> str:
            message, choices = home.CONFIG_ASKS[setting]
            if choices is None:
                port = setting == "Port"
                question = Question(
                    message,
                    kind=QuestionKind.TEXT,
                    hint=home.PORT_HINT if port else "",
                    validate=home.valid_port if port else None,
                )
            else:
                question = Question(message, tuple((c, c) for c in choices))
            answer = await op.ask(question)
            op.asked()
            return str(answer)

        async def play() -> Result:
            await op.nap(0.4)  # reading its .env
            change: dict[str, str] = {}  # setting: the answer
            while True:
                menu = home.config_menu(sandbox)
                hints = {label: home.CONFIG_ENTRIES[setting][1] for setting, label in menu.items()}
                picked = await op.ask(
                    Question(
                        home.CONFIG_QUESTION,
                        tuple((label, label) for label in [*menu.values(), home.CONFIG_APPLY]),
                        hint=home.CONFIG_HINT,
                        hints={**hints, home.CONFIG_APPLY: home.CONFIG_APPLY_HINT},
                    )
                )
                op.asked()
                if picked == home.CONFIG_APPLY:
                    break
                setting = next(s for s, label in menu.items() if label == picked)
                change[setting] = await ask_setting(setting)
            if not change:
                return Result(USAGE, "Nothing to change.")
            port = int(change.get("Port", sandbox.port))
            clash = next((s for s in self._sandboxes if s is not sandbox and s.port == port), None)
            if clash is not None:
                op.say(f"Port {port} is used by the sandbox {clash.name}.")
                return Result(USAGE, f"Port {port} is used by the sandbox {clash.name}. Nothing was changed.")
            keys = list(dict.fromkeys(home.CONFIG_ENTRIES[s][0] for s in change))
            if sandbox.state == SandboxState.RUNNING:
                sandbox.state = SandboxState.STOPPED  # config ends with ComfyUI stopped
            for line in home.config_lines(sandbox.name, keys):
                await op.nap(0.3)
                op.say(line)
            sandbox.port = port
            return Result(
                0,
                f"Changed {', '.join(keys)} in {sandbox.name}, it takes effect when ComfyUI starts.",
                {"distro": sandbox.name},
            )

        return play()

    def _update(self, op: DemoOperation, opts: dict[str, str]):
        target = next(word for word in op.argv[1:] if not word.startswith("--"))
        sandbox = self._find(opts["distro"])
        keys = list(home.UPDATE_STEPS) if target == "all" else [target]
        explicit = opts.get("version")
        channel = explicit or sandbox.channel or "latest"
        pin = channel if channel not in ("latest", "nightly") else None
        steps = home.update_steps(target, sandbox.state == SandboxState.RUNNING, channel, bool(explicit))

        def updated() -> Result:
            versions = sandbox.update_versions.get("comfyui", ("v0.3.10", "v0.3.10"))
            if pin and not explicit:  # a pinned ComfyUI is left as it is: `update comfyui`'s outcome then
                comfyui = {
                    "distro": sandbox.name,
                    "version": pin,
                    "pinned": pin,
                    "previous": None,
                    "installed": None,
                    "env_changed": False,
                    "left_pinned": True,
                }
            else:
                was = f"v{sandbox.pinned}" if sandbox.pinned else versions[0]
                comfyui = {
                    "distro": sandbox.name,
                    "version": channel,
                    "pinned": pin,
                    "previous": was,
                    "installed": f"v{pin}" if pin else versions[1],
                }
                sandbox.channel = channel if explicit else sandbox.channel  # what the record says now
            sandbox.updates = tuple(k for k in sandbox.updates if k not in keys)  # what the next check finds
            sandbox.update_versions = {k: v for k, v in sandbox.update_versions.items() if k not in keys}
            if steps:
                sandbox.state = SandboxState.STOPPED  # every update that does something ends with ComfyUI stopped
            # Worded like the core's message (update's, then ending_stopped's), which the card shows.
            return Result(
                0,
                f"Updated {', '.join(keys)} of {sandbox.name}. ComfyUI is stopped: run "
                f"'comfyui-sandbox start --distro {sandbox.name}' to start it again.",
                {
                    "distro": sandbox.name,
                    **(
                        {"targets": {k: comfyui if k == "comfyui" else {} for k in keys}}
                        if target == "all"
                        else comfyui
                    ),
                },
            )

        return self._steps(op, steps, updated)

    def _backup(self, op: DemoOperation, opts: dict[str, str]):
        sandbox = self._find(opts["distro"])
        # `backup`'s plan: a stopped ComfyUI is started for it (and stopped after, which is no step).
        started = (
            []
            if sandbox.state == SandboxState.RUNNING
            else ["Start ComfyUI for the backup", "Wait for ComfyUI to answer"]
        )
        name = f"comfyui-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}.tar"  # `backup`'s name, UTC
        steps = home.generic_steps([*started, "Save the custom-node snapshot", f"Write {name}"])
        restore = f"comfyui-sandbox restore --distro {sandbox.name} --backup {name}"

        def backed_up() -> Result:
            self.backups.setdefault(sandbox.name, []).append(name)  # Restore… now offers it
            # Worded like the core's message, which the card shows.
            return Result(
                0,
                f"Backed up to {name}. Restore it with '{restore}'.",
                {
                    "distro": sandbox.name,
                    "name": name,
                    "path": f"{home.BACKUPS_DIR}/{name}",
                    "restore": restore,
                    "started_comfyui": bool(started),
                },
            )

        return self._steps(op, steps, backed_up)

    def _restore(self, op: DemoOperation, opts: dict[str, str]):
        """`restore --distro x`: with no --backup it asks which, even when there is one (newest first),
        as the real one does at a terminal, then the gate and its steps. It ends with ComfyUI stopped.
        With no backup it fails before asking anything."""
        sandbox = self._find(opts["distro"])
        available = self.backups.get(sandbox.name, [])

        async def play() -> Result:
            await op.nap(0.3)  # listing ~/comfyui-backups
            if not available:  # worded like the core's errors
                return Result(
                    1, f"There is no backup in {home.BACKUPS_DIR}. Run 'comfyui-sandbox backup' first.", {"backups": []}
                )
            named = opts.get("backup")  # --backup, or None: then the newest, or the one picked
            name = named or available[-1]
            if name not in available:
                return Result(
                    1, f"There is no backup named {name} in {home.BACKUPS_DIR}. Available: {', '.join(available)}."
                )
            if named is None:
                newest_first = available[::-1]
                name = str(
                    await op.ask(
                        Question(
                            home.WHICH_BACKUP,
                            tuple((n, n) for n in newest_first),
                            hint=home.WHICH_BACKUP_HINT,
                            hints=home.backup_hints(available),
                            default=available[-1],
                        )
                    )
                )
                op.asked()

            def restored() -> Result:
                sandbox.state = SandboxState.STOPPED
                return Result(
                    0,
                    f"Restored {name}. ComfyUI is stopped: run "
                    f"'comfyui-sandbox start --distro {sandbox.name}' to start it again.",
                    {
                        "distro": sandbox.name,
                        "name": name,
                        "path": f"{home.BACKUPS_DIR}/{name}",
                        "container": "stopped",
                    },
                )

            steps = home.restore_steps(name, sandbox.state == SandboxState.RUNNING)
            return await self._destructive(op, sandbox, opts, steps, restored)

        return play()

    def _destructive(
        self,
        op: DemoOperation,
        sandbox: Sandbox,
        opts: dict[str, str],
        steps: list[FakeStep],
        result: Callable[[], Result],
    ):
        """A destructive command's play: without --confirm it asks for the name to be typed first, as
        the real gate does, and anything else refuses (exit 8) with nothing changed; then its steps."""

        async def play() -> Result:
            if "confirm" not in opts:
                typed = str(
                    await op.ask(
                        Question(
                            f"Type the sandbox name to confirm ({sandbox.name}):",
                            kind=QuestionKind.TEXT,
                            hint=home.CONFIRM_HINT,
                            confirms=(sandbox.name,),
                        )
                    )
                )
                op.asked()
                if typed.strip().lower() != sandbox.name.lower():
                    return Result(DESTRUCTIVE_REFUSED, f"'{typed.strip()}' is not {sandbox.name}. Nothing was changed.")
            return await self._steps(op, steps, result)

        return play()

    def _recreate(self, op: DemoOperation, opts: dict[str, str]):
        sandbox = self._find(opts["distro"])

        def recreated() -> Result:
            sandbox.state, sandbox.updates = SandboxState.STOPPED, ()
            # Worded like the core's message (recreate's, then ending_stopped's), which the card shows.
            return Result(
                0,
                f"Reinstalled ComfyUI in {sandbox.name}: a fresh ComfyUI installed and answered. Restore a backup "
                "with 'comfyui-sandbox restore' if you want your work back. ComfyUI is stopped: run "
                f"'comfyui-sandbox start --distro {sandbox.name}' to start it again.",
                {"distro": sandbox.name},
            )

        return self._destructive(
            op, sandbox, opts, home.recreate_steps(sandbox.state == SandboxState.RUNNING), recreated
        )

    def _uninstall(self, op: DemoOperation, opts: dict[str, str]):
        sandbox = self._find(opts["distro"])

        def removed() -> Result:
            self._sandboxes.remove(sandbox)
            return Result(0, f"Uninstalled {sandbox.name}.", {"distro": sandbox.name, "unregistered": True})

        # `uninstall`'s plan, as it names its steps.
        return self._destructive(
            op,
            sandbox,
            opts,
            home.generic_steps(
                [f"Stop {sandbox.name}", f"Unregister {sandbox.name}", "Remove the shortcuts and the install record"]
            ),
            removed,
        )

    def _logs(self, op: DemoOperation, opts: dict[str, str]):
        sandbox = self._find(opts["distro"])
        for line in comfyui.backlog(sandbox.port, int(opts.get("tail", "100"))):
            op.say(line)
        follows = "follow" in opts and sandbox.state == SandboxState.RUNNING

        async def play() -> Result:
            if follows:  # a stopped container prints nothing new: it ends with the backlog
                await comfyui.feed(op)
            return Result(0)

        return play()

    def _run(self, op: DemoOperation, opts: dict[str, str]):
        sandbox = self._find(opts["distro"])
        if self.launch is not None:  # the run window's demo: the scripted launch
            return Script(op, sandbox.name, sandbox.port, sandbox.models, self.launch).run()
        sandbox.state = SandboxState.RUNNING  # Run here: ComfyUI's output, until it's cancelled
        op.plan(STEPS)  # `run`'s steps, as the real one announces them: through once ComfyUI answers

        async def play() -> Result:
            try:
                for k in range(LOAD):
                    op.progress.begin(k)
                    await op.nap(0.1)
                    op.progress.end(k)
                op.progress.begin(LOAD)
                for line in comfyui.comfy_startup(sandbox.port):
                    await op.nap(0.04)
                    op.say(line)
                op.progress.end(LOAD)
                for k in range(LOAD + 1, len(STEPS)):
                    op.progress.begin(k)
                    op.progress.end(k)
                op.say(f"ComfyUI is running at http://127.0.0.1:{sandbox.port}")
                await comfyui.feed(op)
                return Result(0)
            finally:
                # However it ends, `run` stops the container on its way out (its own cleanup, as
                # when its window closes): leaving Run here needs no `stop` of its own.
                # Its GPU use stops with the container, at once (as the demo's `stop` has it), so
                # what the meters show while it stops doesn't depend on timing.
                sandbox.state = SandboxState.STOPPED
                op.say("Stopping ComfyUI ...")
                await op.sleep(1)

        return play()


# The command lines the demo knows, by their first word.
COMMANDS: dict[str, Callable[[DemoGateway, DemoOperation, dict[str, str]], Any]] = {
    "install": DemoGateway._install,
    "open": DemoGateway._open,
    "start": DemoGateway._start,
    "stop": DemoGateway._stop,
    "config": DemoGateway._config,
    "prereqs": DemoGateway._prereqs,
    "update": DemoGateway._update,
    "backup": DemoGateway._backup,
    "restore": DemoGateway._restore,
    "reinstall": DemoGateway._recreate,
    "uninstall": DemoGateway._uninstall,
    "logs": DemoGateway._logs,
    "run": DemoGateway._run,
}
