"""installs.json: the distros the CLI owns (docs/scripting.md, "installs.json"; docs/adr/0005).

The one place that answers "is this distro ours?". Commands get their install
through `resolve` (via `Session.resolve_install()`), never by reading the file.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from comfyui_sandbox.errors import AnswerMissing, CliError
from comfyui_sandbox.prompts import Prompter

SCHEMA = 1
FILE = "installs.json"

InstallState = Literal["in_progress", "complete"]
ModelsMode = Literal["wsl", "windows"]
GpuMode = Literal["nvidia", "cpu"]


def now() -> str:
    """UTC, ISO 8601 with Z, to the second."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class StateError(CliError):
    """installs.json can't be used (unreadable, newer schema). Exit 1; the file is left alone."""


class NotOurs(CliError):
    """The distro isn't in installs.json, or there is no installs.json."""


@dataclass
class Models:
    mode: ModelsMode
    path: str  # wsl: absolute Linux path; windows: absolute Windows path


@dataclass
class Gpu:
    mode: GpuMode
    name: str | None = None


@dataclass
class Install:
    """One install (one distro). Field meanings: docs/scripting.md, "installs.json"."""

    distro: str
    linux_user: str
    port: int
    models: Models
    gpu: Gpu
    deployed_by: str
    state: InstallState = "in_progress"
    cuda_version: str | None = None
    location: str | None = None
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)
    distro_created_at: str | None = None  # set once "Create the distro" succeeded (ticket 22)
    answers: dict[str, Any] = field(default_factory=dict)
    # Keys a newer CLI wrote that this one doesn't know: kept on rewrite.
    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    def is_named(self, distro: str) -> bool:
        return self.distro.lower() == distro.lower()

    def to_json(self) -> dict[str, Any]:
        return {
            **self.extra,
            "distro": self.distro,
            "linux_user": self.linux_user,
            "state": self.state,
            "port": self.port,
            "models": {"mode": self.models.mode, "path": self.models.path},
            "gpu": {"mode": self.gpu.mode, "name": self.gpu.name},
            "cuda_version": self.cuda_version,
            "location": self.location,
            "deployed_by": self.deployed_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "distro_created_at": self.distro_created_at,
            "answers": self.answers,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Install:
        known = {
            "distro",
            "linux_user",
            "state",
            "port",
            "models",
            "gpu",
            "cuda_version",
            "location",
            "deployed_by",
            "created_at",
            "updated_at",
            "distro_created_at",
            "answers",
        }
        models, gpu = raw.get("models") or {}, raw.get("gpu") or {}
        return cls(
            distro=str(raw["distro"]),
            linux_user=str(raw["linux_user"]),
            port=int(raw["port"]),
            models=Models(models["mode"], str(models["path"])),
            gpu=Gpu(gpu["mode"], gpu.get("name")),
            deployed_by=str(raw.get("deployed_by") or ""),
            state=raw.get("state") or "in_progress",
            cuda_version=raw.get("cuda_version"),
            location=raw.get("location"),
            created_at=raw.get("created_at") or now(),
            updated_at=raw.get("updated_at") or now(),
            distro_created_at=raw.get("distro_created_at"),
            answers=dict(raw.get("answers") or {}),
            extra={k: v for k, v in raw.items() if k not in known},
        )


LOCK_TIMEOUT = 60.0


@contextmanager
def locked(path: Path) -> Generator[None]:
    """Exclusive lock on `<path>.lock` (cross-process; msvcrt on Windows, flock elsewhere)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path.with_name(path.name + ".lock"), "a+b")  # noqa: SIM115 - closed in the finally below
    try:
        deadline = time.monotonic() + LOCK_TIMEOUT
        while True:
            try:
                _try_lock(handle)
                break
            except OSError as exc:
                if time.monotonic() > deadline:
                    raise StateError(f"Timed out waiting for the lock on {path}.") from exc
                time.sleep(0.05)
        try:
            yield
        finally:
            _unlock(handle)
    finally:
        handle.close()


if sys.platform == "win32":
    import msvcrt

    def _try_lock(handle) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(handle) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class Installs:
    """installs.json, loaded. Change it with `put`/`remove`, then `save()`.

    `save()` re-reads the file under a lock and applies only the puts and removes made on this
    object, so a change another command saved since the load is never undone.
    """

    def __init__(
        self, path: Path, installs: list[Install], *, exists: bool, extra: dict[str, Any] | None = None
    ) -> None:
        self.path = path
        self.exists = exists
        self._installs = installs
        self._extra = extra or {}
        self._changes: list[tuple[Literal["put"], Install] | tuple[Literal["remove"], str]] = []

    @classmethod
    def load(cls, app_dir: Path) -> Installs:
        """Missing file: no installs. Unreadable file or newer schema: StateError."""
        path = app_dir / FILE
        if not path.is_file():
            return cls(path, [], exists=False)
        try:
            raw = json.loads(path.read_text("utf-8"))
            schema = raw.get("schema")
            if not isinstance(schema, int):
                raise ValueError("no schema")
            if schema > SCHEMA:
                raise StateError(
                    f"{path} was written by a newer comfyui-sandbox (schema {schema}). "
                    "Upgrade with 'uv tool upgrade comfyui-sandbox'."
                )
            installs = [Install.from_json(item) for item in raw.get("installs") or []]
        except StateError:
            raise
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise StateError(
                f"{path} can't be read ({type(exc).__name__}: {exc}). Fix or move it, it is never overwritten."
            ) from exc
        extra = {k: v for k, v in raw.items() if k not in ("schema", "installs")}
        return cls(path, installs, exists=True, extra=extra)

    def all(self) -> list[Install]:
        return list(self._installs)

    def find(self, distro: str) -> Install | None:
        """The install for this distro (case-insensitive), or None when it isn't ours."""
        return next((i for i in self._installs if i.is_named(distro)), None)

    def require(self, distro: str) -> Install:
        """The "is this distro ours?" check: the install, or NotOurs."""
        found = self.find(distro)
        if found is None:
            raise NotOurs(
                self._missing_message()
                if not self.exists
                else f"Distro '{distro}' was not installed by comfyui-sandbox (it is not in {self.path}), "
                "so it is never touched."
            )
        return found

    def put(self, install: Install) -> None:
        """Add the install, or replace the one with the same distro name. Sets `updated_at`."""
        install.updated_at = now()
        self._changes.append(("put", install))
        self._apply_put(install)

    def _apply_put(self, install: Install) -> None:
        for i, existing in enumerate(self._installs):
            if existing.is_named(install.distro):
                self._installs[i] = install
                return
        self._installs.append(install)

    def remove(self, distro: str) -> bool:
        self._changes.append(("remove", distro))
        return self._apply_remove(distro)

    def _apply_remove(self, distro: str) -> bool:
        before = len(self._installs)
        self._installs = [i for i in self._installs if not i.is_named(distro)]
        return len(self._installs) != before

    def save(self) -> None:
        """Under the lock: re-read the file, apply this object's own changes, replace it atomically."""
        with locked(self.path):
            fresh = Installs.load(self.path.parent)
            for change in self._changes:
                match change:
                    case ("put", install):
                        fresh._apply_put(install)
                    case ("remove", distro):
                        fresh._apply_remove(distro)
            self._installs, self._extra = fresh._installs, fresh._extra
            self._write()
        self._changes = []
        self.exists = True

    def _write(self) -> None:
        """Atomic: a temporary file in the same folder, then replace."""
        doc = {**self._extra, "schema": SCHEMA, "installs": [i.to_json() for i in self._installs]}
        text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
        fd, tmp = tempfile.mkstemp(prefix=".installs-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def _missing_message(self) -> str:
        return f"No sandboxes found ({self.path} is missing). Install one with 'comfyui-sandbox install'."


def resolve(installs: Installs, distro: str | None, *, non_interactive: bool, prompter: Prompter) -> Install:
    """--distro resolution (docs/scripting.md, "Global options and choosing a sandbox").

    Given: that install, or NotOurs. Not given: the only install; with several, ask
    (AnswerMissing naming --distro under --non-interactive); with none, NotOurs.
    """
    if distro is not None:
        return installs.require(distro)
    found = installs.all()
    if not found:
        if not installs.exists:
            raise NotOurs(installs._missing_message())
        raise NotOurs(f"No sandboxes in {installs.path}. Install one with 'comfyui-sandbox install'.")
    if len(found) == 1:
        return found[0]
    names = [i.distro for i in found]
    if non_interactive:
        raise AnswerMissing(
            f"There are several sandboxes ({', '.join(names)}). Re-run with --distro <name>.", "--distro"
        )
    return installs.require(prompter.select("Which sandbox?", names))
