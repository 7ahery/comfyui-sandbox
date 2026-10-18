"""Read-only questions about the sandboxes, named by what they mean, for a front end to poll.

Home's table asks these every few seconds, in every home that's open (the feat-tui-integration
spec, "Home"). `status` can't serve it: it's per sandbox, and it lists the running distros and
runs a script inside one, which would wake a distro on every poll. So each question here is
answered without `wsl.exe` and without running anything inside a distro, cheaply enough to ask
often.

One question is the exception, and is asked once, when a menu opens, never polled:
`distro_running` lists the running distros with `wsl.exe --list --running`, which starts nothing.

They're named by meaning ("is a run window open for it?", "does its own ComfyUI answer?") rather
than by mechanism (a named mutex, a reach check's `os` field), so a front end written against them
says nothing about Windows. How each is answered is this module's business, and a core for
another platform could answer the same questions its own way (ADR 0017).

Nothing here changes anything: no command line runs, and no file or folder is created that the
commands haven't made already.
"""

from __future__ import annotations

from dataclasses import dataclass

from comfyui_sandbox.deps import Deps
from comfyui_sandbox.instance import instance_name, stopping_name
from comfyui_sandbox.reach import check_windows_reach
from comfyui_sandbox.state import FILE, Install, Installs, locked
from comfyui_sandbox.wsl import Wsl, WslNotInstalled


def sandboxes(deps: Deps) -> list[Install]:
    """Every sandbox `installs.json` records, in its order; none when there's no file yet.

    Raises StateError when the file can't be read, as every command does: a front end that
    shows nothing would hide a broken file.
    """
    path = deps.app_dir / FILE
    if not path.is_file():
        return []  # and no lock file either: a poll mustn't create the CLI's folder
    # Under the file's lock, though only reading: on Windows a file that's open for reading can't
    # be replaced, so an unlocked read landing on another process's save() (an install, a
    # `config` in another terminal) would make that save fail. Several homes poll this file
    # every few seconds, so that's no longer a rare race. save() replaces it under the same lock.
    with locked(path):
        return Installs.load(deps.app_dir).all()


def install_finished(install: Install) -> bool:
    """Whether the sandbox's install ran to its end. One that didn't is resumed with `install`."""
    return install.state == "complete"


def run_window_open(deps: Deps, install: Install) -> bool:
    """Whether a run window is open for the sandbox: `run` holds it, in a window of its own or
    in home's Run here. On Windows, its run-window marker (docs/adr/0010), looked at without
    being taken."""
    return deps.winapi.instance_exists(instance_name(install.distro))


def run_window_stopping(deps: Deps, install: Install) -> bool:
    """Whether the sandbox's run window is stopping ComfyUI now (its cleanup is running: the
    window was closed, Ctrl+C was pressed, or the container ended). Looked at, never taken."""
    return deps.winapi.instance_exists(stopping_name(install.distro))


def distro_running(deps: Deps, distro: str) -> bool:
    """Whether the distro called `distro` is running now. Only lists the running distros: it never
    starts one, which is the point (anything run *in* a stopped distro boots it, and its Docker).
    False when WSL isn't installed. Not for polling; see the module's note."""
    wsl = Wsl(deps.runner, deps.environ)
    try:
        return any(name.lower() == distro.lower() for name in wsl.list_running())
    except WslNotInstalled:
        return False


# How long a poll waits for a reply. Much shorter than the reach check's default: home asks
# every few seconds, and a port that accepts but never answers (a stuck program) mustn't hold up
# the table for long. A ComfyUI that's up answers /system_stats in milliseconds.
ANSWER_TIMEOUT = 2.0


@dataclass(frozen=True)
class PortAnswer:
    """Whether the sandbox's own ComfyUI answers on its port, and if not, who holds the port.

    held_by names another program listening there, as the CLI's messages name it ("python.exe
    (PID 4242)"); None when its own ComfyUI answers, when nothing listens, when the program
    can't be named, or when what holds it is the sandbox's own way in (see own_comfyui).
    """

    answers: bool
    held_by: str | None = None


def own_comfyui(deps: Deps, install: Install) -> PortAnswer:
    """Does the sandbox's own ComfyUI answer on its port, and if not, who holds the port?

    Only the sandbox's own ComfyUI counts: on Windows, a reply whose `os` is linux or posix (the
    reach check, `reach.py`). A ComfyUI running on Windows, or any other program, on the same
    port doesn't, and is named instead. Asked of 127.0.0.1 from Windows, it never wakes the
    distro.

    What holds the port is named only when it's another program. WSL's own localhost relay
    isn't: it holds a port that something inside a distro listens on, and on a sandbox's own port
    that's the sandbox (its distro up, its ComfyUI stopping or not answering yet). Sandboxes'
    ports are kept apart (install and config check them against each other), and the relay is
    nothing anyone could close, so naming it would only make the sandbox look blocked by itself.
    """
    if deps.winapi.port_owner(install.port) is None:
        # Nothing listens: nothing can answer. A connect to a closed loopback port on Windows
        # takes the whole timeout to fail, which would hold up every read of a stopped sandbox.
        return PortAnswer(False)
    reach = check_windows_reach(deps, install.port, timeout=ANSWER_TIMEOUT)
    if reach.ok:
        return PortAnswer(True)
    return PortAnswer(False, None if reach.relayed else reach.holder)
