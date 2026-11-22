"""Tray icon generation with GDI (no Pillow, no image files)."""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from .win32 import get_dpi_for_system, user32

gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HANDLE),
        ("hbmColor", wintypes.HANDLE),
    ]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.restype = ctypes.c_int
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.DrawTextW.restype = ctypes.c_int
user32.DrawTextW.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(RECT), wintypes.UINT]
user32.CreateIconIndirect.restype = wintypes.HICON
user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
user32.DestroyIcon.restype = wintypes.BOOL
user32.DestroyIcon.argtypes = [wintypes.HICON]

gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateBitmap.restype = wintypes.HBITMAP
gdi32.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.UINT, ctypes.c_void_p]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC,
    ctypes.POINTER(BITMAPINFO),
    wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p),
    wintypes.HANDLE,
    wintypes.DWORD,
]
gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
gdi32.CreatePen.restype = wintypes.HPEN
gdi32.CreatePen.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.COLORREF]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.restype = wintypes.BOOL
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.CreateFontW.restype = wintypes.HFONT
gdi32.CreateFontW.argtypes = [
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.LPCWSTR,
]
gdi32.SetBkMode.restype = ctypes.c_int
gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
gdi32.SetTextColor.restype = wintypes.COLORREF
gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
gdi32.SetBkColor.restype = wintypes.COLORREF
gdi32.SetBkColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
gdi32.RoundRect.restype = wintypes.BOOL
gdi32.RoundRect.argtypes = [
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
]
gdi32.GetStockObject.restype = wintypes.HGDIOBJ
gdi32.GetStockObject.argtypes = [ctypes.c_int]
gdi32.PatBlt.restype = wintypes.BOOL
gdi32.PatBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.DWORD]

DT_CENTER = 0x0001
DT_VCENTER = 0x0004
DT_SINGLELINE = 0x0020
DT_NOPREFIX = 0x0800
TRANSPARENT = 1
NULL_BRUSH = 0x00A0  # GetStockObject(NULL_BRUSH)
NULL_PEN = 0x00A8
WHITENESS = 0x00FF0062
SM_CXSMICON = 49


def _colorref(rgb_hex: str) -> int:
    """Convert #RRGGBB to Windows COLORREF (0x00BBGGRR)."""
    text = rgb_hex.lstrip("#")
    r, g, b = int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    return (b << 16) | (g << 8) | r


def _level_color(level: int | None) -> int:
    if level is None:
        return _colorref("#6A7482")
    if level >= 60:
        return _colorref("#35C46A")
    if level >= 25:
        return _colorref("#E6B422")
    return _colorref("#E05252")


def icon_size() -> int:
    metric = user32.GetSystemMetrics(SM_CXSMICON) or 16
    dpi = get_dpi_for_system()
    return max(32, int(round(metric * 2 * dpi / 96)))


def create_icon(level: int | None, kind: str = "") -> int:
    """Create an HICON whose content is the battery number."""
    size = icon_size()
    screen = user32.GetDC(None)
    if not screen:
        return 0
    mem = gdi32.CreateCompatibleDC(screen)
    if not mem:
        user32.ReleaseDC(None, screen)
        return 0
    color_bmp = gdi32.CreateCompatibleBitmap(screen, size, size)
    mask_stride = ((size + 15) // 16) * 2
    mask_bits = ctypes.create_string_buffer(mask_stride * size)
    mask_bmp = gdi32.CreateBitmap(size, size, 1, 1, mask_bits)
    old = None
    hicon = 0
    try:
        old = gdi32.SelectObject(mem, color_bmp)
        bg = gdi32.CreateSolidBrush(_level_color(level))
        null_pen = gdi32.GetStockObject(NULL_PEN)
        gdi32.SelectObject(mem, null_pen)
        gdi32.SelectObject(mem, bg)
        radius = max(4, size // 6)
        gdi32.RoundRect(mem, 0, 0, size, size, radius, radius)
        gdi32.DeleteObject(bg)

        text = "--" if level is None else str(max(0, min(100, int(level))))
        # Bigger font for 2 digits; three digits need a little less width.
        font_height = max(12, int(size * (0.56 if len(text) <= 2 else 0.43)))
        font = gdi32.CreateFontW(
            -font_height,
            0,
            0,
            0,
            700,  # FW_BOLD
            0,
            0,
            0,
            1,  # DEFAULT_CHARSET
            0,
            0,
            5,  # CLEARTYPE_QUALITY
            0,
            "Segoe UI",
        )
        old_font = gdi32.SelectObject(mem, font)
        gdi32.SetBkMode(mem, TRANSPARENT)
        gdi32.SetTextColor(mem, _colorref("#FFFFFF"))
        rect = RECT(0, 0, size, size)
        user32.DrawTextW(
            mem,
            text,
            -1,
            ctypes.byref(rect),
            DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX,
        )
        gdi32.SelectObject(mem, old_font)
        gdi32.DeleteObject(font)

        # The mask is all zeroes (opaque rectangle).  The color bitmap already
        # has a rounded colored shape; the corners use the same color because
        # GDI's mask cannot express partial alpha without more work.
        info = ICONINFO()
        info.fIcon = True
        info.hbmMask = mask_bmp
        info.hbmColor = color_bmp
        hicon = int(user32.CreateIconIndirect(ctypes.byref(info)) or 0)
    finally:
        if old:
            gdi32.SelectObject(mem, old)
        if mask_bmp:
            gdi32.DeleteObject(mask_bmp)
        if color_bmp:
            gdi32.DeleteObject(color_bmp)
        if mem:
            gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, screen)
    return hicon


def destroy_icon(hicon: int | None) -> None:
    if hicon:
        try:
            user32.DestroyIcon(hicon)
        except Exception:
            pass
