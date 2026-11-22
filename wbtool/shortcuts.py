"""Windows shortcut (.lnk) creation/reading with ctypes + COM, and link helpers."""
from __future__ import annotations

import ctypes
import os
import subprocess
import winreg
from ctypes import wintypes
from pathlib import Path

from .win32 import (
    CREATE_NO_WINDOW,
    GUID,
    guid_from_string,
    ole32,
    startup_dir,
)

CLSID_ShellLink = guid_from_string("00021401-0000-0000-C000-000000000046")
IID_IShellLinkW = guid_from_string("000214F9-0000-0000-C000-000000000046")
IID_IPersistFile = guid_from_string("0000010B-0000-0000-C000-000000000046")

CLSCTX_INPROC_SERVER = 1
COINIT_APARTMENTTHREADED = 2
STGM_READ = 0

# IShellLinkW vtable indices (IUnknown 0-2, then IShellLinkW methods)
SL_GETPATH = 3
SL_GETWORKINGDIRECTORY = 8
SL_SETWORKINGDIRECTORY = 9
SL_GETARGUMENTS = 10
SL_SETARGUMENTS = 11
SL_SETDESCRIPTION = 7
SL_SETICONLOCATION = 17
SL_SETPATH = 20

# IPersistFile vtable indices
PF_LOAD = 5
PF_SAVE = 6


class ShortcutError(OSError):
    pass


_ole_initialized = False
_ole_should_uninit = False


def _ensure_ole() -> None:
    global _ole_initialized, _ole_should_uninit
    if _ole_initialized:
        return
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    hr = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    # S_OK=0, S_FALSE=1, RPC_E_CHANGED_MODE=0x80010106
    if hr in (0, 1):
        _ole_should_uninit = True
    elif hr != -2147417850 and hr & 0xFFFFFFFF != 0x80010106:
        raise ShortcutError(f"CoInitializeEx failed: 0x{hr & 0xFFFFFFFF:08X}")
    ole32.CoCreateInstance.restype = ctypes.c_long
    ole32.CoCreateInstance.argtypes = [
        ctypes.POINTER(GUID),
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(GUID),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    _ole_initialized = True


def _vtable_method(ptr, index: int, restype, argtypes):
    vtable = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[index])


def _release(ptr) -> None:
    if not ptr:
        return
    try:
        fn = _vtable_method(ptr, 2, wintypes.ULONG, [])
        fn(ptr)
    except Exception:
        pass


def _create_shell_link():
    p = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(
        ctypes.byref(CLSID_ShellLink),
        None,
        CLSCTX_INPROC_SERVER,
        ctypes.byref(IID_IShellLinkW),
        ctypes.byref(p),
    )
    if hr != 0 or not p:
        raise ShortcutError(f"CoCreateInstance(CLSID_ShellLink) failed: 0x{hr & 0xFFFFFFFF:08X}")
    return p


def create_shortcut(
    lnk_path: str | os.PathLike[str],
    target: str,
    arguments: str = "",
    working_directory: str = "",
    description: str = "",
    icon_location: str = "",
) -> None:
    """Create a .lnk using IShellLinkW + IPersistFile::Save (pure ctypes)."""
    _ensure_ole()
    lnk = str(Path(lnk_path).resolve() if not os.path.isabs(str(lnk_path)) else lnk_path)
    os.makedirs(os.path.dirname(lnk), exist_ok=True)
    link = _create_shell_link()
    persist = ctypes.c_void_p()
    try:
        _vtable_method(link, SL_SETPATH, ctypes.c_long, [wintypes.LPCWSTR])(link, str(target))
        if arguments:
            _vtable_method(link, SL_SETARGUMENTS, ctypes.c_long, [wintypes.LPCWSTR])(link, str(arguments))
        if working_directory:
            _vtable_method(link, SL_SETWORKINGDIRECTORY, ctypes.c_long, [wintypes.LPCWSTR])(
                link, str(working_directory)
            )
        if description:
            _vtable_method(link, SL_SETDESCRIPTION, ctypes.c_long, [wintypes.LPCWSTR])(link, str(description))
        if icon_location:
            _vtable_method(link, SL_SETICONLOCATION, ctypes.c_long, [wintypes.LPCWSTR, ctypes.c_int])(
                link, str(icon_location), 0
            )
        fn = _vtable_method(
            link,
            0,
            ctypes.c_long,
            [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)],
        )
        hr = fn(link, ctypes.byref(IID_IPersistFile), ctypes.byref(persist))
        if hr != 0 or not persist:
            raise ShortcutError(f"QueryInterface(IPersistFile) failed: 0x{hr & 0xFFFFFFFF:08X}")
        save = _vtable_method(persist, PF_SAVE, ctypes.c_long, [wintypes.LPCWSTR, wintypes.BOOL])
        hr = save(persist, lnk, True)
        if hr != 0:
            raise ShortcutError(f"IPersistFile::Save failed: 0x{hr & 0xFFFFFFFF:08X}")
    finally:
        _release(persist)
        _release(link)


def read_shortcut(lnk_path: str | os.PathLike[str]) -> dict[str, str]:
    _ensure_ole()
    lnk = str(lnk_path)
    link = _create_shell_link()
    persist = ctypes.c_void_p()
    try:
        fn = _vtable_method(link, 0, ctypes.c_long, [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)])
        hr = fn(link, ctypes.byref(IID_IPersistFile), ctypes.byref(persist))
        if hr != 0 or not persist:
            raise ShortcutError(f"QueryInterface(IPersistFile) failed: 0x{hr & 0xFFFFFFFF:08X}")
        load = _vtable_method(persist, PF_LOAD, ctypes.c_long, [wintypes.LPCWSTR, wintypes.DWORD])
        hr = load(persist, lnk, STGM_READ)
        if hr != 0:
            raise ShortcutError(f"IPersistFile::Load failed: 0x{hr & 0xFFFFFFFF:08X}")

        path_buf = ctypes.create_unicode_buffer(32768)
        get_path = _vtable_method(
            link,
            SL_GETPATH,
            ctypes.c_long,
            [wintypes.LPWSTR, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD],
        )
        get_path(link, path_buf, len(path_buf), None, 0)

        args_buf = ctypes.create_unicode_buffer(32768)
        get_args = _vtable_method(link, SL_GETARGUMENTS, ctypes.c_long, [wintypes.LPWSTR, ctypes.c_int])
        get_args(link, args_buf, len(args_buf))

        wd_buf = ctypes.create_unicode_buffer(32768)
        get_wd = _vtable_method(link, SL_GETWORKINGDIRECTORY, ctypes.c_long, [wintypes.LPWSTR, ctypes.c_int])
        get_wd(link, wd_buf, len(wd_buf))

        return {"target": path_buf.value, "arguments": args_buf.value, "working_directory": wd_buf.value}
    finally:
        _release(persist)
        _release(link)


def _startup_approved_path(filename: str) -> str:
    return (
        r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder"
        + "\\"
        + filename
    )


def _read_startup_approved(name: str) -> bytes | None:
    path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ) as key:
            data, _ = winreg.QueryValueEx(key, name)
            return bytes(data)
    except OSError:
        return None


def _write_startup_approved(name: str, data: bytes | None) -> None:
    path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder"
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
        if data is None:
            try:
                winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass
        else:
            winreg.SetValueEx(key, name, 0, winreg.REG_BINARY, data)


def startup_shortcut_path() -> Path:
    return Path(startup_dir()) / "电量托盘.lnk"


def _approved_state(raw: bytes | None) -> str:
    if not raw:
        return "default"
    if len(raw) >= 4:
        state = int.from_bytes(raw[:4], "little")
        if state == 2:
            return "enabled"
        if state == 3:
            return "disabled"
        if state == 6:
            return "enabled"
        return "unknown"
    return "unknown"


def startup_state() -> dict:
    lnk = startup_shortcut_path()
    raw = _read_startup_approved(lnk.name)
    approved = _approved_state(raw)
    exists = lnk.is_file()
    target_ok = False
    args_ok = False
    target = ""
    arguments = ""
    read_error = ""
    if exists:
        try:
            info = read_shortcut(lnk)
            target = info.get("target", "")
            arguments = info.get("arguments", "")
            target_ok = os.path.normcase(os.path.basename(target)) == "pythonw.exe"
            # The script path can be quoted or unquoted in Arguments.
            args_ok = "my_devices.py" in arguments and "--tray" in arguments
        except Exception as exc:
            read_error = str(exc)
    enabled = exists and approved != "disabled"
    return {
        "path": str(lnk),
        "exists": exists,
        "approved": approved,
        "approved_raw": raw.hex(" ") if raw else "",
        "enabled": enabled,
        "target": target,
        "arguments": arguments,
        "target_ok": target_ok,
        "args_ok": args_ok,
        "read_error": read_error,
        "task_manager_visible": exists,
    }


def set_startup_enabled(enabled: bool, project_dir: str, script_path: str, description: str = "电量托盘") -> dict:
    """Explicit user action: create/disable the Startup-folder shortcut.

    Disabling keeps the .lnk (so Task Manager still lists it as disabled) and
    writes the same StartupApproved 03 00 ... marker Explorer/Task Manager use.
    Nothing in the running tray ever calls this automatically.
    """
    from .win32 import pythonw_path

    lnk = startup_shortcut_path()
    lnk.parent.mkdir(parents=True, exist_ok=True)
    args = f'"{script_path}" --tray'
    if not lnk.is_file() or not startup_state().get("target_ok") or not startup_state().get("args_ok"):
        create_shortcut(
            lnk,
            pythonw_path(),
            arguments=args,
            working_directory=project_dir,
            description=description,
            icon_location=pythonw_path(),
        )
    if enabled:
        _write_startup_approved(lnk.name, b"\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00")
    else:
        _write_startup_approved(lnk.name, b"\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00")
    return startup_state()


def remove_startup_shortcut() -> None:
    lnk = startup_shortcut_path()
    try:
        if lnk.is_file():
            lnk.unlink()
    except OSError:
        pass
    try:
        _write_startup_approved(lnk.name, None)
    except OSError:
        pass


def _run_mklink_junction(link: str, target: str) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", link, target],
            capture_output=True,
            text=True,
            encoding="mbcs",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
            timeout=20,
        )
        return proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")
    except Exception as exc:
        return False, str(exc)


def _same_target(link: str, target: str) -> bool:
    try:
        return os.path.samefile(link, target)
    except OSError:
        return False


def ensure_directory_link(link: Path, target: Path) -> str:
    """Create Desktop -> project directory link.

    Prefer a real symlink; on Windows without Developer Mode/admin privilege
    fall back to a junction (mklink /J), which also does not copy anything.
    """
    link = Path(link)
    target = Path(target)
    if link.exists() or link.is_symlink():
        if _same_target(str(link), str(target)):
            try:
                return "symlink" if link.is_symlink() else "junction"
            except OSError:
                return "junction"
        raise FileExistsError(f"桌面已存在同名项目，但它不是指向本项目的链接：{link}")
    parent = link.parent
    parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(str(target), str(link), target_is_directory=True)
        if _same_target(str(link), str(target)):
            return "symlink"
    except OSError:
        pass
    # Remove a half-created symlink before falling back.
    try:
        if link.is_symlink():
            link.unlink()
    except OSError:
        pass
    ok, output = _run_mklink_junction(str(link), str(target))
    if not ok or not _same_target(str(link), str(target)):
        raise OSError(f"创建目录链接失败：{output.strip() or 'mklink /J 失败'}")
    return "junction"


def ensure_file_link(link: Path, target: Path) -> str:
    """Create Desktop -> launcher file link; symlink, then hardlink fallback."""
    link = Path(link)
    target = Path(target)
    if link.exists() or link.is_symlink():
        if _same_target(str(link), str(target)):
            return "symlink" if link.is_symlink() else "hardlink"
        raise FileExistsError(f"桌面已存在同名文件，但它不是指向启动脚本的链接：{link}")
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(str(target), str(link))
        return "symlink"
    except OSError:
        pass
    try:
        if link.is_symlink():
            link.unlink()
    except OSError:
        pass
    try:
        os.link(str(target), str(link))
        return "hardlink"
    except OSError as exc:
        raise OSError(f"创建文件链接失败（符号链接与硬链接均失败）：{exc}") from exc


def link_kind(path: Path) -> str:
    try:
        if path.is_symlink():
            return "symlink"
    except OSError:
        pass
    try:
        st = path.stat()
        # A junction appears as a directory reparse point; Windows Python does
        # not always flag it with is_symlink(), so report "junction" for dirs.
        if path.is_dir():
            return "junction"
        # Hardlinks have no distinct flag through os.stat; this is a best-effort
        # label used only for display.
        return "hardlink"
    except OSError:
        return "missing"
