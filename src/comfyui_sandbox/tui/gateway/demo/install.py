"""FAKE: `install` in the demo, as the core plays it: its check, its intake questions, its preflight,
then its steps, all announced as one plan before the first runs.

The questions mirror comfyui_sandbox.intake on the demo's PC (an RTX 4090 with 64 GB and 24 CPUs,
no .wslconfig yet, a C: and a D: drive): the same questions, in the same order, with the same
choices, defaults, display hints and checks, and the "Ready to install? / Change something" review.
The preflight mirrors comfyui_sandbox.preflight's port check: a port another sandbox has, or a
program holds, is asked again. The steps are commands/install.py's, with made-up output. Keep them
in step with the core when its questions or steps change: the demo's screens are meant to be the
real ones.

Like the core, a new install is recorded (its row appears, Incomplete) only once preflight has
passed, so backing out of a question leaves nothing behind; and a recorded one that fails or is
cancelled stays Incomplete, for Resume install.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING

from ...states import SandboxState, StepStatus
from ..records import Question, QuestionKind, Result, Sandbox
from .home import FAIL_LINES, FakeStep, apt_lines, build_lines
from .operation import Cancelled, DemoOperation

if TYPE_CHECKING:
    from . import DemoGateway

INTERRUPTED = 130
ANSWER_MISSING = 9  # the CLI's ExitCode.ANSWER_MISSING: --yes never answers a clash for you

# -- the plan ----------------------------------------------------------------------------------

# The intake's steps print nothing of their own: install's check is quiet (its report goes to the
# transcript only), and the questions are the screen's.
INTAKE = [
    FakeStep("Check the machine", 1.5, []),
    FakeStep("Questions", 20, []),  # the person's time, answering: nothing is played
    FakeStep("Preflight", 0.5, []),
]
WSL_LIMITS = "Set the WSL limits in .wslconfig"
TOOLKIT = "Install the NVIDIA Container Toolkit and test GPU access"
DEPLOY = "Deploy ComfyUI and start the container"


def steps(name: str, port: int, *, tune: bool = True) -> list[FakeStep]:
    """install's steps after its intake (commands/install.py STEPS), for a distro called name on
    port; tune: whether the answers set the WSL limits (on the demo's PC there's always an NVIDIA
    GPU, so the toolkit step is always taken)."""

    def short(title: str, *lines: str, seconds: float = 0.6) -> FakeStep:
        return FakeStep(title, seconds, list(lines) or [f"{title}...", "ok"])

    return [
        *(
            [
                short(
                    WSL_LIMITS,
                    r"Writing C:\Users\you\.wslconfig: memory=56GB processors=24 swap=8GB",
                    "The new limits apply once WSL restarts.",
                )
            ]
            if tune
            else []
        ),
        FakeStep(
            "Create the distro",
            4,
            ["Downloading Ubuntu 26.04 LTS root filesystem..."]
            + [f"[{'=' * k}{' ' * (20 - k)}] {k * 5}%" for k in range(2, 21, 2)]
            + ["Installing, this may take a few minutes...", f"Distribution successfully installed: {name}"],
        ),
        short("Create the Linux user (no password, no sudo)", "Creating user comfy (uid 1000)"),
        short(
            "Harden the distro (no Windows drives, no interop)",
            "Writing /etc/wsl.conf: [automount] enabled=false",
            "Writing /etc/wsl.conf: [interop] enabled=false appendWindowsPath=false",
        ),
        short(
            "Restart the distro so the hardening applies",
            f"Terminating {name}",
            "Checked: /mnt/c is not mounted",
            "Checked: cmd.exe is not reachable from Linux",
        ),
        FakeStep(
            "Install Docker Engine",
            4,
            apt_lines(
                [
                    "containerd.io",
                    "docker-ce",
                    "docker-ce-cli",
                    "docker-buildx-plugin",
                    "docker-compose-plugin",
                    "pigz",
                    "iptables",
                    "libslirp0",
                    "slirp4netns",
                ],
                "https://download.docker.com/linux/ubuntu",
            ),
        ),
        FakeStep(
            TOOLKIT,
            3,
            [
                *apt_lines(
                    [
                        "libnvidia-container1",
                        "libnvidia-container-tools",
                        "nvidia-container-toolkit-base",
                        "nvidia-container-toolkit",
                    ],
                    "https://nvidia.github.io/libnvidia-container/stable/deb",
                ),
                "nvidia-ctk runtime configure --runtime=docker",
                "Restarting docker",
            ],
        ),
        short("Stop the distro", f"Terminating {name}"),
        short("Prepare the data folder", "Created /home/comfy/comfyui-docker/data (owned by comfy)"),
        FakeStep(DEPLOY, 5, build_lines()),
        FakeStep(
            "Wait for the first start (installs PyTorch and ComfyUI: 10-30 minutes)",
            6,
            [
                "Installing PyTorch (cu130)...",
                "Installing ComfyUI requirements...",
                "Starting ComfyUI...",
                "To see the GUI go to: http://0.0.0.0:8188",
            ],
        ),
        short("Create the models structure", "Created checkpoints, loras, vae, ... in /home/comfy/comfyui-models"),
        short("Set the Manager security level", "security_level = normal"),
        short("Restart ComfyUI", " Container comfyui  Restarting", " Container comfyui  Started"),
        short("Wait for ComfyUI to answer again", f"GET http://127.0.0.1:{port}/system_stats -> 200"),
        short(
            "Verify the sandbox (security checks and Windows access)",
            "[ OK ] no Windows drives mounted",
            "[ OK ] no Windows interop",
            f"[ OK ] Windows reaches ComfyUI on 127.0.0.1:{port}",
        ),
        short("Create the launcher shortcuts", rf"Shortcut C:\Users\you\Desktop\ComfyUI ({name}).lnk"),
        short("Stop ComfyUI (it runs from its own window)", " Container comfyui  Stopped"),
        short("Release the keepalive", "ok"),
        short(
            "Starting ComfyUI in a new window",
            f"Waiting for http://127.0.0.1:{port} ...",
            f"GET http://127.0.0.1:{port}/ -> 200",
            seconds=1.5,
        ),
    ]


# -- the intake's questions (comfyui_sandbox.intake, on the demo's PC) ---------------------------

RECOMMENDED = " (Recommended)"
OTHER = "Other (type a value)"
WSL_MODELS_NOTE = (
    "Both live outside the project folder ~/comfyui-docker, so models survive image rebuilds and "
    "reinstalls, only `wsl --unregister` deletes them. Choose Other to type an absolute Linux path."
)
START, CHANGE = "Start the installation (Recommended)", "Change something"
DISTRO_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}")
LINUX_NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}")


def next_free_name(base: str, taken: list[str]) -> str:
    """intake.next_free_name: base, then base-2, base-3, ... skipping taken names."""
    lower = {name.lower() for name in taken}
    name, n = base, 1
    while name.lower() in lower:
        n += 1
        name = f"{base}-{n}"
    return name


class Intake:
    """The demo's intake: asks through op, as intake.gather asks through the Prompter. yes: --yes,
    every default taken and nothing asked."""

    def __init__(self, op: DemoOperation, taken: list[str], *, yes: bool, distro: str | None) -> None:
        self.op, self.taken, self.yes, self.distro_flag = op, taken, yes, distro
        self.previous: dict | None = None  # the earlier round's answers, after Change something

    async def ask(self, question: Question) -> str:
        answer = str(await self.op.ask(question))
        self.op.asked()
        return answer

    async def choose(
        self,
        message: str,
        options: list[tuple[str, str]],
        recommended: str | None,
        key: str,
        *,
        hint: str = "",
        default: str | None = None,
        other: tuple[str, Callable[[str], str | None]] | None = None,
    ) -> str | None:  # None: --yes with neither a recommended option nor a default
        """intake._Questions.choose: the recommended option first, the earlier answer preselected,
        Other last. Returns the option's value, or the text typed for Other."""
        ordered = sorted(options, key=lambda option: option[0] != recommended)
        earlier = self.previous.get(key) if self.previous else None
        if earlier is not None and other and earlier not in [value for value, _ in ordered]:
            ordered.append((earlier, earlier))
        labels = [label + (RECOMMENDED if value == recommended else "") for value, label in ordered]
        values = [value for value, _ in ordered]
        if other:
            labels.append(OTHER)
        if self.yes:
            return recommended or default
        picked = await self.ask(
            Question(
                message,
                tuple((label, label) for label in labels),
                hint=hint,
                default=labels[
                    values.index(earlier if earlier in values else default if default in values else values[0])
                ],
            )
        )
        if other and picked == OTHER:
            prompt, check = other
            return await self.ask(Question(prompt, kind=QuestionKind.TEXT, hint=hint, validate=check))
        return values[labels.index(picked)]

    def name_problem(self, text: str) -> str | None:
        name = text.strip()
        if not DISTRO_NAME.fullmatch(name):
            return "A sandbox name uses letters, digits, '.', '_' and '-', and starts with a letter or digit."
        if name.lower() in {t.lower() for t in self.taken}:
            return f"A distro named {name} already exists. Choose a new name."
        return None

    async def gather(self) -> dict:
        """The answers, once the person says to start (or at once, under --yes)."""
        while True:
            a: dict = {}
            a["models"] = await self.choose(
                "Where should your models live?",
                [
                    ("windows", "Windows folder: shared with other Windows apps, slower initial model loading"),
                    ("wsl", "Inside WSL: faster initial model loading, uses distro disk, not shared with Windows apps"),
                ],
                None,
                "models",
                default="windows",
                hint="where ComfyUI finds checkpoints, LoRAs and VAEs",
            )
            if a["models"] == "windows":  # asked at once: it doesn't depend on the Linux user
                a["models_dir"] = await self.choose(
                    "Windows models folder?",
                    [("C:\\AI\\Models", "C:\\AI\\Models"), ("D:\\AI\\Models", "D:\\AI\\Models")],
                    "C:\\AI\\Models",
                    "models_dir",
                    hint="the folder that directly holds the checkpoints and loras folders, made if missing",
                    other=(
                        "Models folder:",
                        lambda text: (
                            None
                            if re.match(r"[A-Za-z]:\\.", text.strip())
                            else r"Use an absolute Windows path with a drive letter, such as C:\AI\Models."
                        ),
                    ),
                )
            a["memory"] = await self.choose(
                "How should WSL memory be set up?",
                [
                    ("both", "Tune .wslconfig and add --disable-pinned-memory"),
                    ("tune", "Tune .wslconfig: 56 GB RAM, 24 CPUs, 8 GB swap (applies to all WSL distros)"),
                    ("pinned", "Add --disable-pinned-memory to ComfyUI: much less RAM used"),
                    ("neither", "Neither (keep WSL defaults)"),
                ],
                "both",
                "memory",
                hint="a big model can fill WSL's memory and starve Windows",
            )
            a["manager_ui"] = await self.manager_ui()
            a["security"] = await self.security()
            first, second = next_free_name("comfy-docker", self.taken), next_free_name("comfyui", self.taken)
            a["distro"] = self.distro_flag or await self.choose(
                "Name of the new sandbox?",
                [(first, first), (second, second)],
                first,
                "distro",
                hint="the sandbox's name, here and in home",
                other=("Sandbox name:", self.name_problem),
            )
            a["user"] = await self.choose(
                "Linux username (created with no password)?",
                [("comfy", "comfy"), ("comfyui", "comfyui")],
                "comfy",
                "user",
                hint="the account ComfyUI runs as inside the distro",
                other=(
                    "Linux username:",
                    lambda text: (
                        None
                        if LINUX_NAME.fullmatch(text.strip())
                        else "The Linux username must start with a lowercase letter, followed by lowercase letters, "
                        "digits, '-' or '_' (not 'root')."
                    ),
                ),
            )
            if a["models"] == "wsl":
                a["models_dir"] = await self.choose(
                    f"Models folder inside WSL? {WSL_MODELS_NOTE}",
                    [("~/comfyui-models", "~/comfyui-models"), ("~/models", "~/models")],
                    "~/comfyui-models",
                    "models_dir",
                    hint="a folder inside the distro",
                    other=(
                        "Models path (absolute Linux path):",
                        lambda text: (
                            None
                            if text.startswith(("/", "~/"))
                            else "Use an absolute Linux path (or one starting with ~/)."
                        ),
                    ),
                )
            custom = False
            if not self.yes:
                custom = (
                    await self.ask(
                        Question(
                            "Advanced settings (ComfyUI version, disk location, WSL limits)?",
                            (("Use defaults (Recommended)", "Use defaults (Recommended)"), ("Customize", "Customize")),
                            hint="the defaults suit most PCs",
                            default="Customize" if self.previous and self.previous.get("custom") else None,
                        )
                    )
                    == "Customize"
                )
            a["custom"] = custom
            a["location"] = "default"
            a["channel"] = "latest"
            if custom:
                a["channel"] = await self.channel()
                where = f"D:\\WSL\\{a['distro']}"
                a["location"] = await self.choose(
                    "Where should the distro's disk live?",
                    [("default", "WSL default (on C:)"), (where, where)],
                    "default",
                    "location",
                    hint="it grows to tens of GB with ComfyUI and its Python packages",
                )
                if a["memory"] in ("tune", "both"):
                    await self.choose(
                        "WSL limits?",
                        [
                            ("suggested", "Suggested: 56 GB RAM, 24 CPUs, 8 GB swap"),
                            ("low", "More room for Windows: 48 GB RAM, 24 CPUs, 8 GB swap"),
                        ],
                        "suggested",
                        "limits",
                        hint="what every WSL distro may use, written to .wslconfig",
                        other=("Limits (GB), e.g. memory=48 processors=16 swap=16:", lambda text: None),
                    )
            for line in summary(a):
                self.op.say(line)
            if self.yes:
                return a
            if (
                await self.ask(
                    Question("Ready to install?", ((START, START), (CHANGE, CHANGE)), hint="nothing has changed yet")
                )
                == START
            ):
                return a
            self.previous = a

    async def manager_ui(self) -> str | None:
        return await self.choose(
            "Which Manager UI?",
            [
                ("standard", "Standard UI only: the newer built-in Manager panel"),
                ("legacy", "Enable the legacy Manager UI: classic dialog"),
            ],
            "standard",
            "manager_ui",
            hint="how ComfyUI-Manager looks, Settings changes it later",
        )

    async def security(self) -> str | None:
        return await self.choose(
            "ComfyUI Manager security level?",
            [
                (
                    "strict",
                    "Strict: Strictest, blocks even moderately risky actions like installing or updating nodes.",
                ),
                ("normal", "Normal: Verified nodes only, risky actions blocked."),
                ("normal-", "Normal-: Like Normal on a network, but allows risky actions when running locally only."),
                ("weak", "Weak: Anything allowed, including unverified nodes, risky."),
            ],
            "normal",
            "security",
            hint="applies inside ComfyUI only: the TUI and CLI install custom nodes at any level, Settings changes it later",
        )

    async def channel(self) -> str | None:
        return await self.choose(
            "ComfyUI version?",
            [("latest", "Latest stable release"), ("nightly", "Nightly (newest, less tested)")],
            "latest",
            "channel",
            hint="which ComfyUI to install, a release number pins that release",
            other=(
                "ComfyUI release number, e.g. 0.3.40:",
                lambda text: (
                    None if re.fullmatch(r"v?\d+(\.\d+){1,3}", text.strip()) else "Use a release number such as 0.3.40."
                ),
            ),
        )

    async def amend(self, saved: dict) -> None:
        """install --change on a resume (intake.amend): the version, Manager UI and security asked
        again, the saved ones preselected; says what changed."""
        self.previous = saved
        now = {
            "channel": await self.channel(),
            "manager_ui": await self.manager_ui(),
            "security": await self.security(),
        }
        changed = [line for line, was in zip(summary({**saved, **now}), summary(saved), strict=False) if line != was]
        if changed:
            self.op.say("Changed the saved answers:")
            for line in changed:
                self.op.say(f"  {line}")
        saved.update(now)


def summary(a: dict) -> list[str]:
    """intake.summary: every value that will be used."""
    memory = {
        "tune": "tune .wslconfig (56 GB RAM, 24 CPUs, 8 GB swap)",
        "both": "tune .wslconfig and --disable-pinned-memory (56 GB RAM, 24 CPUs, 8 GB swap)",
        "pinned": "--disable-pinned-memory",
        "neither": "WSL defaults",
    }[a["memory"]]
    where = "Windows folder" if a["models"] == "windows" else "inside WSL"
    return [
        f"Distro:        {a['distro']}",
        f"Linux user:    {a['user']}",
        f"Models:        {a['models_dir']} ({where})",
        f"Memory:        {memory}",
        f"Manager UI:    {a['manager_ui']}",
        f"Security:      {a['security']}",
        f"Version:       {a['channel']}",
        "GPU mode:      NVIDIA (NVIDIA GeForce RTX 4090)",
        "Port:          8188",
        f"Disk location: {'WSL default' if a['location'] == 'default' else a['location']}",
    ]


# -- preflight (comfyui_sandbox.preflight's port check) -------------------------------------------

PORT_HINT = "the port ComfyUI answers on, at 127.0.0.1"
HELD_BY = "python.exe (PID 4120)"  # the program the port_taken scenario has on the install's port


async def free_port(intake: Intake, sandboxes: list[Sandbox], port: int, held: set[int]) -> int:
    """preflight.check_port: port, or the one chosen instead while it's another sandbox's or a
    program's. Under --yes a clash isn't asked: the install stops, as the core's does (exit 9)."""
    while True:
        owner = next((s for s in sandboxes if s.port == port), None)
        if owner is None and port not in held:
            return port
        free = next(p for p in range(port + 1, 65536) if p not in held and all(s.port != p for s in sandboxes))
        who = f"is the port of the sandbox {owner.name} (it may be stopped)" if owner else f"is used by {HELD_BY}"
        if intake.yes:
            raise PortClash(f"Port {port} {who}. Re-run with `--port <n>`. Port {free} is free.")
        instead, keep = (
            f"Use port {free} instead (Recommended)",
            None if owner else f"Keep {port}, I'll close python.exe first",
        )
        choices = [c for c in (instead, keep, "Other (type a port)") if c]
        hints = {
            instead: "nothing else uses it",
            keep: "it's checked again once you say it's closed",
            "Other (type a port)": "any port from 1024 to 65535",
        }
        picked = await intake.ask(
            Question(
                f"Port {port} {who}.",
                tuple((c, c) for c in choices),
                hint=PORT_HINT,
                hints={c: hints[c] for c in choices},
                default=instead,
            )
        )
        if picked == instead:
            port = free
        elif picked == keep:
            await intake.ask(
                Question(f"Close python.exe now. Is port {port} free?", kind=QuestionKind.CONFIRM, default=True)
            )
            held.discard(port)
        else:
            port = int(
                await intake.ask(
                    Question("Port (1024-65535):", kind=QuestionKind.TEXT, hint=PORT_HINT, validate=valid_port)
                )
            )


def valid_port(text: str) -> str | None:
    return (
        None
        if text.strip().isdigit() and 1024 <= int(text) <= 65535
        else "The port must be a number from 1024 to 65535."
    )


class PortClash(Exception):
    """A clash --yes can't answer: the install ends with exit 9, naming the flag."""


# -- the play ----------------------------------------------------------------------------------


async def play_steps(
    gateway: DemoGateway, op: DemoOperation, plan: list[FakeStep], first: int, *, fail_at: int | None
) -> bool:
    """Plays plan's steps from first on (DemoGateway._step); True when one failed (fail_at)."""
    for k in range(first, len(plan)):
        if await gateway._step(op, k, plan[k], fail_at=fail_at):
            return True
    return False


def done_steps(op: DemoOperation, first: int) -> int:
    """How many steps from first on had finished (done or passed over): where a resume carries on."""
    plan = op.progress.plan
    return (
        next(
            (k for k in range(first, len(plan)) if plan[k].status not in (StepStatus.DONE, StepStatus.SKIPPED)),
            len(plan),
        )
        - first
    )


def failure(name: str) -> Result:
    """How the deploy fails (--fail), worded as the core words it (steps.ensure_ok)."""
    return Result(1, f"Deploying ComfyUI failed (exit 1): {FAIL_LINES[-1]}", {"distro": name})


def installed(sandbox: Sandbox) -> Result:
    sandbox.state = SandboxState.RUNNING  # install ends by opening its run window (start)
    return Result(
        0,
        f"{sandbox.name} is installed. ComfyUI opens at http://localhost:{sandbox.port}.",
        {"distro": sandbox.name, "resumed": False},
    )


def install(gateway: DemoGateway, op: DemoOperation, opts: dict[str, str]):
    """`install [--distro x] [--port n] [--yes]`: the resume of the Incomplete sandbox --distro
    names, or else a new install, its intake first. Returns the play."""
    name = opts.get("distro")
    found = next((s for s in gateway._sandboxes if s.name == name), None)
    if found is not None and found.state == SandboxState.INCOMPLETE:
        return resume(gateway, op, found, change="change" in opts)
    if found is not None:
        return refused(f"{name} is already installed.")
    yes = "yes" in opts
    port = int(opts.get("port", 8188))
    # Before the questions, every step the answers might take (Output.plan).
    op.plan([(s.title, s.seconds) for s in [*INTAKE, *steps(name or "", port)]])
    fails = gateway.scenario.take_fail_at() is not None
    held = {port} if gateway.scenario.port_taken else set()

    async def play() -> Result:
        progress = op.progress
        progress.begin(0)
        await op.nap(INTAKE[0].seconds)  # the check: this PC is ready
        progress.end(0)
        progress.begin(1)
        intake = Intake(op, [s.name for s in gateway._sandboxes] + ["Ubuntu"], yes=yes, distro=name)
        answers = await intake.gather()
        progress.end(1)
        progress.begin(2)
        try:
            chosen = await free_port(intake, gateway._sandboxes, port, held)
        except PortClash as clash:
            progress.end(2, StepStatus.FAILED)
            return Result(ANSWER_MISSING, str(clash))
        # Preflight has passed: the answers are saved, and the sandbox is listed (Incomplete).
        sandbox = Sandbox(answers["distro"], SandboxState.INCOMPLETE, chosen, answers["models_dir"])
        gateway._sandboxes.append(sandbox)
        op.say(rf"Saved the answers for {sandbox.name} in ~\.comfyui-sandbox\installs.json.")
        plan = [*INTAKE, *steps(sandbox.name, chosen, tune=answers["memory"] in ("tune", "both"))]
        op.replan([(s.title, s.seconds) for s in plan])  # now with only the steps the answers take
        progress.end(2)
        fail_at = next(k for k, s in enumerate(plan) if s.title == DEPLOY) if fails else None
        try:
            if await play_steps(gateway, op, plan, len(INTAKE), fail_at=fail_at):
                gateway._done_steps[sandbox.name] = done_steps(op, len(INTAKE))
                return failure(sandbox.name)
        except Cancelled:  # it stays Incomplete, to resume from the step it was on
            gateway._done_steps[sandbox.name] = done_steps(op, len(INTAKE))
            raise
        return installed(sandbox)

    return play()


def resume(gateway: DemoGateway, op: DemoOperation, sandbox: Sandbox, *, change: bool = False):
    """`install --distro x` on an Incomplete sandbox: its saved answers, no questions (change:
    --change, the version, Manager UI and security asked again first); the steps it had finished
    are passed over (the real one runs them again, quickly: each finds itself done)."""
    plan = steps(sandbox.name, sandbox.port)
    done = gateway._done_steps.get(sandbox.name, 0)
    fail_at = (
        next(k for k, s in enumerate(plan) if s.title == DEPLOY)
        if gateway.scenario.take_fail_at() is not None
        else None
    )
    op.say(f"Resuming the install of {sandbox.name} with the saved answers.")

    def start() -> None:
        op.plan([(s.title, s.seconds) for s in plan])
        for k in range(done):
            op.progress.plan[k].status = StepStatus.SKIPPED

    if not change:
        start()

    async def play() -> Result:
        if change:  # asked before the plan, as the core asks them
            answers = gateway._answers.setdefault(
                sandbox.name,
                {
                    "distro": sandbox.name,
                    "user": "comfy",
                    "models": "wsl",
                    "models_dir": sandbox.models,
                    "memory": "both",
                    "manager_ui": "standard",
                    "security": "normal",
                    "channel": "latest",
                    "location": "default",
                },
            )
            await Intake(op, [], yes=False, distro=sandbox.name).amend(answers)
            start()
        try:
            if await play_steps(gateway, op, plan, done, fail_at=fail_at):
                gateway._done_steps[sandbox.name] = done_steps(op, 0)
                return failure(sandbox.name)
        except Cancelled:
            gateway._done_steps[sandbox.name] = done_steps(op, 0)
            raise
        gateway._done_steps.pop(sandbox.name, None)
        return installed(sandbox)

    return play()


async def refused_play(message: str) -> Result:
    return Result(1, message)


def refused(message: str):
    return refused_play(message)
