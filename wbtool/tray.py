"""Notification-area tray: two icons with numeric battery content.

A dedicated thread owns the hidden window and its message loop.  Clicks are
forwarded to the Tk main thread through a Queue; the main thread never touches
Win32 tray windows, and the tray thread never touches Tk.
"""
from __future__ import annotations

import ctypes
import queue
import threading
import time
from dataclasses import dataclass
from ctypes import wintypes

from . import icons
from .win32 import kernel32, user32

shell32 = __import__("ctypes").WinDLL("shell32", use_last_error=True)

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B
WM_INPUT = 0x00FF
WM_NULL = 0x0000
WM_APP = 0x8000
WM_APP_OPEN_PANEL = WM_APP + 1
WM_APP_UPDATE = WM_APP + 2
WM_APP_ICONS = WM_APP + 4
WM_APP_RESTORE = WM_APP + 5
WM_APP_SHOW_MENU = WM_APP + 6
WM_APP_KINDS = WM_APP + 7
WM_APP_TRAY_CALLBACK = WM_APP + 10
NIN_SELECT = 0x0400
NIN_KEYSELECT = 0x0401

NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIM_SETVERSION = 4

NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_STATE = 0x00000008
NIF_SHOWTIP = 0x00000080
NIF_INFO = 0x00000010
NIS_HIDDEN = 0x00000001

NOTIFYICON_VERSION_4 = 4
WS_EX_TOOLWINDOW = 0x00000080

MENU_OPEN_PANEL = 1
MENU_REFRESH = 2
MENU_QUIT = 3
MENU_TOGGLE_TRAY = 4
MENU_TOGGLE_STRIP = 5
MF_STRING = 0x00000000
MF_CHECKED = 0x00000008
MF_SEPARATOR = 0x00000800
MF_DEFAULT = 0x00001000
TPM_RETURNCMD = 0x0100
TPM_RIGHTBUTTON = 0x0002
TPM_NONOTIFY = 0x0080

RIDEV_INPUTSINK = 0x00000100
RID_INPUT = 0x10000003
RIM_TYPEKEYBOARD = 1
RI_KEY_BREAK = 0x01

CLASS_NAME = "WirelessBatteryTrayWindow_v1"
UID_KIND = {1: "mouse", 2: "keyboard"}
MUTEX_NAME = "Local\\WirelessBatteryTray_v1"


class WNDCLASSW(__import__("ctypes").Structure):
    pass


WNDPROC = __import__("ctypes").WINFUNCTYPE(
    __import__("ctypes").c_ssize_t,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)

WNDCLASSW._fields_ = [
    ("style", wintypes.UINT),
    ("lpfnWndProc", WNDPROC),
    ("cbClsExtra", __import__("ctypes").c_int),
    ("cbWndExtra", __import__("ctypes").c_int),
    ("hInstance", wintypes.HINSTANCE),
    ("hIcon", wintypes.HICON),
    ("hCursor", wintypes.HANDLE),
    ("hbrBackground", wintypes.HBRUSH),
    ("lpszMenuName", wintypes.LPCWSTR),
    ("lpszClassName", wintypes.LPCWSTR),
]


class NOTIFYICONDATAW(__import__("ctypes").Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uTimeout", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", __import__("ctypes").c_ubyte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


class POINT(__import__("ctypes").Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MSG(__import__("ctypes").Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
        ("lPrivate", wintypes.DWORD),
    ]


class RAWINPUTDEVICE(__import__("ctypes").Structure):
    _fields_ = [
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
        ("dwFlags", wintypes.DWORD),
        ("hwndTarget", wintypes.HWND),
    ]


class RAWINPUTHEADER(__import__("ctypes").Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD),
        ("dwSize", wintypes.DWORD),
        ("hDevice", wintypes.HANDLE),
        ("wParam", wintypes.WPARAM),
    ]


user32.RegisterRawInputDevices.restype = wintypes.BOOL
user32.RegisterRawInputDevices.argtypes = [
    __import__("ctypes").POINTER(RAWINPUTDEVICE),
    wintypes.UINT,
    wintypes.UINT,
]
user32.GetRawInputData.restype = wintypes.UINT
user32.GetRawInputData.argtypes = [
    wintypes.HANDLE,
    wintypes.UINT,
    __import__("ctypes").c_void_p,
    __import__("ctypes").POINTER(wintypes.UINT),
    wintypes.UINT,
]
user32.GetRawInputDeviceInfoW.restype = wintypes.UINT
user32.GetRawInputDeviceInfoW.argtypes = [
    wintypes.HANDLE,
    wintypes.UINT,
    __import__("ctypes").c_void_p,
    __import__("ctypes").POINTER(wintypes.UINT),
]
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.CreatePopupMenu.argtypes = []
user32.AppendMenuW.restype = wintypes.BOOL
user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.UINT, wintypes.LPCWSTR]
user32.SetMenuDefaultItem.restype = wintypes.BOOL
user32.SetMenuDefaultItem.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.UINT]
user32.TrackPopupMenuEx.restype = wintypes.UINT
user32.TrackPopupMenuEx.argtypes = [
    wintypes.HMENU,
    wintypes.UINT,
    __import__("ctypes").c_int,
    __import__("ctypes").c_int,
    wintypes.HWND,
    __import__("ctypes").c_void_p,
]
user32.DestroyMenu.restype = wintypes.BOOL
user32.DestroyMenu.argtypes = [wintypes.HMENU]
user32.GetCursorPos.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = [__import__("ctypes").POINTER(POINT)]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]


user32.RegisterClassW.restype = wintypes.ATOM
user32.RegisterClassW.argtypes = [__import__("ctypes").POINTER(WNDCLASSW)]
user32.UnregisterClassW.restype = wintypes.BOOL
user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    __import__("ctypes").c_int,
    __import__("ctypes").c_int,
    __import__("ctypes").c_int,
    __import__("ctypes").c_int,
    wintypes.HWND,
    wintypes.HMENU,
    wintypes.HINSTANCE,
    __import__("ctypes").c_void_p,
]
user32.DefWindowProcW.restype = __import__("ctypes").c_ssize_t
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DestroyWindow.restype = wintypes.BOOL
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.PostQuitMessage.restype = None
user32.PostQuitMessage.argtypes = [__import__("ctypes").c_int]
user32.PostMessageW.restype = wintypes.BOOL
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.GetMessageW.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [__import__("ctypes").POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.TranslateMessage.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [__import__("ctypes").POINTER(MSG)]
user32.DispatchMessageW.restype = __import__("ctypes").c_ssize_t
user32.DispatchMessageW.argtypes = [__import__("ctypes").POINTER(MSG)]
user32.RegisterWindowMessageW.restype = wintypes.UINT
user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, __import__("ctypes").POINTER(NOTIFYICONDATAW)]


@dataclass
class TrayView:
    id: str
    name: str
    level: int | None
    charging: bool | None
    updated_text: str
    status_text: str = ""
    kind: str = ""


class TrayThread(threading.Thread):
    def __init__(self, event_queue: queue.Queue,
                 show_icons: bool = True, strip_visible: bool = True,
                 shown_kinds: list[str] | set[str] | None = None):
        super().__init__(name="battery-tray", daemon=True)
        self.events = event_queue
        self._icons_visible = bool(show_icons)
        self._strip_visible = bool(strip_visible)
        self._shown_kinds = set(shown_kinds if shown_kinds is not None else UID_KIND.values())
        self.hwnd: int = 0
        self.error = ""
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._wndproc_ref = WNDPROC(self._wndproc)
        self._class_atom = 0
        self._taskbar_created = 0
        self._lock = threading.Lock()
        self._pending: list[TrayView] | None = None
        self._latest: list[TrayView] = []
        self._icons: dict[int, int] = {1: 0, 2: 0}
        self._icons_added = False
        self._icon_live: dict[int, bool] = {1: False, 2: False}
        self._last: dict[int, tuple[str, int | None, str]] = {}
        self._tooltips: dict[int, str] = {1: "无线鼠标：正在读取…", 2: "无线键盘：正在读取…"}
        self._names: dict[int, str] = {1: "无线鼠标", 2: "无线键盘"}
        self._last_key_event = 0.0
        self._raw_input_ok = False
        self._menu_open = False
        self.raw_device_name = ""

    # ---- public API (called from the Tk main thread) --------------------
    def start_and_wait(self, timeout: float = 4.0) -> bool:
        self.start()
        return self._ready.wait(timeout) and not self.error

    def update(self, views: list[TrayView]) -> None:
        with self._lock:
            self._pending = list(views)
            self._latest = list(views)
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_APP_UPDATE, 0, 0)

    def set_icon_visible(self, visible: bool) -> None:
        """Hide or restore both notification-area icons."""
        with self._lock:
            self._icons_visible = bool(visible)
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_APP_ICONS, int(bool(visible)), 0)

    def set_menu_state(self, tray_visible: bool, strip_visible: bool) -> None:
        """Remember what the check marks in the context menu should show."""
        with self._lock:
            self._icons_visible = bool(tray_visible)
            self._strip_visible = bool(strip_visible)

    def set_shown_kinds(self, kinds: list[str] | set[str]) -> None:
        """Choose which device icons are visible in the notification area."""
        with self._lock:
            self._shown_kinds = {str(kind) for kind in kinds}
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_APP_KINDS, 0, 0)

    def show_menu(self) -> None:
        """Open the same context menu from the taskbar strip."""
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_APP_SHOW_MENU, 0, 0)

    def stop(self, wait: float = 3.0) -> None:
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
        self._stopped.wait(wait)

    # ---- thread body ----------------------------------------------------
    def run(self) -> None:
        try:
            self._create_window()
            if not self.hwnd:
                self.error = "CreateWindowExW failed"
                self._ready.set()
                return
            self._taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
            if self._icons_visible:
                self._add_icons()
            self._apply_pending()
            self._ready.set()
            msg = MSG()
            while True:
                ret = user32.GetMessageW(__import__("ctypes").byref(msg), None, 0, 0)
                if ret in (0, -1):
                    break
                user32.TranslateMessage(__import__("ctypes").byref(msg))
                user32.DispatchMessageW(__import__("ctypes").byref(msg))
        except Exception as exc:
            self.error = f"tray thread: {exc}"
            self._ready.set()
        finally:
            try:
                if self.hwnd:
                    self._delete_icons()
                    user32.DestroyWindow(self.hwnd)
                    self.hwnd = 0
                if self._class_atom:
                    user32.UnregisterClassW(CLASS_NAME, kernel32.GetModuleHandleW(None))
            except Exception:
                pass
            self._stopped.set()

    def _create_window(self) -> None:
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.style = 0
        wc.lpfnWndProc = self._wndproc_ref
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = hinst
        wc.hIcon = 0
        wc.hCursor = 0
        wc.hbrBackground = 0
        wc.lpszMenuName = None
        wc.lpszClassName = CLASS_NAME
        self._class_atom = user32.RegisterClassW(__import__("ctypes").byref(wc))
        if not self._class_atom:
            self.error = f"RegisterClassW failed ({kernel32.GetLastError()})"
            return
        self.hwnd = int(
            user32.CreateWindowExW(
                WS_EX_TOOLWINDOW,
                CLASS_NAME,
                "电量托盘 TrayHost",
                0,
                0,
                0,
                0,
                0,
                None,
                None,
                hinst,
                None,
            )
            or 0
        )
        if not self.hwnd:
            self.error = f"CreateWindowExW failed ({kernel32.GetLastError()})"

    def _nid(self, uid: int) -> NOTIFYICONDATAW:
        nid = NOTIFYICONDATAW()
        nid.cbSize = __import__("ctypes").sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = uid
        nid.uCallbackMessage = WM_APP_TRAY_CALLBACK
        return nid

    def _icon_hidden(self, uid: int) -> bool:
        with self._lock:
            return UID_KIND.get(uid, "") not in self._shown_kinds

    def _set_icon_hidden(self, uid: int, hidden: bool) -> None:
        if not self._icon_live.get(uid):
            return
        nid = self._nid(uid)
        nid.uFlags = NIF_STATE
        nid.dwStateMask = NIS_HIDDEN
        nid.dwState = NIS_HIDDEN if hidden else 0
        shell32.Shell_NotifyIconW(NIM_MODIFY, __import__("ctypes").byref(nid))

    def _apply_kinds(self) -> None:
        for uid in (1, 2):
            if self._icon_live.get(uid):
                self._set_icon_hidden(uid, self._icon_hidden(uid))

    def _add_icons(self) -> None:
        if not self._icons_visible:
            return
        for uid in (1, 2):
            if not self._icons.get(uid):
                self._icons[uid] = icons.create_icon(None, "mouse" if uid == 1 else "keyboard")
            nid = self._nid(uid)
            nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP
            nid.hIcon = self._icons[uid]
            nid.szTip = self._tooltips[uid][:120]
            ok = bool(shell32.Shell_NotifyIconW(NIM_ADD, __import__("ctypes").byref(nid)))
            if not ok:
                # Explorer may not be ready immediately after logon.
                for _ in range(8):
                    time.sleep(0.15)
                    if shell32.Shell_NotifyIconW(NIM_ADD, __import__("ctypes").byref(nid)):
                        ok = True
                        break
            if ok:
                self._icons_added = True
                self._icon_live[uid] = True
                self._set_icon_hidden(uid, self._icon_hidden(uid))
                version = self._nid(uid)
                version.uFlags = 0
                version.uTimeout = NOTIFYICON_VERSION_4
                shell32.Shell_NotifyIconW(NIM_SETVERSION, __import__("ctypes").byref(version))
            elif not self.error:
                self.error = f"Shell_NotifyIconW(NIM_ADD) failed for icon {uid}"

    def _delete_icons(self) -> None:
        self._icons_added = False
        self._icon_live = {1: False, 2: False}
        for uid in (1, 2):
            if self.hwnd:
                nid = self._nid(uid)
                try:
                    shell32.Shell_NotifyIconW(NIM_DELETE, __import__("ctypes").byref(nid))
                except Exception:
                    pass
            icon = self._icons.get(uid)
            if icon:
                icons.destroy_icon(icon)
                self._icons[uid] = 0

    def _set_icons_visible(self, visible: bool) -> None:
        visible = bool(visible)
        self._icons_visible = visible
        if visible:
            # Recreate icons and replay the most recent readings so a restored
            # icon shows the current percentage immediately.
            if not self._icons_added:
                self._add_icons()
            self._last.clear()
            with self._lock:
                self._pending = list(self._latest)
            self._apply_pending()
        else:
            self._delete_icons()
            self._last.clear()

    def _apply_pending(self) -> None:
        with self._lock:
            views = self._pending
            self._pending = None
        if not views or not self._icons_visible or not self._icons_added:
            return
        for uid, view in zip((1, 2), views):
            self._names[uid] = view.name or self._names[uid]
            kind = view.kind or ("mouse" if uid == 1 else "keyboard")
            percent = "--" if view.level is None else f"{view.level}%"
            when = view.updated_text or "尚未读取"
            tooltip = f"{self._names[uid]}：{percent}（{when}）"
            self._tooltips[uid] = tooltip[:120]
            key = (self._names[uid], view.level, tooltip, kind)
            if self._last.get(uid) == key:
                continue
            nid = self._nid(uid)
            nid.uFlags = NIF_ICON | NIF_TIP | NIF_SHOWTIP
            old_icon = self._icons.get(uid)
            new_icon = None
            if not old_icon or self._last.get(uid, (None, None, None))[1] != view.level:
                new_icon = icons.create_icon(view.level, kind)
                nid.hIcon = new_icon or old_icon
            else:
                nid.hIcon = old_icon
            nid.szTip = tooltip[:120]
            ok = bool(shell32.Shell_NotifyIconW(NIM_MODIFY, __import__("ctypes").byref(nid)))
            if new_icon:
                if ok:
                    self._icons[uid] = new_icon
                    if old_icon:
                        icons.destroy_icon(old_icon)
                else:
                    icons.destroy_icon(new_icon)
            self._last[uid] = key

    def _register_raw_keyboard(self) -> None:
        """Ask for keyboard input even while our window is not foreground."""
        try:
            device = RAWINPUTDEVICE(1, 6, RIDEV_INPUTSINK, self.hwnd)
            self._raw_input_ok = bool(
                user32.RegisterRawInputDevices(
                    __import__("ctypes").byref(device), 1, __import__("ctypes").sizeof(device)
                )
            )
        except Exception:
            self._raw_input_ok = False

    def _raw_device_name(self, hdevice: int) -> str:
        try:
            size = wintypes.UINT(0)
            user32.GetRawInputDeviceInfoW(hdevice, 0x20000007, None, __import__("ctypes").byref(size))
            if not size.value:
                return ""
            buf = __import__("ctypes").create_unicode_buffer(size.value + 1)
            got = user32.GetRawInputDeviceInfoW(
                hdevice, 0x20000007, buf, __import__("ctypes").byref(size)
            )
            return buf.value if got not in (0, 0xFFFFFFFF) else ""
        except Exception:
            return ""

    def _on_raw_input(self, lparam: int) -> None:
        if not self._raw_input_ok:
            return
        try:
            size = wintypes.UINT(0)
            user32.GetRawInputData(
                wintypes.HANDLE(lparam),
                RID_INPUT,
                None,
                __import__("ctypes").byref(size),
                __import__("ctypes").sizeof(RAWINPUTHEADER),
            )
            if size.value < __import__("ctypes").sizeof(RAWINPUTHEADER) + 16:
                return
            buf = __import__("ctypes").create_string_buffer(size.value)
            got = user32.GetRawInputData(
                wintypes.HANDLE(lparam),
                RID_INPUT,
                buf,
                __import__("ctypes").byref(size),
                __import__("ctypes").sizeof(RAWINPUTHEADER),
            )
            if got in (0, 0xFFFFFFFF):
                return
            header = RAWINPUTHEADER.from_buffer(buf)
            if header.dwType != RIM_TYPEKEYBOARD:
                return
            base = __import__("ctypes").sizeof(RAWINPUTHEADER)
            flags = int.from_bytes(buf.raw[base + 2 : base + 4], "little")
            if flags & RI_KEY_BREAK:
                return
            now = time.monotonic()
            if now - self._last_key_event < 1.0:
                return
            self._last_key_event = now
            if not self.raw_device_name:
                self.raw_device_name = self._raw_device_name(header.hDevice)
            try:
                self.events.put_nowait("keyboard_activity")
            except queue.Full:
                pass
        except Exception:
            pass

    def _show_context_menu(self) -> None:
        if self._menu_open or not self.hwnd:
            return
        self._menu_open = True
        menu = 0
        try:
            menu = user32.CreatePopupMenu()
            if not menu:
                return
            user32.AppendMenuW(menu, MF_STRING, MENU_OPEN_PANEL, "打开主面板")
            user32.AppendMenuW(menu, MF_STRING, MENU_REFRESH, "立即刷新")
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            tray_flag = MF_CHECKED if self._icons_visible else 0
            strip_flag = MF_CHECKED if self._strip_visible else 0
            user32.AppendMenuW(menu, MF_STRING | tray_flag, MENU_TOGGLE_TRAY, "托盘图标")
            user32.AppendMenuW(menu, MF_STRING | strip_flag, MENU_TOGGLE_STRIP, "任务栏图标")
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(menu, MF_STRING, MENU_QUIT, "退出")
            user32.SetMenuDefaultItem(menu, MENU_OPEN_PANEL, 0)
            pt = POINT()
            user32.GetCursorPos(__import__("ctypes").byref(pt))
            user32.SetForegroundWindow(self.hwnd)
            command = int(
                user32.TrackPopupMenuEx(
                    menu,
                    TPM_RETURNCMD | TPM_RIGHTBUTTON | TPM_NONOTIFY,
                    pt.x,
                    pt.y,
                    self.hwnd,
                    None,
                )
            )
            user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
            if command == MENU_OPEN_PANEL:
                self.events.put_nowait("open_panel")
            elif command == MENU_REFRESH:
                self.events.put_nowait("refresh")
            elif command == MENU_TOGGLE_TRAY:
                visible = not self._icons_visible
                self._set_icons_visible(visible)
                self.events.put_nowait("tray_icon_on" if visible else "tray_icon_off")
            elif command == MENU_TOGGLE_STRIP:
                visible = not self._strip_visible
                self._strip_visible = visible
                self.events.put_nowait("strip_on" if visible else "strip_off")
            elif command == MENU_QUIT:
                self.events.put_nowait("quit")
        except Exception:
            pass
        finally:
            if menu:
                user32.DestroyMenu(menu)
            self._menu_open = False

    def _on_callback(self, lparam: int) -> None:
        event = int(lparam) & 0xFFFF
        if event in (WM_LBUTTONUP, NIN_SELECT, NIN_KEYSELECT):
            # Left click: open/foreground the panel and refresh its data.
            try:
                self.events.put_nowait("open_panel")
                self.events.put_nowait("refresh")
            except queue.Full:
                pass
        elif event in (WM_RBUTTONUP, WM_CONTEXTMENU):
            self._show_context_menu()

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_DESTROY:
                self._delete_icons()
                user32.PostQuitMessage(0)
                self._stopped.set()
                try:
                    self.events.put_nowait("tray_closed")
                except queue.Full:
                    pass
                return 0
            if msg == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_APP_TRAY_CALLBACK:
                self._on_callback(int(lparam))
                return 0
            if msg == WM_INPUT:
                self._on_raw_input(int(lparam))
                return 0
            if msg == WM_APP_UPDATE:
                self._apply_pending()
                return 0
            if msg == WM_APP_OPEN_PANEL:
                try:
                    self.events.put_nowait("open_panel")
                except queue.Full:
                    pass
                return 0
            if msg == WM_APP_ICONS:
                self._set_icons_visible(bool(wparam))
                return 0
            if msg == WM_APP_SHOW_MENU:
                self._show_context_menu()
                return 0
            if msg == WM_APP_KINDS:
                self._apply_kinds()
                return 0
            if msg == WM_APP_RESTORE:
                self._set_icons_visible(True)
                try:
                    self.events.put_nowait("restore_tray_icons")
                except queue.Full:
                    pass
                return 0
            if self._taskbar_created and msg == self._taskbar_created:
                # Explorer restarted: icons were lost with the old taskbar.
                for icon in self._icons.values():
                    icons.destroy_icon(icon)
                self._icons = {1: 0, 2: 0}
                self._icon_live = {1: False, 2: False}
                self._last.clear()
                if self._icons_visible:
                    self._add_icons()
                    self._apply_pending()
                try:
                    self.events.put_nowait("refresh")
                except queue.Full:
                    pass
                return 0
            if msg == WM_QUERYENDSESSION:
                return 1
            if msg == WM_ENDSESSION:
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
        except Exception:
            return 0


# ---------------------------------------------------------------------------
# Single-instance helpers and process-level window control
# ---------------------------------------------------------------------------
def find_tray_window() -> int:
    try:
        return int(user32.FindWindowW(CLASS_NAME, None) or 0)
    except Exception:
        return 0


def notify_existing_instance(timeout: float = 2.5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hwnd = find_tray_window()
        if hwnd:
            user32.PostMessageW(hwnd, WM_APP_OPEN_PANEL, 0, 0)
            user32.PostMessageW(hwnd, WM_APP_RESTORE, 0, 0)
            return True
        time.sleep(0.1)
    return False


class SingleInstance:
    """Named mutex owned by the process; Windows releases it even on crash."""

    def __init__(self, name: str = MUTEX_NAME):
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        self.handle = kernel32.CreateMutexW(None, False, name)
        self.already_exists = ctypes.get_last_error() == 183
        self.name = name

    def close(self) -> None:
        if self.handle:
            try:
                kernel32.CloseHandle(self.handle)
            except Exception:
                pass
            self.handle = 0

    def __del__(self):
        self.close()
