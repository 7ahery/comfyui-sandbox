"""ctypes calls behind WindowsApi's shell methods: known folders, .lnk files, package identity.

Kept apart from winapi.py so each ticket's Win32 code lives in its own place. Windows only;
imported lazily by WindowsApi.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
from ctypes import wintypes
from pathlib import PureWindowsPath

from comfyui_sandbox.errors import CliError

FOLDER_IDS = {
    "desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "programs": "{A77F5D77-2E2B-44C3-A6A2-ABA601054A51}",
}
CLSID_SHELL_LINK = "{00021401-0000-0000-C000-000000000046}"
IID_SHELL_LINK_W = "{000214F9-0000-0000-C000-000000000046}"
IID_PERSIST_FILE = "{0000010B-0000-0000-C000-000000000046}"

# Slots in the IShellLinkW and IPersistFile vtables (after IUnknown's three).
QUERY_INTERFACE = 0
SET_DESCRIPTION, SET_WORKING_DIRECTORY, SET_ARGUMENTS, SET_ICON_LOCATION, SET_PATH = 7, 9, 11, 17, 20
PERSIST_SAVE = 6


class Guid(ctypes.Structure):
    _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]


def _guid(text: str) -> Guid:
    ole32 = ctypes.OleDLL("ole32")
    ole32.IIDFromString.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(Guid)]
    guid = Guid()
    ole32.IIDFromString(text, ctypes.byref(guid))
    return guid


def special_folder(name: str) -> PureWindowsPath:
    """A known folder of the current user (SHGetKnownFolderPath)."""
    shell32 = ctypes.WinDLL("shell32")
    ole32 = ctypes.WinDLL("ole32")
    shell32.SHGetKnownFolderPath.argtypes = [
        ctypes.POINTER(Guid),
        wintypes.DWORD,
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    shell32.SHGetKnownFolderPath.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    buffer = ctypes.c_void_p()
    hr = shell32.SHGetKnownFolderPath(ctypes.byref(_guid(FOLDER_IDS[name])), 0, None, ctypes.byref(buffer))
    try:
        if hr != 0:
            raise CliError(f"Could not find the {name} folder (HRESULT {hr & 0xFFFFFFFF:#010x}).")
        assert buffer.value is not None  # the call succeeded, so it wrote the string's address
        return PureWindowsPath(ctypes.wstring_at(buffer.value))
    finally:
        ole32.CoTaskMemFree(buffer)


def _slots(obj: ctypes.c_void_p):
    return ctypes.cast(ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))


def _call(obj: ctypes.c_void_p, slot: int, what: str, *typed_args) -> None:
    """Call a COM method; `typed_args` are (ctypes type, value) pairs. A failed HRESULT is a CliError."""
    function = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *[t for t, _ in typed_args])(_slots(obj)[slot])
    hr = function(obj, *[v for _, v in typed_args])
    if hr < 0:
        raise CliError(f"Could not create the shortcut ({what} failed: COM error {hr & 0xFFFFFFFF:#010x}).")


def _release(obj: ctypes.c_void_p) -> None:
    ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(_slots(obj)[2])(obj)


def create_shortcut(
    path: PureWindowsPath, target: str, arguments: str, working_dir: str, description: str, icon: str
) -> None:
    """Write a .lnk through IShellLinkW and IPersistFile."""
    ole32 = ctypes.OleDLL("ole32")
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoCreateInstance.argtypes = [
        ctypes.POINTER(Guid),
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Guid),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    coinit_apartmentthreaded, clsctx_inproc_server = 0x2, 0x1
    # S_FALSE (already initialized) and RPC_E_CHANGED_MODE both leave COM usable on this thread.
    with contextlib.suppress(OSError):
        ole32.CoInitializeEx(None, coinit_apartmentthreaded)
    link = ctypes.c_void_p()
    ole32.CoCreateInstance(
        ctypes.byref(_guid(CLSID_SHELL_LINK)),
        None,
        clsctx_inproc_server,
        ctypes.byref(_guid(IID_SHELL_LINK_W)),
        ctypes.byref(link),
    )
    persist = ctypes.c_void_p()
    text = wintypes.LPCWSTR
    try:
        _call(link, SET_PATH, "SetPath", (text, target))
        if arguments:
            _call(link, SET_ARGUMENTS, "SetArguments", (text, arguments))
        if working_dir:
            _call(link, SET_WORKING_DIRECTORY, "SetWorkingDirectory", (text, working_dir))
        if description:
            _call(link, SET_DESCRIPTION, "SetDescription", (text, description))
        if icon:
            file, _, index = icon.rpartition(",")
            if not file or not index.strip().lstrip("-").isdigit():
                file, index = icon, "0"
            _call(link, SET_ICON_LOCATION, "SetIconLocation", (text, file), (ctypes.c_int, int(index)))
        iid_persist = _guid(IID_PERSIST_FILE)
        _call(
            link,
            QUERY_INTERFACE,
            "QueryInterface",
            (ctypes.POINTER(Guid), ctypes.pointer(iid_persist)),
            (ctypes.POINTER(ctypes.c_void_p), ctypes.pointer(persist)),
        )
        os.makedirs(os.path.dirname(str(path)), exist_ok=True)
        _call(persist, PERSIST_SAVE, "Save", (text, str(path)), (wintypes.BOOL, True))
    finally:
        if persist:
            _release(persist)
        _release(link)


def is_packaged_app() -> bool:
    """True when the process has a package identity (running inside a packaged MSIX app)."""
    function = ctypes.WinDLL("kernel32").GetCurrentPackageFullName
    function.argtypes = [ctypes.POINTER(wintypes.UINT), wintypes.LPWSTR]
    function.restype = ctypes.c_long
    error_insufficient_buffer, appmodel_error_no_package = 122, 15700
    result = function(ctypes.byref(wintypes.UINT(0)), None)
    return result != appmodel_error_no_package and result in (0, error_insufficient_buffer)
