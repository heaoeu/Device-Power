"""Install/uninstall helpers: portable launcher, Startup shortcut, Desktop links.

The project directory is never baked into a shortcut.  Instead a small,
path-independent launcher is copied to a fixed location::

    %LOCALAPPDATA%\\电量托盘\\电量托盘启动器.pyw

and both the Startup entry and the Desktop entry point at that copy.  Moving
the project to another folder or another drive therefore cannot break autostart:
the launcher looks the project up again (``location.json`` first, then a scan).
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from .config import PROJECT_DIR
from .shortcuts import (
    ICON_NAME,
    LAUNCHER_NAME,
    create_shortcut,
    ensure_directory_link,
    link_kind,
    remove_startup_shortcut,
    set_startup_enabled,
    startup_shortcut_path,
    startup_state,
)
from .win32 import desktop_dir, pythonw_path

SCRIPT_PATH = PROJECT_DIR / "my_devices.py"
LAUNCHER_SOURCE = PROJECT_DIR / LAUNCHER_NAME
ICON_SOURCE = PROJECT_DIR / "wbtool" / "app.ico"
DESKTOP_DIR_LINK = "电量托盘项目"
DESKTOP_LINK_NAME = "电量托盘.lnk"
# The previous layout used a Desktop copy/link of the launcher; it is removed.
LEGACY_DESKTOP_FILE = "电量托盘.pyw"

FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def app_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "电量托盘"


def launcher_path() -> Path:
    return app_dir() / LAUNCHER_NAME


def icon_path() -> Path:
    """The shortcut icon next to the launcher (stable across project moves)."""
    return app_dir() / ICON_NAME


def location_path() -> Path:
    return app_dir() / "location.json"


def _is_reparse_dir(path: Path) -> bool:
    """True for a junction/symlink that points at a directory."""
    try:
        info = os.lstat(str(path))
    except OSError:
        return False
    if not stat.S_ISDIR(info.st_mode):
        return False
    return bool(getattr(info, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)


def _same_target(link: Path, target: Path) -> bool:
    try:
        return os.path.samefile(str(link), str(target))
    except OSError:
        return False


def _remove_path(path: Path) -> bool:
    """Remove a file, a symlink or a directory junction (never a real folder)."""
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
            return True
        if _is_reparse_dir(path):
            os.rmdir(str(path))
            return True
    except OSError:
        pass
    return False


def _install_bootstrap() -> Path:
    """Copy the path-independent launcher to its fixed location."""
    dest = launcher_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    source = LAUNCHER_SOURCE
    if not source.is_file():
        raise FileNotFoundError(f"缺少引导脚本：{source}")
    data = source.read_bytes()
    try:
        if dest.is_file() and dest.read_bytes() == data:
            return dest
    except OSError:
        pass
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)
    return dest


def _install_icon() -> Path:
    """Copy ``wbtool/app.ico`` next to the launcher for the .lnk icons."""
    dest = icon_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    source = ICON_SOURCE
    if not source.is_file():
        raise FileNotFoundError(f"缺少图标：{source}")
    data = source.read_bytes()
    try:
        if dest.is_file() and dest.read_bytes() == data:
            return dest
    except OSError:
        pass
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)
    return dest


def _write_location(project: Path | None = None) -> Path:
    """Remember where this copy of the project lives (fast path for the launcher)."""
    target = str(project or PROJECT_DIR)
    path = location_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        current = json.loads(path.read_text(encoding="utf-8")).get("project")
    except (OSError, ValueError, AttributeError):
        current = None
    if str(current) != target:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps({"project": target}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
    return path


def desktop_paths() -> tuple[Path, Path]:
    desktop = Path(desktop_dir())
    return desktop / DESKTOP_DIR_LINK, desktop / DESKTOP_LINK_NAME


def _link_launcher(lnk: Path, launcher: Path) -> None:
    try:
        icon = str(_install_icon())
    except Exception:
        icon = pythonw_path()
    create_shortcut(
        lnk,
        pythonw_path(),
        arguments=f'"{launcher}"',
        working_directory=str(launcher.parent),
        description="电量托盘",
        icon_location=icon,
    )


def _remove_legacy() -> list[str]:
    removed = []
    _, desktop_link = desktop_paths()
    legacy = desktop_link.with_name(LEGACY_DESKTOP_FILE)
    try:
        if legacy.is_file() and not legacy.is_symlink():
            head = legacy.read_text(encoding="utf-8", errors="ignore")[:200]
            if "Generated by my_devices.py --install" in head or "PROJECT = " in head:
                legacy.unlink()
                removed.append(str(legacy))
    except OSError:
        pass
    return removed


def install(startup_enabled: bool = True) -> dict:
    """Install (or repair) the launcher, the Startup entry and the Desktop entries."""
    launch_error = ""
    icon_error = ""
    launcher = launcher_path()
    icon = ""
    try:
        launcher = _install_bootstrap()
        _write_location()
    except Exception as exc:
        launch_error = str(exc)
    try:
        icon = str(_install_icon())
    except Exception as exc:
        icon_error = str(exc)

    startup = {"created": False, "error": ""}
    try:
        state = set_startup_enabled(bool(startup_enabled), str(launcher.parent), str(launcher))
        startup = {"created": True, "error": "", "state": state}
    except Exception as exc:
        startup = {"created": False, "error": str(exc)}

    dir_link, desktop_link = desktop_paths()
    links = {}
    try:
        kind = ensure_directory_link(dir_link, PROJECT_DIR)
        links["directory"] = {"ok": True, "path": str(dir_link), "kind": kind}
    except Exception as exc:
        links["directory"] = {"ok": False, "path": str(dir_link), "error": str(exc)}
    try:
        _link_launcher(desktop_link, launcher)
        links["launcher"] = {"ok": True, "path": str(desktop_link), "kind": "shortcut"}
    except Exception as exc:
        links["launcher"] = {"ok": False, "path": str(desktop_link), "error": str(exc)}

    return {
        "project": str(PROJECT_DIR),
        "script": str(SCRIPT_PATH),
        "launcher": str(launcher),
        "launcher_error": launch_error,
        "icon": icon,
        "icon_error": icon_error,
        "location": str(location_path()),
        "startup": startup,
        "startup_state": startup_state(),
        "links": links,
        "removed": _remove_legacy(),
    }


def sync_pointers() -> dict:
    """Cheap silent refresh done at every app start.

    Refreshes the launcher copy and the remembered project path, and repairs the
    Desktop folder link if the project was moved.  Never creates or re-enables
    the Startup entry.
    """
    result = {"launcher": "", "location": "", "desktop": "", "icon": ""}
    try:
        result["launcher"] = str(_install_bootstrap())
        result["location"] = str(_write_location())
    except Exception:
        pass
    try:
        result["icon"] = str(_install_icon())
    except Exception:
        pass
    dir_link, _ = desktop_paths()
    try:
        if os.path.lexists(str(dir_link)) and not _same_target(dir_link, PROJECT_DIR) and _is_reparse_dir(dir_link):
            os.rmdir(str(dir_link))
            result["desktop"] = ensure_directory_link(dir_link, PROJECT_DIR)
    except Exception:
        pass
    return result


def uninstall(remove_links: bool = True) -> dict:
    remove_startup_shortcut()
    removed: list[str] = []
    errors: list[str] = []
    if remove_links:
        dir_link, desktop_link = desktop_paths()
        for path in (dir_link, desktop_link):
            try:
                if _remove_path(path):
                    removed.append(str(path))
            except Exception as exc:
                errors.append(f"{path}: {exc}")
        removed.extend(_remove_legacy())
        for path in (launcher_path(), location_path(), icon_path()):
            try:
                if path.is_file():
                    path.unlink()
                    removed.append(str(path))
            except Exception as exc:
                errors.append(f"{path}: {exc}")
        try:
            os.rmdir(str(app_dir()))
        except OSError:
            pass
    return {"removed": removed, "errors": errors}


def current_state() -> dict:
    dir_link, desktop_link = desktop_paths()
    return {
        "startup": startup_state(),
        "launcher": {"path": str(launcher_path()), "exists": launcher_path().is_file()},
        "icon": {"path": str(icon_path()), "exists": icon_path().is_file()},
        "location": {"path": str(location_path()), "exists": location_path().is_file()},
        "desktop_directory": {
            "path": str(dir_link),
            "exists": dir_link.exists(),
            "kind": link_kind(dir_link),
        },
        "desktop_launcher": {
            "path": str(desktop_link),
            "exists": desktop_link.is_file(),
        },
    }


def describe_install(result: dict) -> str:
    lines = []
    startup = result.get("startup", {})
    if startup.get("created"):
        lines.append(f"启动项：{startup_shortcut_path()}")
    else:
        lines.append(f"启动项创建失败：{startup.get('error', '未知错误')}")
    if result.get("launcher_error"):
        lines.append(f"引导程序安装失败：{result['launcher_error']}")
    else:
        lines.append(f"引导程序（固定位置）：{result.get('launcher', '')}")
        lines.append(f"项目位置记录：{result.get('location', '')}")
    if result.get("icon_error"):
        lines.append(f"图标安装失败：{result['icon_error']}")
    else:
        lines.append(f"图标：{result.get('icon', '')}")
    kinds = {"symlink": "符号链接", "hardlink": "硬链接", "junction": "目录联接", "shortcut": "快捷方式", "copy": "副本"}
    for label, item in result.get("links", {}).items():
        if item.get("ok"):
            kind = kinds.get(str(item.get("kind")), str(item.get("kind")))
            name = "目录" if label == "directory" else "启动"
            lines.append(f"桌面{name}项：{item['path']}（{kind}）")
        else:
            lines.append(f"桌面链接失败：{item.get('path')}：{item.get('error')}")
    for path in result.get("removed", []):
        lines.append(f"已清理旧版入口：{path}")
    return "\n".join(lines)
