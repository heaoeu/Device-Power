"""Settings and calibration persistence.

The only file this project writes at runtime is ``settings.json``.  It is a
configuration file, not a log: no timestamps are appended and no device traffic
is recorded.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from .providers import Target

PROJECT_DIR = Path(__file__).resolve().parent.parent
SETTINGS_PATH = PROJECT_DIR / "settings.json"
DEFAULT_REFRESH_SECONDS = 60
MIN_REFRESH_SECONDS = 30
MAX_REFRESH_SECONDS = 30 * 60

DEFAULT_SETTINGS: dict[str, Any] = {
    "refresh_seconds": DEFAULT_REFRESH_SECONDS,
    "show_tray_icon": True,
    "show_taskbar_strip": True,
    "tray_devices": ["mouse", "keyboard"],
    "targets": [
        {
            "id": "mouse",
            "kind": "mouse",
            "name": "无线鼠标",
            "vid": "A8A5",
            "pid": "2255",
            "profile": "kysona",
        },
        {
            "id": "keyboard",
            "kind": "keyboard",
            "name": "AULA F87 Pro V2 键盘",
            "vid": "0C45",
            "pid": "FEFE",
            "profile": "sonix",
        },
    ],
    "calibrations": {},
}


def _to_int(value: Any, default: int = 0) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text, 0)
        except ValueError:
            try:
                return int(text, 16)
            except ValueError:
                return default
    return default


def _to_hex_int(value: Any, default: int = 0) -> int:
    """VID/PID strings in settings are conventionally hexadecimal."""
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text, 16)
        except ValueError:
            return default
    return default


def normalize_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    data = dict(DEFAULT_SETTINGS)
    if isinstance(raw, dict):
        data.update(raw)
    try:
        seconds = int(data.get("refresh_seconds", DEFAULT_REFRESH_SECONDS))
    except (TypeError, ValueError):
        seconds = DEFAULT_REFRESH_SECONDS
    data["refresh_seconds"] = max(MIN_REFRESH_SECONDS, min(MAX_REFRESH_SECONDS, seconds))

    targets: list[dict[str, Any]] = []
    raw_targets = data.get("targets")
    if isinstance(raw_targets, list):
        for item in raw_targets[:2]:
            if not isinstance(item, dict):
                continue
            vid = _to_hex_int(item.get("vid"), 0)
            pid = _to_hex_int(item.get("pid"), 0)
            if vid <= 0 or pid <= 0:
                continue
            targets.append(
                {
                    "id": str(item.get("id") or f"device{len(targets) + 1}"),
                    "kind": str(item.get("kind") or "device"),
                    "name": str(item.get("name") or f"设备 {vid:04X}:{pid:04X}"),
                    "vid": f"{vid:04X}",
                    "pid": f"{pid:04X}",
                    "profile": str(item.get("profile") or ""),
                }
            )
    if not targets:
        targets = json.loads(json.dumps(DEFAULT_SETTINGS["targets"]))
    data["targets"] = targets

    raw_devices = data.get("tray_devices")
    if isinstance(raw_devices, list):
        wanted = {str(item) for item in raw_devices}
        data["tray_devices"] = [kind for kind in ("mouse", "keyboard") if kind in wanted]
    else:
        data["tray_devices"] = ["mouse", "keyboard"]

    cals = data.get("calibrations")
    if isinstance(cals, dict):
        data["calibrations"] = {str(key).upper(): value for key, value in cals.items() if isinstance(value, dict)}
    else:
        data["calibrations"] = {}
    return data


def load_settings() -> dict[str, Any]:
    raw = None
    if SETTINGS_PATH.exists():
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            raw = None
    data = normalize_settings(raw)
    if raw is None and not SETTINGS_PATH.exists():
        try:
            save_settings(data)
        except OSError:
            pass
    return data


def save_settings(data: dict[str, Any]) -> None:
    """Atomically write settings (no log files, no temp litter)."""
    data = normalize_settings(data)
    PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".settings-", suffix=".tmp", dir=str(PROJECT_DIR))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, SETTINGS_PATH)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


def targets_from_settings(data: dict[str, Any]) -> list[Target]:
    out: list[Target] = []
    for item in data.get("targets", []):
        out.append(
            Target(
                id=str(item.get("id", "")),
                name=str(item.get("name", "")),
                kind=str(item.get("kind", "device")),
                vid=_to_hex_int(item.get("vid"), 0),
                pid=_to_hex_int(item.get("pid"), 0),
                profile=str(item.get("profile", "")),
            )
        )
    return out


def set_refresh_seconds(data: dict[str, Any], seconds: int) -> dict[str, Any]:
    data = dict(data)
    data["refresh_seconds"] = max(MIN_REFRESH_SECONDS, min(MAX_REFRESH_SECONDS, int(seconds)))
    return normalize_settings(data)


def save_calibration(calibrations: dict[str, dict], key: str, calibration: dict) -> None:
    calibrations[key.upper()] = calibration


def calibration_for(calibrations: dict[str, dict], target: Target) -> dict:
    return calibrations.get(target.key) or calibrations.get(target.key.upper()) or {}


def ensure_project_cwd() -> None:
    """Make relative resource access predictable when launched from a link."""
    try:
        os.chdir(PROJECT_DIR)
    except OSError:
        pass


def project_display_path() -> str:
    return str(PROJECT_DIR)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))
