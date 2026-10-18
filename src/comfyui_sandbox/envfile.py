"""`.env` of the ComfyUI project folder (`~/comfyui-docker/.env` in the distro).

It is the source of truth for runtime settings (docs/adr/0005). `render` writes a fresh file from the intake
answers (install). `merge`/`apply` change or add keys in the existing file, keep everything else the user wrote,
and keep a backup (README.md, "`config`").
"""

from __future__ import annotations

import base64
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from comfyui_sandbox.errors import CliError
from comfyui_sandbox.intake import Answers
from comfyui_sandbox.steps import Subject, models_in_distro, project_path
from comfyui_sandbox.wsl import Wsl

HEADER = """\
# Written by comfyui-sandbox from the install answers. Edit, then apply with:
# docker compose up -d
"""


def models_dir(a: Answers) -> str:
    """The models folder as the distro sees it."""
    return models_in_distro(a.models, a.models_dir)


def comfy_args(a: Answers) -> str:
    """ComfyUI launch flags. `--enable-manager` stays, or the Manager disappears."""
    args = ["--enable-manager"]
    if a.manager_legacy:
        args.append("--enable-manager-legacy-ui")
    if a.disable_pinned_memory:
        args.append("--disable-pinned-memory")
    if a.gpu == "cpu":
        args.append("--cpu")
    return " ".join(args)


def install_args(a: Answers) -> str:
    """comfy-cli install flags: used by the first install and after a recreate."""
    if a.gpu == "cpu":
        args = ["--cpu"]
    else:
        args = ["--nvidia"]
        if a.cuda_version:
            args += ["--cuda-version", a.cuda_version]
    return " ".join([*args, "--version", a.channel])


def render(a: Answers, puid: int, pgid: int) -> str:
    """The `.env` text (LF line endings) for these answers and the Linux user's ids."""
    compose = "docker-compose.yml" if a.gpu == "cpu" else "docker-compose.yml:docker-compose.gpu.yml"
    return HEADER + "\n".join(
        [
            f"PUID={puid}",
            f"PGID={pgid}",
            "# Host folder mounted at /models inside the container.",
            f'MODELS_DIR="{models_dir(a)}"',
            "# Windows-side port: ComfyUI opens at http://localhost:<COMFY_PORT>.",
            f"COMFY_PORT={a.port}",
            "# NVIDIA mode adds docker-compose.gpu.yml (GPU access), CPU mode leaves it out.",
            f"COMPOSE_FILE={compose}",
            "# ComfyUI launch flags. Keep --enable-manager, or the Manager disappears.",
            f'COMFY_ARGS="{comfy_args(a)}"',
            "# comfy-cli install flags, used only for the first install or after a reinstall.",
            f'COMFY_INSTALL_ARGS="{install_args(a)}"',
            "",
        ]
    )


# -- merge (ticket 18) ----------------------------------------------------------

ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
BARE = re.compile(r"[A-Za-z0-9_./:@%+,=-]+")
UNSAFE = re.compile(r"[\n\r\"$`\\]")


class EnvMissing(CliError):
    """There is no `.env` to merge into (the install never deployed one)."""


def _split(raw: str) -> tuple[str, str, str, str]:
    """The text after `=` as (leading blanks, value, quote character or "", rest after the value)."""
    text = raw.lstrip()
    lead = raw[: len(raw) - len(text)]
    if text[:1] in ('"', "'"):
        end = text.find(text[0], 1)
        if end > 0:
            return lead, text[1:end], text[0], text[end + 1 :]
        return lead, text[1:], text[0], ""
    comment = re.search(r"\s#", text)
    body, rest = (text[: comment.start()], text[comment.start() :]) if comment else (text, "")
    value = body.rstrip()
    return lead, value, "", body[len(value) :] + rest


def _value(raw: str) -> tuple[str, bool]:
    """The value of the text after `=`: (value, was it quoted)."""
    _, value, quote, _ = _split(raw)
    return value, bool(quote)


def _assignment(line: str):
    return ASSIGNMENT.match(line.rstrip("\r"))


def parse(text: str) -> dict[str, str]:
    """The settings of a `.env` text: KEY -> value, unquoted. The last assignment of a key wins."""
    found: dict[str, str] = {}
    for line in text.split("\n"):
        match = _assignment(line)
        if match:
            found[match.group(1)] = _value(match.group(2))[0]
    return found


def _check(key: str, value: str) -> None:
    if UNSAFE.search(value):
        raise ValueError(f"{key}: a value can't hold a quote, $, backtick, backslash or line break: {value!r}")


def _line(key: str, value: str, quoted: bool) -> str:
    _check(key, value)
    return f'{key}="{value}"' if quoted or not BARE.fullmatch(value) else f"{key}={value}"


def _replace_value(line: str, key: str, value: str) -> str:
    """`line` with only its value replaced: the `export ` prefix, spacing, quote style and
    trailing comment stay."""
    _check(key, value)
    cr = "\r" if line.endswith("\r") else ""
    head, raw = line[: len(line) - len(cr)].split("=", 1)
    lead, _, quote, rest = _split(raw)
    if quote == "'" and "'" in value:
        quote = '"'
    if not quote and not BARE.fullmatch(value):
        quote = '"'
    return f"{head}={lead}{quote}{value}{quote}{rest}{cr}"


@dataclass(frozen=True)
class Merged:
    """The result of merging settings into a `.env`."""

    text: str  # the new file text
    changes: Mapping[str, tuple[str | None, str]] = field(default_factory=dict)  # KEY -> (old or None, new)
    backup: str = ""  # file name of the copy of the old file, inside the project folder

    @property
    def changed(self) -> bool:
        return bool(self.changes)


def merge(existing: str, updates: Mapping[str, str], *, stamp: str) -> Merged:
    """Put `updates` into `existing`. A key already there changes in place (same quoting); a new
    key is appended; a key whose value is already right, and every other line (comments, blank
    lines, the user's own keys), stays byte for byte. Raises ValueError for a value that could
    not be written safely. Nothing changed means `changed` is False: no write, no backup."""
    lines = existing.split("\n")
    if lines[-1] == "":
        lines.pop()
    last: dict[str, int] = {}
    for i, line in enumerate(lines):
        match = _assignment(line)
        if match:
            last[match.group(1)] = i
    changes: dict[str, tuple[str | None, str]] = {}
    appended: list[str] = []
    for key, new in updates.items():
        if key in last:
            old, _ = _value(_assignment(lines[last[key]]).group(2))
            if old != new:
                lines[last[key]] = _replace_value(lines[last[key]], key, new)
                changes[key] = (old, new)
        else:
            appended.append(_line(key, new, False))
            changes[key] = (None, new)
    if not changes:
        return Merged(existing)
    return Merged("\n".join([*lines, *appended]) + "\n", changes, f".env.bak-{stamp}")


def read(wsl: Wsl, who: Subject) -> str:
    """The project's current `.env` text. Raises EnvMissing."""
    project = project_path(who.linux_user)
    done = wsl.run_command(who.distro, who.linux_user, ["cat", f"{project}/.env"])
    if done.returncode != 0:
        raise EnvMissing(f"There is no {project}/.env in {who.distro}. Run 'comfyui-sandbox install' first.")
    return done.out()


def apply(wsl: Wsl, who: Subject, updates: Mapping[str, str], *, stamp: str) -> Merged:
    """Merge `updates` into the project's `.env` in the distro. When something changes, the old
    file is kept as `.env.bak-<stamp>` and the new one is written; otherwise nothing is touched.
    Raises EnvMissing (no file), ValueError (unsafe value) or CliError (the write failed)."""
    project = project_path(who.linux_user)
    merged = merge(read(wsl, who), updates, stamp=stamp)
    if merged.changed:
        done = wsl.run_script(
            who.distro,
            who.linux_user,
            "write_env.sh",
            {
                "PROJECT_DIR": project,
                "STAMP": stamp,
                "ENV_B64": base64.b64encode(merged.text.encode("utf-8")).decode("ascii"),
            },
        )
        if done.returncode != 0:
            raise CliError(f"Writing {project}/.env failed (exit {done.returncode}): {done.err().strip()}")
    return merged
