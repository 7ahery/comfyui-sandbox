"""Preflight: the checks that can raise a question after the intake (ticket 08).

Ports preflight.sh: the port is free on Windows, the distro name is new. Where the old script
printed `[FAIL]`, a terminal asks the question; under --non-interactive (and --yes, which never
answers a question for you) it exits 9 with a plain message: what is wrong, the options, and the
flag that answers it. Read-only: nothing is changed before this passes.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path, PureWindowsPath

from comfyui_sandbox import intake
from comfyui_sandbox.errors import AnswerMissing
from comfyui_sandbox.intake import Answers, InvalidAnswer
from comfyui_sandbox.winapi import PortOwner

# How far past the wanted port to look for a free one.
PORT_SCAN = 50
# Display hints (prompts.Prompter) for the questions preflight asks again.
PORT_HINT = "the port ComfyUI answers on, at 127.0.0.1"
NAME_HINT = "the existing distro is never touched"


def next_free_port(winapi, port: int, reserved: frozenset[int] = frozenset()) -> int | None:
    """The first free port after `port` (within PORT_SCAN of it), or None. `reserved` ports count as taken."""
    for candidate in range(port + 1, min(port + PORT_SCAN, intake.MAX_PORT) + 1):
        if candidate not in reserved and winapi.port_owner(candidate) is None:
            return candidate
    return None


def _owner_text(owner: PortOwner) -> str:
    return f"{owner.name or 'an unknown program'} (PID {owner.pid})"


def _prompting(session) -> bool:
    """Questions are asked only in a terminal that was not told to accept defaults."""
    return not session.non_interactive and not session.yes


def check_port(session, port: int, *, other_than: str | None = None, fix: str = "Re-run with `--port <n>`") -> int:
    """A free Windows port: `port` itself, or the one the user chose instead (the same question and
    exit 9 as install). `config` (ticket 18) reuses it.

    With `other_than` (a distro name), the recorded port of every other install also counts as taken,
    even while that install is stopped (`install`, `config`, `start` and `run` pass their own distro). `fix` says
    how to answer when it cannot ask."""
    winapi, prompter = session.deps.winapi, session.deps.prompter
    from comfyui_sandbox import ports  # ports imports this module

    others = [i for i in session.installs().all() if other_than and not i.is_named(other_than)]
    # An install's `.env` is what its next start uses; its record is the fallback (docs/adr/0005).
    # Reading `.env` runs a command in the distro, which would boot a stopped one: like `status`, only
    # read it while the distro is up.
    running = {name.lower() for name in session.wsl().list_running()} if others else set()
    held = {i.distro: (ports.env_port(session, i) if i.distro.lower() in running else None) or i.port for i in others}
    reserved = frozenset(held.values())
    while True:
        install = next((i for i in others if held[i.distro] == port), None)
        owner = winapi.port_owner(port)
        if install is None and owner is None:
            return port
        free = next_free_port(winapi, port, reserved)
        who = f"the port of the sandbox {install.distro} (it may be stopped)" if install else _owner_text(owner)
        verb = "is" if install else "is used by"
        if not _prompting(session):
            hint = f". Port {free} is free" if free is not None else ""
            raise AnswerMissing(f"Port {port} {verb} {who}. {fix}{hint}.", "--port")
        program = None if install else (owner.name or "that program")
        instead = f"Use port {free} instead (Recommended)" if free is not None else None
        keep = f"Keep {port}, I'll close {program} first" if program else None
        choices = [c for c in (instead, keep, "Other (type a port)") if c]
        picked = prompter.select(f"Port {port} {verb} {who}.", choices, default=choices[0], hint=PORT_HINT)
        if picked == instead and free is not None:
            port = free
        elif picked == keep:
            # No hint of its own: the choice that led here said it ("checked again").
            prompter.confirm(f"Close {program} now. Is port {port} free?", default=True)
        else:
            port = intake.parse_port(prompter.text("Port (1024-65535):", validate=_port_problem, hint=PORT_HINT))


def _port_problem(text: str) -> str | None:
    try:
        intake.parse_port(text)
    except InvalidAnswer as exc:
        return str(exc)
    return None


def _distro(session, answers: Answers) -> Answers:
    wsl = session.wsl()
    name = answers.distro
    if not wsl.exists(name):
        return answers
    prompter = session.deps.prompter
    taken = wsl.list_distros() + [i.distro for i in session.installs().all()]
    while name.lower() in {t.lower() for t in taken}:
        first = intake.next_free_name(intake.DEFAULT_DISTRO, taken)
        second = intake.next_free_name(intake.ALT_DISTRO, taken)
        free = [first] if first == second else [first, second]
        problem = f"A distro named {name} already exists, and install never touches an existing distro."
        if not _prompting(session):
            raise AnswerMissing(
                f"{problem} Re-run with `--distro <name>`, {' and '.join(free)} "
                f"{'is' if len(free) == 1 else 'are'} free.",
                "--distro",
            )
        choices = [f"{first} (Recommended)", *free[1:], "Other (type a name)"]
        picked = prompter.select(f"{problem} Choose another name.", choices, default=choices[0], hint=NAME_HINT)
        if picked.startswith("Other"):
            name = intake.parse_distro(
                prompter.text("Sandbox name:", validate=lambda t: _name_problem(t, taken), hint=NAME_HINT)
            )
        else:
            name = free[choices.index(picked)]
    return _renamed(answers, name)


def _name_problem(text: str, taken: list[str]) -> str | None:
    try:
        name = intake.parse_distro(text)
    except InvalidAnswer as exc:
        return str(exc)
    if name.lower() in {t.lower() for t in taken}:
        return f"A distro named {name} already exists. Choose a new name."
    return None


def _renamed(answers: Answers, name: str) -> Answers:
    """The answers for a different distro name. A disk location derived from the old name
    (`D:\\WSL\\<name>`) follows it."""
    location = answers.location
    if location and PureWindowsPath(location).name.lower() == answers.distro.lower():
        location = str(PureWindowsPath(location).with_name(name))
    return replace(answers, distro=name, location=location)


def run(session, answers: Answers) -> Answers:
    """Check the answers against this machine. Returns the answers, with a different port or
    name when the user chose one. Raises AnswerMissing (exit 9) when it can't ask."""
    answers = _distro(session, answers)
    answers = replace(answers, port=check_port(session, answers.port, other_than=answers.distro))
    if docker_desktop_installed(session.deps.environ):
        session.out.warn(
            f"Docker Desktop is installed. Keep its WSL integration off for {answers.distro} "
            "(Docker Desktop > Settings > Resources > WSL integration), or it will take over "
            "this distro's Docker."
        )
    return answers


def docker_desktop_installed(environ) -> bool:
    program_files = environ.get("ProgramFiles") or r"C:\Program Files"
    return (Path(program_files) / "Docker" / "Docker" / "Docker Desktop.exe").is_file()
