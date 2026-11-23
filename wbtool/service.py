"""Refresh orchestration and the small view-model shared by all UIs."""
from __future__ import annotations

import datetime as _dt
import threading
from dataclasses import dataclass, field, replace
from typing import Iterable

from . import state as state_cache
from .config import discovered_from_settings, targets_from_settings
from .hid import (
    HidInterface,
    HidP_Feature,
    HidP_Input,
    battery_value_caps,
    enumerate_hid,
)
from .providers import (
    VENDOR_PROVIDERS,
    BatteryReading,
    Target,
    TargetResult,
    read_target,
)


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
    transport: str = ""     # "2.4G" / "蓝牙" / "有线" / "" when unknown

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
        return self.updated.strftime("%H:%M:%S")


@dataclass
class RefreshResult:
    views: list[DeviceView]
    elapsed_s: float
    error: str = ""
    results: list[TargetResult] = field(default_factory=list)
    discovered: list[dict] = field(default_factory=list)
    settings_changed: bool = False  # a device kind was corrected from HID data


# Standard HID usages that name a device class.  A wireless receiver exposes a
# keyboard collection for its hotkeys even when it belongs to a mouse, so
# "keyboard wins" is not a valid rule; the main function is the collection on
# the lowest interface number (MI_00) and extra functions come after it.
_BOOT_KINDS = {
    (0x01, 0x06): "keyboard",
    (0x01, 0x02): "mouse",
}
_KIND_ORDER = {"keyboard": 0, "mouse": 1}


def _interface_number(iface: HidInterface) -> int:
    try:
        return int(iface.mi, 16)
    except (TypeError, ValueError):
        return 0xFF


def _guess_kind(interfaces: Iterable[HidInterface]) -> str:
    """Pick the device class from the collections the device reports.

    Composite wireless devices expose several top-level collections: the main
    function sits on the lowest interface number, while a 2.4G dongle adds
    keyboard/consumer collections after its mouse and a keyboard adds mouse
    and consumer collections after its keyboard.  Consumer-control-only
    devices (headsets) have no boot collection at all.
    """
    best: tuple[tuple[int, int], str] | None = None
    consumer = False
    for iface in interfaces:
        if iface.error:
            continue
        usage = (iface.usage_page, iface.usage)
        if usage == (0x0C, 0x01):
            consumer = True
            continue
        kind = _BOOT_KINDS.get(usage)
        if not kind:
            continue
        rank = (_interface_number(iface), _KIND_ORDER[kind])
        if best is None or rank < best[0]:
            best = (rank, kind)
    if best is not None:
        return best[1]
    return "headset" if consumer else "device"


def _vendor_candidate(iface: HidInterface) -> bool:
    """True when a built-in vendor protocol claims this interface.

    Allows a deleted device (whose VID/PID is no longer configured) to be found
    again by ``扫描设备`` even when it has no standard battery usage.
    """
    probe = Target(id="probe", name="", kind="", vid=iface.vid, pid=iface.pid)
    for provider in VENDOR_PROVIDERS:
        try:
            if provider.matches(iface, probe):
                return True
        except Exception:
            continue
    return False


def _device_name(interfaces: Iterable[HidInterface], vid: int, pid: int) -> str:
    """A readable name for a discovered device (product first, receiver last)."""
    items = list(interfaces)
    for iface in items:
        product = (iface.product or "").strip()
        if product and "USB Receiver" not in product:
            return product
    for iface in items:
        product = (iface.product or "").strip()
        if product:
            return product
    for iface in items:
        manufacturer = (iface.manufacturer or "").strip()
        if manufacturer:
            return f"{manufacturer} 外设"
    return f"外设 {vid:04X}:{pid:04X}"


def _live_kind(target: Target, interfaces: Iterable[HidInterface]) -> str:
    """The kind implied by the collections this device currently reports."""
    own = [
        iface
        for iface in interfaces
        if (iface.vid, iface.pid) == (target.vid, target.pid)
    ]
    if not own:
        return ""
    kind = _guess_kind(own)
    return "" if kind == "device" else kind


class BatteryService:
    """Stateful battery reader.

    The UI calls :meth:`refresh` from a worker thread.  This class itself does
    no sleeping loop and never starts a thread.
    """

    def __init__(self, settings: dict):
        self._lock = threading.Lock()
        self.settings = settings
        self._base_targets: list[Target] = targets_from_settings(settings)
        self.discovered_targets: list[Target] = discovered_from_settings(settings)
        self.targets: list[Target] = self._base_targets + self.discovered_targets
        self._keyboard_key = next(
            (target.key for target in self._base_targets if target.kind == "keyboard"), ""
        )
        self.removed_keys: set[str] = {
            str(item).upper() for item in settings.get("removed", []) if str(item).strip()
        }
        self.calibrations: dict[str, dict] = dict(settings.get("calibrations", {}))
        self._last: dict[str, BatteryReading] = {}
        self._last_path: dict[str, str] = {}
        self._last_error: dict[str, str] = {}
        self._last_attempts: dict[str, int] = {}
        self._last_transport: dict[str, str] = {}
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
                transport = item.get("transport")
                if transport:
                    self._last_transport[str(key).upper()] = str(transport)
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
                "transport": self._last_transport.get(key, ""),
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
            self._base_targets = targets_from_settings(settings)
            self.discovered_targets = discovered_from_settings(settings)
            self.targets = self._base_targets + self.discovered_targets
            self._keyboard_key = next(
                (target.key for target in self._base_targets if target.kind == "keyboard"), ""
            )
            self.removed_keys = {
                str(item).upper() for item in settings.get("removed", []) if str(item).strip()
            }
            self.calibrations = dict(settings.get("calibrations", {}))

    def apply_keyboard_reading(self, reading: BatteryReading) -> DeviceView | None:
        """Accept a pushed keyboard packet without running a full refresh."""
        if not reading.has_level:
            return None
        for target in self.targets:
            if target.key != self._keyboard_key:
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
                transport=self._last_transport.get(target.key, ""),
            )
        return None

    def last_refresh(self) -> _dt.datetime | None:
        return self._last_refresh

    def _adopt_kinds(self, interfaces: Iterable[HidInterface]) -> bool:
        """Correct stored kinds from the HID collections currently reported.

        A device type is a property of the device, not of ``settings.json``:
        an entry written by an older scan (or by hand) must not pin the card to
        the wrong icon when the hardware says otherwise.
        """
        live = {
            target.id: kind
            for target in self.targets
            if (kind := _live_kind(target, interfaces))
        }
        if not live:
            return False
        changed = False
        for attr in ("targets", "_base_targets", "discovered_targets"):
            updated: list[Target] = []
            for target in getattr(self, attr):
                kind = live.get(target.id, "")
                if kind and kind != target.kind:
                    target = replace(target, kind=kind)
                    changed = True
                updated.append(target)
            setattr(self, attr, updated)
        if not changed:
            return False
        for collection in ("targets", "discovered"):
            for item in self.settings.get(collection) or []:
                kind = live.get(str(item.get("id", "")), "")
                if kind and str(item.get("kind", "")) != kind:
                    item["kind"] = kind
        return True

    def refresh(self, debug: bool = False, log=None,
                skip_kinds: set[str] | None = None,
                discover: bool = False) -> RefreshResult:
        import time

        started = time.perf_counter()
        try:
            interfaces = enumerate_hid()
        except Exception as exc:
            return RefreshResult([], time.perf_counter() - started, f"HID 枚举失败: {exc}")
        if debug and log:
            log(f"enumerated {len(interfaces)} HID interfaces")
        found: list[dict] = []
        if discover:
            try:
                found, _new_views = self._discover(interfaces, manual=False)
            except Exception as exc:
                found = []
                if debug and log:
                    log(f"discovery failed: {exc}")
            if debug and log and found:
                log("discovered: " + ", ".join(item["name"] for item in found))
        results: list[TargetResult] = []
        views: list[DeviceView] = []
        now = _dt.datetime.now()
        successes = 0
        skipped = skip_kinds or set()
        kinds_changed = self._adopt_kinds(interfaces)
        for target in self.targets:
            display_target = self._named_target(target, interfaces)
            if "keyboard" in skipped and target.key == self._keyboard_key:
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
        # The link type is a property of the HID interface, not of the battery
        # payload, so it is filled in after the reads (and remembered, because a
        # sleeping device stops enumerating but its 2.4G/Bluetooth link does not
        # change).
        for target, view in zip(self.targets, views):
            transport = ""
            for iface in interfaces:
                if (iface.vid, iface.pid) == (target.vid, target.pid) and iface.transport:
                    transport = iface.transport
                    break
            if transport:
                self._last_transport[target.key] = transport
            view.transport = transport or self._last_transport.get(target.key, "")
        if successes:
            self._persist_cached_state()
        self._last_refresh = now
        return RefreshResult(
            views,
            time.perf_counter() - started,
            results=results,
            discovered=found,
            settings_changed=kinds_changed,
        )

    def _discover(self, interfaces: Iterable[HidInterface], manual: bool = False):
        """Find devices that answer a standard HID battery query.

        Only interfaces that advertise a battery usage are probed (a few
        hundred milliseconds each), so a scan stays cheap on a busy HID bus.
        Returns ``(settings entries, views)``; the new targets are appended to
        ``self.targets`` so later refreshes read them like configured devices.
        """
        known = {(target.vid, target.pid) for target in self.targets}
        by_device: dict[tuple[int, int], list[HidInterface]] = {}
        for iface in interfaces:
            key = (iface.vid, iface.pid)
            if iface.error or not iface.vid or key in known:
                continue
            if not manual and f"{iface.vid:04X}:{iface.pid:04X}" in self.removed_keys:
                continue
            by_device.setdefault(key, []).append(iface)

        groups: dict[tuple[int, int], list[HidInterface]] = {}
        for key, ifaces in by_device.items():
            # Collect every collection of the device before qualifying it: the
            # kind needs the main (lowest MI) collection, which is not
            # necessarily the one that advertises the battery usage.
            if any(
                battery_value_caps(iface, HidP_Feature)
                or battery_value_caps(iface, HidP_Input)
                or _vendor_candidate(iface)
                for iface in ifaces
            ):
                groups[key] = ifaces

        found: list[dict] = []
        views: list[DeviceView] = []
        for (vid, pid), ifaces in groups.items():
            target = Target(
                id=f"HID-{vid:04X}-{pid:04X}",
                name=_device_name(ifaces, vid, pid),
                kind=_guess_kind(ifaces),
                vid=vid,
                pid=pid,
            )
            try:
                result = read_target(target, ifaces, self.calibrations)
            except Exception:
                continue
            reading = result.reading
            if not (reading and reading.has_level):
                continue
            transport = next((iface.transport for iface in ifaces if iface.transport), "")
            self.discovered_targets.append(target)
            self.targets.append(target)
            self.removed_keys.discard(f"{vid:04X}:{pid:04X}")
            self.settings["removed"] = sorted(self.removed_keys)
            with self._lock:
                self._last[target.key] = reading
                self._display_names[target.key] = target.name
                self._last_error[target.key] = ""
                self._last_attempts[target.key] = result.attempts
                if result.path:
                    self._last_path[target.key] = result.path
                if transport:
                    self._last_transport[target.key] = transport
            views.append(
                DeviceView(
                    target=target,
                    level=reading.level,
                    charging=reading.charging,
                    source=reading.source,
                    detail=reading.detail,
                    updated=_dt.datetime.fromtimestamp(reading.timestamp),
                    stale=False,
                    connected=True,
                    error="",
                    attempts=result.attempts,
                    transport=transport or self._last_transport.get(target.key, ""),
                )
            )
            found.append(
                {
                    "id": target.id,
                    "kind": target.kind,
                    "name": target.name,
                    "vid": f"{vid:04X}",
                    "pid": f"{pid:04X}",
                }
            )
        return found, views

    def scan_new(self):
        """Look for unknown devices only; known ones are left to normal refresh."""
        found, views = self._discover(enumerate_hid(), manual=True)
        if found:
            self._persist_cached_state()
        return found, views

    def remove_target(self, device_id: str) -> None:
        """Forget a device: drop its target, caches and cached state entry."""
        removed = [target for target in self.targets if target.id == device_id]
        with self._lock:
            self.targets = [target for target in self.targets if target.id != device_id]
            self._base_targets = [target for target in self._base_targets if target.id != device_id]
            self.discovered_targets = [
                target for target in self.discovered_targets if target.id != device_id
            ]
            for target in removed:
                self._last.pop(target.key, None)
                self._last_path.pop(target.key, None)
                self._last_error.pop(target.key, None)
                self._last_attempts.pop(target.key, None)
                self._last_transport.pop(target.key, None)
                self._display_names.pop(target.key, None)
        if removed:
            self._persist_cached_state()

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
                        transport=self._last_transport.get(target.key, ""),
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
                        transport=self._last_transport.get(target.key, ""),
                    )
                )
        return out
