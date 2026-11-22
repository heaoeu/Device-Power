"""Battery providers, ordered from standards-based to manual calibration."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Iterable

from .hid import (
    BATTERY_USAGE_LABELS,
    HidDevice,
    HidError,
    HidInterface,
    HidP_Feature,
    HidP_Input,
    battery_value_caps,
    scale_battery_value,
)


@dataclass(frozen=True)
class Target:
    id: str
    name: str
    kind: str  # "mouse" / "keyboard"
    vid: int
    pid: int
    profile: str = ""

    @property
    def key(self) -> str:
        return f"{self.vid:04X}:{self.pid:04X}"


@dataclass
class BatteryReading:
    level: int | None
    charging: bool | None = None
    source: str = ""
    detail: str = ""
    voltage_mv: int | None = None
    raw: bytes = b""
    timestamp: float = field(default_factory=time.time)

    @property
    def has_level(self) -> bool:
        return self.level is not None and 0 <= self.level <= 100

    @property
    def status_text(self) -> str:
        if self.charging is True:
            return "充电中"
        if self.charging is False:
            return "放电中"
        return ""


@dataclass
class TargetResult:
    target: Target
    reading: BatteryReading | None
    error: str = ""
    attempts: int = 0
    path: str = ""

    @property
    def connected(self) -> bool:
        return self.reading is not None and self.reading.has_level


class DeviceProvider(ABC):
    """Base class for one vendor battery protocol.

    A new device only needs a subclass with two methods:

    * ``matches(iface, target)`` — decide whether this HID interface belongs
      to the protocol (VID/PID, usage page, target profile, report length).
    * ``read(iface)`` — perform the exchange and return a ``BatteryReading``.

    The protected helpers are the shared HID patterns; subclasses should use
    them instead of duplicating timeouts, retries and report-ID handling.
    """

    name = "vendor"

    @abstractmethod
    def matches(self, iface: HidInterface, target: Target) -> bool:
        """Return True when this provider can read ``iface``."""

    @abstractmethod
    def read(self, iface: HidInterface) -> BatteryReading | None:
        """Read one battery sample from the matched interface."""

    @staticmethod
    def strip_report_id(raw: bytes) -> bytes:
        return _strip_report_id(raw)

    @staticmethod
    def read_until(dev: HidDevice,
                   matcher: Callable[[bytes], BatteryReading | None],
                   timeout_ms: int) -> BatteryReading | None:
        return _read_until(dev, matcher, timeout_ms)

    @staticmethod
    def write_and_match(dev: HidDevice, request: bytes,
                        matcher: Callable[[bytes], BatteryReading | None],
                        timeout_ms: int = 700,
                        attempts: int = 2) -> BatteryReading | None:
        return _write_and_match(dev, request, matcher, timeout_ms, attempts)

    @staticmethod
    def feature_exchange(iface: HidInterface, report_id: int, payload: bytes,
                         length: int,
                         matcher: Callable[[bytes], BatteryReading | None],
                         wait_s: float,
                         attempts: int = 2) -> BatteryReading | None:
        return _feature_exchange(iface, report_id, payload, length, matcher,
                                 wait_s, attempts)


def _strip_report_id(raw: bytes) -> bytes:
    """Return the report payload without the leading HID Report ID.

    HID Class drivers prepend a zero report ID on some (but not all) devices.
    Callers only use this after checking a known signature, so an initial 0 can
    be removed without guessing for arbitrary reports.
    """
    if len(raw) >= 2 and raw[0] == 0x00:
        return raw[1:]
    return raw


def _read_until(dev: HidDevice, matcher: Callable[[bytes], BatteryReading | None], timeout_ms: int) -> BatteryReading | None:
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        remain = max(1, int((deadline - time.monotonic()) * 1000))
        try:
            raw = dev.read_input(min(remain, 300))
        except Exception:
            return None
        if not raw:
            time.sleep(0.01)
            continue
        try:
            result = matcher(raw)
        except Exception:
            result = None
        if result is not None:
            return result
    return None


# ---------------------------------------------------------------------------
# Standard HID battery usage
# ---------------------------------------------------------------------------
def _standard_from_report(iface: HidInterface, dev: HidDevice, report_type: int, raw: bytes, cap) -> BatteryReading | None:
    value = dev.usage_value(report_type, raw, cap)
    if value is None:
        return None
    level = scale_battery_value(value, cap.logical_max)
    label = BATTERY_USAGE_LABELS.get((cap.usage_page, cap.usage), cap.usage_label)
    return BatteryReading(
        level=level,
        charging=None,
        source=f"标准 HID 电量字段（{label}）",
        detail=(
            f"usage={cap.usage_label} report=0x{cap.report_id:02X} "
            f"raw={value} logical_max={cap.logical_max}"
        ),
        raw=raw,
    )


def read_standard_feature(iface: HidInterface) -> BatteryReading | None:
    caps = battery_value_caps(iface, HidP_Feature)
    if not caps or not iface.feature_len:
        return None
    with HidDevice(iface) as dev:
        for cap in caps:
            try:
                raw = dev.get_feature(cap.report_id)
            except HidError:
                continue
            reading = _standard_from_report(iface, dev, HidP_Feature, raw, cap)
            if reading is not None:
                return reading
    return None


def read_standard_input(iface: HidInterface, listen_ms: int = 1200) -> BatteryReading | None:
    caps = battery_value_caps(iface, HidP_Input)
    if not caps or not iface.input_len:
        return None
    with HidDevice(iface) as dev:
        def matcher(raw: bytes) -> BatteryReading | None:
            for cap in caps:
                reading = _standard_from_report(iface, dev, HidP_Input, raw, cap)
                if reading is not None:
                    return reading
            return None

        return _read_until(dev, matcher, listen_ms)


# ---------------------------------------------------------------------------
# Known vendor protocols
# ---------------------------------------------------------------------------
def _write_and_match(dev: HidDevice, request: bytes, matcher, timeout_ms: int = 700, attempts: int = 2) -> BatteryReading | None:
    for attempt in range(max(1, attempts)):
        try:
            dev.drain(8)
            dev.write_output(request)
        except HidError:
            return None
        found = _read_until(dev, matcher, timeout_ms)
        if found is not None:
            return found
        if attempt + 1 < attempts:
            time.sleep(0.06)
    return None


def read_sonix_aula(iface: HidInterface) -> BatteryReading | None:
    """AULA F87ProV2D / SONiX 2.4G receiver.

    Two formats are observed on ``0C45:FEFE`` (MI_03, FF60:0061):

    * the documented 0x20/0x01 query reply (report id stripped):
      ``20 01 00 <level>``;
    * the receiver's periodic status frame, which is what AULA Hub itself
      reports: ``55 10 18 ...`` with the battery percentage at payload[25]
      (raw byte 26 when the report id is present).  The 55 10 18 frame repeats
      roughly every 5 seconds; older AULA/SONiX tools often only listen for
      the 0x20 reply and show 0 while this frame carries the real value.

    The dongle also echoes the host's Output Report back as an Input Report;
    the echo is ignored instead of being mistaken for a 0% reading.
    """
    if iface.usage_page != 0xFF60 or iface.usage != 0x0061:
        return None
    if iface.output_len != 33 or iface.input_len < 27:
        return None

    request = bytearray(33)
    request[1] = 0x20
    request[2] = 0x01
    # AULA Hub sends this query with a zero tail, not a checksum.
    echo_payload = bytes(request[1:])

    def parse(raw: bytes) -> BatteryReading | None:
        payload = raw[1:] if len(raw) >= 2 and raw[0] == 0x00 else raw
        # Ignore the loopback of our own query.
        if payload == echo_payload:
            return None
        if len(payload) >= 4 and payload[0:3] == b"\x20\x01\x00":
            level = int(payload[3])
            if 1 <= level <= 100:
                return BatteryReading(
                    level=level,
                    charging=None,
                    source="SONiX 厂商协议（0x20/0x01）",
                    detail="2.4G dongle battery report",
                    raw=raw,
                )
        # AULA Hub's status packet: command 0x01, battery status at 8,
        # percentage at 9.  This is the layout the web UI uses.
        if len(payload) >= 10 and payload[0] == 0x01:
            level = int(payload[9])
            if 1 <= level <= 100:
                return BatteryReading(
                    level=level,
                    charging=None,
                    source="AULA 状态包（0x01）",
                    detail=f"status=0x{payload[8]:02X}, battery=byte 9",
                    raw=raw,
                )
        # Periodic AULA F87ProV2D status frame: 55 10 18 ... battery at 25.
        if len(payload) >= 26 and payload[0:3] == b"\x55\x10\x18":
            level = int(payload[25])
            if 1 <= level <= 100:
                return BatteryReading(
                    level=level,
                    charging=None,
                    source="AULA 状态帧（55 10 18）",
                    detail=f"status=0x{payload[24]:02X}, battery=byte 25",
                    raw=raw,
                )
        return None

    with HidDevice(iface) as dev:
        try:
            dev.drain(8)
        except HidError:
            pass
        # A sleeping keyboard ignores a single query.  AULA Hub keeps asking
        # until the receiver is awake; poll every 300 ms for up to 8 s instead
        # of writing once.
        deadline = time.monotonic() + 8.0
        next_write = 0.0
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_write:
                try:
                    dev.write_output(bytes(request))
                except HidError:
                    pass
                next_write = now + 0.3
            remain = max(1, int((deadline - now) * 1000))
            try:
                raw = dev.read_input(min(remain, 150))
            except HidError:
                break
            if not raw:
                continue
            reading = parse(raw)
            if reading is not None:
                return reading
    return None

def read_kysona_eweadn(iface: HidInterface) -> BatteryReading | None:
    """A8A5:2255 2.4G mouse receiver (AULA Hub / K-snake / EWEADN family).

    AULA Hub's official mouse page sends a 65-byte output report and listens
    for an input report beginning ``AA 30``; the level is byte 8 and the charge
    flag byte 9.  The K-snake open-source plugin uses the same reply with a
    slightly different request header, so both are tried.
    """
    if iface.usage_page != 0xFF01 or iface.usage != 0x0010:
        return None
    if iface.output_len != 65 or iface.input_len < 10:
        return None

    requests = [
        # AULA Hub SC620 official web driver.
        bytes((0x00, 0x55, 0x30, 0x01, 0x0B, 0x2E, 0x00, 0x00)),
        # K-snake X11 open-source plugin (works with the same receiver).
        bytes((0x00, 0x55, 0x30, 0xA5, 0x0B, 0x2E, 0x01, 0x01)),
    ]

    def matcher(raw: bytes) -> BatteryReading | None:
        payload = raw[1:] if len(raw) >= 2 and raw[0] == 0x00 and raw[1] == 0xAA else raw
        if len(payload) < 10 or payload[0:2] != b"\xaa\x30":
            return None
        level = int(payload[8])
        if not (0 <= level <= 100):
            return None
        return BatteryReading(
            level=level,
            charging=bool(payload[9]),
            source="AULA 2.4G 厂商协议（AA 30）",
            detail="2.4G dongle battery report",
            raw=raw,
        )

    with HidDevice(iface) as dev:
        for header in requests:
            request = bytearray(65)
            request[0:8] = header
            reading = _write_and_match(dev, bytes(request), matcher, timeout_ms=650, attempts=2)
            if reading is not None:
                return reading
    return None

def read_aula_20(iface: HidInterface) -> BatteryReading | None:
    """AULA / MCHOSE BY 2.4G receiver: 20-byte report 0x13, command 0x4A."""
    if iface.usage_page not in (0xFF02, 0xFF04):
        return None
    if iface.output_len != 20 or iface.input_len < 20:
        return None

    request = bytearray(20)
    request[0] = 0x13
    request[1] = 0x4A
    request[2] = 0x01
    request[19] = sum(request[:19]) & 0xFF

    def matcher(raw: bytes) -> BatteryReading | None:
        if len(raw) < 20 or raw[0] != 0x13:
            return None
        if (raw[1] & 0x7F) != 0x4A:
            return None
        if sum(raw[:19]) & 0xFF != raw[19]:
            return None
        state = int(raw[6])
        level = int(raw[5])
        if state == 0x10:
            return BatteryReading(level=100, charging=True, source="AULA BY 2.4G 协议（0x13/0x4A）", detail="cable in", raw=raw)
        if state == 0x01 and 1 <= level <= 100:
            return BatteryReading(level=level, charging=False, source="AULA BY 2.4G 协议（0x13/0x4A）", detail="on battery", raw=raw)
        return None

    with HidDevice(iface) as dev:
        return _write_and_match(dev, bytes(request), matcher, timeout_ms=1200, attempts=2)


def read_aula_13_4a(iface: HidInterface) -> BatteryReading | None:
    """AULA F75/F87-style 0x13/0x4A query on a 20/33/65-byte vendor report."""
    if iface.output_len not in (20, 33, 65) or iface.input_len < 20:
        return None
    if iface.usage_page not in (0xFF02, 0xFF04, 0xFF59, 0xFF60):
        return None

    request = bytearray(iface.output_len)
    request[0] = 0x13
    request[1] = 0x4A
    request[2] = 0x01
    request[19] = sum(request[:19]) & 0xFF

    def matcher(raw: bytes) -> BatteryReading | None:
        if len(raw) < 20 or raw[0] != 0x13 or (raw[1] & 0x7F) != 0x4A:
            return None
        if sum(raw[:19]) & 0xFF != raw[19]:
            return None
        state = int(raw[6])
        level = int(raw[5])
        if state == 0x10:
            return BatteryReading(
                level=100,
                charging=True,
                source="AULA 0x13/0x4A（充电）",
                detail="cable in",
                raw=raw,
            )
        if state == 0x01 and 1 <= level <= 100:
            return BatteryReading(
                level=level,
                charging=False,
                source="AULA 0x13/0x4A",
                detail="on battery",
                raw=raw,
            )
        return None

    with HidDevice(iface) as dev:
        return _write_and_match(dev, bytes(request), matcher, timeout_ms=900, attempts=2)


def _feature_exchange(iface: HidInterface, report_id: int, payload: bytes, length: int,
                      matcher: Callable[[bytes], BatteryReading | None], wait_s: float,
                      attempts: int = 2) -> BatteryReading | None:
    if iface.feature_len < length:
        return None
    body = bytearray(length)
    body[0] = report_id & 0xFF
    body[1 : 1 + len(payload)] = payload[: max(0, length - 1)]
    with HidDevice(iface) as dev:
        for attempt in range(attempts):
            try:
                dev.set_feature(bytes(body))
            except HidError:
                return None
            time.sleep(wait_s)
            try:
                raw = dev.get_feature(report_id, length)
            except HidError:
                continue
            result = matcher(raw)
            if result is not None:
                return result
            if attempt + 1 < attempts:
                time.sleep(0.12)
    return None


def read_endgame(iface: HidInterface) -> BatteryReading | None:
    if iface.usage_page != 0xFF01 or iface.feature_len < 64:
        return None

    def matcher(raw: bytes) -> BatteryReading | None:
        if len(raw) < 20 or raw[1] != 0x01:
            return None
        level = int(raw[16])
        if not (1 <= level <= 100):
            return None
        voltage = int(raw[17]) | (int(raw[18]) << 8)
        return BatteryReading(
            level=level,
            charging=None,
            voltage_mv=voltage if 2500 <= voltage <= 5000 else None,
            source="Endgame Gear 厂商协议（Feature 0xA1）",
            detail="XM2w / OP1W family",
            raw=raw,
        )

    return _feature_exchange(iface, 0xA1, bytes((0xB4,)), 64, matcher, 0.35, 2)


def read_vaxee(iface: HidInterface) -> BatteryReading | None:
    if iface.usage_page != 0xFF05 or iface.feature_len < 64:
        return None

    def matcher(raw: bytes) -> BatteryReading | None:
        if len(raw) < 6:
            return None
        step = int(raw[5])
        if not (0 <= step <= 20):
            return None
        return BatteryReading(
            level=min(100, step * 5),
            charging=None,
            source="VAXEE / ZYGEN 厂商协议（Feature 0x0E）",
            detail="5% steps",
            raw=raw,
        )

    return _feature_exchange(iface, 0x0E, bytes((0xA5, 0x0B, 0x01, 0x01)), 64, matcher, 0.12, 2)


def read_nordic(iface: HidInterface) -> BatteryReading | None:
    if iface.usage_page != 0xFFA0 or iface.feature_len < 32:
        return None

    def matcher(raw: bytes) -> BatteryReading | None:
        if len(raw) < 10:
            return None
        level = int(raw[9])
        if not (0 <= level <= 100):
            return None
        return BatteryReading(
            level=level,
            charging=None,
            source="Nordic 厂商协议（Feature 0x05）",
            detail="nRF 2.4G receiver",
            raw=raw,
        )

    return _feature_exchange(iface, 0x05, bytes((0x15, 0x00, 0x00, 0x01)), 32, matcher, 0.09, 2)


# ---------------------------------------------------------------------------
# Logitech HID++ 2.0
# ---------------------------------------------------------------------------
_HIDPP_STATUS = {
    0: ("discharging", "放电中"),
    1: ("charging", "充电中"),
    2: ("charging", "充电中（末段）"),
    3: ("full", "已充满"),
    4: ("charging", "充电中（慢速）"),
    5: ("unknown", "电池类型无效"),
    6: ("unknown", "温度异常"),
    7: ("unknown", "充电异常"),
}


def _hidpp_call(dev: HidDevice, rid: int, index: int, feature_index: int, function: int,
                params: Iterable[int], timeout_ms: int = 280) -> bytes | None:
    total = 20 if rid == 0x11 else 7
    body = bytearray(total)
    body[0] = rid
    body[1] = index
    body[2] = feature_index
    body[3] = ((function & 0x0F) << 4) | 0x01
    pos = 4
    for value in params:
        if pos >= total:
            break
        body[pos] = value & 0xFF
        pos += 1
    try:
        dev.drain(4)
        dev.write_output(bytes(body))
    except HidError:
        return None
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        try:
            raw = dev.read_input(min(150, max(1, int((deadline - time.monotonic()) * 1000))))
        except HidError:
            return None
        if not raw:
            continue
        if raw[0] != rid or len(raw) < 4:
            continue
        if raw[1] != index or raw[2] != feature_index:
            continue
        if ((raw[3] >> 4) & 0x0F) != function:
            continue
        return raw
    return None


def _hidpp_feature_index(dev: HidDevice, rid: int, index: int, feature_id: int) -> int | None:
    reply = _hidpp_call(dev, rid, index, 0x00, 0x00, (feature_id >> 8, feature_id & 0xFF))
    if not reply or len(reply) < 5:
        return None
    if reply[3] & 0x80:
        return None
    # HID++ 2.0 returns the feature index in the first return-parameter byte
    # (offset 4).  A few clones shift it; only fall back when offset 4 is zero.
    for pos in (4, 5, 6):
        if len(reply) > pos and reply[pos]:
            return int(reply[pos])
    return None


def _hidpp_probe(iface: HidInterface) -> BatteryReading | None:
    with HidDevice(iface) as dev:
        for rid in (0x11, 0x10):
            need = 20 if rid == 0x11 else 7
            if iface.output_len < need or iface.input_len < need:
                continue
            for index in (0xFF, 1, 2, 3, 4, 5, 6):
                fidx = _hidpp_feature_index(dev, rid, index, 0x1004)
                if fidx:
                    reply = _hidpp_call(dev, rid, index, fidx, 0x00, ())
                    if reply and len(reply) >= 6 and not (reply[3] & 0x80):
                        raw_level = int(reply[4])
                        status = int(reply[5])
                        _, text = _HIDPP_STATUS.get(status, ("unknown", f"状态 {status}"))
                        return BatteryReading(
                            level=max(0, min(100, raw_level)),
                            charging=status in (1, 2, 3, 4),
                            source="罗技 HID++ 2.0（0x1004 统一电池）",
                            detail=f"device index {index}: {text}",
                            raw=reply,
                        )
                fidx = _hidpp_feature_index(dev, rid, index, 0x1000)
                if fidx:
                    reply = _hidpp_call(dev, rid, index, fidx, 0x00, ())
                    if reply and len(reply) >= 7 and not (reply[3] & 0x80):
                        level = int(reply[4])
                        status = int(reply[6])
                        _, text = _HIDPP_STATUS.get(status, ("unknown", f"状态 {status}"))
                        if level == 0 and status in (1, 3):
                            level = 100 if status == 3 else 80
                        return BatteryReading(
                            level=max(0, min(100, level)),
                            charging=status in (1, 2, 3, 4),
                            source="罗技 HID++ 2.0（0x1000 电池状态）",
                            detail=f"device index {index}: {text}",
                            raw=reply,
                        )
                fidx = _hidpp_feature_index(dev, rid, index, 0x1001)
                if fidx:
                    reply = _hidpp_call(dev, rid, index, fidx, 0x00, ())
                    if reply and len(reply) >= 6 and not (reply[3] & 0x80):
                        mv = int(reply[4]) | (int(reply[5]) << 8)
                        if 2000 <= mv <= 5000:
                            return BatteryReading(
                                level=None,
                                charging=None,
                                voltage_mv=mv,
                                source="罗技 HID++ 2.0（0x1001 电池电压）",
                                detail=f"device index {index}: {mv} mV（仅电压）",
                                raw=reply,
                            )
    return None


# ---------------------------------------------------------------------------
# Manual calibration
# ---------------------------------------------------------------------------
def read_calibration(iface: HidInterface, cal: dict) -> BatteryReading | None:
    if not cal:
        return None
    mode = str(cal.get("mode", "input")).lower()
    try:
        index = int(str(cal.get("index", 0)), 10)
        scale = float(cal.get("scale", 1))
        offset = float(cal.get("offset", 0))
        report_text = str(cal.get("report_id", "0")).strip()
        if report_text.lower().startswith("0x"):
            report_id = int(report_text, 16)
        else:
            try:
                report_id = int(report_text, 16)
            except ValueError:
                report_id = int(report_text, 0)
    except (TypeError, ValueError):
        return None
    if index < 0 or scale == 0:
        return None
    charging_index = cal.get("charging_index")
    try:
        charging_index = int(str(charging_index), 10) if charging_index not in (None, "") else None
    except (TypeError, ValueError):
        charging_index = None

    raw: bytes | None = None
    if mode == "feature":
        if not iface.feature_len:
            return None
        with HidDevice(iface) as dev:
            try:
                raw = dev.get_feature(report_id)
            except HidError:
                return None
    else:
        if not iface.input_len:
            return None
        with HidDevice(iface) as dev:
            deadline = time.monotonic() + 0.8
            while time.monotonic() < deadline:
                try:
                    candidate = dev.read_input(200)
                except HidError:
                    break
                if not candidate:
                    continue
                if report_id and candidate[0] != report_id:
                    continue
                raw = candidate
                break

    if not raw or index >= len(raw):
        return None
    value = round(int(raw[index]) * scale + offset)
    if not (0 <= value <= 100):
        return None
    charging = None
    if charging_index is not None and charging_index < len(raw):
        charging = bool(raw[charging_index])
    return BatteryReading(
        level=value,
        charging=charging,
        source=f"手动标定（{mode}, report 0x{report_id:02X}, byte {index}）",
        detail=f"raw={raw[index]} × {scale} + {offset}",
        raw=raw,
    )


# ---------------------------------------------------------------------------
# Target-level scheduler
# ---------------------------------------------------------------------------
class SonixAulaProvider(DeviceProvider):
    name = "sonix"

    def matches(self, iface, target):
        profile = target.profile.lower()
        return (
            profile in ("sonix", "sonix_aula")
            or (iface.vid == 0x0C45 and iface.pid in (0xFEFE, 0xFDFD))
            or (iface.vid == 0x05AC and iface.pid == 0x024F)
        )

    def read(self, iface):
        return read_sonix_aula(iface)


class Aula13Provider(DeviceProvider):
    name = "aula13"

    def matches(self, iface, target):
        return iface.usage_page in (0xFF59, 0xFF60) and iface.output_len in (33, 65)

    def read(self, iface):
        return read_aula_13_4a(iface)


class KysonaProvider(DeviceProvider):
    name = "kysona"

    def matches(self, iface, target):
        profile = target.profile.lower()
        return (
            profile in ("kysona", "eweadn", "kysona_a8a5")
            or (iface.vid in (0xA8A5, 0xA8A4) and iface.pid == 0x2255)
        )

    def read(self, iface):
        return read_kysona_eweadn(iface)


class EndgameProvider(DeviceProvider):
    name = "endgame"

    def matches(self, iface, target):
        return iface.vid == 0x3367

    def read(self, iface):
        return read_endgame(iface)


class VaxeeProvider(DeviceProvider):
    name = "vaxee"

    def matches(self, iface, target):
        return iface.vid == 0x3057

    def read(self, iface):
        return read_vaxee(iface)


class NordicProvider(DeviceProvider):
    name = "nordic"

    def matches(self, iface, target):
        return iface.vid == 0x1915

    def read(self, iface):
        return read_nordic(iface)


class LogitechHidppProvider(DeviceProvider):
    name = "logitech"

    def matches(self, iface, target):
        return iface.vid == 0x046D

    def read(self, iface):
        return _hidpp_probe(iface)


class Aula20Provider(DeviceProvider):
    name = "aula20"

    def matches(self, iface, target):
        return iface.usage_page in (0xFF02, 0xFF04) and iface.output_len == 20

    def read(self, iface):
        return read_aula_20(iface)


# Order matters: the first matching provider for an interface is tried first.
VENDOR_PROVIDERS: list[DeviceProvider] = [
    SonixAulaProvider(),
    Aula13Provider(),
    KysonaProvider(),
    EndgameProvider(),
    VaxeeProvider(),
    NordicProvider(),
    LogitechHidppProvider(),
    Aula20Provider(),
]


def register_provider(provider: DeviceProvider) -> None:
    """Register a provider for a new device family.

    Custom providers are appended after the built-in ones; insert manually
    before a broader provider if it must take priority.
    """
    VENDOR_PROVIDERS.append(provider)


def _candidate_interfaces(target: Target, interfaces: Iterable[HidInterface], prefer_path: str | None) -> list[HidInterface]:
    matches = [i for i in interfaces if i.vid == target.vid and i.pid == target.pid and not i.error]
    if prefer_path:
        matches.sort(key=lambda i: (i.path != prefer_path, i.usage_page, i.usage))
    else:
        # Vendor collections before keyboard/mouse collections; output-capable
        # interfaces before pure-input ones.
        matches.sort(key=lambda i: (i.is_system_reserved, not i.is_vendor, not i.output_len, i.usage_page, i.usage))
    return matches


def _vendor_providers(iface: HidInterface, target: Target) -> list[DeviceProvider]:
    return [provider for provider in VENDOR_PROVIDERS if provider.matches(iface, target)]


def read_target(target: Target, interfaces: Iterable[HidInterface], calibrations: dict[str, dict],
                prefer_path: str | None = None) -> TargetResult:
    candidates = _candidate_interfaces(target, interfaces, prefer_path)
    if not candidates:
        return TargetResult(target, None, "未检测到该设备（VID:PID 不匹配或已拔出）")
    attempts = 0
    last_error = ""
    # Keep a single calibration lookup per target; manual profiles are keyed by
    # VID:PID and may be used against any of its vendor interfaces.
    cal = calibrations.get(target.key) or calibrations.get(target.key.upper()) or {}

    for iface in candidates:
        # A cached path is tried first, but the full chain still runs if it fails.
        standard_caps = battery_value_caps(iface, HidP_Feature)
        if standard_caps:
            attempts += 1
            try:
                reading = read_standard_feature(iface)
            except Exception as exc:
                reading = None
                last_error = f"标准 Feature 读取失败: {exc}"
            if reading and reading.has_level:
                return TargetResult(target, reading, attempts=attempts, path=iface.path)
            if reading and reading.detail:
                last_error = reading.detail

        if battery_value_caps(iface, HidP_Input):
            attempts += 1
            try:
                reading = read_standard_input(iface)
            except Exception as exc:
                reading = None
                last_error = f"标准 Input 读取失败: {exc}"
            if reading and reading.has_level:
                return TargetResult(target, reading, attempts=attempts, path=iface.path)
            if reading and reading.detail:
                last_error = reading.detail

        for provider in _vendor_providers(iface, target):
            attempts += 1
            try:
                reading = provider.read(iface)
            except Exception as exc:
                reading = None
                last_error = f"{provider.name} 协议失败: {exc}"
            if reading is not None:
                if reading.has_level:
                    return TargetResult(target, reading, attempts=attempts, path=iface.path)
                if reading.detail:
                    last_error = reading.detail
                if reading.voltage_mv is not None and not reading.has_level:
                    # A voltage-only result is informative but not a percentage;
                    # keep the text for the panel but keep looking for a level.
                    last_error = f"{reading.source}: {reading.detail}"

        if cal:
            attempts += 1
            try:
                reading = read_calibration(iface, cal)
            except Exception as exc:
                reading = None
                last_error = f"手动标定失败: {exc}"
            if reading and reading.has_level:
                return TargetResult(target, reading, attempts=attempts, path=iface.path)

    return TargetResult(
        target,
        None,
        last_error or "设备已连接，但没有可用的电量协议（可尝试唤醒设备或在调试版中手动标定）",
        attempts=attempts,
    )
