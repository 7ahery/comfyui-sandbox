"""`comfyui-sandbox update comfyui [--version latest|nightly|<release number>]`.

The install checks out a release tag (a detached HEAD), so a plain `comfy update` cannot move
forward: the version is always passed. An explicit --version is also written to `COMFY_INSTALL_ARGS`
in `.env` (used by a recreate), so the channel survives, and to the install's record (its `channel`
answer), which home reads to mark a pinned sandbox without waking its distro (docs/adr/0005).

A release number (0.3.40) pins the sandbox there: an update without --version (`update all` too)
leaves a pinned ComfyUI as it is, and its check never reports an update; only another --version
moves it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import typer

from comfyui_sandbox import envfile, plans
from comfyui_sandbox.commands.update_support import (
    CHECK_TIMEOUT,
    NOT_CHECKED,
    Context,
    Pending,
    Prepared,
    in_container,
    prepared,
    probe,
    run_ending_stopped,
    unknown,
    version_key,
)
from comfyui_sandbox.container import COMFYUI_PATH, run_in_container
from comfyui_sandbox.envfile import EnvMissing
from comfyui_sandbox.errors import CliError, UsageError
from comfyui_sandbox.intake import CHANNEL, RELEASE
from comfyui_sandbox.output import Outcome
from comfyui_sandbox.steps import backup_stamp, command_for, container_running

NAME = "comfyui"
VERSION_FLAG = re.compile(r"--version\s+\S+")
UNPIN = "update comfyui --version latest"


def pending(session, install) -> Pending:
    """The checked-out ComfyUI against the newest release tag (stable) or the remote master (nightly).
    A pinned sandbox is never pending: it says what it is pinned to (and the newest release, when it
    can read it). Read-only: `git ls-remote` fetches nothing."""
    wsl = session.wsl()
    try:
        text = envfile.read(wsl, install)
    except EnvMissing as exc:
        return unknown(f"the .env is missing ({exc})")
    channel = current_channel(envfile.parse(text).get("COMFY_INSTALL_ARGS", ""))
    pin = pinned_release(channel)
    if not container_running(wsl, install, timeout=CHECK_TIMEOUT):
        return _pinned(pin, None) if pin else NOT_CHECKED
    git = ["git", "-C", COMFYUI_PATH]
    if channel == "nightly":
        head = probe(session, install, [*git, "rev-parse", "HEAD"])
        remote = probe(session, install, [*git, "ls-remote", "origin", "refs/heads/master"])
        if head.returncode != 0 or not head.out().strip():
            return unknown("could not read the installed commit")
        if remote.returncode != 0 or not remote.out().strip():
            return unknown("could not reach the ComfyUI repository")
        current, available = head.out().split()[0][:7], remote.out().split()[0][:7]
        if current != available:
            return Pending(True, f"nightly {current} -> {available}", current, available)
        return Pending(False, f"nightly {current} is the newest commit", current, available)
    remote = probe(session, install, [*git, "ls-remote", "--tags", "--refs", "origin"])
    tags = [line.split("refs/tags/")[-1] for line in remote.out().splitlines() if "refs/tags/" in line]
    tags = [name for name in tags if re.fullmatch(r"v\d+(\.\d+)*", name)]
    newest = max(tags, key=version_key) if remote.returncode == 0 and tags else None
    if pin:
        return _pinned(pin, newest)
    tag = probe(session, install, [*git, "describe", "--tags", "--exact-match"])
    if tag.returncode != 0 or not tag.out().strip():
        return unknown("the sandbox is not on a release tag")
    if newest is None:
        return unknown("could not reach the ComfyUI repository")
    current, available = tag.out().strip(), newest
    if version_key(available) > version_key(current):
        return Pending(True, f"{current} -> {available}", current, available)
    return Pending(False, f"{current} is the newest release", current, available)


def _pinned(pin: str, newest: str | None) -> Pending:
    newer = f", {newest} is the newest release" if newest and version_key(newest) > version_key(pin) else ""
    return Pending(False, f"pinned to {pin}{newer} ('{UNPIN}' unpins it)", f"v{pin}", newest, pinned=pin)


def _install_args(current: str, version: str) -> str:
    if VERSION_FLAG.search(current):
        return VERSION_FLAG.sub(f"--version {version}", current)
    return f"{current} --version {version}".strip()


def parse_version(text: str) -> str | None:
    """latest, nightly or a release number (`v0.3.40` reads as 0.3.40); None for anything else."""
    value = text.strip().lower()
    if value in CHANNEL:
        return value
    release = RELEASE.fullmatch(value)
    return release.group(1) if release else None


def current_channel(install_args: str) -> str:
    """The channel `COMFY_INSTALL_ARGS` names: nightly, a release number (pinned), else stable
    (`latest`, also when unset or unreadable)."""
    found = VERSION_FLAG.search(install_args)
    return (parse_version(found.group().split()[-1]) if found else None) or "latest"


def pinned_release(channel: str | None) -> str | None:
    """The release a channel pins ComfyUI to, or None for latest and nightly (and no channel)."""
    return channel if channel and channel not in CHANNEL and parse_version(channel) == channel else None


def require_version(version: str | None) -> str | None:
    """`version` as a channel (None stays None). Raises a UsageError for anything else."""
    if version is None:
        return None
    chosen = parse_version(version)
    if chosen is None:
        raise UsageError(f"--version must be latest, nightly or a release number such as 0.3.40, not {version!r}.")
    return chosen


UPDATE_SECONDS = 90  # expected: pulling ComfyUI and installing its new requirements


@dataclass
class _Context(Context):
    current: str = ""  # COMFY_INSTALL_ARGS as the .env had it
    explicit: str | None = None  # the --version asked for, if any
    version: str = "latest"  # the channel updated to
    previous: str | None = None  # the version installed before the update (None: could not be read)


def prepare(session, install, version: str | None = None) -> Prepared:
    """The update, made ready: `version` None keeps the install's current channel (what `update all`
    does), which for a pinned sandbox means no steps at all. The channel is read from the .env first,
    because the plan names the version."""
    version = require_version(version)
    text = envfile.read(session.wsl(), install)
    current = envfile.parse(text).get("COMFY_INSTALL_ARGS", "")
    channel = current_channel(current)
    chosen = version or channel
    context = _Context(session, install, current=current, explicit=version, version=chosen)
    if version is None and pinned_release(channel):
        return prepared(NAME, [], context, _left_pinned)
    if chosen != channel:
        session.out.info(f"Switching ComfyUI from {channel} to {chosen}.")
    what = f"Update ComfyUI ({chosen})" if not pinned_release(chosen) else f"Switch ComfyUI to {chosen}"
    table = [
        _noting_previous(step) if step.title == what else step
        for step in in_container(what, UPDATE_SECONDS, ["update", "comfy", "--version", chosen])
    ]
    return prepared(NAME, table, context, lambda c: _keep_channel(c, _updated(c)))


def installed_version(wsl, install, channel: str) -> str | None:
    """The ComfyUI version checked out in the (running) container: the release tag (stable) or the
    short commit (nightly, or a checkout with no exact tag). None when it can't be read."""
    git = ["git", "-C", COMFYUI_PATH]
    tries = (
        [[*git, "rev-parse", "--short=7", "HEAD"]]
        if channel == "nightly"
        else [[*git, "describe", "--tags", "--exact-match"], [*git, "rev-parse", "--short=7", "HEAD"]]
    )
    for argv in tries:
        try:
            done = run_in_container(wsl, install, argv, timeout=CHECK_TIMEOUT)
        except CliError:
            continue
        if done.returncode == 0 and done.out().strip():
            return done.out().split()[0]
    return None


def _noting_previous(step: plans.Step) -> plans.Step:
    """`step`, reading the installed version first (the container is up for it), for the outcome to report."""

    def run(context: _Context) -> None:
        context.previous = installed_version(context.wsl(), context.install, context.version)
        step.run(context)

    return plans.Step(step.title, step.seconds, run, step.when)


def _left_pinned(context: _Context) -> Outcome:
    """The outcome of an update that kept the sandbox's pin: nothing ran."""
    pin = context.version
    unpin = command_for(UNPIN, context.session, context.install.distro)
    return Outcome(
        f"ComfyUI is pinned to {pin}, so it was left as it is. '{unpin}' moves it to the newest release.",
        {
            "distro": context.install.distro,
            "version": pin,
            "pinned": pin,
            "previous": None,
            "installed": None,
            "env_changed": False,
            "left_pinned": True,
        },
    )


def _updated(context: _Context) -> Outcome:
    """The outcome once the steps have run (ComfyUI is up still): what it was on and what it is on now."""
    now = installed_version(context.wsl(), context.install, context.version)
    pin = pinned_release(context.version)
    kind = "commit" if context.version == "nightly" else "release"
    if pin:
        if now and context.previous and now != context.previous:
            said = f"ComfyUI switched from {context.previous} to {now}, and is pinned there."
        elif now:
            said = f"ComfyUI is on {now}, and is pinned there."
        else:
            said = f"ComfyUI switched to {pin}, and is pinned there (its version could not be read)."
        said += " Updates leave it on this release until you pick another version."
    elif now and context.previous and now != context.previous:
        said = f"ComfyUI updated from {context.previous} to {now} ({context.version} channel)."
    elif now:
        said = f"ComfyUI was already on {now}, the newest {kind}, nothing changed."
    else:
        said = f"ComfyUI updated ({context.version} channel, its version could not be read)."
    return Outcome(
        f"{said} It answered again after the restart.",
        {
            "distro": context.install.distro,
            "version": context.version,
            "pinned": pin,
            "previous": context.previous,
            "installed": now,
        },
    )


def _keep_channel(context: _Context, outcome: Outcome) -> Outcome:
    """After the update: an explicit --version is written to the .env, so a recreate keeps the channel,
    and to the install's record, so home knows it without asking the distro."""
    if context.explicit is None:
        outcome.data["env_changed"] = False  # no --version: the .env is left as it is
        return outcome
    wanted = _install_args(context.current, context.version)
    try:
        merged = envfile.apply(context.wsl(), context.install, {"COMFY_INSTALL_ARGS": wanted}, stamp=backup_stamp())
    except ValueError as exc:
        raise UsageError(f"{exc} Nothing was changed.") from None
    if merged.changed:
        context.out.info(f'COMFY_INSTALL_ARGS is now "{wanted}", the old .env is {merged.backup}.')
    outcome.data["env_changed"] = merged.changed
    _record_channel(context.session, context.install, context.version)
    return outcome


def _record_channel(session, install, channel: str) -> None:
    """Write `channel` to the install's record (its `channel` answer), as it now is in the .env."""
    installs = session.installs()
    current = installs.find(install.distro)
    if current is None or current.answers.get("channel") == channel:
        return
    current.answers["channel"] = channel
    installs.put(current)
    installs.save()
    install.answers["channel"] = channel


def register(group: typer.Typer) -> None:
    @group.command(NAME)
    def update_comfyui(
        ctx: typer.Context,
        version: str | None = typer.Option(
            None,
            "--version",
            help="latest (newest stable release), nightly, or a "
            "release number such as 0.3.40, which pins the sandbox to it. "
            "Default: the sandbox's current channel (a pinned one is left as it is).",
        ),
    ) -> Outcome:
        """Update ComfyUI itself to the newest release, the nightly channel, or a release you pick. ComfyUI ends stopped."""
        require_version(version)  # before ComfyUI is stopped for a command that cannot run
        return run_ending_stopped(ctx.obj, prepare, version)
