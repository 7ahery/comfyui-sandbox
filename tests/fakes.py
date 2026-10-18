"""Fakes for the two process boundaries: Runner and WinApi (docs/adr/0013)."""

from __future__ import annotations

import copy
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import PureWindowsPath

from comfyui_sandbox.cancel import CancelSwitch
from comfyui_sandbox.instance import instance_name
from comfyui_sandbox.net import HttpReply
from comfyui_sandbox.runner import Completed
from comfyui_sandbox.winapi import ElevationDeclined, InstanceLock, PortOwner, Shortcut


@dataclass(frozen=True)
class Call:
    argv: tuple[str, ...]
    stdin: bytes | None
    env: dict[str, str]
    timeout: float | None
    streamed: bool


def _bytes(value: str | bytes) -> bytes:
    return value.encode("utf-8") if isinstance(value, str) else value


@dataclass
class _Script:
    match: Callable[[tuple[str, ...], bytes | None], bool]
    returncode: int
    stdout: bytes
    stderr: bytes
    then: Callable[[], None] | None = None  # runs when the script is matched (may raise)
    answer: Callable[[tuple[str, ...]], tuple[int, bytes]] | None = None  # (returncode, stdout) worked out per call


class FakeProcess:
    """A process started with `Runner.start`: pid, whether it was ended, and how many
    calls had been made by then (`stopped_after`, None while it runs)."""

    def __init__(self, runner: FakeRunner, call: Call, pid: int, exited: int | None = None) -> None:
        self._runner, self.call, self.pid = runner, call, pid
        self.stopped_after: int | None = None
        self.exited = exited  # the exit code it ended with on its own (a followed one scripted to fail)

    @property
    def terminated(self) -> bool:
        return self.stopped_after is not None

    def poll(self) -> int | None:
        if self.exited is not None:
            return self.exited
        return None if self.stopped_after is None else -15

    def terminate(self) -> None:
        if self.stopped_after is None:
            self.stopped_after = len(self._runner.calls)


class FakeChild:
    """The child of a `run` or `stream` call made under a cancel switch: what a flip terminates."""

    pid = 0

    def __init__(self) -> None:
        self.ended = threading.Event()

    def poll(self) -> int | None:
        return -15 if self.ended.is_set() else None

    def terminate(self) -> None:
        self.ended.set()


class FakeRunner:
    """Records every call and returns scripted results.

    Script a result with `on(*prefix, returncode=, stdout=, stderr=)`: the latest
    script whose argv prefix matches wins. Unscripted calls succeed with no output.

    `cancellable(switch)` plays the real runner's cancel switch: it's this runner (the same calls,
    scripts and processes) with `run` and `stream` calls the switch stops. A flip during a call
    terminates its child (recorded in `terminated`), and the call then raises KeyboardInterrupt;
    script `then=runner.until_terminated` for a call that runs until it's cancelled.
    """

    def __init__(self) -> None:
        self.calls: list[Call] = []
        self._scripts: list[_Script] = []
        self.started: list[FakeProcess] = []
        self.terminated: list[Call] = []  # calls whose child a cancel switch terminated
        self._switch: CancelSwitch | None = None
        self._current = threading.local()  # the child of the call running on each thread

    def cancellable(self, switch: CancelSwitch) -> FakeRunner:
        view = copy.copy(self)  # shares the lists: one runner, seen with and without the switch
        view._switch = switch
        return view

    def until_terminated(self, timeout: float = 30) -> None:
        """A `then=` for a call that runs until a cancel terminates it, like a long wsl.exe step."""
        child = getattr(self._current, "child", None)
        assert child is not None, "until_terminated needs a call made under a cancel switch"
        assert child.ended.wait(timeout), "the call was never cancelled"

    def _cancellable(self, call: Callable[[], Completed]) -> Completed:
        """call() made as the real runner makes it under a switch: checked before it starts, its
        child terminated by a flip meanwhile, and the interruption raised once it has ended."""
        if self._switch is None:
            return call()
        self._switch.check()
        child = FakeChild()
        self._current.child = child
        try:
            with self._switch.running(child):
                result = call()
        finally:
            self._current.child = None
            if child.ended.is_set():
                self.terminated.append(self._current.call)
        self._switch.check()
        return result

    def on(
        self,
        *prefix: str,
        returncode: int = 0,
        stdout: str | bytes = b"",
        stderr: str | bytes = b"",
        then: Callable[[], None] | None = None,
    ) -> None:
        """`then` runs when the script is matched, before it answers (may raise, e.g. KeyboardInterrupt)."""
        n = len(prefix)
        self._scripts.append(
            _Script(lambda argv, stdin: argv[:n] == prefix, returncode, _bytes(stdout), _bytes(stderr), then)
        )

    def on_script(
        self,
        name: str,
        *,
        returncode: int = 0,
        stdout: str | bytes = b"",
        stderr: str | bytes = b"",
        then: Callable[[], None] | None = None,
    ) -> None:
        """Script the result of in-distro package script `name` (e.g. "wait_ready.sh"),
        wherever and as whoever it runs. Latest wins, like `on`. `then` runs when the script
        is called, before it answers: use it to look at the world while a long script
        "runs", or to raise KeyboardInterrupt (Ctrl+C)."""
        marker = f"# comfyui-sandbox: {name}\n".encode()
        self._scripts.append(
            _Script(
                lambda argv, stdin: stdin is not None and stdin.startswith(marker),
                returncode,
                _bytes(stdout),
                _bytes(stderr),
                then,
            )
        )

    def on_match(
        self, match: Callable[[tuple[str, ...]], bool], answer: Callable[[tuple[str, ...]], tuple[int, str]]
    ) -> None:
        """Answer every call `match` accepts with `answer(argv) -> (returncode, stdout)`, worked out when
        the call is made (for a fake that has state). Latest wins, like `on`."""
        self._scripts.append(
            _Script(
                lambda argv, stdin: match(argv),
                0,
                b"",
                b"",
                answer=lambda argv: (lambda rc, out: (rc, _bytes(out)))(*answer(argv)),
            )
        )

    def _respond(self, argv, stdin, env, timeout, streamed) -> Completed:
        argv = tuple(argv)
        call = Call(argv, stdin, dict(env or {}), timeout, streamed)
        self.calls.append(call)
        self._current.call = call  # for a cancel switch to name, whatever other threads call meanwhile
        for script in reversed(self._scripts):
            if script.match(argv, stdin):
                if script.then is not None:
                    script.then()
                if script.answer is not None:
                    code, out = script.answer(argv)
                    return Completed(argv, code, out, b"")
                return Completed(argv, script.returncode, script.stdout, script.stderr)
        if "--exec" in argv and argv[-2:] in (("id", "-u"), ("id", "-g")):
            return Completed(argv, 0, b"1000\n", b"")  # the fake distro's user ids, unless scripted
        return Completed(argv, 0, b"", b"")

    def run(
        self,
        argv: Sequence[str],
        *,
        stdin: bytes | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> Completed:
        return self._cancellable(lambda: self._respond(argv, stdin, env, timeout, streamed=False))

    def start(self, argv: Sequence[str], *, env: Mapping[str, str] | None = None) -> FakeProcess:
        call = Call(tuple(argv), None, dict(env or {}), None, streamed=False)
        self.calls.append(call)
        process = FakeProcess(self, call, 7000 + len(self.started))
        self.started.append(process)
        return process

    def stream(
        self,
        argv: Sequence[str],
        on_line: Callable[[str], None],
        *,
        stdin: bytes | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> Completed:
        def call() -> Completed:
            result = self._respond(argv, stdin, env, timeout, streamed=True)
            merged = result.stdout + result.stderr
            for line in _lines(merged):
                on_line(line)
            return Completed(result.argv, result.returncode, merged, b"")

        return self._cancellable(call)

    def follow(
        self, argv: Sequence[str], on_line: Callable[[str], None], *, env: Mapping[str, str] | None = None
    ) -> FakeProcess:
        """A long-running process: its scripted stdout is replayed through on_line before this returns
        (as if those lines had all arrived), and then it keeps running, saying nothing more, until it's
        terminated. A script with a non-zero returncode plays one that ended on its own. It's recorded
        in `calls` (streamed) and in `started`."""
        result = self._respond(argv, None, env, None, streamed=True)
        for line in _lines(result.stdout):
            on_line(line)
        process = FakeProcess(self, self.calls[-1], 7000 + len(self.started), result.returncode or None)
        self.started.append(process)
        return process


def _lines(data: bytes) -> list[str]:
    """data's lines as the real runner decodes them (runner.clean)."""
    return data.decode("utf-8", "replace").replace("\0", "").replace("\r", "").splitlines()


class _FakeLock:
    def __init__(self, owner: FakeWinapi, name: str) -> None:
        self._owner, self.name = owner, name

    def release(self) -> None:
        self._owner.instances.discard(self.name)


@dataclass
class FakeWinapi:
    """In-memory stand-in for the ctypes layer. Set the attributes to script it."""

    packaged_app: bool = False
    elevation_declined: bool = False
    elevated_returncode: int = 0
    port_owners: dict[int, PortOwner] = field(default_factory=dict)
    folders: dict[str, PureWindowsPath] = field(
        default_factory=lambda: {
            "desktop": PureWindowsPath(r"C:\Users\user\Desktop"),
            "programs": PureWindowsPath(r"C:\Users\user\AppData\Roaming\Microsoft\Windows\Start Menu\Programs"),
        }
    )
    shortcuts: dict[PureWindowsPath, Shortcut] = field(default_factory=dict)
    instances: set[str] = field(default_factory=set)
    job_bound: list[int] = field(default_factory=list)
    consoles: list[tuple[str, ...]] = field(default_factory=list)
    elevated: list[tuple[str, ...]] = field(default_factory=list)
    window_dies: bool = False  # a run window opened by open_console ends at once (never holds the marker)
    next_pid: int = 4000
    opened_urls: list[str] = field(default_factory=list)
    close_handlers: list[Callable[[], None]] = field(default_factory=list)
    own_console: bool = False  # this process owns its console (a run window), not a terminal the user had open
    key_waits: int = 0  # how many times a command paused for a keypress

    def owns_console(self) -> bool:
        return self.own_console

    def wait_for_key(self) -> None:
        self.key_waits += 1

    def open_url(self, url: str) -> None:
        self.opened_urls.append(url)

    def on_console_close(self, callback: Callable[[], None]) -> None:
        self.close_handlers.append(callback)

    def close_console(self) -> None:
        """Play the user closing the console window: Windows calls the handlers."""
        for callback in self.close_handlers:
            callback()

    def kill_with_parent(self, pid: int) -> None:
        self.job_bound.append(pid)

    def open_console(self, argv: Sequence[str]) -> int:
        """Open a run window: it takes the run-window marker, unless `window_dies` (it ended at once)."""
        self.consoles.append(tuple(argv))
        if "--distro" in argv and not self.window_dies:
            self.instances.add(instance_name(argv[list(argv).index("--distro") + 1]))
        self.next_pid += 1
        return self.next_pid

    def run_elevated(self, argv: Sequence[str]) -> int:
        self.elevated.append(tuple(argv))
        if self.elevation_declined:
            raise ElevationDeclined()
        return self.elevated_returncode

    def special_folder(self, name: str) -> PureWindowsPath:
        return self.folders[name]

    def create_shortcut(self, shortcut: Shortcut) -> None:
        self.shortcuts[shortcut.path] = shortcut

    def remove_shortcut(self, path: PureWindowsPath) -> bool:
        return self.shortcuts.pop(path, None) is not None

    def is_packaged_app(self) -> bool:
        return self.packaged_app

    def port_owner(self, port: int) -> PortOwner | None:
        return self.port_owners.get(port)

    def acquire_instance(self, name: str) -> InstanceLock | None:
        if name in self.instances:
            return None
        self.instances.add(name)
        return _FakeLock(self, name)

    def instance_exists(self, name: str) -> bool:
        return name in self.instances


CANCEL = object()  # an answer that cancels the question, as Ctrl+C at a terminal or Esc in the TUI does


@dataclass
class FakePrompter:
    """Scripted terminal answers. Queue answers in `answers` (str for select/text, bool for
    confirm, or CANCEL to back out of the question); `asked` records each select as (message,
    choices), `questions` every question of any kind as (kind, message), and `hints` every
    question as (message, its hint, its choices' hints).

    A select answer is a choice, or the start of exactly one choice ("Windows folder").
    A text answer that fails `validate` is recorded in `rejected` and the next answer is
    used, like a person typing again. CANCEL raises KeyboardInterrupt, the Prompter's cancel.
    """

    answers: list[str | bool | object] = field(default_factory=list)
    asked: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    questions: list[tuple[str, str]] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)
    defaults: list[tuple[str, str | None]] = field(default_factory=list)  # select (message, preselected choice)
    hints: list[tuple[str, str, dict[str, str]]] = field(default_factory=list)
    # Each type-the-name confirmation (a text question marked with type_to_confirm): (message, the names).
    type_to_confirm: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)

    def _next(self, message: str):
        assert self.answers, f"unexpected prompt: {message}"
        answer = self.answers.pop(0)
        if answer is CANCEL:
            raise KeyboardInterrupt
        return answer

    def select(
        self,
        message: str,
        choices: Sequence[str],
        default: str | None = None,
        *,
        hint: str = "",
        hints: Mapping[str, str] | None = None,
    ) -> str:
        self.asked.append((message, tuple(choices)))
        self.questions.append(("select", message))
        self.defaults.append((message, default))
        self.hints.append((message, hint, dict(hints or {})))
        answer = self._next(message)
        if answer in choices:
            return answer
        starts = [c for c in choices if c.startswith(str(answer))]
        assert len(starts) == 1, f"{answer!r} is not one of {choices}"
        return starts[0]

    def text(
        self,
        message: str,
        default: str = "",
        validate: Callable[[str], str | None] | None = None,
        *,
        hint: str = "",
        type_to_confirm: Sequence[str] = (),
    ) -> str:
        self.questions.append(("text", message))
        self.hints.append((message, hint, {}))
        if type_to_confirm:
            self.type_to_confirm.append((message, tuple(type_to_confirm)))
        while True:
            answer = self._next(message)
            assert isinstance(answer, str), f"{message}: expected text, got {answer!r}"
            problem = validate(answer) if validate else None
            if problem is None:
                return answer
            self.rejected.append((answer, problem))

    def confirm(self, message: str, default: bool = True, *, hint: str = "") -> bool:
        self.questions.append(("confirm", message))
        self.hints.append((message, hint, {}))
        answer = self._next(message)
        assert isinstance(answer, bool), f"{message}: expected yes/no, got {answer!r}"
        return answer


@dataclass
class FakeHttp:
    """What answers on Windows ports. By default the container does (`/system_stats` os linux);
    script a port with `answer(port, body)` or `refuse(port)`. `urls` records every GET."""

    replies: dict[int, str | None] = field(default_factory=dict)
    urls: list[str] = field(default_factory=list)

    def answer(self, port: int, body: str) -> None:
        self.replies[port] = body

    def refuse(self, port: int) -> None:
        self.replies[port] = None

    def get(self, url: str, *, timeout: float = 10) -> HttpReply:
        self.urls.append(url)
        port = int(url.split("://", 1)[1].split("/", 1)[0].rsplit(":", 1)[1])
        body = self.replies.get(port, '{"system": {"os": "linux"}}')
        if body is None:
            raise ConnectionRefusedError(f"nothing listens on {port}")
        return HttpReply(200, body)


class FakeContainer:
    """The ComfyUI container of the fake distro, with a state: `docker compose up/start/restart` start
    it, `stop/down` stop it, `ps --status running --quiet` answers from it, `deploy_comfyui.sh` ends with
    it up, and `systemctl restart docker` stops it. `running` is the state now; `history` lists the
    compose verbs run, in order."""

    def __init__(self, runner: FakeRunner, *, running: bool) -> None:
        self.running, self.history = running, []
        runner.on_match(self._is_compose, self._compose)
        runner.on_match(
            lambda argv: "systemctl" in argv and "restart" in argv and "docker" in argv, self._docker_restart
        )
        runner.on_script("deploy_comfyui.sh", then=self._up)

    @staticmethod
    def _is_compose(argv: tuple[str, ...]) -> bool:
        return "--exec" in argv and "compose" in argv and argv[argv.index("compose") - 1] == "docker"

    def _up(self) -> None:
        self.history.append("deploy")
        self.running = True

    def _docker_restart(self, argv) -> tuple[int, str]:
        self.history.append("docker-restart")
        self.running = False
        return 0, ""

    def _compose(self, argv) -> tuple[int, str]:
        verb = argv[argv.index("compose") + 1]
        if verb == "ps":
            return 0, "3f2a9c1d7e44\n" if self.running else ""
        self.history.append(verb)
        if verb in ("up", "start", "restart"):
            self.running = True
        elif verb in ("stop", "down"):
            self.running = False
        return 0, ""


@dataclass
class FakeLauncher:
    """The front-end launcher (`Deps.launch`): records each screen it was asked to open in
    `opened`, and the Deps it was handed for it in `deps`, and returns `exit_code`, or raises
    `crash` as a TUI that died would."""

    exit_code: int = 0
    crash: BaseException | None = None
    opened: list[object] = field(default_factory=list)
    deps: list[object] = field(default_factory=list)

    def __call__(self, screen: object, deps: object) -> int:
        self.opened.append(screen)
        self.deps.append(deps)
        if self.crash is not None:
            raise self.crash
        return self.exit_code
