"""The install questions: flags plus prompts become one `Answers` (ticket 07).

Every question is also a flag (see `Flags`; the answers keys are the flag names in snake_case).
For each question: a flag wins; else `--yes` takes the default; else `--non-interactive` stops
with exit 9 naming the flag; else the question is asked through the injected `Prompter`.
Defaults come from `check`'s data (`Facts`); the example numbers in the old prose never appear.

Spec decision 25: the default models location is inside WSL, so `--yes` needs nothing on Windows.
"""

from __future__ import annotations

import ntpath
import posixpath
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path, PureWindowsPath
from typing import Any, cast

from comfyui_sandbox.errors import AnswerMissing, CliError, UsageError
from comfyui_sandbox.prompts import Prompter
from comfyui_sandbox.state import GpuMode, ModelsMode
from comfyui_sandbox.steps import project_path
from comfyui_sandbox.wslconfig import Limits

DEFAULT_DISTRO = "comfy-docker"
ALT_DISTRO = "comfyui"
DEFAULT_USER = "comfy"
ALT_USER = "comfyui"
DEFAULT_PORT = 8188
LINUX_NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}")
DISTRO_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}")
WSL_PATH = re.compile(r"/[A-Za-z0-9._/ -]+")
RESERVED_USERS = frozenset({"root"})

MODELS = ("wsl", "windows")
MEMORY = ("tune", "keep", "pinned", "both", "keep-pinned", "neither")
MANAGER_UI = ("legacy", "standard")
SECURITY = ("strict", "normal", "normal-", "weak")
CHANNEL = ("latest", "nightly")
RELEASE = re.compile(r"v?(\d+(?:\.\d+){1,3})")  # a ComfyUI release number: 0.3.40, or v0.3.40

# C: with less free space than this gets the second drive recommended (more if the models live in WSL).
LOW_C_FREE_GB = 60
LOW_C_FREE_GB_WITH_MODELS = 100
MIN_PORT, MAX_PORT = 1024, 65535
BIG_RAM_GB = 32

# ComfyUI's models layout: the folders created in a new Windows models folder.
MODEL_FOLDERS = (
    "checkpoints",
    "clip",
    "clip_vision",
    "configs",
    "controlnet",
    "diffusers",
    "diffusion_models",
    "embeddings",
    "gligen",
    "hypernetworks",
    "loras",
    "style_models",
    "text_encoders",
    "unet",
    "upscale_models",
    "vae",
    "vae_approx",
)


class InvalidAnswer(ValueError):
    """An answer that fails validation; the message says why."""


@dataclass
class Answers:
    """The intake answers. Saved in installs.json `answers` (keys: the flag names in snake_case)
    and replayed on resume."""

    distro: str
    linux_user: str
    models: ModelsMode
    models_dir: str  # wsl: absolute Linux path; windows: absolute Windows path
    port: int
    gpu: GpuMode
    gpu_name: str | None = None
    cuda_version: str | None = None
    location: str | None = None  # Windows folder for the distro disk; None = WSL default
    memory: str = "neither"  # tune | keep | pinned | both | keep-pinned | neither
    wsl_memory_gb: int | None = None  # the .wslconfig limits, set when memory is tune or both
    wsl_processors: int | None = None
    wsl_swap_gb: int | None = None
    manager_ui: str = "standard"  # legacy | standard
    security: str = "normal"  # normal | weak
    channel: str = "latest"  # latest | nightly | a release number such as 0.3.40

    @property
    def tune_wslconfig(self) -> bool:
        return self.memory in ("tune", "both")

    @property
    def limits(self) -> tuple[int, int, int] | None:
        """The .wslconfig limits (memory GB, processors, swap GB), when they are set."""
        if self.wsl_memory_gb is None or self.wsl_processors is None or self.wsl_swap_gb is None:
            return None
        return self.wsl_memory_gb, self.wsl_processors, self.wsl_swap_gb

    @property
    def disable_pinned_memory(self) -> bool:
        return self.memory in ("pinned", "both", "keep-pinned")

    @property
    def manager_legacy(self) -> bool:
        return self.manager_ui == "legacy"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Answers:
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in raw.items() if k in known})


@dataclass
class Flags:
    """What was given on the command line (None = not given). Raw text; validated by `gather`."""

    models: str | None = None
    models_path: str | None = None
    memory: str | None = None
    manager_ui: str | None = None
    security: str | None = None
    distro: str | None = None
    user: str | None = None
    location: str | None = None
    channel: str | None = None
    wsl_limits: str | None = None
    port: int | None = None
    create_models_folders: bool = False


@dataclass(frozen=True)
class Facts:
    """What `check` found, as far as intake needs it."""

    taken: tuple[str, ...] = ()  # distro names in use (existing distros and our installs)
    drives: tuple[tuple[str, int], ...] = ()  # ("C:", free GB)
    user_profile: str | None = None
    win_user: str | None = None  # valid Linux name derived from the Windows user, or None
    limits: Limits | None = None
    current_memory_gb: int | None = None  # `memory` in the existing .wslconfig
    gpu: GpuMode = "cpu"
    gpu_name: str | None = None
    cuda_version: str | None = None

    @classmethod
    def from_check(cls, machine: dict[str, Any], taken: list[str], current_memory_gb: int | None) -> Facts:
        gpu = machine.get("gpu") or {}
        gpus = gpu.get("gpus") or []
        nvidia = gpu.get("mode") == "nvidia" and bool(gpus)
        limits = machine.get("wsl_limits")
        return cls(
            taken=tuple(taken),
            drives=tuple((d["drive"], d["free_gb"]) for d in machine.get("drives") or []),
            user_profile=machine.get("user_profile"),
            win_user=machine.get("win_user_suggested"),
            limits=Limits(**limits) if limits else None,
            current_memory_gb=current_memory_gb,
            gpu="nvidia" if nvidia else "cpu",
            gpu_name=gpus[0]["name"] if nvidia else None,
            cuda_version=gpu.get("cuda_version_suggested") if nvidia else None,
        )

    @property
    def c_free_gb(self) -> int | None:
        return next((free for letter, free in self.drives if letter.upper() == "C:"), None)

    @property
    def other_drives(self) -> list[tuple[str, int]]:
        """Drives other than C:, most free space first."""
        return sorted(((d, f) for d, f in self.drives if d.upper() != "C:"), key=lambda d: -d[1])


# -- validation (pure) ----------------------------------------------------------


def _low_threshold(models: str) -> int:
    return LOW_C_FREE_GB_WITH_MODELS if models == "wsl" else LOW_C_FREE_GB


def c_is_low(facts: Facts, models: str) -> bool:
    c_free = facts.c_free_gb
    return c_free is not None and c_free < _low_threshold(models)


def next_free_name(base: str, taken: list[str] | tuple[str, ...]) -> str:
    """`base`, then `base-2`, `base-3`, ... skipping names taken (case-insensitive)."""
    lower = {name.lower() for name in taken}
    name, n = base, 1
    while name.lower() in lower:
        n += 1
        name = f"{base}-{n}"
    return name


def _one_of(value: str, options: tuple[str, ...], what: str) -> str:
    text = value.strip().lower()
    if text not in options:
        raise InvalidAnswer(f"{what} must be one of: {', '.join(options)}.")
    return text


def parse_channel(text: str) -> str:
    """latest, nightly or a release number (a leading `v` is dropped: 0.3.40)."""
    value = text.strip().lower()
    if value in CHANNEL:
        return value
    release = RELEASE.fullmatch(value)
    if release is None:
        raise InvalidAnswer("The version must be latest, nightly or a release number such as 0.3.40.")
    return release.group(1)


def parse_distro(text: str) -> str:
    name = text.strip()
    if not DISTRO_NAME.fullmatch(name):
        raise InvalidAnswer("A sandbox name uses letters, digits, '.', '_' and '-', and starts with a letter or digit.")
    return name


def parse_user(text: str) -> str:
    name = text.strip()
    if not LINUX_NAME.fullmatch(name) or name in RESERVED_USERS:
        raise InvalidAnswer(
            "The Linux username must start with a lowercase letter, followed by lowercase "
            "letters, digits, '-' or '_' (not 'root')."
        )
    return name


def parse_port(text: str | int) -> int:
    try:
        port = int(str(text).strip())
    except ValueError:
        raise InvalidAnswer(f"The port must be a number from {MIN_PORT} to {MAX_PORT}.") from None
    if not MIN_PORT <= port <= MAX_PORT:
        raise InvalidAnswer(f"The port must be from {MIN_PORT} to {MAX_PORT}.")
    return port


def parse_wsl_models_path(text: str, user: str) -> str:
    """An absolute Linux path outside the project folder; `~` is the user's home. The scripts
    reject `/`, `/home`, the home folder and anything in `~/comfyui-docker` (recreate would delete it)."""
    home = f"/home/{user}"
    path = text.strip()
    if path == "~" or path.startswith("~/"):
        path = home + path[1:]
    if not WSL_PATH.fullmatch(path):
        raise InvalidAnswer(
            "Use an absolute Linux path (or one starting with ~/) made of letters, digits, spaces and . _ - /"
        )
    path = posixpath.normpath(re.sub(r"/{2,}", "/", path))
    project = project_path(user)
    if path in ("/", "/home", home):
        raise InvalidAnswer(
            f"{path} can't hold the models: a reinstall would delete it. Use a folder inside it, "
            f"such as {home}/comfyui-models."
        )
    if path == project or path.startswith(project + "/"):
        raise InvalidAnswer(
            f"{path} is inside the project folder {project}, which a reinstall deletes. "
            f"Use a folder outside it, such as {home}/comfyui-models."
        )
    return path


def parse_windows_path(text: str) -> str:
    """An absolute Windows path with a drive letter (the folder may not exist yet)."""
    path = PureWindowsPath(text.strip())
    if not re.fullmatch(r"[A-Za-z]:", path.drive) or not path.root:
        raise InvalidAnswer(r"Use an absolute Windows path with a drive letter, such as C:\AI\Models.")
    return str(path)


def drive_root(text: str) -> str | None:
    """The drive (`D:`) when `text` names a whole drive (`D:\\`, `D:`, `D:/`), else None."""
    drive, rest = ntpath.splitdrive(ntpath.normpath(text.strip()))
    return drive if re.fullmatch(r"[A-Za-z]:", drive) and rest in ("", "\\") else None


def drive_root_refusal(drive: str) -> str:
    """Why a whole drive is refused as the models folder (the part every refusal shares)."""
    return f"{drive}\\ is a whole drive: using it as the models folder would defeat the sandbox's isolation."


def parse_windows_models_path(text: str) -> str:
    """The Windows models folder: an absolute Windows path, but never a drive root, which would mount
    the whole drive read-write inside the sandbox."""
    drive = drive_root(text)
    if drive:
        raise InvalidAnswer(f"{drive_root_refusal(drive)} Use a folder on that drive, such as {drive}\\AI\\Models.")
    return parse_windows_path(text)


def parse_location(text: str) -> str | None:
    """A Windows folder for the distro disk; "default" is WSL's own place."""
    if text.strip().lower() == "default":
        return None
    return parse_windows_path(text)


def parse_limits(text: str, suggested: Limits | None) -> tuple[int, int, int]:
    """`suggested`, `low`, or `memory=48 processors=16 swap=16` (GB; missing keys keep the suggestion)
    as (memory, processors, swap)."""
    if suggested is None:
        raise InvalidAnswer("The machine's RAM and CPU count are unknown, so there are no WSL limits to suggest.")
    word = text.strip().lower()
    if word in ("suggested", ""):
        return suggested.memory_gb, suggested.processors, suggested.swap_gb
    if word == "low":
        return suggested.memory_low_gb, suggested.processors, suggested.swap_gb
    values = {"memory": suggested.memory_gb, "processors": suggested.processors, "swap": suggested.swap_gb}
    for token in re.split(r"[\s,]+", word):
        found = re.fullmatch(r"(memory|processors|swap)=(\d+)(?:gb)?", token)
        if not found:
            raise InvalidAnswer("Use suggested, low, or values like: memory=48 processors=16 swap=16 (GB).")
        values[found.group(1)] = int(found.group(2))
    memory, processors, swap = values["memory"], values["processors"], values["swap"]
    if not 2 <= memory <= suggested.host_ram_gb:
        raise InvalidAnswer(f"memory must be from 2 to the machine's {suggested.host_ram_gb} GB.")
    if not 1 <= processors <= suggested.host_cpus:
        raise InvalidAnswer(f"processors must be from 1 to the machine's {suggested.host_cpus}.")
    if not 0 <= swap <= 512:
        raise InvalidAnswer("swap must be from 0 to 512 GB.")
    return memory, processors, swap


# -- the Windows models folder ---------------------------------------------------


@dataclass(frozen=True)
class FolderPlan:
    action: str  # "create" (new or empty) | "ok" (complete) | "ask" (some folders missing) | "refuse"
    missing: tuple[str, ...] = ()
    problem: str | None = None  # for "refuse": what is wrong, with a "did you mean" hint


def plan_models_folder(path: Path) -> FolderPlan:
    """What to do with a Windows models folder (spec decision 25)."""
    if not path.exists():
        return FolderPlan("create", MODEL_FOLDERS)
    if not path.is_dir():
        return FolderPlan("refuse", problem=f"{path} is a file, not a folder.")
    children = list(path.iterdir())
    if not children:
        return FolderPlan("create", MODEL_FOLDERS)
    present = {c.name.lower() for c in children if c.is_dir()}
    if not present & set(MODEL_FOLDERS):
        if (path / "models").is_dir():
            hint = f"Did you mean {path / 'models'}?"
        elif path.name.lower() in MODEL_FOLDERS:
            hint = f"Did you mean {path.parent}? Point at the folder that contains `{path.name}`."
        else:
            hint = "Did you mean the folder that contains `checkpoints`, `loras` and `vae`?"
        return FolderPlan(
            "refuse",
            problem=(
                f"{path} isn't empty and has none of ComfyUI's model folders (checkpoints, loras, vae, ...). {hint}"
            ),
        )
    missing = tuple(f for f in MODEL_FOLDERS if f not in present)
    return FolderPlan("ask" if missing else "ok", missing)


def create_models_folders(path: Path) -> list[str]:
    """Create the missing ComfyUI subfolders (empty folders only; never touches files)."""
    made = []
    for name in MODEL_FOLDERS:
        folder = path / name
        if not folder.exists():
            folder.mkdir(parents=True)
            made.append(name)
    return made


# -- the questions ---------------------------------------------------------------

WSL_MODELS_NOTE = (
    "Both live outside the project folder ~/comfyui-docker, so models survive image rebuilds and "
    "reinstalls, only `wsl --unregister` deletes them. Choose Other to type an absolute Linux path."
)

Option = tuple[str, str]  # (value, label)


class _Questions:
    def __init__(
        self,
        facts: Facts,
        flags: Flags,
        prompter: Prompter,
        *,
        yes: bool,
        interactive: bool,
        previous: Answers | None = None,
        previous_custom: bool = False,
    ) -> None:
        self.previous_custom = previous_custom  # the earlier round took "Customize"
        self.facts, self.flags, self.prompter = facts, flags, prompter
        self.previous = previous  # the earlier round's answers, offered as defaults after "Change something"
        self.yes, self.interactive = yes, interactive

    # -- the shared rule: flag, else --yes, else exit 9, else ask
    def resolve(
        self,
        flag: str,
        hint: str,
        given: Any,
        parse: Callable[[Any], Any],
        default: Callable[[], Any],
        ask: Callable[[], Any],
    ) -> Any:
        if given is not None:
            try:
                return parse(given)
            except InvalidAnswer as exc:
                raise UsageError(f"{flag}: {exc}") from None
        if self.yes:
            return default()
        if not self.interactive:
            raise AnswerMissing(f"Missing answer. Re-run with {flag} {hint}, or --yes to accept every default.", flag)
        return ask()

    def choose(
        self,
        message: str,
        options: list[Option],
        recommended: str | None,
        other: tuple[str, Callable[[str], Any]] | None = None,
        earlier: Any = None,
        *,
        hint: str = "",
        default: Any = None,
    ) -> Any:
        """Ask a select question; the recommended option comes first (None: no recommendation,
        the options keep their order and none is marked). Returns the option's value,
        or (with `other`) the parsed free text, asked again until it is valid. `earlier` is the
        answer given in the previous round: it is preselected (a typed value becomes an option).
        `hint` explains the question: the Prompter's display hint, which the TUI shows and a
        terminal doesn't."""
        ordered = sorted(options, key=lambda o: o[0] != recommended)
        if earlier is not None and other and earlier not in [value for value, _ in ordered]:
            ordered.append((earlier, str(earlier)))
        labels = [label + (" (Recommended)" if value == recommended else "") for value, label in ordered]
        values = [value for value, _ in ordered]
        start = earlier if earlier in values else default if default in values else values[0]
        preselected = labels[values.index(start)]
        if other:
            labels.append("Other (type a value)")
        picked = self.prompter.select(message, labels, default=preselected, hint=hint)
        index = labels.index(picked)
        if other and index == len(labels) - 1:
            prompt, parse = other
            return self.type_in(prompt, parse, hint=hint)
        return values[index]

    def type_in(self, message: str, parse: Callable[[str], Any], default: str = "", *, hint: str = "") -> Any:
        def problem(text: str) -> str | None:
            try:
                parse(text)
            except InvalidAnswer as exc:
                return str(exc)
            return None

        return parse(self.prompter.text(message, default=default, validate=problem, hint=hint))

    # -- Round A
    def models(self) -> ModelsMode:
        # `resolve` returns text, which the validator below has already limited to MODELS.
        return cast(
            ModelsMode,
            self.resolve(
                "--models",
                "windows|wsl",
                self.flags.models,
                lambda v: _one_of(v, MODELS, "--models"),
                lambda: "wsl",
                lambda: self.choose(
                    "Where should your models live?",
                    [
                        ("windows", "Windows folder: shared with other Windows apps, slower initial model loading"),
                        (
                            "wsl",
                            "Inside WSL: faster initial model loading, uses distro disk, not shared with Windows apps",
                        ),
                    ],
                    recommended=None,
                    default="windows",
                    earlier=self.previous.models if self.previous else None,
                    hint="where ComfyUI finds checkpoints, LoRAs and VAEs",
                ),
            ),
        )

    def memory(self) -> str:
        limits = self.facts.limits
        keep = (
            limits is not None
            and self.facts.current_memory_gb is not None
            and self.facts.current_memory_gb >= limits.memory_gb
        )
        big = limits is None or limits.host_ram_gb >= BIG_RAM_GB
        if limits is None:
            recommended = "pinned"
        elif keep:
            recommended = "keep" if big else "keep-pinned"
        else:
            recommended = "both"
        pinned = ("pinned", "Add --disable-pinned-memory to ComfyUI: much less RAM used")
        neither = ("neither", "Neither (keep WSL defaults)")
        if limits is None:
            options = [pinned, neither]
        elif keep:
            options = [
                ("keep", f"Keep current .wslconfig ({self.facts.current_memory_gb} GB RAM already set)"),
                ("keep-pinned", "Keep current .wslconfig and add --disable-pinned-memory"),
                pinned,
                neither,
            ]
        else:
            options = [
                ("both", "Tune .wslconfig and add --disable-pinned-memory"),
                (
                    "tune",
                    f"Tune .wslconfig: {limits.memory_gb} GB RAM, {limits.processors} CPUs, "
                    f"{limits.swap_gb} GB swap (applies to all WSL distros)",
                ),
                pinned,
                neither,
            ]
        return self.resolve(
            "--memory",
            "tune|keep|pinned|both|keep-pinned|neither",
            self.flags.memory,
            lambda v: self._memory(v, limits is not None),
            lambda: recommended,
            lambda: self.choose(
                "How should WSL memory be set up?",
                options,
                recommended,
                earlier=self.previous.memory if self.previous else None,
                hint="a big model can fill WSL's memory and starve Windows",
            ),
        )

    @staticmethod
    def _memory(value: str, have_limits: bool) -> str:
        memory = _one_of(value, MEMORY, "--memory")
        if memory in ("tune", "both") and not have_limits:
            raise InvalidAnswer("this machine's RAM is unknown, so there are no limits to tune.")
        return memory

    def manager_ui(self) -> str:
        return self.resolve(
            "--manager-ui",
            "legacy|standard",
            self.flags.manager_ui,
            lambda v: _one_of(v, MANAGER_UI, "--manager-ui"),
            lambda: "standard",
            lambda: self.choose(
                "Which Manager UI?",
                [
                    ("standard", "Standard UI only: the newer built-in Manager panel"),
                    ("legacy", "Enable the legacy Manager UI: classic dialog"),
                ],
                recommended="standard",
                earlier=self.previous.manager_ui if self.previous else None,
                hint="how ComfyUI-Manager looks, Settings changes it later",
            ),
        )

    def security(self) -> str:
        return self.resolve(
            "--security",
            "strict|normal|normal-|weak",
            self.flags.security,
            lambda v: _one_of(v, SECURITY, "--security"),
            lambda: "normal",
            lambda: self.choose(
                "ComfyUI Manager security level?",
                [
                    (
                        "strict",
                        "Strict: Strictest, blocks even moderately risky actions like installing or updating nodes.",
                    ),
                    ("normal", "Normal: Verified nodes only, risky actions blocked."),
                    (
                        "normal-",
                        "Normal-: Like Normal on a network, but allows risky actions when running locally only.",
                    ),
                    ("weak", "Weak: Anything allowed, including unverified nodes, risky."),
                ],
                recommended="normal",
                earlier=self.previous.security if self.previous else None,
                hint="applies inside ComfyUI only: the TUI and CLI install custom nodes at any level, Settings changes it later",
            ),
        )

    # -- Round B
    def distro(self) -> str:
        taken = self.facts.taken

        def parse_new(text: str) -> str:
            name = parse_distro(text)
            if name.lower() in {t.lower() for t in taken}:
                raise InvalidAnswer(f"A distro named {name} already exists. Choose a new name.")
            return name

        first, second = next_free_name(DEFAULT_DISTRO, taken), next_free_name(ALT_DISTRO, taken)
        options = [(first, first), (second, second)] if first != second else [(first, first)]
        return self.resolve(
            "--distro",
            "<name>",
            self.flags.distro,
            parse_distro,
            lambda: first,
            lambda: self.choose(
                "Name of the new sandbox?",
                options,
                first,
                other=("Sandbox name:", parse_new),
                earlier=self.previous.distro if self.previous else None,
                hint="the sandbox's name, here and in home",
            ),
        )

    def user(self) -> str:
        win = self.facts.win_user
        second = win if win and win != DEFAULT_USER else ALT_USER
        return self.resolve(
            "--user",
            "<name>",
            self.flags.user,
            parse_user,
            lambda: DEFAULT_USER,
            lambda: self.choose(
                "Linux username (created with no password)?",
                [(DEFAULT_USER, DEFAULT_USER), (second, second)],
                DEFAULT_USER,
                other=("Linux username:", parse_user),
                earlier=self.previous.linux_user if self.previous else None,
                hint="the account ComfyUI runs as inside the distro",
            ),
        )

    def models_path(self, models: str, user: str) -> str:
        if models == "wsl":

            def parse(text: str) -> str:
                return parse_wsl_models_path(text, user)

            return self.resolve(
                "--models-path",
                "<absolute Linux path>",
                self.flags.models_path,
                parse,
                lambda: parse("~/comfyui-models"),
                lambda: parse(
                    self.choose(
                        f"Models folder inside WSL? {WSL_MODELS_NOTE}",
                        [("~/comfyui-models", "~/comfyui-models"), ("~/models", "~/models")],
                        "~/comfyui-models",
                        other=("Models path (absolute Linux path):", parse),
                        earlier=self._earlier_wsl_path(parse),
                        hint="a folder inside the distro",
                    )
                ),
            )
        profile = self.facts.user_profile
        others = self.facts.other_drives
        alt = f"{others[0][0]}\\AI\\Models" if others else (f"{profile}\\AI\\Models" if profile else None)
        options = [("C:\\AI\\Models", "C:\\AI\\Models")] + ([(alt, alt)] if alt else [])
        return self.resolve(
            "--models-path",
            r"<absolute Windows path, e.g. C:\AI\Models>",
            self.flags.models_path,
            parse_windows_models_path,
            lambda: "C:\\AI\\Models",
            lambda: self.choose(
                "Windows models folder?",
                options,
                "C:\\AI\\Models",
                other=("Models folder:", parse_windows_models_path),
                earlier=(self.previous.models_dir if self.previous and self.previous.models == "windows" else None),
                hint="the folder that directly holds the checkpoints and loras folders, made if missing",
            ),
        )

    def _earlier_wsl_path(self, parse: Callable[[str], str]) -> str | None:
        """The earlier WSL models path, as the listed option it came from when it was one."""
        before = self.previous
        if before is None or before.models != "wsl":
            return None
        for option in ("~/comfyui-models", "~/models"):
            if parse(option) == before.models_dir:
                return option
        return before.models_dir

    def windows_folder(self, path: str) -> None:
        """Check the chosen Windows folder (spec decision 25). Raises CliError for a folder that is
        not a models folder; asks (or needs --create-models-folders) before adding missing subfolders
        to an existing one."""
        plan = plan_models_folder(Path(path))
        if plan.action == "refuse":
            raise CliError(plan.problem or f"{path} can't be used as the models folder.")
        if plan.action != "ask" or self.yes or self.flags.create_models_folders:
            return
        message = (
            f"{path} already has some of ComfyUI's model folders. Create the missing ones ({', '.join(plan.missing)})?"
        )
        if not self.interactive:
            raise AnswerMissing(f"{message} Re-run with --create-models-folders, or --yes.", "--create-models-folders")
        if not self.prompter.confirm(message, True, hint="empty folders only, nothing in it is touched"):
            raise CliError(
                "Not creating the missing model folders. Install cancelled. Choose another "
                "models folder, or answer yes."
            )

    # -- Round C (advanced)
    def customize(self) -> bool:
        """Whether the advanced questions are asked."""
        f = self.flags
        unasked = [v for v in (f.channel, f.location, f.wsl_limits) if v is None]
        if self.yes or not self.interactive or not unasked:
            return False
        picked = self.prompter.select(
            "Advanced settings (ComfyUI version, disk location, WSL limits)?",
            ["Use defaults (Recommended)", "Customize"],
            default="Customize" if self.previous_custom else None,
            hint="the defaults suit most PCs",
        )
        return picked == "Customize"

    def location(self, models: str, distro: str, custom: bool) -> str | None:
        others = self.facts.other_drives
        low = c_is_low(self.facts, models)
        # A low C: with another drive that has room makes the question mandatory, even under defaults.
        pressed = low and bool(others) and others[0][1] >= _low_threshold(models)
        if self.flags.location is None and not (pressed or (custom and others)):
            return self.previous.location if self.previous else None
        drive = others[0][0] if others else "D:"

        def ask() -> str | None:
            where = f"{drive}\\WSL\\{distro}"
            options = [("default", "WSL default (on C:)"), (where, where)]
            earlier = None
            if self.previous:
                earlier = self.previous.location or "default"
                if earlier not in [value for value, _ in options]:
                    options.append((earlier, earlier))
            return parse_location(
                self.choose(
                    "Where should the distro's disk live?",
                    options,
                    where if low else "default",
                    earlier=earlier,
                    hint="it grows to tens of GB with ComfyUI and its Python packages",
                )
            )

        def default() -> str | None:
            if self.previous:
                return self.previous.location
            return f"{drive}\\WSL\\{distro}" if pressed else None

        return self.resolve(
            "--location", r"<Windows folder, or 'default'>", self.flags.location, parse_location, default, ask
        )

    def channel(self, custom: bool) -> str:
        before = self.previous.channel if self.previous else "latest"
        if self.flags.channel is None and not custom:
            return before
        return self.resolve(
            "--channel",
            "latest|nightly|<release number>",
            self.flags.channel,
            parse_channel,
            lambda: before,
            lambda: self.choose(
                "ComfyUI version?",
                [("latest", "Latest stable release"), ("nightly", "Nightly (newest, less tested)")],
                "latest",
                other=("ComfyUI release number, e.g. 0.3.40:", parse_channel),
                earlier=self.previous.channel if self.previous else None,
                hint="which ComfyUI to install, a release number pins that release",
            ),
        )

    def limits(self, memory: str, custom: bool) -> tuple[int | None, int | None, int | None]:
        if memory not in ("tune", "both"):
            return None, None, None
        suggested = self.facts.limits
        assert suggested is not None  # _memory refuses tune/both without limits

        def parse(text: str) -> tuple[int, int, int]:
            return parse_limits(text, suggested)

        before = self._earlier_limits()
        earlier = None
        if before:
            earlier = (
                "suggested"
                if before == (suggested.memory_gb, suggested.processors, suggested.swap_gb)
                else "low"
                if before == (suggested.memory_low_gb, suggested.processors, suggested.swap_gb)
                else f"memory={before[0]} processors={before[1]} swap={before[2]}"
            )

        def ask() -> tuple[int, int, int]:
            options = [
                (
                    "suggested",
                    f"Suggested: {suggested.memory_gb} GB RAM, {suggested.processors} CPUs, "
                    f"{suggested.swap_gb} GB swap",
                ),
                (
                    "low",
                    f"More room for Windows: {suggested.memory_low_gb} GB RAM, "
                    f"{suggested.processors} CPUs, {suggested.swap_gb} GB swap",
                ),
            ]
            picked = self.choose(
                "WSL limits?",
                options,
                "suggested",
                earlier=earlier,
                other=("Limits (GB), e.g. memory=48 processors=16 swap=16:", parse),
                hint="what every WSL distro may use, written to .wslconfig",
            )
            return picked if isinstance(picked, tuple) else parse(picked)

        fallback = before or (suggested.memory_gb, suggested.processors, suggested.swap_gb)
        if self.flags.wsl_limits is None and not custom:
            return fallback
        return self.resolve(
            "--wsl-limits",
            "suggested|low|memory=<GB> processors=<n> swap=<GB>",
            self.flags.wsl_limits,
            parse,
            lambda: fallback,
            ask,
        )

    def _earlier_limits(self) -> tuple[int, int, int] | None:
        p = self.previous
        if p and p.wsl_memory_gb is not None and p.wsl_processors is not None and p.wsl_swap_gb is not None:
            return p.wsl_memory_gb, p.wsl_processors, p.wsl_swap_gb
        return None

    def port(self) -> int:
        given = self.flags.port
        if given is None:
            return DEFAULT_PORT
        try:
            return parse_port(given)
        except InvalidAnswer as exc:
            raise UsageError(f"--port: {exc}") from None


def summary(a: Answers, facts: Facts) -> list[str]:
    """The confirmation table: every value that will be used."""
    memory = {
        "tune": "tune .wslconfig",
        "keep": "keep the current .wslconfig",
        "pinned": "--disable-pinned-memory",
        "both": "tune .wslconfig and --disable-pinned-memory",
        "keep-pinned": "keep the current .wslconfig and --disable-pinned-memory",
        "neither": "WSL defaults",
    }[a.memory]
    if a.tune_wslconfig:
        memory += f" ({a.wsl_memory_gb} GB RAM, {a.wsl_processors} CPUs, {a.wsl_swap_gb} GB swap)"
    gpu = f"NVIDIA ({a.gpu_name})" if a.gpu == "nvidia" else "CPU only"
    return [
        f"Distro:        {a.distro}",
        f"Linux user:    {a.linux_user}",
        f"Models:        {a.models_dir} ({'Windows folder' if a.models == 'windows' else 'inside WSL'})",
        f"Memory:        {memory}",
        f"Manager UI:    {'legacy' if a.manager_legacy else 'standard'}",
        f"Security:      {a.security}",
        f"Version:       {a.channel}",
        f"GPU mode:      {gpu}",
        f"Port:          {a.port}",
        f"Disk location: {a.location or 'WSL default'}" + _location_reason(a, facts),
    ]


def _location_reason(a: Answers, facts: Facts) -> str:
    on_other_drive = a.location and PureWindowsPath(a.location).drive.upper() not in ("", "C:")
    if on_other_drive and c_is_low(facts, a.models):
        return f" (C: has only {facts.c_free_gb} GB free)"
    return ""


# The answers a resumed install may change, as their flags. They only reach the .env and the
# Manager's settings, which a resume writes again; the others built the distro (its name, user,
# disk and models folder) or .wslconfig, so they stay as saved.
AMENDABLE = ("--channel", "--manager-ui", "--security", "--port")


def amend(saved: Answers, flags: Flags, prompter: Prompter, *, ask: bool) -> Answers:
    """A resume's answers: saved, with the version, Manager UI and security its flags give, and
    (ask) the others of those asked again, the saved ones preselected. The port isn't asked (a new
    install doesn't ask it either): --port gives it, and the caller checks it is free."""
    q = _Questions(Facts(), flags, prompter, yes=False, interactive=True, previous=saved)
    port = saved.port if flags.port is None else q.port()
    return replace(
        saved,
        port=port,
        channel=q.channel(True) if ask or flags.channel is not None else saved.channel,
        manager_ui=q.manager_ui() if ask or flags.manager_ui is not None else saved.manager_ui,
        security=q.security() if ask or flags.security is not None else saved.security,
    )


# The review's choices, and what each does.
START, CHANGE = "Start the installation (Recommended)", "Change something"


def gather(
    facts: Facts,
    flags: Flags,
    prompter: Prompter,
    *,
    yes: bool,
    interactive: bool,
    show: Callable[[list[str]], None] = lambda lines: None,
) -> Answers:
    """Turn flags and questions into answers. `show` prints the summary; interactively, the user
    confirms it ("Change something" asks everything again, with the earlier answers as defaults)."""
    previous: Answers | None = None
    previous_custom = False
    while True:
        q = _Questions(
            facts, flags, prompter, yes=yes, interactive=interactive, previous=previous, previous_custom=previous_custom
        )
        models = q.models()
        path = ""
        if models == "windows":
            path = q.models_path(models, "")
            q.windows_folder(path)
        memory = q.memory()
        manager_ui = q.manager_ui()
        security = q.security()
        distro = q.distro()
        user = q.user()
        # A Windows folder doesn't depend on the Linux user, so it was asked right after the choice
        # of Windows; an in-WSL path expands ~ to the user's home, so it waits for the user.
        if models == "wsl":
            path = q.models_path(models, user)
        custom = q.customize()
        channel = q.channel(custom)
        location = q.location(models, distro, custom)
        memory_gb, processors, swap_gb = q.limits(memory, custom)
        answers = Answers(
            distro=distro,
            linux_user=user,
            models=models,
            models_dir=path,
            port=q.port(),
            gpu=facts.gpu,
            gpu_name=facts.gpu_name,
            cuda_version=facts.cuda_version,
            location=location,
            memory=memory,
            wsl_memory_gb=memory_gb,
            wsl_processors=processors,
            wsl_swap_gb=swap_gb,
            manager_ui=manager_ui,
            security=security,
            channel=channel,
        )
        show(summary(answers, facts))
        if yes or not interactive:
            return answers
        picked = prompter.select("Ready to install?", [START, CHANGE], hint="nothing has changed yet")
        if picked.startswith("Start"):
            return answers
        # Ask every question again, with the earlier answers as defaults. Port and
        # --create-models-folders have no question, so they keep their flag values.
        previous, previous_custom = answers, custom
        flags = Flags(port=flags.port, create_models_folders=flags.create_models_folders)
