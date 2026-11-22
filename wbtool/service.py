"""Refresh orchestration and the small view-model shared by all UIs."""
from __future__ import annotations

import datetime as _dt
import threading
from dataclasses import dataclass, field, replace
from typing import Iterable

from . import state as state_cache
from .config import targets_from_settings
from .hid import HidInterface, enumerate_hid
from .providers import BatteryReading, Target, TargetResult, read_target

@dataclass
class DeviceView:
    target: Target
    level: int | None
    charging: bool | None
    source: str
    detail: str
    updated: _dt.datetime | None
    stale: bool = False
    connected: bool = False
    error: str = ""
    attempts: int = 0

    @property
    def color(self) -> str:
        if self.level is None:
            return "#8b97a8"
        if self.level >= 60:
            return "#35c46a"
        if self.level >= 25:
            return "#e6b422"
        return "#e05252"

    @property
    def status_text(self) -> str:
        if self.charging is True:
            return "充电中"
        if self.charging is False:
            return "放电中"
        if self.level is None:
            return "未读取" if self.connected else "未连接"
        return ""

    @property
    def display_percent(self) -> str:
        return "--" if self.level is None else f"{self.level}%"

    @property
    def updated_text(self) -> str:
        if self.updated is None:
            return "尚未读取"
        suffix = "（上次）" if self.stale else ""
        return self.updated.strftime("%H:%M:%S") + suffix


@dataclass
class RefreshResult:
    views: list[DeviceView]
    elapsed_s: float
    error: str = ""
    results: list[TargetResult] = field(default_factory=list)


class BatteryService:
    """Stateful battery reader.

    The UI calls :meth:`refresh` from a worker thread.  This class itself does
    no sleeping loop and never starts a thread.
    """

    def __init__(self, settings: dict):
        self._lock = threading.Lock()
        self.settings = settings
        self.targets: list[Target] = targets_from_settings(settings)
        self.calibrations: dict[str, dict] = dict(settings.get("calibrations", {}))
        self._last: dict[str, BatteryReading] = {}
        self._last_path: dict[str, str] = {}
        self._last_error: dict[str, str] = {}
        self._last_attempts: dict[str, int] = {}
        self._last_refresh: _dt.datetime | None = None
        self._display_names: dict[str, str] = {}
        self._load_cached_state()

    # ---- last-reading cache ---------------------------------------------
    def _load_cached_state(self) -> None:
        data = state_cache.load()
        readings = data.get("readings")
        if not isinstance(readings, dict):
            return
        for key, item in readings.items():
            if not isinstance(item, dict):
                continue
            try:
                level = int(item.get("level"))
                if not 0 <= level <= 100:
                    continue
                timestamp = float(item.get("timestamp"))
                charging = item.get("charging")
                if charging is not None:
                    charging = bool(charging)
                self._last[str(key).upper()] = BatteryReading(
                    level=level,
                    charging=charging,
                    source=str(item.get("source") or "上次读数"),
                    detail=str(item.get("detail") or ""),
                    raw=b"",
                    timestamp=timestamp,
                )
                name = item.get("name")
                if name:
                    self._display_names[str(key).upper()] = str(name)
            except (TypeError, ValueError, OSError):
                continue

    def _persist_cached_state(self) -> None:
        readings = {}
        for key, last in self._last.items():
            if last.level is None:
                continue
            readings[key] = {
                "level": last.level,
                "charging": last.charging,
                "source": last.source,
                "detail": last.detail,
                "timestamp": last.timestamp,
                "name": self._display_names.get(key, ""),
            }
        state_cache.save({"version": 1, "readings": readings})

    # ---- target display name --------------------------------------------
    def _named_target(self, target: Target, interfaces: Iterable[HidInterface] | None = None) -> Target:
        name = target.name
        if target.kind == "mouse":
            manufacturer = ""
            if interfaces is not None:
                for iface in interfaces:
                    if iface.vid == target.vid and iface.pid == target.pid and iface.manufacturer:
                        manufacturer = iface.manufacturer.strip()
                        break
            if manufacturer:
                name = manufacturer
                self._display_names[target.key] = manufacturer
            elif self._display_names.get(target.key):
                name = self._display_names[target.key]
        if name != target.name:
            return replace(target, name=name)
        return target

    def update_settings(self, settings: dict) -> None:
        with self._lock:
            self.settings = settings
            self.targets = targets_from_settings(settings)
            self.calibrations = dict(settings.get("calibrations", {}))

    def apply_keyboard_reading(self, reading: BatteryReading) -> DeviceView | None:
        """Accept a pushed keyboard packet without running a full refresh."""
        if not reading.has_level:
            return None
        for target in self.targets:
            if target.kind != "keyboard":
                continue
            display_target = self._named_target(target)
            with self._lock:
                self._last[target.key] = reading
                self._display_names[target.key] = display_target.name
                self._last_error[target.key] = ""
            self._persist_cached_state()
            return DeviceView(
                target=display_target,
                level=reading.level,
                charging=reading.charging,
                source=reading.source,
                detail=reading.detail,
                updated=_dt.datetime.fromtimestamp(reading.timestamp),
                stale=False,
                connected=True,
                error="",
                attempts=1,
            )
        return None

    def last_refresh(self) -> _dt.datetime | None:
        return self._last_refresh

    def refresh(self, debug: bool = False, log=None,
                skip_kinds: set[str] | None = None) -> RefreshResult:
        import time

        started = time.perf_counter()
        try:
            interfaces = enumerate_hid()
        except Exception as exc:
            return RefreshResult([], time.perf_counter() - started, f"HID 枚举失败: {exc}")
        if debug and log:
            log(f"enumerated {len(interfaces)} HID interfaces")
        results: list[TargetResult] = []
        views: list[DeviceView] = []
        now = _dt.datetime.now()
        successes = 0
        skipped = skip_kinds or set()
        for target in self.targets:
            display_target = self._named_target(target, interfaces)
            if target.kind in skipped:
                # A background watcher owns this device's HID handle.  Do not
                # send competing queries; reuse the last pushed reading.
                with self._lock:
                    last = self._last.get(target.key)
                if last is not None:
                    view = DeviceView(
                        target=display_target,
                        level=last.level,
                        charging=last.charging,
                        source=last.source,
                        detail="后台监听中",
                        updated=_dt.datetime.fromtimestamp(last.timestamp),
                        stale=True,
                        connected=True,
                        error="",
                        attempts=self._last_attempts.get(target.key, 0),
                    )
                else:
                    view = DeviceView(
                        target=display_target,
                        level=None,
                        charging=None,
                        source="",
                        detail="后台监听中，等待键盘状态包",
                        updated=None,
                        stale=False,
                        connected=True,
                        error="",
                        attempts=0,
                    )
                views.append(view)
                continue
            try:
                result = read_target(
                    display_target,
                    interfaces,
                    self.calibrations,
                    prefer_path=self._last_path.get(target.key),
                )
            except Exception as exc:
                result = TargetResult(display_target, None, f"读取失败: {exc}")
            results.append(result)
            if debug and log:
                if result.reading:
                    log(
                        f"{display_target.name}: {result.reading.level}% "
                        f"source={result.reading.source} attempts={result.attempts} "
                        f"detail={result.reading.detail}"
                    )
                else:
                    log(f"{display_target.name}: no reading ({result.error}, attempts={result.attempts})")

            with self._lock:
                if result.reading and result.reading.has_level:
                    self._last[target.key] = result.reading
                    self._display_names[target.key] = display_target.name
                    if result.path:
                        self._last_path[target.key] = result.path
                    self._last_error[target.key] = ""
                    successes += 1
                    view = DeviceView(
                        target=display_target,
                        level=result.reading.level,
                        charging=result.reading.charging,
                        source=result.reading.source,
                        detail=result.reading.detail,
                        updated=_dt.datetime.fromtimestamp(result.reading.timestamp),
                        stale=False,
                        connected=True,
                        error="",
                        attempts=result.attempts,
                    )
                else:
                    self._last_error[target.key] = result.error
                    last = self._last.get(target.key)
                    if last is not None:
                        # Explicitly keep the previous level and timestamp:
                        # a sleeping keyboard must not reset the cards.
                        view = DeviceView(
                            target=display_target,
                            level=last.level,
                            charging=last.charging,
                            source=last.source,
                            detail=f"{result.error}；正在显示上次读数",
                            updated=_dt.datetime.fromtimestamp(last.timestamp),
                            stale=True,
                            connected=True,
                            error=result.error,
                            attempts=result.attempts,
                        )
                    else:
                        view = DeviceView(
                            target=display_target,
                            level=None,
                            charging=None,
                            source="",
                            detail=result.error,
                            updated=None,
                            stale=False,
                            connected=bool(result.path) or "未检测到" not in result.error,
                            error=result.error,
                            attempts=result.attempts,
                        )
                self._last_attempts[target.key] = result.attempts
                views.append(view)
        if successes:
            self._persist_cached_state()
        self._last_refresh = now
        return RefreshResult(views, time.perf_counter() - started, results=results)

    def cached_views(self) -> list[DeviceView]:
        """Return last known views without touching HID (used for startup)."""
        out = []
        for target in self.targets:
            display_target = self._named_target(target)
            with self._lock:
                last = self._last.get(target.key)
                err = self._last_error.get(target.key, "")
            if last:
                out.append(
                    DeviceView(
                        target=display_target,
                        level=last.level,
                        charging=last.charging,
                        source=last.source,
                        detail=last.detail,
                        updated=_dt.datetime.fromtimestamp(last.timestamp),
                        stale=True,
                        connected=True,
                        error=err,
                        attempts=self._last_attempts.get(target.key, 0),
                    )
                )
            else:
                out.append(
                    DeviceView(
                        target=display_target,
                        level=None,
                        charging=None,
                        source="",
                        detail=err or "正在读取…",
                        updated=None,
                        stale=False,
                        connected=False,
                        error=err,
                    )
                )
        return out
