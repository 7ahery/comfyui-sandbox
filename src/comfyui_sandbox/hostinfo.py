"""Read-only facts about the Windows host, from one PowerShell call through the Runner.

Used by `check` (ticket 02); `wslconfig` and intake reuse RAM, CPUs and drives.
Everything here reads fine without admin rights.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from comfyui_sandbox.runner import CommandNotFound, CommandTimeout, Runner

# One CIM round trip, printed as a single JSON object. NVIDIA's PCI vendor ID is 10DE:
# a card without a working driver shows up as "Microsoft Basic Display Adapter", so
# look at the device IDs. The class test skips the card's HDMI audio and USB-C
# functions; WQL's `_` matches the backslash in PCI\VEN_10DE.
# Win32_OptionalFeature.InstallState: 1 enabled, 2 disabled, 3 absent. The firmware
# flag reads False whenever a hypervisor is already running.
PROBE = r"""
$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$cs = Get-CimInstance Win32_ComputerSystem
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
@{
  vmp = (Get-CimInstance Win32_OptionalFeature -Filter "Name='VirtualMachinePlatform'").InstallState
  hypervisor = $cs.HypervisorPresent
  firmware = $cpu.VirtualizationFirmwareEnabled
  ram_bytes = $cs.TotalPhysicalMemory
  cpus = $cs.NumberOfLogicalProcessors
  drives = @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' |
    ForEach-Object { @{ id = $_.DeviceID; free_bytes = $_.FreeSpace } })
  video = @(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name })
  nvidia_pnp = @(Get-CimInstance Win32_PnPEntity -Filter "PNPDeviceID LIKE 'PCI_VEN_10DE%'" |
    Where-Object { -not $_.PNPClass -or $_.PNPClass -eq 'Display' } | ForEach-Object { $_.Name })
} | ConvertTo-Json -Compress -Depth 4
"""

GIB = 1024**3


@dataclass(frozen=True)
class Drive:
    letter: str  # "C:"
    free_gb: int


@dataclass(frozen=True)
class HostFacts:
    """None means Windows didn't say."""

    vmp_state: int | None = None
    hypervisor: bool | None = None
    firmware_virtualization: bool | None = None
    ram_gb: int | None = None
    cpus: int | None = None
    drives: tuple[Drive, ...] = ()
    video_controllers: tuple[str, ...] = ()
    nvidia_devices: tuple[str, ...] = ()


def _gb(value) -> int | None:
    # PowerShell's [math]::Round rounds half to even, like Python's round().
    return round(value / GIB) if isinstance(value, int | float) else None


def _names(value) -> tuple[str, ...]:
    return tuple(str(v) for v in value or () if v)


def probe(runner: Runner) -> HostFacts:
    """Ask Windows once. A failed or unreadable answer gives empty facts."""
    try:
        done = runner.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", PROBE], timeout=120)
        raw = json.loads(done.out()) if done.returncode == 0 else {}
    except CommandNotFound, CommandTimeout, ValueError:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    drives = tuple(
        Drive(str(d.get("id")), _gb(d.get("free_bytes")) or 0)
        for d in raw.get("drives") or ()
        if isinstance(d, dict) and d.get("id")
    )
    return HostFacts(
        vmp_state=raw.get("vmp") if isinstance(raw.get("vmp"), int) else None,
        hypervisor=raw.get("hypervisor") if isinstance(raw.get("hypervisor"), bool) else None,
        firmware_virtualization=raw.get("firmware") if isinstance(raw.get("firmware"), bool) else None,
        ram_gb=_gb(raw.get("ram_bytes")),
        cpus=raw.get("cpus") if isinstance(raw.get("cpus"), int) else None,
        drives=drives,
        video_controllers=_names(raw.get("video")),
        nvidia_devices=_names(raw.get("nvidia_pnp")),
    )
