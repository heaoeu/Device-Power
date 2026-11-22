"""Small, dependency-free Windows helpers shared by the battery tool.

Only the standard library and ctypes are used.  No file logging happens in this
module (or anywhere else in the project); diagnostics go to stdout only when the
caller explicitly asks for them.
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from dataclasses import dataclass

if sys.platform != "win32":
    raise RuntimeError("This project only runs on Windows")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
ole32 = ctypes.WinDLL("ole32", use_last_error=True)

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
ERROR_ALREADY_EXISTS = 183
ERROR_FILE_NOT_FOUND = 2
ERROR_ACCESS_DENIED = 5
ERROR_INSUFFICIENT_BUFFER = 122
ERROR_NO_MORE_ITEMS = 259

FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_FLAG_OVERLAPPED = 0x40000000
ERROR_IO_PENDING = 997
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x102
WAIT_FAILED = 0xFFFFFFFF
INFINITE = 0xFFFFFFFF

# CREATE_NO_WINDOW is useful when we shell out to mklink in a pythonw process.
CREATE_NO_WINDOW = 0x08000000


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def guid_from_string(text: str) -> GUID:
    """Parse a GUID without losing the hyphens embedded in the last section."""
    parts = text.split("-", 3)
    if len(parts) != 4:
        raise ValueError(f"bad GUID: {text!r}")
    g = GUID()
    g.Data1 = int(parts[0], 16)
    g.Data2 = int(parts[1], 16)
    g.Data3 = int(parts[2], 16)
    tail = bytes.fromhex(parts[3].replace("-", ""))
    if len(tail) != 8:
        raise ValueError(f"bad GUID tail: {text!r}")
    for i, b in enumerate(tail):
        g.Data4[i] = b
    return g


FOLDERID_Desktop = guid_from_string("B4BFCC3A-DB2C-424C-B029-7FE99A87C641")
FOLDERID_Startup = guid_from_string("B97D20BB-F46A-4C97-BA10-5E3608430854")


class OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.ReadFile.restype = wintypes.BOOL
kernel32.ReadFile.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(OVERLAPPED),
]
kernel32.WriteFile.restype = wintypes.BOOL
kernel32.WriteFile.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(OVERLAPPED),
]
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.CreateEventW.argtypes = [
    ctypes.c_void_p,
    wintypes.BOOL,
    wintypes.BOOL,
    wintypes.LPCWSTR,
]
kernel32.ResetEvent.restype = wintypes.BOOL
kernel32.ResetEvent.argtypes = [wintypes.HANDLE]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.GetOverlappedResult.restype = wintypes.BOOL
kernel32.GetOverlappedResult.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(OVERLAPPED),
    ctypes.POINTER(wintypes.DWORD),
    wintypes.BOOL,
]
kernel32.CancelIoEx.restype = wintypes.BOOL
kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(OVERLAPPED)]


def is_invalid_handle(handle) -> bool:
    return not handle or handle == INVALID_HANDLE_VALUE or handle == ctypes.c_void_p(-1).value


def last_error() -> int:
    return int(ctypes.get_last_error())


def win_error_text(code: int | None = None) -> str:
    if code is None:
        code = last_error()
    try:
        return ctypes.FormatError(int(code)).strip()
    except Exception:
        return f"WinError {code}"


def set_dpi_aware() -> None:
    """Make both Tk and GDI tray icons crisp on scaled displays.

    Windows 10 1607+ supports per-monitor-v2 through a pseudo handle.  Older
    systems get the (still vastly better) system-DPI-aware fallback.
    """
    try:
        fn = user32.SetProcessDpiAwarenessContext
        fn.argtypes = [wintypes.HANDLE]
        fn.restype = wintypes.BOOL
        if fn(wintypes.HANDLE(-4)):
            return
    except Exception:
        pass
    try:
        fn = ctypes.WinDLL("shcore", use_last_error=True).SetProcessDpiAwareness
        fn.argtypes = [ctypes.c_int]
        fn.restype = ctypes.c_long
        fn(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        return
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


def get_dpi_for_system() -> int:
    try:
        fn = user32.GetDpiForSystem
        fn.restype = wintypes.UINT
        return max(96, int(fn()))
    except Exception:
        return 96


def known_folder(folder_id: GUID) -> str:
    """Return a known folder path, falling back to the classic environment."""
    try:
        ptr = ctypes.c_wchar_p()
        fn = shell32.SHGetKnownFolderPath
        fn.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
        fn.restype = ctypes.c_long
        hr = fn(ctypes.byref(folder_id), 0, None, ctypes.byref(ptr))
        if hr == 0 and ptr.value:
            value = ptr.value
            ole32.CoTaskMemFree(ptr)
            return value
    except Exception:
        pass
    return ""


def desktop_dir() -> str:
    path = known_folder(FOLDERID_Desktop)
    if path:
        return path
    return os.path.join(os.environ.get("USERPROFILE", os.path.expanduser("~")), "Desktop")


def startup_dir() -> str:
    path = known_folder(FOLDERID_Startup)
    if path:
        return path
    return os.path.join(
        os.environ.get("APPDATA", os.path.expanduser("~")),
        "Microsoft",
        "Windows",
        "Start Menu",
        "Programs",
        "Startup",
    )


def pythonw_path() -> str:
    exe = sys.executable
    if os.path.basename(exe).lower() == "python.exe":
        candidate = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(candidate):
            return candidate
    if os.path.basename(exe).lower() == "pythonw.exe":
        return exe
    return exe


def loword(value: int) -> int:
    return int(value) & 0xFFFF


def hiword(value: int) -> int:
    return (int(value) >> 16) & 0xFFFF


def make_lparam(low: int, high: int) -> int:
    return (int(high) << 16) | (int(low) & 0xFFFF)


@dataclass
class WindowMessage:
    hwnd: int
    msg: int
    wparam: int
    lparam: int
