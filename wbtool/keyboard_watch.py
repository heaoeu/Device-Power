"""Passive listener for the AULA 0C45:FEFE 2.4G keyboard.

The keyboard pushes status packets when it is awake.  Holding the vendor
collection open and reading continuously is how the web UI sees them; polling
with one short-lived handle misses most packets.  A refresh sends the same
three-frame AA 10 read request used by Aula Hub, which asks the keyboard for its
status block; the resulting 55 10 18 frame carries the battery.
"""
from __future__ import annotations

import threading
import time

from .hid import HidDevice, HidInterface, enumerate_hid
from .providers import BatteryReading


def parse_status(raw: bytes) -> BatteryReading | None:
    if not raw:
        return None
    payload = raw[1:] if len(raw) >= 2 and raw[0] == 0x00 else raw
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


class KeyboardWatcher(threading.Thread):
    def __init__(self, vid: int = 0x0C45, pid: int = 0xFEFE):
        super().__init__(name="keyboard-watch", daemon=True)
        self.vid = vid
        self.pid = pid
        self._lock = threading.Lock()
        self._reading: BatteryReading | None = None
        self._seen_at = 0.0
        self._stop = threading.Event()
        self._nudge = threading.Event()
        self._nudge_frames: list[bytes] = []
        for seq in (
            b"\xAA\x10\x18\x00",
            b"\xAA\x10\x18\x18",
            b"\xAA\x10\x08\x30\x00\x00\x01\x00",
        ):
            frame = bytearray(33)
            frame[1 : 1 + len(seq)] = seq
            self._nudge_frames.append(bytes(frame))

    def snapshot(self) -> tuple[BatteryReading | None, float]:
        with self._lock:
            return self._reading, self._seen_at

    def stop(self) -> None:
        self._stop.set()

    def nudge(self) -> None:
        """Ask the keyboard for its 0x10 status block (battery)."""
        self._nudge.set()

    def _interface(self) -> HidInterface | None:
        try:
            candidates = [
                iface
                for iface in enumerate_hid()
                if iface.vid == self.vid
                and iface.pid == self.pid
                and not iface.error
                and (iface.usage_page, iface.usage) == (0xFF60, 0x0061)
            ]
            return candidates[0] if candidates else None
        except Exception:
            return None

    def run(self) -> None:
        while not self._stop.is_set():
            iface = self._interface()
            if iface is None:
                self._stop.wait(5.0)
                continue
            try:
                with HidDevice(iface) as dev:
                    dev.drain(8)
                    while not self._stop.is_set():
                        if self._nudge.is_set():
                            self._nudge.clear()
                            try:
                                for frame in self._nudge_frames:
                                    dev.write_output(frame)
                                    time.sleep(0.08)
                            except Exception:
                                pass
                        raw = dev.read_input(500)
                        if not raw:
                            continue
                        reading = parse_status(raw)
                        if reading is None:
                            continue
                        with self._lock:
                            self._reading = reading
                            self._seen_at = time.time()
            except Exception:
                pass
            self._stop.wait(3.0)
