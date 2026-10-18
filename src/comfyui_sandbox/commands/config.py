"""`comfyui-sandbox config`: change the port, launch flags, models folder, Manager UI, pinned
memory and security level without a rebuild.

Edits the distro's `.env` through `envfile.apply` (merge: the user's own edits stay, a backup is
written). The security level lives in the Manager's own config, so it goes through
`manager_settings.sh`. Like every command that changes the sandbox it ends with ComfyUI stopped
(`steps.ending_stopped`), so nothing is applied or restarted: the new values take effect the next
time ComfyUI starts. Every value is checked first: anything that would weaken the isolation is
refused and nothing is changed.
Ports `06_manager_settings.sh` and section 3 of the old operations notes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import typer

from comfyui_sandbox import envfile, intake, preflight, steps
from comfyui_sandbox.errors import UsageError
from comfyui_sandbox.output import Outcome
from comfyui_sandbox.state import Models
from comfyui_sandbox.steps import backup_stamp, ensure_ok, project_path

SECURITY_LEVELS = ("weak", "normal-", "normal", "strict")
MANAGER_UIS = ("legacy", "standard")
PINNED = ("on", "off")
CUDA_VERSION = re.compile(r"[0-9]{1,2}\.[0-9]{1,2}")
LEGACY_FLAG = "--enable-manager-legacy-ui"
PINNED_FLAG = "--disable-pinned-memory"
MANAGER_FLAG = "--enable-manager"
FLAGS_HELP = "--port, --launch-flags, --models-path, --manager-ui, --pinned-memory, --security or --cuda-version"

# What would weaken the isolation, and why. Matched against every free-text value.
ISOLATION_RULES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), why)
    for pattern, why in (
        (r"0\.0\.0\.0|(^|[\s=])\[?::\]?($|[\s])", "it would listen on every network address"),
        (r"--privileged|privileged\s*[:=]\s*true", "a privileged container can reach the whole distro"),
        (r"--net(work)?[\s=]+host|network_mode|--(pid|ipc|uts|userns)[\s=]+host", "host networking or namespaces"),
        (r"--cap-add|cap_add|--security-opt|--device\b", "extra capabilities or security options"),
        (r"docker\.sock", "the Docker socket gives control of the host's Docker"),
        (r"(^|\s)(-v|--volume|--mount)\b|volumes\s*:", "extra mounts"),
        (r"(^|[\s=\"'])[a-z]:[\\/]|\\\\|/mnt/", "a Windows folder or mount"),
    )
)


class Refused(UsageError):
    """A value refused (exit 2). `flag` names the flag it came from."""

    def __init__(self, message: str, flag: str) -> None:
        super().__init__(message)
        self.flag = flag


def check_isolation(flag: str, value: str) -> None:
    """Raise Refused when `value` would weaken the isolation (docs/scripting.md, "Answering questions"; docs/adr/0012)."""
    for pattern, why in ISOLATION_RULES:
        if pattern.search(value):
            raise Refused(
                f"Refused: {flag} {value!r} would weaken the sandbox's isolation ({why}). Nothing was changed.", flag
            )


@dataclass
class Change:
    """What was asked for; None = leave as it is."""

    port: int | None = None
    launch_flags: str | None = None
    models_path: str | None = None
    manager_ui: str | None = None
    pinned_memory: str | None = None
    security: str | None = None
    cuda_version: str | None = None

    def asked(self) -> bool:
        return any(value is not None for value in vars(self).values())


# -- parsing: text in, a checked value out (raises UsageError) ---------------------


def _choice(flag: str, text: str, options: tuple[str, ...]) -> str:
    value = text.strip().lower()
    if value not in options:
        raise UsageError(f"{flag} must be one of: {', '.join(options)}.")
    return value


def parse_port(text: str | int) -> int:
    try:
        return intake.parse_port(text)
    except intake.InvalidAnswer as exc:
        raise UsageError(f"--port: {exc}") from None


def parse_launch_flags(text: str) -> str:
    check_isolation("--launch-flags", text)
    if re.search(r"--models-directory", text):
        raise UsageError("--launch-flags can't hold --models-directory: the entrypoint passes it. Use --models-path.")
    if envfile.UNSAFE.search(text):
        raise UsageError("--launch-flags can't hold a quote, $, backtick, backslash or line break.")
    return with_manager(text.split())


def parse_models_path(text: str, user: str) -> str:
    drive = intake.drive_root(text)
    if drive:
        raise Refused(
            f"Refused: --models-path {text!r}: {intake.drive_root_refusal(drive)} "
            "Use a folder inside the distro, such as ~/comfyui-models. Nothing was changed.",
            "--models-path",
        )
    check_isolation("--models-path", text)
    try:
        return intake.parse_wsl_models_path(text, user)
    except intake.InvalidAnswer as exc:
        raise UsageError(f"--models-path: {exc}") from None


def parse_cuda_version(text: str) -> str:
    value = text.strip()
    check_isolation("--cuda-version", value)
    if not CUDA_VERSION.fullmatch(value):
        raise UsageError("--cuda-version must be a CUDA version like 12.8.")
    return value


def with_cuda_version(install_args: str, version: str) -> str:
    """`COMFY_INSTALL_ARGS` with `--cuda-version <version>` (replacing an earlier one), after `--nvidia`."""
    tokens = install_args.split()
    if "--cpu" in tokens:
        raise UsageError("This is a CPU-only sandbox: there is no CUDA version to set. Nothing was changed.")
    kept: list[str] = []
    skip = False
    for token in tokens:
        if skip:
            skip = False
        elif token == "--cuda-version":
            skip = True
        else:
            kept.append(token)
    at = kept.index("--nvidia") + 1 if "--nvidia" in kept else 0
    return " ".join([*kept[:at], "--cuda-version", version, *kept[at:]])


def with_manager(tokens: list[str]) -> str:
    """`--enable-manager` must stay, or the Manager disappears."""
    return " ".join(tokens if MANAGER_FLAG in tokens else [MANAGER_FLAG, *tokens])


def toggle(args: str, flag: str, present: bool) -> str:
    tokens = [t for t in args.split() if t != flag]
    if present:
        tokens.append(flag)
    return with_manager(tokens)


# -- the interactive menu ----------------------------------------------------------


def _validated(parse):
    def check(text: str) -> str | None:
        try:
            parse(text)
        except UsageError as exc:
            return exc.message
        return None

    return check


# The menu's display hints (prompts.Prompter): one line per entry, which a front end with room for
# them (the TUI) shows beside the entry under the pointer. The terminal's prompts don't show them.
MENU_HINT = "One at a time, nothing is saved until Apply changes."
ENTRY_HINTS = {
    "Port": "the Windows port ComfyUI answers on, at 127.0.0.1",
    "Launch flags": "the flags ComfyUI starts with",
    "Models folder": "where ComfyUI finds its models, inside the distro",
    "Manager UI": "the Manager's classic dialog, or its newer screens",
    "Pinned memory": "on by default, off starts ComfyUI with --disable-pinned-memory",
    "Security level": "applies inside ComfyUI only: the TUI and CLI install custom nodes at any level",
}
APPLY_HINT = "saves them, ComfyUI ends stopped, and they apply at its next start"
PORT_HINT = "ComfyUI answers on http://127.0.0.1:<port>. A port in use is caught before anything changes."


def ask(session, current: dict[str, str], user: str) -> Change:
    """Ask what to change, one question at a time, until the user applies."""
    prompter = session.deps.prompter
    change = Change()
    args = current.get("COMFY_ARGS", "")
    entries = {
        "Port": f"Port ({current.get('COMFY_PORT', '?')})",
        "Launch flags": "Launch flags",
        "Models folder": f"Models folder ({current.get('MODELS_DIR', '?')})",
        "Manager UI": f"Manager UI ({'legacy' if LEGACY_FLAG in args.split() else 'standard'})",
        "Pinned memory": f"Pinned memory ({'off' if PINNED_FLAG in args.split() else 'on'})",
        "Security level": "Security level",
    }
    apply_label = "Apply changes"
    hints = {**{label: ENTRY_HINTS[name] for name, label in entries.items()}, apply_label: APPLY_HINT}
    while True:
        picked = prompter.select(
            "What do you want to change?", [*entries.values(), apply_label], hint=MENU_HINT, hints=hints
        )
        if picked == apply_label:
            return change
        name = next(key for key, label in entries.items() if label == picked)
        if name == "Port":
            change.port = parse_port(
                prompter.text("Port (1024-65535):", validate=_validated(parse_port), hint=PORT_HINT)
            )
        elif name == "Launch flags":
            change.launch_flags = parse_launch_flags(
                prompter.text(
                    "ComfyUI launch flags (--enable-manager is kept):",
                    default=args,
                    validate=_validated(parse_launch_flags),
                )
            )
        elif name == "Models folder":
            change.models_path = parse_models_path(
                prompter.text(
                    "Models folder in the distro (an absolute Linux path or ~/...):",
                    validate=_validated(lambda t: parse_models_path(t, user)),
                ),
                user,
            )
        elif name == "Manager UI":
            change.manager_ui = prompter.select("Manager UI", ["legacy (classic dialog)", "standard"]).split()[0]
        elif name == "Pinned memory":
            change.pinned_memory = prompter.select(
                "Pinned memory", ["on (default)", "off (--disable-pinned-memory)"]
            ).split()[0]
        else:
            change.security = (
                prompter.select(
                    "ComfyUI Manager security level",
                    [
                        "Strict: Strictest, blocks even moderately risky actions like installing or updating nodes.",
                        "Normal: Verified nodes only, risky actions blocked.",
                        "Normal-: Like Normal on a network, but allows risky actions when running locally only.",
                        "Weak: Anything allowed, including unverified nodes, risky.",
                    ],
                )
                .split(":")[0]
                .lower()
            )


# -- the command -------------------------------------------------------------------


def updates_for(change: Change, current: dict[str, str]) -> dict[str, str]:
    """The `.env` keys this change sets. Launch flags are rebuilt from the current COMFY_ARGS, so
    the user's own flags survive the toggles."""
    updates: dict[str, str] = {}
    if change.port is not None:
        updates["COMFY_PORT"] = str(change.port)
    if change.models_path is not None:
        updates["MODELS_DIR"] = change.models_path
    args = current.get("COMFY_ARGS", "")
    touched = False
    if change.launch_flags is not None:
        args, touched = change.launch_flags, True
    if change.manager_ui is not None:
        args, touched = toggle(args, LEGACY_FLAG, change.manager_ui == "legacy"), True
    if change.pinned_memory is not None:
        args, touched = toggle(args, PINNED_FLAG, change.pinned_memory == "off"), True
    if touched:
        updates["COMFY_ARGS"] = args
    if change.cuda_version is not None:
        updates["COMFY_INSTALL_ARGS"] = with_cuda_version(current.get("COMFY_INSTALL_ARGS", ""), change.cuda_version)
    return updates


def run_config(session, install, change: Change, current: dict[str, str]) -> Outcome:
    if change.models_path is not None and install.models.mode == "windows":
        # The Windows folder is mounted at /mnt/models by an /etc/fstab line (hardening); a WSL path
        # would leave that mount behind. Changing the mode is a reinstall, never a config edit.
        raise UsageError(
            f"{install.distro} keeps its models in a Windows folder ({install.models.path}). "
            "--models-path only moves a models folder inside the distro. To change the models "
            "location mode, uninstall it and install it again with the other mode. "
            "Nothing was changed."
        )
    out, wsl = session.out, session.wsl()
    user, distro, project = install.linux_user, install.distro, project_path(install.linux_user)
    updates = updates_for(change, current)
    if updates.get("COMFY_PORT") == current.get("COMFY_PORT"):
        updates.pop("COMFY_PORT")
    if updates.get("MODELS_DIR") == current.get("MODELS_DIR"):
        updates.pop("MODELS_DIR")
        change.models_path = None

    if "COMFY_PORT" in updates:
        out.step("Check the port")
        chosen = preflight.check_port(
            session, int(updates["COMFY_PORT"]), other_than=distro, fix="Re-run with `--port <n>`"
        )
        updates["COMFY_PORT"] = str(chosen)
        change.port = chosen
    if "MODELS_DIR" in updates:
        out.step("Prepare the models folder")
        path = updates["MODELS_DIR"]
        ensure_ok(wsl.run_command(distro, user, ["mkdir", "-p", path]), f"Creating {path}")
        ensure_ok(
            wsl.run_script(
                distro,
                user,
                "models_structure.sh",
                {"MODELS_DIR": path, "PROJECT_DIR": project, "CREATE": "1"},
                on_line=out.progress,
            ),
            "Creating the models structure",
        )

    def apply_change() -> Outcome:
        # ComfyUI is stopped here (`ending_stopped`): the new values take effect when it is started again
        # (`start` runs `docker compose up -d`, which re-reads `.env`), so there is nothing to apply or restart.
        out.step("Update .env")
        stamp = backup_stamp()
        try:
            merged = envfile.apply(wsl, install, updates, stamp=stamp)
        except ValueError as exc:
            raise UsageError(f"{exc} Nothing was changed.") from None
        changes = {key: {"from": old, "to": new} for key, (old, new) in merged.changes.items()}
        if merged.changed:
            out.info(f"Changed {', '.join(merged.changes)} in {project}/.env, the old file is {merged.backup}.")
            installs = session.installs()
            record = installs.require(distro)
            if change.port is not None:
                record.port = change.port
            if change.models_path is not None:
                record.models = Models("wsl", change.models_path)
            if change.cuda_version is not None:
                record.cuda_version = change.cuda_version
                out.info(
                    "The CUDA version applies to the next PyTorch install: run 'comfyui-sandbox reinstall' "
                    "(it wipes data/, so back up first)."
                )
            installs.put(record)
            installs.save()
        else:
            out.info(".env already has these values.")

        if change.security is not None:
            out.step("Set the Manager security level")
            steps.manager_security(wsl, out, install, change.security)

        data: dict[str, Any] = {
            "distro": distro,
            "changes": changes,
            "backup": merged.backup or None,
            "security": change.security,
        }
        what = [*merged.changes, *(["Manager security level"] if change.security else [])]
        message = (
            f"Changed {', '.join(what)} in {distro}, it takes effect when ComfyUI starts."
            if what
            else "Nothing to change: already set."
        )
        return Outcome(message, data)

    return steps.ending_stopped(session, install, apply_change)


def register(app: typer.Typer) -> None:
    @app.command()
    def config(
        ctx: typer.Context,
        port: int | None = typer.Option(None, "--port", help="The Windows port for ComfyUI (1024-65535)."),
        launch_flags: str | None = typer.Option(
            None, "--launch-flags", help="ComfyUI launch flags (replaces COMFY_ARGS, --enable-manager is kept)."
        ),
        models_path: str | None = typer.Option(
            None, "--models-path", help="A models folder inside the distro (an absolute Linux path or ~/...)."
        ),
        manager_ui: str | None = typer.Option(None, "--manager-ui", help="legacy or standard."),
        pinned_memory: str | None = typer.Option(
            None, "--pinned-memory", help="on, or off (adds --disable-pinned-memory)."
        ),
        security: str | None = typer.Option(
            None, "--security", help="Manager security level: weak, normal-, normal or strict."
        ),
        cuda_version: str | None = typer.Option(
            None,
            "--cuda-version",
            help="The CUDA build PyTorch is installed with, e.g. 12.8 (for RTX 50-series, applied by 'reinstall').",
        ),
    ) -> Outcome:
        """Change ComfyUI's port, launch flags, models folder, Manager UI, pinned memory or security level. ComfyUI ends stopped. The change takes effect at the next start."""
        session = ctx.obj
        session.transcript()
        install = session.resolve_install()
        wsl = session.wsl()
        current = envfile.parse(envfile.read(wsl, install))
        change = Change(
            port=None if port is None else parse_port(port),
            launch_flags=None if launch_flags is None else parse_launch_flags(launch_flags),
            models_path=None if models_path is None else parse_models_path(models_path, install.linux_user),
            manager_ui=None if manager_ui is None else _choice("--manager-ui", manager_ui, MANAGER_UIS),
            pinned_memory=None if pinned_memory is None else _choice("--pinned-memory", pinned_memory, PINNED),
            security=None if security is None else _choice("--security", security, SECURITY_LEVELS),
            cuda_version=None if cuda_version is None else parse_cuda_version(cuda_version),
        )
        if not change.asked():
            if session.non_interactive or session.yes:
                raise UsageError(f"Nothing to change. Give at least one of {FLAGS_HELP}.")
            change = ask(session, current, install.linux_user)
            if not change.asked():
                raise UsageError("Nothing to change.")
        return run_config(session, install, change, current)
