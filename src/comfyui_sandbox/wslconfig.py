""".wslconfig limits. Ticket 02 adds the suggestions (`check`); ticket 08 adds show/apply."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Limits:
    host_ram_gb: int
    host_cpus: int
    memory_gb: int  # suggested `memory`
    memory_low_gb: int  # "more room for Windows"
    processors: int
    swap_gb: int

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def read_memory_gb(wslconfig_text: str) -> int | None:
    """`memory` from the [wsl2] section of a .wslconfig, in whole GB (None when not set).

    Understands `8GB`, `8192MB` and plain byte counts, as WSL does.
    """
    section = None
    for raw in wslconfig_text.splitlines():
        line = raw.split("#", 1)[0].split(";", 1)[0].strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
        elif section == "wsl2" and "=" in line:
            key, value = (part.strip() for part in line.split("=", 1))
            if key.lower() != "memory":
                continue
            found = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(tb|gb|mb|kb|b)?", value.lower())
            if not found:
                return None
            gb_per_unit = {"tb": 1024, "gb": 1, "mb": 1 / 1024, "kb": 1 / 1024**2, "b": 1 / 1024**3, None: 1 / 1024**3}
            return int(float(found.group(1)) * gb_per_unit[found.group(2)])
    return None


def suggest(ram_gb: int, cpus: int) -> Limits:
    """What to give WSL on this machine (integer arithmetic, floor division).

    Keep a quarter of the RAM for Windows (4 to 8 GB). "More room for Windows" keeps twice
    that, but never goes below WSL's 50% default. Swap is an eighth of the RAM, 4 to 16 GB.
    """
    reserve = min(max(ram_gb // 4, 4), 8)
    memory = max(ram_gb - reserve, 2)
    low = max(ram_gb - 2 * reserve, ram_gb // 2, 2)
    swap = min(max(ram_gb // 8, 4), 16)
    return Limits(ram_gb, cpus, memory, low, cpus, swap)


# -- reading and merging the file (ticket 08) --------------------------------------------

SECTION = re.compile(r"^\s*\[(.*?)\]\s*$")
KEY = re.compile(r"^\s*([A-Za-z]+)\s*=")


def decode(raw: bytes) -> str:
    """The text of a .wslconfig. Windows editors save UTF-16 or UTF-8 with a BOM; read as plain
    text those would hide the `[wsl2]` section and a second one would get added. Line endings
    are returned as LF."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16", errors="replace")
    else:
        text = raw.decode("utf-8-sig", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def merge(raw: bytes | None, memory_gb: int, processors: int, swap_gb: int) -> bytes:
    """`raw` (the current file, None when there is none) with `memory`, `processors` and `swap`
    set under `[wsl2]`; every other line is kept. UTF-8 without a BOM, in the file's line endings.

    Section and key names match case-insensitively. A key is rewritten in place as
    `memory=56GB` (no spaces); missing keys go after the last setting of the first `[wsl2]`
    section; with no `[wsl2]` section one is appended.
    """
    wanted = {"memory": f"{memory_gb}GB", "processors": str(processors), "swap": f"{swap_gb}GB"}
    text = decode(raw) if raw else ""
    crlf = bool(raw) and "\r\n" in (
        raw.decode("utf-16", errors="replace")
        if raw.startswith((b"\xff\xfe", b"\xfe\xff"))
        else raw.decode("utf-8", errors="replace")
    )
    lines = text.split("\n") if text else []
    if lines and lines[-1] == "":
        lines.pop()
    seen: set[str] = set()
    section: str | None = None
    first_end: int | None = None  # one past the last setting of the first [wsl2] section
    in_first = first_done = False
    for i, line in enumerate(lines):
        header = SECTION.match(line)
        if header:
            section = header.group(1).strip().lower()
            if in_first:
                in_first, first_done = False, True
            if section == "wsl2" and not first_done:
                in_first, first_end = True, i + 1
            continue
        if section != "wsl2":
            continue
        key = KEY.match(line)
        if key and key.group(1).lower() in wanted:
            name = key.group(1).lower()
            lines[i] = f"{name}={wanted[name]}"
            seen.add(name)
        if in_first and line.strip():
            first_end = i + 1
    missing = [f"{name}={value}" for name, value in wanted.items() if name not in seen]
    if first_end is None:
        lines += ([""] if lines else []) + ["[wsl2]", *missing]
    else:
        lines[first_end:first_end] = missing
    merged = "\n".join(lines) + "\n"
    return (merged.replace("\n", "\r\n") if crlf else merged).encode("utf-8")
