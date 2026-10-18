"""The Win32 boundary (ctypes, no pywin32: docs/adr/0016).

Every Win32 call goes through a WinApi, injected through `Deps` (docs/adr/0013). Tests use
`tests/fakes.py: FakeWinapi`; a few `@pytest.mark.windows_only` tests exercise the real WindowsApi.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import PureWindowsPath
from typing import Literal, Protocol

from comfyui_sandbox.errors import CliError

Folder = Literal["desktop", "programs"]


class ElevationDeclined(CliError):
    def __init__(self, message: str = "Administrator permission was declined (UAC).") -> None:
        super().__init__(message)


@dataclass(frozen=True)
class PortOwner:
    pid: int
    name: str


@dataclass(frozen=True)
class Shortcut:
    path: PureWindowsPath
    target: str
    arguments: str = ""
    working_dir: str = ""
    description: str = ""
    icon: str = ""


class InstanceLock(Protocol):
    def release(self) -> None: ...


class WinApi(Protocol):
    def kill_with_parent(self, pid: int) -> None: ...
    def open_console(self, argv: Sequence[str]) -> int: ...
    def run_elevated(self, argv: Sequence[str]) -> int: ...
    def special_folder(self, name: Folder) -> PureWindowsPath: ...
    def create_shortcut(self, shortcut: Shortcut) -> None: ...
    def remove_shortcut(self, path: PureWindowsPath) -> bool: ...
    def is_packaged_app(self) -> bool: ...
    def port_owner(self, port: int) -> PortOwner | None: ...
    def acquire_instance(self, name: str) -> InstanceLock | None: ...
    def instance_exists(self, name: str) -> bool: ...
    def open_url(self, url: str) -> None: ...
    def on_console_close(self, callback: Callable[[], None]) -> None: ...
    def owns_console(self) -> bool: ...
    def wait_for_key(self) -> None: ...


class _Mutex:
    """A held named mutex: the run-window marker. Released by `release()` or when the process ends."""

    def __init__(self, kernel32, handle) -> None:
        self._kernel32, self._handle = kernel32, handle

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle:
            self._kernel32.CloseHandle(handle)


class WindowsApi:
    """The real implementation. Every method of the WinApi protocol is implemented."""

    def __init__(self) -> None:
        self._jobs: list[int] = []  # Job Object handles held for the life of the process
        self._handlers: list[object] = []  # console control handlers (must not be garbage collected)

    def kill_with_parent(self, pid: int) -> None:
        """Bind process `pid` to a Job Object that kills it when the job's last handle closes.

        This process holds the only handle (kept in `self._jobs`), so the child dies when the
        CLI exits, also on a crash or a kill: Windows closes the handle then.
        """
        import ctypes
        from ctypes import wintypes

        job_object_extended_limit_information = 9
        kill_on_job_close = 0x2000
        process_set_quota, process_terminate = 0x0100, 0x0001

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_uint64)
                for name in (
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                )
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        def fail(what: str) -> CliError:
            return CliError(
                f"Could not bind process {pid} to a Job Object ({what}): {ctypes.FormatError(ctypes.get_last_error())}"
            )

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise fail("CreateJobObject")
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = kill_on_job_close
        if not kernel32.SetInformationJobObject(
            job, job_object_extended_limit_information, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            error = fail("SetInformationJobObject")
            kernel32.CloseHandle(job)
            raise error
        process = kernel32.OpenProcess(process_set_quota | process_terminate, False, pid)
        if not process:
            error = fail("OpenProcess")
            kernel32.CloseHandle(job)
            raise error
        try:
            if not kernel32.AssignProcessToJobObject(job, process):
                error = fail("AssignProcessToJobObject")
                kernel32.CloseHandle(job)
                raise error
        finally:
            kernel32.CloseHandle(process)
        self._jobs.append(job)  # never closed: closing it is what kills the child

    def open_console(self, argv: Sequence[str]) -> int:
        """Start argv in a new console window of its own and return its PID without waiting.

        CREATE_NEW_CONSOLE gives it a console that is not the caller's, so closing the calling
        terminal (or ending this process) does not touch it; CREATE_NEW_PROCESS_GROUP keeps a
        Ctrl+C in the caller from reaching it. No std handles are passed or inherited, so the child's
        are the new console's: `run` needs them to be a console to open the run window there (NUL
        would pass its isatty() check too, and the window would stay blank).
        """
        import subprocess

        create_new_console, create_new_process_group = 0x00000010, 0x00000200
        try:
            process = subprocess.Popen(
                list(argv), creationflags=create_new_console | create_new_process_group, close_fds=True
            )
        except OSError as error:
            raise CliError(f"Could not open a window for {argv[0]}: {error}") from error
        pid = process.pid
        # We never wait for it, so don't leak the handle. Detach (not Close) it: Close leaves Popen
        # believing the child is alive, and its __del__ / subprocess._cleanup then poll a dead handle
        # and raise "[WinError 6] The handle is invalid".
        import ctypes
        from ctypes import wintypes

        handle = process._handle.Detach()  # ty: ignore[unresolved-attribute]  # private to Popen on Windows, where it is a handle object
        process.returncode = 0  # nothing left to poll; keeps Popen.__del__ / _cleanup quiet
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle(handle)
        return pid

    def run_elevated(self, argv: Sequence[str]) -> int:
        """Start argv as administrator (UAC prompt), wait for it, return its exit code.

        ShellExecuteExW with the "runas" verb; declining the prompt fails at once with
        ERROR_CANCELLED, so it never hangs. The elevated process gets its own console window.
        """
        import ctypes
        import subprocess
        from ctypes import wintypes

        class ShellExecuteInfo(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("fMask", ctypes.c_ulong),
                ("hwnd", wintypes.HWND),
                ("lpVerb", wintypes.LPCWSTR),
                ("lpFile", wintypes.LPCWSTR),
                ("lpParameters", wintypes.LPCWSTR),
                ("lpDirectory", wintypes.LPCWSTR),
                ("nShow", ctypes.c_int),
                ("hInstApp", wintypes.HINSTANCE),
                ("lpIDList", ctypes.c_void_p),
                ("lpClass", wintypes.LPCWSTR),
                ("hkeyClass", wintypes.HKEY),
                ("dwHotKey", wintypes.DWORD),
                ("hIconOrMonitor", wintypes.HANDLE),
                ("hProcess", wintypes.HANDLE),
            ]

        see_mask_nocloseprocess, see_mask_noasync, sw_shownormal = 0x40, 0x100, 1
        error_cancelled, infinite = 1223, 0xFFFFFFFF
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
        shell32.ShellExecuteExW.restype = wintypes.BOOL
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        info = ShellExecuteInfo(
            cbSize=ctypes.sizeof(ShellExecuteInfo),
            fMask=see_mask_nocloseprocess | see_mask_noasync,
            lpVerb="runas",
            lpFile=argv[0],
            lpParameters=subprocess.list2cmdline(list(argv[1:])),
            nShow=sw_shownormal,
        )
        if not shell32.ShellExecuteExW(ctypes.byref(info)):
            error = ctypes.get_last_error()
            if error == error_cancelled:
                raise ElevationDeclined()
            raise CliError(f"Could not start {argv[0]} as administrator: {ctypes.FormatError(error)}")
        if not info.hProcess:
            raise CliError(f"Could not start {argv[0]} as administrator: no process handle.")
        try:
            kernel32.WaitForSingleObject(info.hProcess, infinite)
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code)):
                raise CliError(
                    f"Could not read the exit code of {argv[0]}: {ctypes.FormatError(ctypes.get_last_error())}"
                )
            return code.value
        finally:
            kernel32.CloseHandle(info.hProcess)

    def special_folder(self, name: Folder) -> PureWindowsPath:
        from comfyui_sandbox import shell_api

        return shell_api.special_folder(name)

    def create_shortcut(self, shortcut: Shortcut) -> None:
        """Write a .lnk (IShellLink/COM), replacing one that exists."""
        from comfyui_sandbox import shell_api

        shell_api.create_shortcut(
            shortcut.path,
            shortcut.target,
            shortcut.arguments,
            shortcut.working_dir,
            shortcut.description,
            shortcut.icon,
        )

    def remove_shortcut(self, path: PureWindowsPath) -> bool:
        """Delete a .lnk; False when it wasn't there."""
        import os

        try:
            os.remove(str(path))
        except FileNotFoundError:
            return False
        return True

    def is_packaged_app(self) -> bool:
        """Running inside a packaged (MSIX) app (GetCurrentPackageFullName)."""
        from comfyui_sandbox import shell_api

        return shell_api.is_packaged_app()

    def port_owner(self, port: int) -> PortOwner | None:
        """The process listening on TCP `port` on this machine's loopback or any address, or None.

        Reads the IP Helper listener tables (IPv4 and IPv6, with owning PIDs) through
        GetExtendedTcpTable, so no `netstat` or PowerShell is involved. Only listeners that would
        collide with binding 127.0.0.1:<port> count: 0.0.0.0, 127.x.x.x, `::` and `::1`.
        """
        import ctypes
        import socket
        from ctypes import wintypes

        af_inet, af_inet6, listener_table, insufficient_buffer = socket.AF_INET, socket.AF_INET6, 3, 122
        iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
        iphlpapi.GetExtendedTcpTable.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.BOOL,
            wintypes.ULONG,
            ctypes.c_int,
            wintypes.ULONG,
        ]
        iphlpapi.GetExtendedTcpTable.restype = wintypes.DWORD

        class Row4(ctypes.Structure):
            _fields_ = [
                ("state", wintypes.DWORD),
                ("local_addr", wintypes.DWORD),
                ("local_port", wintypes.DWORD),
                ("remote_addr", wintypes.DWORD),
                ("remote_port", wintypes.DWORD),
                ("pid", wintypes.DWORD),
            ]

        class Row6(ctypes.Structure):
            _fields_ = [
                ("local_addr", ctypes.c_ubyte * 16),
                ("local_scope", wintypes.DWORD),
                ("local_port", wintypes.DWORD),
                ("remote_addr", ctypes.c_ubyte * 16),
                ("remote_scope", wintypes.DWORD),
                ("remote_port", wintypes.DWORD),
                ("state", wintypes.DWORD),
                ("pid", wintypes.DWORD),
            ]

        def rows(family: int, row_type) -> list:
            size = wintypes.DWORD(0)
            buffer = None
            for _ in range(5):  # the table can grow between the size query and the read
                status = iphlpapi.GetExtendedTcpTable(buffer, ctypes.byref(size), False, family, listener_table, 0)
                if status == 0:
                    break
                if status != insufficient_buffer:
                    raise CliError(f"Could not read the TCP listener table (error {status}).")
                buffer = ctypes.create_string_buffer(size.value)
            else:
                raise CliError("Could not read the TCP listener table: it kept changing.")
            if buffer is None:
                return []
            count = ctypes.c_ulong.from_buffer(buffer).value
            table = (row_type * count).from_buffer(buffer, ctypes.sizeof(ctypes.c_ulong))
            return list(table)

        def network_port(value: int) -> int:
            return ((value & 0xFF) << 8) | ((value >> 8) & 0xFF)

        pid = None
        for row in rows(af_inet, Row4):
            first_octet = row.local_addr & 0xFF  # network order read little-endian: first octet is the low byte
            if network_port(row.local_port) == port and (row.local_addr == 0 or first_octet == 127):
                pid = row.pid
                break
        if pid is None:
            for row in rows(af_inet6, Row6):
                address = bytes(row.local_addr)
                if network_port(row.local_port) == port and address in (bytes(16), bytes(15) + b"\x01"):
                    pid = row.pid
                    break
        if pid is None:
            return None
        return PortOwner(pid, self._process_name(pid))

    @staticmethod
    def _process_name(pid: int) -> str:
        """The program name (`python.exe`) of a process, or "" when Windows won't say."""
        import ctypes
        from ctypes import wintypes

        if pid == 4:
            return "System"
        query_limited_information = 0x1000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(query_limited_information, False, pid)
        if not handle:
            return ""  # e.g. a protected system process
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return ""
            return PureWindowsPath(buffer.value).name
        finally:
            kernel32.CloseHandle(handle)

    @staticmethod
    def _mutex_kernel32():
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.OpenMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        return kernel32

    def acquire_instance(self, name: str) -> InstanceLock | None:
        """Take the run-window marker `name` (a named mutex in the user's session).

        Returns the lock, or None when another process (or this one) already holds it. Windows
        frees it when the holder ends, so a crashed run window never leaves a stale marker.
        """
        import ctypes

        already_exists = 183
        kernel32 = self._mutex_kernel32()
        handle = kernel32.CreateMutexW(None, False, "Local\\" + name)
        if not handle:
            raise CliError(
                f"Could not create the run-window marker {name}: {ctypes.FormatError(ctypes.get_last_error())}"
            )
        if ctypes.get_last_error() == already_exists:
            kernel32.CloseHandle(handle)
            return None
        return _Mutex(kernel32, handle)

    def instance_exists(self, name: str) -> bool:
        """Is some process holding the marker `name`? (Looks only; takes nothing.)"""
        synchronize = 0x00100000
        kernel32 = self._mutex_kernel32()
        handle = kernel32.OpenMutexW(synchronize, False, "Local\\" + name)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True

    def open_url(self, url: str) -> None:
        """Open `url` in the default browser."""
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"not a web address: {url}")
        import os

        os.startfile(url)  # type: ignore[attr-defined]  # Windows only

    def on_console_close(self, callback: Callable[[], None]) -> None:
        """Call `callback` (and wait for it) when the console window is closed, or on logoff or
        shutdown: Windows gives the process a few seconds, then ends it. Ctrl+C still arrives
        as KeyboardInterrupt."""
        import ctypes
        from ctypes import wintypes

        close_events = (2, 5, 6)  # CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
        def handler(event: int) -> bool:
            if event not in close_events:
                return False
            with contextlib.suppress(Exception):  # nothing may escape into Windows
                callback()
            return True

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.SetConsoleCtrlHandler.argtypes = [ctypes.c_void_p, wintypes.BOOL]
        kernel32.SetConsoleCtrlHandler.restype = wintypes.BOOL
        if not kernel32.SetConsoleCtrlHandler(handler, True):
            raise CliError(
                f"Could not watch for the console window closing: {ctypes.FormatError(ctypes.get_last_error())}"
            )
        self._handlers.append(handler)  # must outlive the process's use of it

    def owns_console(self) -> bool:
        """Is this process the only program in its console window, i.e. was the window opened for it
        (a shortcut, `start`) rather than being a terminal the user already had open?

        True when every other process attached to the console is part of our own launch chain
        (python, or the `comfyui-sandbox.exe` launcher in front of it). A shell, or anything else
        in the console, means the user's own terminal.
        """
        import ctypes
        import os

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetConsoleProcessList.argtypes = [ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32]
        kernel32.GetConsoleProcessList.restype = ctypes.c_uint32
        buffer = (ctypes.c_uint32 * 64)()
        count = kernel32.GetConsoleProcessList(buffer, len(buffer))
        if count == 0 or count > len(buffer):
            return False  # no console, or an implausibly crowded one: not ours
        ours = {"python.exe", "pythonw.exe", "comfyui-sandbox.exe", self._process_name(os.getpid()).lower()}
        others = [pid for pid in buffer[:count] if pid != os.getpid()]
        return all(self._process_name(pid).lower() in ours for pid in others)

    def wait_for_key(self) -> None:
        """Block until a key is pressed in the console."""
        import msvcrt

        msvcrt.getwch()  # type: ignore[attr-defined]  # Windows only; reads the console, not stdin
