"""`comfyui-sandbox check`: is this machine ready? Read-only, writes no transcript.

Ports system_check.sh and the suggestions of `wslconfig.sh show`.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Any

import typer

from comfyui_sandbox import hostinfo, wslconfig
from comfyui_sandbox.errors import CliError, ExitCode
from comfyui_sandbox.hostinfo import HostFacts
from comfyui_sandbox.output import Outcome, Output
from comfyui_sandbox.runner import CommandNotFound, CommandTimeout, Runner
from comfyui_sandbox.wsl import MIN_KERNEL, MIN_VERSION, Version, Wsl

UBUNTU = "Ubuntu-26.04"

NVIDIA_SMI = "nvidia-smi.exe"
GPU_QUERY = ("--query-gpu=name,driver_version,memory.total,compute_cap", "--format=csv,noheader")
# PyTorch CUDA build suggested for Blackwell cards. 12.8 is the community default
# (2026-10): most prebuilt custom-node wheels (SageAttention, xformers, ...) target
# cu128. Revisit when the ecosystem moves on.
PREFERRED_CUDA = "12.8"


PRIORITY = (
    ExitCode.UNSUPPORTED_HOST,
    ExitCode.VIRTUALIZATION_OFF,
    ExitCode.WSL_NOT_READY,
    ExitCode.NO_DRIVER,
    ExitCode.NO_NVIDIA,
)

DRIVER_URL = "https://www.nvidia.com/drivers"
NO_DRIVER = (
    f"NVIDIA GPU present, but no working NVIDIA driver (nvidia-smi.exe missing or failed). "
    f"Install the Windows driver from {DRIVER_URL}."
)
NO_NVIDIA = "No NVIDIA GPU detected."

# The result message per exit code: what is wrong and the next step.
SUMMARY = {
    ExitCode.OK: "This machine is ready.",
    ExitCode.NO_NVIDIA: "No NVIDIA GPU detected. You can stop here or install in CPU-only mode.",
    ExitCode.UNSUPPORTED_HOST: "Unsupported host: comfyui-sandbox runs on Windows only.",
    ExitCode.VIRTUALIZATION_OFF: (
        "Virtualization is off in the firmware. Turn on Intel VT-x / AMD SVM in the "
        "BIOS/UEFI settings, then run check again."
    ),
    ExitCode.WSL_NOT_READY: (
        "WSL is missing, too old, or Virtual Machine Platform is off. "
        "Run 'comfyui-sandbox prereqs', then run check again."
    ),
    ExitCode.NO_DRIVER: (
        f"The NVIDIA GPU has no working driver. Install the Windows driver from {DRIVER_URL}, then run check again."
    ),
}


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


@dataclass(frozen=True)
class Finding:
    """One line of the report as data: its section ("GPU", "WSL"...), what it says, and whether
    it passes (only a [FAIL] doesn't). A front end lists these instead of the printed report, so a
    check for another platform brings its own rows and the front end needn't change."""

    label: str
    value: str
    ok: bool = True
    # `prereqs` fixes this failure (WSL missing, too old, or Virtual Machine Platform off)
    fixed_by_prereqs: bool = False


@dataclass
class Report:
    """The human report (stderr), the JSON `data` and the findings, built side by side."""

    out: Output
    data: dict[str, Any] = field(default_factory=lambda: {"problems": [], "warnings": []})
    findings: list[Finding] = field(default_factory=list)
    _section: str = field(default="", init=False, repr=False)  # the findings' label, from section()

    def section(self, title: str) -> None:
        self.out.info(f"== {title} ==")
        self._section = title

    def ok(self, text: str) -> None:
        self.out.info(f"[ OK ] {text}")
        self.findings.append(Finding(self._section, text))

    def note(self, text: str) -> None:
        self.out.info(f"[INFO] {text}")
        self.findings.append(Finding(self._section, text))

    def names(self, title: str, names: list[str]) -> None:
        """A note that lists names, one per line under it in the printed report, and as one
        finding ("title: a, b")."""
        self.out.info(f"[INFO] {title}:")
        for name in names:
            self.out.info(f"        - {name}")
        self.findings.append(Finding(self._section, f"{title}: {', '.join(names)}"))

    def warn(self, text: str) -> None:
        self.out.info(f"[WARN] {text}")
        self.data["warnings"].append(text)
        # A warning passes: it's worth knowing, but nothing stops an install.
        self.findings.append(Finding(self._section, text))

    def fail(self, code: ExitCode, text: str) -> None:
        self.out.info(f"[FAIL] {text}")
        self.data["problems"].append({"exit_code": int(code), "message": text})
        self.findings.append(Finding(self._section, text, ok=False, fixed_by_prereqs=code == ExitCode.WSL_NOT_READY))

    def exit_code(self) -> ExitCode:
        """Host, virtualization and WSL failures win over the GPU codes, in that order."""
        codes = {p["exit_code"] for p in self.data["problems"]}
        for code in PRIORITY:
            if code in codes:
                return code
        return ExitCode.OK


def _run(runner: Runner, argv: list[str], timeout: float = 60):
    """A probe call; a missing program or a timeout counts as a failed call."""
    try:
        return runner.run(argv, timeout=timeout)
    except CommandNotFound, CommandTimeout:
        return None


def _gpu(report: Report, runner: Runner, host: HostFacts) -> None:
    report.section("GPU")
    gpu: dict[str, Any] = {
        "mode": "none",
        "gpus": [],
        "driver_cuda": None,
        "cuda_version_suggested": None,
        "windows_gpus": [],
    }
    report.data["gpu"] = gpu
    query = _run(runner, [NVIDIA_SMI, *GPU_QUERY])
    rows = [line for line in query.out().splitlines() if line.strip()] if query and query.returncode == 0 else []
    if not rows:
        if host.nvidia_devices:
            gpu["mode"] = "nodriver"
            gpu["windows_gpus"] = list(host.nvidia_devices)
            report.fail(ExitCode.NO_DRIVER, NO_DRIVER)
            report.names("Windows lists it as", gpu["windows_gpus"])
        else:
            gpu["windows_gpus"] = list(host.video_controllers)
            report.fail(ExitCode.NO_NVIDIA, NO_NVIDIA)
            report.names("GPUs Windows reports", gpu["windows_gpus"])
        return
    gpu["mode"] = "nvidia"
    blackwell = False
    for row in rows:
        name, driver, memory, cap = (part.strip() for part in ([*row.split(","), "", "", ""])[:4])
        vram = re.match(r"\d+", memory)
        gpu["gpus"].append(
            {"name": name, "driver": driver, "vram_mib": int(vram.group()) if vram else None, "compute_capability": cap}
        )
        report.ok(f"NVIDIA GPU: {name} | driver {driver} | VRAM {memory} | compute {cap}")
        if re.fullmatch(r"1[2-9]\..*", cap):
            blackwell = True
            report.warn(
                f"Blackwell-class GPU (compute {cap}): needs a PyTorch build with CUDA {PREFERRED_CUDA} or newer."
            )
    # Highest CUDA version the driver supports, from the banner. Older drivers print
    # "CUDA Version: X.Y", newer ones "CUDA UMD Version: X.Y".
    banner = _run(runner, [NVIDIA_SMI])
    found = re.search(r"CUDA (?:UMD )?Version: *([0-9]+\.[0-9]+)", banner.out()) if banner else None
    if found:
        gpu["driver_cuda"] = found.group(1)
        report.note(f"Driver supports CUDA up to {gpu['driver_cuda']}")
    if blackwell:
        # comfy-cli falls back to cu126 if it can't detect the driver, which these cards
        # can't use: pin PREFERRED_CUDA when the driver supports it.
        if found and _version(found.group(1)) >= _version(PREFERRED_CUDA):
            gpu["cuda_version_suggested"] = PREFERRED_CUDA
        else:
            report.warn(
                f"Driver too old for this GPU (needs CUDA {PREFERRED_CUDA}+, has "
                f"{gpu['driver_cuda'] or 'unknown'}): update the Windows NVIDIA driver."
            )


VMP_STATES = {1: "enabled", 2: "disabled", 3: "absent"}
PREREQS = "Run 'comfyui-sandbox prereqs'"


def _virtualization(report: Report, host: HostFacts) -> None:
    """WSL2 runs a VM: it needs virtualization on in the firmware and the Virtual Machine
    Platform feature on in Windows."""
    report.section("Virtualization")
    report.data["virtualization"] = {
        "hypervisor": host.hypervisor,
        "firmware_enabled": host.firmware_virtualization,
        "virtual_machine_platform": VMP_STATES.get(host.vmp_state) if host.vmp_state is not None else None,
    }
    virt_off = False
    # The firmware flag reads False whenever a hypervisor is already running, so it only
    # means something when none is.
    if host.hypervisor:
        report.ok("Hypervisor running")
    elif host.firmware_virtualization is False:
        virt_off = True
        report.fail(
            ExitCode.VIRTUALIZATION_OFF,
            "Virtualization is off in the BIOS/UEFI (Intel VT-x / AMD SVM). Turn it on in the firmware settings.",
        )
    elif host.firmware_virtualization:
        report.note("Virtualization on in the firmware, no hypervisor running yet")
    else:
        report.warn("Could not read the virtualization state")
    if host.vmp_state == 1:
        report.ok("Virtual Machine Platform enabled")
        if not host.hypervisor and not virt_off:
            report.warn("No hypervisor running: reboot Windows if WSL was just installed")
    elif host.vmp_state is None:
        report.warn("Could not read the Virtual Machine Platform state")
    else:
        report.fail(ExitCode.WSL_NOT_READY, f"Virtual Machine Platform is off. {PREREQS}, then reboot.")


def _wsl(report: Report, wsl: Wsl) -> None:
    report.section("WSL")
    info: dict[str, Any] = {
        "installed": wsl.installed(),
        "version": None,
        "kernel": None,
        "ubuntu_26_04_available": None,
        "distros": [],
    }
    report.data["wsl"] = info
    if not info["installed"]:
        report.fail(ExitCode.WSL_NOT_READY, f"WSL is not installed. {PREREQS}, then reboot.")
        return
    try:
        version = wsl.version()
    except CommandTimeout:
        version = Version(124, "", None)
    except CommandNotFound:
        version = Version(127, "", None)
    info["version"] = version.text
    minimum = ".".join(map(str, MIN_VERSION))
    if version.number is None:
        said = f", said: {version.first_line}" if version.first_line else ""
        report.fail(
            ExitCode.WSL_NOT_READY,
            f"WSL isn't working ('wsl --version' exit {version.returncode}{said}). {PREREQS} (it runs 'wsl --update').",
        )
        return
    if version.number < MIN_VERSION:
        report.fail(
            ExitCode.WSL_NOT_READY,
            f"WSL {version.text} is too old: {minimum} or newer is required. {PREREQS} (it runs 'wsl --update').",
        )
        return
    info["kernel"] = version.kernel_text
    minimum_kernel = ".".join(map(str, MIN_KERNEL))
    if version.kernel is None or version.kernel < MIN_KERNEL:
        shown = version.kernel_text or "unknown"
        report.fail(
            ExitCode.WSL_NOT_READY,
            f"WSL kernel {shown} is too old to boot {UBUNTU}. "
            f"{minimum_kernel} or newer is required. {PREREQS} (it runs 'wsl --update').",
        )
        return
    report.ok(version.first_line)
    report.ok(f"WSL kernel {version.kernel_text}")
    info["ubuntu_26_04_available"] = wsl.online_has(UBUNTU)
    if info["ubuntu_26_04_available"]:
        report.ok(f"{UBUNTU} available")
    else:
        report.warn(f"{UBUNTU} not in the online list (try 'wsl --update')")
    info["distros"] = wsl.list_distros()
    report.note(f"Existing distros: {', '.join(info['distros']) or '(none)'}")


LINUX_NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}")


def _defaults(report: Report, host: HostFacts, environ) -> None:
    """Inputs for the install questions: drives, Windows user, WSL limits."""
    report.section("Defaults")
    data = report.data
    data["drives"] = [{"drive": d.letter, "free_gb": d.free_gb} for d in host.drives]
    data["c_free_gb"] = next((d.free_gb for d in host.drives if d.letter.upper() == "C:"), None)
    if data["c_free_gb"] is not None:
        report.note(
            f"Free space on C: {data['c_free_gb']} GB (ComfyUI + PyTorch need ~30 GB, plus models if stored in WSL)"
        )
    if host.drives:
        report.note("Fixed drives: " + ", ".join(f"{d.letter} {d.free_gb} GB free" for d in host.drives))
    data["user_profile"] = environ.get("USERPROFILE")
    user = (environ.get("USERNAME") or "").lower()
    data["win_user_suggested"] = user if LINUX_NAME.fullmatch(user) else None
    data["wsl_limits"] = None
    if host.ram_gb and host.cpus:
        limits = wslconfig.suggest(host.ram_gb, host.cpus)
        data["wsl_limits"] = limits.as_dict()
        report.note(
            f"Host: {limits.host_ram_gb} GB RAM, {limits.host_cpus} CPUs. Suggested WSL limits: "
            f"memory {limits.memory_gb} GB (or {limits.memory_low_gb} GB for more room for Windows), "
            f"{limits.processors} processors, swap {limits.swap_gb} GB"
        )


def inspect(deps, out: Output) -> Report:
    """Run every check (read-only). `report.data` is check's JSON `data`; `report.exit_code()`
    its exit code. `install` reuses it for its defaults."""
    report = Report(out)
    report.section("Host")
    report.data["host"] = {"platform": deps.platform}
    if deps.platform != "win32":
        report.fail(ExitCode.UNSUPPORTED_HOST, f"Unsupported host ({deps.platform}): runs on Windows only")
        return report
    report.ok("Windows")
    host = hostinfo.probe(deps.runner)
    _gpu(report, deps.runner, host)
    _virtualization(report, host)
    _wsl(report, Wsl(deps.runner, deps.environ))
    _defaults(report, host, deps.environ)
    return report


def findings(deps) -> list[Finding]:
    """What `check` finds about this machine, line by line, as data, without printing anything:
    for a front end that shows the check its own way (home's Check this PC). Read-only, like
    `check`; it asks Windows and `wsl.exe`, so it takes a few seconds."""
    return inspect(deps, Output(io.StringIO(), io.StringIO(), json_mode=False)).findings


def register(app: typer.Typer) -> None:
    @app.command()
    def check(ctx: typer.Context) -> Outcome:
        """Report whether this machine is ready (changes nothing)."""
        report = inspect(ctx.obj.deps, ctx.obj.out)
        code = report.exit_code()
        if code != ExitCode.OK:
            raise CliError(SUMMARY[code], code, data=report.data)
        return Outcome(SUMMARY[code], report.data)
