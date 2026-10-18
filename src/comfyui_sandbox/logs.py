"""Per-command transcripts under <home>\\.comfyui-sandbox\\logs (docs/scripting.md, "Transcripts").

A command that changes something calls `session.transcript()` once; cli.main writes
the footer, prints the path on failure and returns it as "log". Output messages
(info/warn/error/step) and every wsl call made through `Wsl(..., log=)` land here.

The layout is for people, not a contract. LF line endings, a header, body lines, a footer:

    # comfyui-sandbox 1.0.0 transcript
    # command: install --yes --distro comfy-docker
    # started: 2026-10-06T16:48:00Z
    [16:48:00] step  Create the distro
    [16:48:00] run   wsl.exe --install Ubuntu-26.04 --name comfy-docker --no-launch
    [16:48:31] out   Installing: Ubuntu 26.04 LTS
    [16:48:40] exit  0
    # finished: 2026-10-06T17:02:13Z exit=0

Body lines are `[HH:MM:SS] <tag> <text>` (UTC, tag padded to 5 characters). Tags: `step`, `info`,
`warn`, `error`, `run` (a subprocess argv; for an in-distro script, the wsl.exe argv plus
`< <script name>` and its env vars), `out` (one output line), `exit` (a subprocess exit code). The
footer is written even on failure; a crash's traceback is written as `error` lines before it.
"""

from __future__ import annotations

import shlex
import traceback
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

KEEP = 20
FOLDER = "logs"


def logs_dir(app_dir: Path) -> Path:
    return app_dir / FOLDER


def transcripts(app_dir: Path) -> list[Path]:
    """Every transcript, newest first: by the timestamp the name starts with, then (same
    second, `-2` suffix) by modification time."""
    folder = logs_dir(app_dir)
    if not folder.is_dir():
        return []
    files = [p for p in folder.glob("*.log") if p.is_file()]
    return sorted(files, key=lambda p: (p.name[:16], p.stat().st_mtime_ns, p.name), reverse=True)


def _clean(text: str) -> str:
    return text.replace("\r", "").replace("\0", "")


def quote_argv(argv: Sequence[str]) -> str:
    return " ".join(shlex.quote(str(a)) for a in argv)


class Transcript:
    """One transcript file, written line by line (so a crash keeps what happened)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.closed = False

    @classmethod
    def create(
        cls, app_dir: Path, command: str, argv: Sequence[str], version: str, *, when: datetime | None = None
    ) -> Transcript:
        """A new transcript `<UTC YYYYMMDDTHHMMSSZ>-<command words>.log`, then rotation."""
        when = when or datetime.now(UTC)
        folder = logs_dir(app_dir)
        folder.mkdir(parents=True, exist_ok=True)
        stem = f"{when.strftime('%Y%m%dT%H%M%SZ')}-{'-'.join(command.split()) or 'comfyui-sandbox'}"
        path, n = folder / f"{stem}.log", 1
        while path.exists():
            n += 1
            path = folder / f"{stem}-{n}.log"
        path.touch()
        transcript = cls(path)
        transcript._header(f"comfyui-sandbox {version} transcript")
        transcript._header(f"command: {quote_argv(argv)}")
        transcript._header(f"started: {when.strftime('%Y-%m-%dT%H:%M:%SZ')}")
        for old in transcripts(app_dir)[KEEP:]:
            old.unlink(missing_ok=True)
        return transcript

    def _write(self, text: str) -> None:
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(text + "\n")

    def _header(self, text: str) -> None:
        self._write(f"# {text}")

    def line(self, tag: str, text: str) -> None:
        stamp = datetime.now(UTC).strftime("%H:%M:%S")
        for part in _clean(text).split("\n") or [""]:
            self._write(f"[{stamp}] {tag:<5} {part}")

    def step(self, text: str) -> None:
        self.line("step", text)

    def info(self, text: str) -> None:
        self.line("info", text)

    def warn(self, text: str) -> None:
        self.line("warn", text)

    def error(self, text: str) -> None:
        self.line("error", text)

    def run(self, argv: Sequence[str], *, script: str | None = None, env: Mapping[str, str] | None = None) -> None:
        """A subprocess call. For an in-distro script: `< name` and the exported env vars."""
        text = quote_argv(argv)
        if script is not None:
            text += f" < {script}"
        if env:
            text += " " + " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
        self.line("run", text)

    def out(self, line: str) -> None:
        self.line("out", line)

    def exit(self, code: int) -> None:
        self.line("exit", str(code))

    def crash(self, exc: BaseException) -> None:
        for line in traceback.format_exception(exc):
            self.error(line.rstrip("\n"))

    def close(self, exit_code: int) -> None:
        if self.closed:
            return
        self.closed = True
        self._header(f"finished: {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')} exit={int(exit_code)}")
