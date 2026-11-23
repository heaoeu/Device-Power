"""Minimal HID access layer built directly on setupapi/hid/kernel32.

Design rules that matter for stability:

* enumeration only uses ``CreateFileW(..., 0, ...)`` so system-exclusive
  keyboard/mouse top-level collections can still be inspected;
* every Input Report read runs on its own ``FILE_FLAG_OVERLAPPED`` handle with
  an event and a timeout (``CancelIoEx`` on timeout);
* Output/Feature writes use a different handle, so a blocking read can never
  share a handle with a concurrent write/IOCTL.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Iterable

from .win32 import (
    ERROR_IO_PENDING,
    ERROR_NO_MORE_ITEMS,
    FILE_FLAG_OVERLAPPED,
    FILE_SHARE_READ,
    FILE_SHARE_WRITE,
    GENERIC_READ,
    GENERIC_WRITE,
    GUID,
    OPEN_EXISTING,
    OVERLAPPED,
    WAIT_OBJECT_0,
    guid_from_string,
    is_invalid_handle,
    kernel32,
    last_error,
    win_error_text,
)

setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
hid = ctypes.WinDLL("hid", use_last_error=True)
cfgmgr32 = ctypes.WinDLL("cfgmgr32", use_last_error=True)

DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10
HIDP_STATUS_SUCCESS = 0x00110000

HidP_Input = 0
HidP_Output = 1
HidP_Feature = 2

HID_GUID = guid_from_string("4D1E55B2-F16F-11CF-88CB-001111000030")

# Windows takes these top-level collections exclusively.  They can be opened
# with desired access 0, which is enough for HidD_GetFeature and property reads
# but not for output/input traffic.
SYSTEM_RESERVED_USAGES = {
    (0x0001, 0x0001),  # Pointer
    (0x0001, 0x0002),  # Mouse
    (0x0001, 0x0006),  # Keyboard
    (0x0001, 0x0007),  # Keypad
    (0x000D, 0x0001),  # Pen
    (0x000D, 0x0002),  # Pen
    (0x000D, 0x0004),  # Touchscreen
    (0x000D, 0x0005),  # Precision Touchpad
}

BATTERY_USAGES = {
    (0x0006, 0x0020),  # Generic Device Controls / Battery Strength
    (0x000D, 0x003B),  # Digitizer / Battery Strength (pens)
    (0x0085, 0x0001),  # Battery System / Absolute State of Charge
    (0x0085, 0x0065),  # Battery System / Battery Level
    (0x0085, 0x0066),  # Battery System / Relative State of Charge
}

BATTERY_USAGE_LABELS = {
    (0x0006, 0x0020): "Generic Device Controls 0x0006/0x0020 Battery Strength",
    (0x000D, 0x003B): "Digitizer 0x000D/0x003B Battery Strength",
    (0x0085, 0x0001): "Battery System 0x0085/0x0001 Absolute State of Charge",
    (0x0085, 0x0065): "Battery System 0x0085/0x0065 Battery Level",
    (0x0085, 0x0066): "Battery System 0x0085/0x0066 Relative State of Charge",
}

# Transport labels shown in the UI.  A USB link is reported as 2.4G only when
# the receiver advertises itself, so a plain cable never poses as a dongle.
TRANSPORT_2G4 = "2.4G"
TRANSPORT_BLUETOOTH = "蓝牙"
TRANSPORT_WIRED = "有线"

WIRELESS_MARKERS = ("dongle", "receiver", "2.4g", "wireless", "接收器")


class HidError(OSError):
    pass


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.c_size_t),
    ]


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("ClassGuid", GUID),
        ("DevInst", wintypes.DWORD),
        ("Reserved", ctypes.c_size_t),
    ]


class SP_DEVICE_INTERFACE_DETAIL_DATA_W(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("DevicePath", wintypes.WCHAR * 1),
    ]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Size", wintypes.ULONG),
        ("VendorID", wintypes.USHORT),
        ("ProductID", wintypes.USHORT),
        ("VersionNumber", wintypes.USHORT),
    ]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", wintypes.USHORT),
        ("UsagePage", wintypes.USHORT),
        ("InputReportByteLength", wintypes.USHORT),
        ("OutputReportByteLength", wintypes.USHORT),
        ("FeatureReportByteLength", wintypes.USHORT),
        ("Reserved", wintypes.USHORT * 17),
        ("NumberLinkCollectionNodes", wintypes.USHORT),
        ("NumberInputButtonCaps", wintypes.USHORT),
        ("NumberInputValueCaps", wintypes.USHORT),
        ("NumberInputDataIndices", wintypes.USHORT),
        ("NumberOutputButtonCaps", wintypes.USHORT),
        ("NumberOutputValueCaps", wintypes.USHORT),
        ("NumberOutputDataIndices", wintypes.USHORT),
        ("NumberFeatureButtonCaps", wintypes.USHORT),
        ("NumberFeatureValueCaps", wintypes.USHORT),
        ("NumberFeatureDataIndices", wintypes.USHORT),
    ]


class _RANGE(ctypes.Structure):
    _fields_ = [
        ("UsageMin", wintypes.USHORT),
        ("UsageMax", wintypes.USHORT),
        ("StringMin", wintypes.USHORT),
        ("StringMax", wintypes.USHORT),
        ("DesignatorMin", wintypes.USHORT),
        ("DesignatorMax", wintypes.USHORT),
        ("DataIndexMin", wintypes.USHORT),
        ("DataIndexMax", wintypes.USHORT),
    ]


class _NOT_RANGE(ctypes.Structure):
    _fields_ = [
        ("Usage", wintypes.USHORT),
        ("Reserved1", wintypes.USHORT),
        ("StringIndex", wintypes.USHORT),
        ("Reserved2", wintypes.USHORT),
        ("DesignatorIndex", wintypes.USHORT),
        ("Reserved3", wintypes.USHORT),
        ("DataIndex", wintypes.USHORT),
        ("Reserved4", wintypes.USHORT),
    ]


class _USAGE_UNION(ctypes.Union):
    _fields_ = [("Range", _RANGE), ("NotRange", _NOT_RANGE)]


class HIDP_VALUE_CAPS(ctypes.Structure):
    _fields_ = [
        ("UsagePage", wintypes.USHORT),
        ("ReportID", ctypes.c_ubyte),
        ("IsAlias", ctypes.c_ubyte),
        ("BitField", wintypes.USHORT),
        ("LinkCollection", wintypes.USHORT),
        ("LinkUsage", wintypes.USHORT),
        ("LinkUsagePage", wintypes.USHORT),
        ("IsRange", ctypes.c_ubyte),
        ("IsStringRange", ctypes.c_ubyte),
        ("IsDesignatorRange", ctypes.c_ubyte),
        ("IsAbsolute", ctypes.c_ubyte),
        ("HasNull", ctypes.c_ubyte),
        ("Reserved", ctypes.c_ubyte),
        ("BitSize", wintypes.USHORT),
        ("ReportCount", wintypes.USHORT),
        ("Reserved2", wintypes.USHORT * 5),
        # These two fields are easy to omit, and omitting them makes the
        # ctypes structure 8 bytes too small.  HidP_GetValueCaps then writes
        # past the array and the process dies without a Python traceback.
        ("UnitsExp", wintypes.ULONG),
        ("Units", wintypes.ULONG),
        ("LogicalMin", wintypes.LONG),
        ("LogicalMax", wintypes.LONG),
        ("PhysicalMin", wintypes.LONG),
        ("PhysicalMax", wintypes.LONG),
        ("u", _USAGE_UNION),
    ]


setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
setupapi.SetupDiGetClassDevsW.argtypes = [
    ctypes.POINTER(GUID),
    wintypes.LPCWSTR,
    wintypes.HWND,
    wintypes.DWORD,
]
setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.POINTER(GUID),
    wintypes.DWORD,
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
]
setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(SP_DEVINFO_DATA),
]
setupapi.SetupDiGetDeviceInstanceIdW.restype = wintypes.BOOL
setupapi.SetupDiGetDeviceInstanceIdW.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(SP_DEVINFO_DATA),
    wintypes.LPWSTR,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]

cfgmgr32.CM_Get_Parent.restype = wintypes.DWORD
cfgmgr32.CM_Get_Parent.argtypes = [
    ctypes.POINTER(wintypes.DWORD),
    wintypes.DWORD,
    wintypes.ULONG,
]
cfgmgr32.CM_Get_Device_IDW.restype = wintypes.DWORD
cfgmgr32.CM_Get_Device_IDW.argtypes = [
    wintypes.DWORD,
    wintypes.LPWSTR,
    wintypes.ULONG,
    wintypes.ULONG,
]

hid.HidD_GetAttributes.restype = wintypes.BOOL
hid.HidD_GetAttributes.argtypes = [wintypes.HANDLE, ctypes.POINTER(HIDD_ATTRIBUTES)]
hid.HidD_GetPreparsedData.restype = wintypes.BOOL
hid.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
hid.HidD_FreePreparsedData.restype = wintypes.BOOL
hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
hid.HidP_GetCaps.restype = ctypes.c_long
hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
hid.HidP_GetValueCaps.restype = ctypes.c_long
hid.HidP_GetValueCaps.argtypes = [
    ctypes.c_int,
    ctypes.POINTER(HIDP_VALUE_CAPS),
    ctypes.POINTER(wintypes.USHORT),
    ctypes.c_void_p,
]
hid.HidP_GetUsageValue.restype = ctypes.c_long
hid.HidP_GetUsageValue.argtypes = [
    ctypes.c_int,
    wintypes.USHORT,
    wintypes.USHORT,
    wintypes.USHORT,
    ctypes.POINTER(wintypes.ULONG),
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.ULONG,
]
hid.HidD_GetFeature.restype = wintypes.BOOL
hid.HidD_GetFeature.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
hid.HidD_SetFeature.restype = wintypes.BOOL
hid.HidD_SetFeature.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
for _fn_name in ("HidD_GetProductString", "HidD_GetManufacturerString", "HidD_GetSerialNumberString"):
    _fn = getattr(hid, _fn_name)
    _fn.restype = wintypes.BOOL
    _fn.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]


@dataclass(frozen=True)
class ValueCap:
    usage_page: int
    report_id: int
    link_collection: int
    link_usage_page: int
    link_usage: int
    usage: int
    usage_min: int
    usage_max: int
    is_range: bool
    bit_size: int
    report_count: int
    logical_min: int
    logical_max: int
    data_index: int

    def contains_usage(self, page: int, usage: int) -> bool:
        if self.usage_page != page:
            return False
        if self.is_range:
            return self.usage_min <= usage <= self.usage_max
        return self.usage == usage

    @property
    def usage_label(self) -> str:
        if self.is_range:
            return f"0x{self.usage_page:04X}:0x{self.usage_min:04X}-0x{self.usage_max:04X}"
        return f"0x{self.usage_page:04X}:0x{self.usage:04X}"


@dataclass
class HidInterface:
    path: str
    instance_id: str = ""
    enumerator: str = ""
    vid: int = 0
    pid: int = 0
    version: int = 0
    usage_page: int = 0
    usage: int = 0
    input_len: int = 0
    output_len: int = 0
    feature_len: int = 0
    product: str = ""
    manufacturer: str = ""
    serial: str = ""
    feature_value_caps: tuple[ValueCap, ...] = ()
    input_value_caps: tuple[ValueCap, ...] = ()
    output_value_caps: tuple[ValueCap, ...] = ()
    group_key: str = ""
    error: str = ""
    _preparsed: int | None = field(default=None, repr=False, compare=False)

    @property
    def vid_pid(self) -> str:
        return f"{self.vid:04X}:{self.pid:04X}"

    @property
    def usage_label(self) -> str:
        return f"0x{self.usage_page:04X}:0x{self.usage:04X}"

    @property
    def is_vendor(self) -> bool:
        return self.usage_page >= 0xFF00

    @property
    def transport(self) -> str:
        """How the device is attached: ``2.4G``, ``蓝牙``, ``有线`` or ``""``.

        The HID node itself is always a child of ``HID``; the enumerator of its
        ancestor tells the truth: ``USB\\VID_...`` is a USB link, while
        ``BTHENUM`` / ``BTHLE`` come from Bluetooth.  A USB link is a 2.4G
        dongle when the receiver says so (``Dongle`` / ``Receiver`` / ``2.4G``
        in the product or manufacturer string), otherwise it is a cable.
        """
        enumerator = (self.enumerator or "").upper()
        if enumerator.startswith("BTH"):
            return "蓝牙"
        if enumerator == "USB":
            hint = f"{self.product} {self.manufacturer}".strip().lower()
            if any(marker in hint for marker in WIRELESS_MARKERS):
                return TRANSPORT_2G4
            # A device that answers no string descriptor tells us nothing about
            # the link; staying silent beats calling a receiver a cable.
            return TRANSPORT_WIRED if hint else ""
        return ""

    @property
    def is_system_reserved(self) -> bool:
        return (self.usage_page, self.usage) in SYSTEM_RESERVED_USAGES

    @property
    def mi(self) -> str:
        low = self.path.lower()
        pos = low.find("&mi_")
        if pos >= 0:
            return low[pos + 4 : pos + 6]
        return ""

    @property
    def display_name(self) -> str:
        if self.product and "USB Receiver" not in self.product:
            return self.product
        if self.manufacturer:
            return f"{self.manufacturer} HID"
        return "HID device"

    def matches(self, usage_page: int | None = None, usage: int | None = None) -> bool:
        if usage_page is not None and self.usage_page != usage_page:
            return False
        if usage is not None and self.usage != usage:
            return False
        return True


def _get_wide_string(fn, handle, chars: int = 256) -> str:
    buf = ctypes.create_unicode_buffer(chars)
    try:
        if fn(handle, buf, ctypes.sizeof(buf)):
            return buf.value.strip()
    except Exception:
        pass
    return ""


def _parse_vid_pid(path: str) -> tuple[int, int]:
    low = path.lower()
    vid = pid = 0
    try:
        p = low.find("vid_")
        if p >= 0:
            vid = int(low[p + 4 : p + 8], 16)
    except ValueError:
        pass
    try:
        p = low.find("pid_")
        if p >= 0:
            pid = int(low[p + 4 : p + 8], 16)
    except ValueError:
        pass
    return vid, pid


def _group_key(path: str) -> str:
    parts = path.split("#")
    if len(parts) < 2:
        return path.lower()
    keep = []
    for piece in parts[1].split("&"):
        low = piece.lower()
        if low.startswith("mi_") or low.startswith("col"):
            continue
        keep.append(piece)
    return "&".join(keep).lower() or parts[1].lower()


def _open_handle(path: str, access: int, overlapped: bool = False) -> int:
    flags = FILE_FLAG_OVERLAPPED if overlapped else 0
    handle = kernel32.CreateFileW(
        path,
        access,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        flags,
        None,
    )
    if is_invalid_handle(handle):
        code = last_error()
        raise HidError(f"CreateFileW failed ({code}: {win_error_text(code)})")
    return int(handle)


def _read_value_caps(preparsed: int, report_type: int) -> tuple[ValueCap, ...]:
    if not preparsed:
        return ()
    count = wintypes.USHORT(0)
    try:
        status = hid.HidP_GetValueCaps(report_type, None, ctypes.byref(count), preparsed)
        if status != HIDP_STATUS_SUCCESS or count.value == 0:
            return ()
        arr = (HIDP_VALUE_CAPS * count.value)()
        status = hid.HidP_GetValueCaps(report_type, arr, ctypes.byref(count), preparsed)
        if status != HIDP_STATUS_SUCCESS:
            return ()
    except Exception:
        return ()
    out = []
    for i in range(count.value):
        cap = arr[i]
        if cap.IsRange:
            usage = cap.u.Range.UsageMin
            usage_min = cap.u.Range.UsageMin
            usage_max = cap.u.Range.UsageMax
            data_index = cap.u.Range.DataIndexMin
        else:
            usage = cap.u.NotRange.Usage
            usage_min = usage_max = usage
            data_index = cap.u.NotRange.DataIndex
        out.append(
            ValueCap(
                usage_page=int(cap.UsagePage),
                report_id=int(cap.ReportID),
                link_collection=int(cap.LinkCollection),
                link_usage_page=int(cap.LinkUsagePage),
                link_usage=int(cap.LinkUsage),
                usage=int(usage),
                usage_min=int(usage_min),
                usage_max=int(usage_max),
                is_range=bool(cap.IsRange),
                bit_size=int(cap.BitSize),
                report_count=int(cap.ReportCount),
                logical_min=int(cap.LogicalMin),
                logical_max=int(cap.LogicalMax),
                data_index=int(data_index),
            )
        )
    return tuple(out)


_ENUMERATOR_CACHE: dict[int, str] = {}


def _enumerator_name(devinst: int) -> str:
    """Return the enumerator of the first non-``HID`` ancestor of ``devinst``.

    The HID top-level collections of a wireless receiver hang below
    ``USB\\VID_...`` while Bluetooth HID collections hang below ``BTHENUM\\...``
    or ``BTHLE\\...``.  Walking up the device tree is the only reliable way to
    tell those apart, because both end up as ``HID\\VID_...`` nodes.
    """
    try:
        devinst = int(devinst)
    except (TypeError, ValueError):
        return ""
    if devinst in _ENUMERATOR_CACHE:
        return _ENUMERATOR_CACHE[devinst]
    name = ""
    current = devinst
    for _ in range(6):
        buf = ctypes.create_unicode_buffer(512)
        if cfgmgr32.CM_Get_Device_IDW(current, buf, 512, 0) != 0 or not buf.value:
            break
        prefix = buf.value.split("\\", 1)[0].upper()
        if prefix and prefix != "HID":
            name = prefix
            break
        parent = wintypes.DWORD(0)
        if cfgmgr32.CM_Get_Parent(ctypes.byref(parent), current, 0) != 0 or not parent.value:
            break
        current = parent.value
    if len(_ENUMERATOR_CACHE) > 512:
        _ENUMERATOR_CACHE.clear()
    _ENUMERATOR_CACHE[devinst] = name
    return name


def enumerate_hid() -> list[HidInterface]:
    """Enumerate all present HID top-level collections."""
    result: list[HidInterface] = []
    hdev = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(HID_GUID), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE
    )
    if is_invalid_handle(hdev):
        raise HidError(f"SetupDiGetClassDevsW failed ({win_error_text()})")
    try:
        index = 0
        while True:
            did = SP_DEVICE_INTERFACE_DATA()
            did.cbSize = ctypes.sizeof(did)
            ok = setupapi.SetupDiEnumDeviceInterfaces(
                hdev, None, ctypes.byref(HID_GUID), index, ctypes.byref(did)
            )
            if not ok:
                if last_error() == ERROR_NO_MORE_ITEMS:
                    break
                break
            index += 1

            devinfo = SP_DEVINFO_DATA()
            devinfo.cbSize = ctypes.sizeof(devinfo)
            need = wintypes.DWORD(0)
            setupapi.SetupDiGetDeviceInterfaceDetailW(
                hdev,
                ctypes.byref(did),
                None,
                0,
                ctypes.byref(need),
                ctypes.byref(devinfo),
            )
            if not need.value:
                continue
            buf = ctypes.create_string_buffer(need.value)
            detail = ctypes.cast(buf, ctypes.POINTER(SP_DEVICE_INTERFACE_DETAIL_DATA_W))
            detail.contents.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DETAIL_DATA_W)
            ok = setupapi.SetupDiGetDeviceInterfaceDetailW(
                hdev,
                ctypes.byref(did),
                detail,
                need.value,
                ctypes.byref(need),
                ctypes.byref(devinfo),
            )
            if not ok:
                continue
            # DevicePath is a variable-length array.  ctypes truncates
            # c_wchar * 1 at the first character, so read the raw wide string.
            path = ctypes.wstring_at(ctypes.addressof(detail.contents) + 4)
            if not path:
                continue

            iid_buf = ctypes.create_unicode_buffer(512)
            instance_id = ""
            if setupapi.SetupDiGetDeviceInstanceIdW(
                hdev, ctypes.byref(devinfo), iid_buf, 512, None
            ):
                instance_id = iid_buf.value
            enumerator = _enumerator_name(devinfo.DevInst)

            try:
                item = _probe_interface(path)
            except Exception as exc:  # a single broken interface must not kill enumeration
                item = HidInterface(path=path, instance_id=instance_id, error=str(exc))
            if not item.instance_id:
                item.instance_id = instance_id
            item.enumerator = enumerator
            item.group_key = _group_key(path)
            if not item.group_key:
                item.group_key = f"{item.vid:04X}:{item.pid:04X}"
            result.append(item)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdev)
    result.sort(key=lambda x: (x.group_key, x.usage_page, x.usage, x.path.lower()))
    return result


def _probe_interface(path: str) -> HidInterface:
    vid, pid = _parse_vid_pid(path)
    item = HidInterface(path=path, vid=vid, pid=pid)
    handle = _open_handle(path, 0)
    try:
        attrs = HIDD_ATTRIBUTES()
        attrs.Size = ctypes.sizeof(HIDD_ATTRIBUTES)
        if hid.HidD_GetAttributes(handle, ctypes.byref(attrs)):
            item.vid = int(attrs.VendorID)
            item.pid = int(attrs.ProductID)
            item.version = int(attrs.VersionNumber)
        item.product = _get_wide_string(hid.HidD_GetProductString, handle)
        item.manufacturer = _get_wide_string(hid.HidD_GetManufacturerString, handle)
        item.serial = _get_wide_string(hid.HidD_GetSerialNumberString, handle)

        preparsed = ctypes.c_void_p()
        if not hid.HidD_GetPreparsedData(handle, ctypes.byref(preparsed)) or not preparsed:
            item.error = "HidD_GetPreparsedData failed"
            return item
        try:
            item._preparsed = int(preparsed.value)
            caps = HIDP_CAPS()
            status = hid.HidP_GetCaps(preparsed, ctypes.byref(caps))
            if status != HIDP_STATUS_SUCCESS:
                item.error = f"HidP_GetCaps failed (0x{status & 0xFFFFFFFF:08X})"
                return item
            item.usage_page = int(caps.UsagePage)
            item.usage = int(caps.Usage)
            item.input_len = int(caps.InputReportByteLength)
            item.output_len = int(caps.OutputReportByteLength)
            item.feature_len = int(caps.FeatureReportByteLength)
            item.feature_value_caps = _read_value_caps(preparsed, HidP_Feature)
            item.input_value_caps = _read_value_caps(preparsed, HidP_Input)
            item.output_value_caps = _read_value_caps(preparsed, HidP_Output)
        finally:
            try:
                hid.HidD_FreePreparsedData(preparsed)
            except Exception:
                pass
            item._preparsed = None
    finally:
        try:
            kernel32.CloseHandle(handle)
        except Exception:
            pass
    return item


def battery_value_caps(iface: HidInterface, report_type: int = HidP_Feature) -> list[ValueCap]:
    caps = {
        HidP_Feature: iface.feature_value_caps,
        HidP_Input: iface.input_value_caps,
        HidP_Output: iface.output_value_caps,
    }.get(report_type, ())
    return [c for c in caps if (c.usage_page, c.usage) in BATTERY_USAGES or _cap_matches_battery(c)]


def _cap_matches_battery(cap: ValueCap) -> bool:
    for page, usage in BATTERY_USAGES:
        if cap.contains_usage(page, usage):
            return True
    return False


def scale_battery_value(raw: int, logical_max: int) -> int:
    """Map a HID logical value to a percentage without inventing precision."""
    raw = int(raw)
    logical_max = int(logical_max or 0)
    if logical_max <= 0:
        value = raw
    elif logical_max == 100:
        value = raw
    elif logical_max == 255:
        value = round(raw * 100 / 255)
    elif logical_max > 100:
        value = round(raw * 100 / logical_max)
    else:
        value = raw
    return max(0, min(100, value))


class HidDevice:
    """One HID interface with lazily-opened, strictly separated handles."""

    def __init__(self, iface: HidInterface):
        self.iface = iface
        self._control: int | None = None
        self._read: int | None = None
        self._write: int | None = None
        self._feature_write: int | None = None
        self._read_event: int | None = None
        self._write_event: int | None = None
        self._error: str = ""

    @property
    def path(self) -> str:
        return self.iface.path

    @property
    def caps(self) -> HidInterface:
        return self.iface

    def __enter__(self) -> "HidDevice":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        for attr in ("_read", "_write", "_control", "_feature_write"):
            h = getattr(self, attr, None)
            if h:
                try:
                    if attr == "_read":
                        kernel32.CancelIoEx(h, None)
                    kernel32.CloseHandle(h)
                except Exception:
                    pass
                setattr(self, attr, None)
        for attr in ("_read_event", "_write_event"):
            h = getattr(self, attr, None)
            if h:
                try:
                    kernel32.CloseHandle(h)
                except Exception:
                    pass
                setattr(self, attr, None)

    def _ensure_control(self) -> int:
        if self._control:
            return self._control
        last_exc: Exception | None = None
        for access in (0, GENERIC_READ):
            try:
                self._control = _open_handle(self.path, access)
                return self._control
            except Exception as exc:
                last_exc = exc
        raise HidError(f"cannot open HID control handle: {last_exc}")

    def _ensure_read(self) -> int:
        if self._read:
            return self._read
        try:
            self._read = _open_handle(self.path, GENERIC_READ, overlapped=True)
        except Exception as exc:
            raise HidError(f"cannot open HID read handle: {exc}")
        self._read_event = kernel32.CreateEventW(None, True, False, None)
        if not self._read_event:
            raise HidError(f"CreateEventW failed ({win_error_text()})")
        return self._read

    def _ensure_write(self) -> int:
        if self._write:
            return self._write
        last_exc: Exception | None = None
        for access in (GENERIC_WRITE, GENERIC_READ | GENERIC_WRITE):
            try:
                self._write = _open_handle(self.path, access, overlapped=True)
                break
            except Exception as exc:
                last_exc = exc
        if not self._write:
            raise HidError(f"cannot open HID write handle: {last_exc}")
        self._write_event = kernel32.CreateEventW(None, True, False, None)
        if not self._write_event:
            raise HidError(f"CreateEventW failed ({win_error_text()})")
        return self._write

    # ---- Feature reports -------------------------------------------------
    def get_feature(self, report_id: int, length: int | None = None) -> bytes:
        length = int(length or self.iface.feature_len or 0)
        if length <= 0:
            raise HidError("interface has no Feature Report length")
        handle = self._ensure_control()
        buf = ctypes.create_string_buffer(length)
        buf[0] = bytes((report_id & 0xFF,))
        if not hid.HidD_GetFeature(handle, buf, length):
            err = last_error()
            # Some drivers refuse feature reads on a zero-access handle; a
            # read-access handle is still safe and does not need overlapped I/O.
            try:
                h2 = _open_handle(self.path, GENERIC_READ)
                try:
                    if hid.HidD_GetFeature(h2, buf, length):
                        return buf.raw
                finally:
                    kernel32.CloseHandle(h2)
            except Exception:
                pass
            raise HidError(f"HidD_GetFeature(0x{report_id:02X}) failed ({err}: {win_error_text(err)})")
        return buf.raw

    def set_feature(self, data: bytes) -> None:
        if not data:
            raise HidError("empty Feature Report")
        if not self._feature_write:
            last_exc: Exception | None = None
            for access in (GENERIC_READ | GENERIC_WRITE, GENERIC_WRITE):
                try:
                    self._feature_write = _open_handle(self.path, access)
                    break
                except Exception as exc:
                    last_exc = exc
            if not self._feature_write:
                raise HidError(f"cannot open HID feature-write handle: {last_exc}")
        buf = ctypes.create_string_buffer(bytes(data), len(data))
        if not hid.HidD_SetFeature(self._feature_write, buf, len(data)):
            err = last_error()
            raise HidError(f"HidD_SetFeature failed ({err}: {win_error_text(err)})")

    # ---- Output reports --------------------------------------------------
    def write_output(self, data: bytes | bytearray, length: int | None = None) -> int:
        total = int(length or self.iface.output_len or 0)
        if total <= 0:
            raise HidError("interface has no Output Report")
        payload = (bytes(data) + b"\x00" * total)[:total]
        handle = self._ensure_write()
        return self._overlapped_io(handle, self._write_event, payload, timeout_ms=800, is_write=True)

    # ---- Input reports ---------------------------------------------------
    def read_input(self, timeout_ms: int = 250, length: int | None = None) -> bytes | None:
        total = int(length or self.iface.input_len or 0)
        if total <= 0:
            raise HidError("interface has no Input Report")
        try:
            handle = self._ensure_read()
        except HidError:
            return None
        return self._overlapped_io(handle, self._read_event, total, timeout_ms=timeout_ms, is_write=False)

    def _overlapped_io(self, handle: int, event, payload_or_size, timeout_ms: int, is_write: bool) -> bytes | int | None:
        ov = OVERLAPPED()
        ov.hEvent = event
        kernel32.ResetEvent(event)
        transferred = wintypes.DWORD(0)
        if is_write:
            data = bytes(payload_or_size)
            buf = ctypes.create_string_buffer(data, len(data))
            ok = kernel32.WriteFile(handle, buf, len(data), ctypes.byref(transferred), ctypes.byref(ov))
        else:
            size = int(payload_or_size)
            buf = ctypes.create_string_buffer(size)
            ok = kernel32.ReadFile(handle, buf, size, ctypes.byref(transferred), ctypes.byref(ov))
        if ok:
            return int(transferred.value) if is_write else buf.raw[: transferred.value]
        err = last_error()
        if err != ERROR_IO_PENDING:
            if err in (6, 87, 1167):
                return None
            raise HidError(f"{'WriteFile' if is_write else 'ReadFile'} failed ({err}: {win_error_text(err)})")

        wait = kernel32.WaitForSingleObject(event, max(1, int(timeout_ms)))
        if wait != WAIT_OBJECT_0:
            try:
                kernel32.CancelIoEx(handle, ctypes.byref(ov))
            except Exception:
                pass
            kernel32.WaitForSingleObject(event, 300)
            done = wintypes.DWORD(0)
            try:
                kernel32.GetOverlappedResult(handle, ctypes.byref(ov), ctypes.byref(done), False)
            except Exception:
                pass
            return None
        done = wintypes.DWORD(0)
        if not kernel32.GetOverlappedResult(handle, ctypes.byref(ov), ctypes.byref(done), False):
            return None
        return int(done.value) if is_write else buf.raw[: done.value]

    def drain(self, max_reports: int = 32) -> int:
        count = 0
        for _ in range(max_reports):
            try:
                data = self.read_input(8)
            except Exception:
                break
            if not data:
                break
            count += 1
        return count

    def usage_value(self, report_type: int, report: bytes, cap: ValueCap) -> int | None:
        """Extract a declared value from a raw HID report."""
        if not report:
            return None
        # Re-parse once per call.  The preparsed data is cheap and this avoids
        # keeping a pointer whose lifetime is tied to a failed probe.
        handle = self._ensure_control()
        preparsed = ctypes.c_void_p()
        if not hid.HidD_GetPreparsedData(handle, ctypes.byref(preparsed)) or not preparsed:
            return None
        try:
            value = wintypes.ULONG(0)
            buf = ctypes.create_string_buffer(bytes(report), len(report))
            status = hid.HidP_GetUsageValue(
                int(report_type),
                cap.usage_page,
                cap.link_collection,
                cap.usage,
                ctypes.byref(value),
                preparsed,
                buf,
                len(report),
            )
            if status != HIDP_STATUS_SUCCESS:
                return None
            return int(value.value)
        finally:
            try:
                hid.HidD_FreePreparsedData(preparsed)
            except Exception:
                pass


def grouped_interfaces(interfaces: Iterable[HidInterface]) -> dict[str, list[HidInterface]]:
    out: dict[str, list[HidInterface]] = {}
    for iface in interfaces:
        out.setdefault(iface.group_key, []).append(iface)
    return out


def find_group(interfaces: Iterable[HidInterface], vid: int, pid: int) -> list[HidInterface]:
    return [i for i in interfaces if i.vid == vid and i.pid == pid]


def interface_summary(iface: HidInterface) -> str:
    return (
        f"{iface.vid_pid} {iface.usage_label} "
        f"in={iface.input_len} out={iface.output_len} feat={iface.feature_len}"
    )
