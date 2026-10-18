"""The shared destructive-action gate (docs/scripting.md, "The destructive-action gate"; docs/adr/0009).

A destructive action (`uninstall`, `restore`, `recreate`, and `wsl --shutdown` while other
distros run) needs the user to name the sandbox (for a shutdown, a distro it would stop): `--confirm <sandbox>`,
or typing the name in a terminal. Without it under --non-interactive the exit code is 8. `--yes` never confirms.
"""

from __future__ import annotations

from collections.abc import Sequence

from comfyui_sandbox.errors import DestructiveRefused

# The typed confirmation's display hint (prompts.Prompter): what a front end with room for it shows
# under the question. It names no key: which key backs out is the front end's business.
CONFIRM_HINT = "This can't be undone."


def confirm_destructive(session, names: Sequence[str], *, confirm: str | None, warning: str) -> str:
    """Return the name that was confirmed, or raise `DestructiveRefused` (exit 8).

    `names` are the distros that confirm the action (usually the one distro it acts on; for a
    shutdown, the distros it would stop). `confirm` is the command's `--confirm` value
    (None when not given). Names compare case-insensitively; the real name is returned.
    Without `confirm` a terminal is asked to type the name once, and anything else refuses;
    under `--non-interactive` nothing is asked. `session.yes` is never read.
    """
    wanted = " or ".join(names)
    flag = f"--confirm {names[0]}"
    by_lower = {name.lower(): name for name in names}
    if confirm is not None:
        if confirm.strip().lower() in by_lower:
            return by_lower[confirm.strip().lower()]
        raise DestructiveRefused(
            f"--confirm {confirm} does not match: {wanted}. Nothing was changed. {warning} To go ahead, pass {flag}."
        )
    if session.non_interactive:
        raise DestructiveRefused(
            f"{warning} This needs confirmation, and --yes never confirms. Nothing was changed. Re-run with {flag}."
        )
    session.out.warn(warning)
    typed = session.deps.prompter.text(
        f"Type the sandbox name to confirm ({wanted}):", hint=CONFIRM_HINT, type_to_confirm=tuple(names)
    )
    if typed.strip().lower() in by_lower:
        return by_lower[typed.strip().lower()]
    raise DestructiveRefused(f"'{typed.strip()}' is not {wanted}. Nothing was changed.")
