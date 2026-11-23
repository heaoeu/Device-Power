# -*- coding: utf-8 -*-
"""电量托盘引导程序：与项目所在的磁盘、目录名都无关。

启动文件夹和桌面快捷方式只指向这个文件的一个固定副本：

    %LOCALAPPDATA%\\电量托盘\\电量托盘启动器.pyw

本文件里没有项目的绝对路径，启动顺序是：

1. 读同目录 ``location.json`` 里上次记住的项目位置，能用就直接启动（很快）；
2. 不能用（项目换了磁盘、改了目录名或搬了家）就搜索本机所有固定磁盘，
   按 ``my_devices.py`` + ``wbtool\\app.py`` 两个特征文件识别项目，
   优先选名为“电量托盘项目”的目录；
3. 找到后更新 ``location.json`` 并启动，因此下一次开机又能从新位置起来。

项目本身不写日志；Location.json 只是一条“项目在哪”的指针记录。
"""
from __future__ import annotations

import ctypes
import json
import os
import runpy
import sys
import time
import traceback

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(HERE, "location.json")
LAUNCH_ARG = "--tray"
PREFERRED_NAME = "电量托盘项目"

MAX_DEPTH = 5          # D:\Develop\Python\电量托盘项目 只有 3 层，留两级余量
MAX_SCAN_DIRS = 400000
MAX_SCAN_SECONDS = 12.0

SKIP_DIRS = {
    "$recycle.bin",
    "system volume information",
    "windows",
    "windows.old",
    "windows.~bt",
    "program files",
    "program files (x86)",
    "programdata",
    "appdata",
    "recovery",
    "perflogs",
    "msocache",
    "config.msi",
    "$winreagent",
    "node_modules",
    ".git",
    ".venv",
    "venv",
    "site-packages",
    "__pycache__",
    "winsxs",
}


def is_project(path: str) -> bool:
    """A directory is the battery project only if both marker files exist."""
    if not path:
        return False
    return os.path.isfile(os.path.join(path, "my_devices.py")) and os.path.isfile(
        os.path.join(path, "wbtool", "app.py")
    )


def remembered() -> str:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return ""
    path = str(data.get("project") or "") if isinstance(data, dict) else ""
    return path if is_project(path) else ""


def remember(path: str) -> None:
    """Write the pointer only when it actually changes."""
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("project") == path:
            return
    except (OSError, ValueError):
        pass
    try:
        os.makedirs(HERE, exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"project": path}, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, STATE_PATH)
    except OSError:
        pass


def fixed_drives() -> list[str]:
    kernel32 = ctypes.windll.kernel32
    mask = kernel32.GetLogicalDrives()
    drives = []
    for index in range(26):
        if not mask & (1 << index):
            continue
        root = f"{chr(65 + index)}:\\"
        try:
            if kernel32.GetDriveTypeW(ctypes.c_wchar_p(root)) == 3:  # DRIVE_FIXED
                drives.append(root)
        except Exception:
            continue
    return drives


def search() -> str:
    """Breadth-first scan of fixed drives; returns "" when nothing matches."""
    started = time.monotonic()
    scanned = 0
    fallback = ""
    for root in fixed_drives():
        queue = [(root, 0)]
        while queue:
            path, depth = queue.pop(0)
            scanned += 1
            if scanned > MAX_SCAN_DIRS or (scanned % 400 == 0 and time.monotonic() - started > MAX_SCAN_SECONDS):
                return fallback
            try:
                with os.scandir(path) as entries:
                    for entry in entries:
                        try:
                            if not entry.is_dir(follow_symlinks=False):
                                continue
                            # Skip junctions/symlinks: the Desktop folder link points
                            # back at the project and would otherwise be found first.
                            is_junction = getattr(entry, "is_junction", None)
                            if is_junction is not None and is_junction():
                                continue
                        except OSError:
                            continue
                        name = entry.name
                        if name.lower() in SKIP_DIRS or name.startswith("$"):
                            continue
                        child = entry.path
                        if is_project(child):
                            if name == PREFERRED_NAME:
                                return child
                            if not fallback:
                                fallback = child
                        if depth + 1 < MAX_DEPTH:
                            queue.append((child, depth + 1))
            except OSError:
                continue
    return fallback


def _error_box(text: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, text, "电量托盘", 0x00000030)
    except Exception:
        pass


def main() -> int:
    project = remembered()
    if not project:
        project = search()
    if not project:
        _error_box(
            "没有找到“电量托盘”项目文件夹。\n\n"
            "请把项目放回任意本地磁盘（例如 D:\\Develop\\Python\\电量托盘项目），"
            "或在项目目录里运行一次：\n    python my_devices.py --install"
        )
        return 2
    remember(project)
    script = os.path.join(project, "my_devices.py")
    try:
        os.chdir(project)
        sys.argv = [script, LAUNCH_ARG]
        sys.path.insert(0, project)
        runpy.run_path(script, run_name="__main__")
    except SystemExit:
        raise
    except Exception:
        _error_box("电量托盘启动失败：\n\n" + traceback.format_exc(limit=4))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
