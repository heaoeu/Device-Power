"""An icon-and-percentage battery strip embedded in the Windows taskbar.

The strip is display-only: clicks are not handled (the tray and the panel are
the interactive entry points).  A right-click on the icon/text still opens the
tray menu.
"""
from __future__ import annotations

import ctypes
import math
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes

from .win32 import get_dpi_for_system, user32

GWL_STYLE = -16
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_CLIPSIBLINGS = 0x04000000
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

COLOR_DARK_BG = "#1f1f1f"
COLOR_DARK_FG = "#ffffff"
COLOR_LIGHT_BG = "#f3f3f3"
COLOR_LIGHT_FG = "#2f2f2f"
COLOR_DARK_DIVIDER = "#707070"
COLOR_LIGHT_DIVIDER = "#a6a6a6"
COLOR_DARK_MUTED = "#b8b8b8"
COLOR_LIGHT_MUTED = "#666666"



class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


user32.GetParent.restype = wintypes.HWND
user32.GetParent.argtypes = [wintypes.HWND]
user32.SetParent.restype = wintypes.HWND
user32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
user32.SetWindowLongW.restype = wintypes.LONG
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
user32.GetWindowLongW.restype = wintypes.LONG
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.FindWindowExW.restype = wintypes.HWND
user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.EnumWindows.restype = wintypes.BOOL
user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.SetWindowPos.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.ShowWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.EnumChildWindows.restype = wintypes.BOOL
user32.EnumChildWindows.argtypes = [
    wintypes.HWND,
    ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM),
    wintypes.LPARAM,
]
user32.InvalidateRect.restype = wintypes.BOOL
user32.InvalidateRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT), wintypes.BOOL]
user32.UpdateWindow.restype = wintypes.BOOL
user32.UpdateWindow.argtypes = [wintypes.HWND]




def _system_uses_light_theme() -> bool:
    """Return whether Windows uses its light taskbar theme."""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            for value_name in ("SystemUsesLightTheme", "AppsUseLightTheme"):
                try:
                    light, _ = winreg.QueryValueEx(key, value_name)
                except OSError:
                    continue
                return bool(light)
    except Exception:
        pass
    return False


def _taskbar_theme() -> tuple[bool, str, str]:
    """Return (light, background, foreground) for the current taskbar."""
    if _system_uses_light_theme():
        return True, COLOR_LIGHT_BG, COLOR_LIGHT_FG
    return False, COLOR_DARK_BG, COLOR_DARK_FG


def _battery_color(level: int | None, light: bool) -> str:
    """Battery percentage colour, with a readable variant on light taskbars."""
    if level is None:
        return COLOR_LIGHT_MUTED if light else COLOR_DARK_MUTED
    if level >= 60:
        return "#168a4c" if light else "#35c46a"
    if level >= 25:
        return "#a86b00" if light else "#e6b422"
    return "#c53030" if light else "#e05252"


def _trafficmonitor_rect() -> RECT | None:
    """Return the rectangle TrafficMonitor actually paints on the taskbar.

    TrafficMonitor's frame window (class ``TrafficMonitor_...``) is usually
    hidden and wider than its content; the numbers are painted by a child
    window titled ``TrafficMonitorTaskbarWindow`` that lives under the taskbar
    (normally ``ReBarWindow32``).  Reserving the frame would leave a wide empty
    gap between the strip and the visible numbers, so the union of the visible
    pieces that sit on the taskbar is used instead.
    """
    proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    pieces: list[RECT] = []

    shell = int(user32.FindWindowW("Shell_TrayWnd", None) or 0)
    bar = RECT()
    if not shell or not user32.GetWindowRect(shell, ctypes.byref(bar)):
        return None

    def matches(hwnd) -> bool:
        name = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, name, 128)
        title = ctypes.create_unicode_buffer(128)
        user32.GetWindowTextW(hwnd, title, 128)
        return "trafficmonitor" in (name.value + " " + title.value).lower()

    def consider(hwnd) -> None:
        if not user32.IsWindowVisible(hwnd):
            return
        rect = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return
        if rect.right - rect.left <= 8 or rect.bottom - rect.top <= 5:
            return
        # Only taskbar-sized pieces count; a normal window whose title merely
        # mentions TrafficMonitor must not push the strip around.
        if rect.top < bar.top - 8 or rect.bottom > bar.bottom + 8:
            return
        pieces.append(rect)

    def scan(hwnd, _lparam):
        try:
            if matches(hwnd):
                consider(hwnd)
        except Exception:
            pass
        return True

    try:
        user32.EnumChildWindows(shell, proc_type(scan), 0)
    except Exception:
        pass

    def top_cb(hwnd, _lparam):
        try:
            if matches(hwnd):
                consider(hwnd)
        except Exception:
            pass
        return True

    try:
        user32.EnumWindows(proc_type(top_cb), 0)
    except Exception:
        pass
    if not pieces:
        return None
    return RECT(
        min(rect.left for rect in pieces),
        min(rect.top for rect in pieces),
        max(rect.right for rect in pieces),
        max(rect.bottom for rect in pieces),
    )


def _rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))


def _blend(back: tuple[int, int, int], ink: tuple[int, int, int], cover: float) -> str:
    return "#%02x%02x%02x" % tuple(
        round(back[i] + (ink[i] - back[i]) * cover) for i in range(3)
    )


def _coverage(distance: float, half: float) -> float:
    """Pixel coverage for a stroke of half-width ``half`` at ``distance``."""
    value = half + 0.5 - distance
    return 0.0 if value <= 0.0 else (1.0 if value >= 1.0 else value)


def _segment_distance(px, py, x1, y1, x2, y2) -> float:
    dx, dy = x2 - x1, y2 - y1
    length = dx * dx + dy * dy
    if length <= 0.0:
        return ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
    t = ((px - x1) * dx + (py - y1) * dy) / length
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    qx, qy = x1 + t * dx, y1 + t * dy
    return ((px - qx) ** 2 + (py - qy) ** 2) ** 0.5


def _box_distance(px, py, cx, cy, half_w, half_h, radius) -> float:
    qx = abs(px - cx) - (half_w - radius)
    qy = abs(py - cy) - (half_h - radius)
    outside = (max(qx, 0.0) ** 2 + max(qy, 0.0) ** 2) ** 0.5
    return outside + min(max(qx, qy), 0.0) - radius


def _snap(value: float, half: float) -> float:
    """Centre an axis-aligned stroke on the pixel grid to keep it crisp."""
    return math.floor(value - half + 0.5) + half


def _add_line(shapes, x1, y1, x2, y2, half) -> None:
    if abs(x1 - x2) < 1e-6:
        x1 = x2 = _snap(x1, half)
    elif abs(y1 - y2) < 1e-6:
        y1 = y2 = _snap(y1, half)
    pad = half + 1.0
    box = (
        min(x1, x2) - pad, min(y1, y2) - pad,
        max(x1, x2) + pad, max(y1, y2) + pad,
    )

    def test(px, py):
        return _coverage(_segment_distance(px, py, x1, y1, x2, y2), half)

    shapes.append((box, test))


def _add_box(shapes, x1, y1, x2, y2, radius, half) -> None:
    x1, x2 = _snap(x1, half), _snap(x2, half)
    y1, y2 = _snap(y1, half), _snap(y2, half)
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    half_w, half_h = (x2 - x1) / 2.0, (y2 - y1) / 2.0
    radius = max(0.0, min(radius, half_w, half_h))
    box = (x1 - 1.0, y1 - 1.0, x2 + 1.0, y2 + 1.0)

    def test(px, py):
        return _coverage(abs(_box_distance(px, py, cx, cy, half_w, half_h, radius)), half)

    shapes.append((box, test))


def _add_oval(shapes, x1, y1, x2, y2, half) -> None:
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    a, b = (x2 - x1) / 2.0, (y2 - y1) / 2.0
    scale = min(a, b)
    box = (x1 - 1.0, y1 - 1.0, x2 + 1.0, y2 + 1.0)

    def test(px, py):
        nx, ny = (px - cx) / a, (py - cy) / b
        return _coverage(abs(((nx * nx + ny * ny) ** 0.5) - 1.0) * scale, half)

    shapes.append((box, test))


def _add_arc(shapes, x1, y1, x2, y2, start, extent, half) -> None:
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    radius = ((x2 - x1) + (y2 - y1)) / 4.0
    box = (x1 - 1.0, y1 - 1.0, x2 + 1.0, y2 + 1.0)
    end = start + extent

    def test(px, py):
        angle = math.degrees(math.atan2(cy - py, px - cx)) % 360.0
        if not (start <= angle <= end):
            # Round caps at both ends of the arc.
            for cap in (start, end):
                rad = math.radians(cap)
                qx = cx + radius * math.cos(rad)
                qy = cy - radius * math.sin(rad)
                if ((px - qx) ** 2 + (py - qy) ** 2) ** 0.5 <= half:
                    return 1.0
            return 0.0
        return _coverage(abs(((px - cx) ** 2 + (py - cy) ** 2) ** 0.5 - radius), half)

    shapes.append((box, test))


def _add_dot(shapes, cx, cy, radius) -> None:
    box = (cx - radius - 1.0, cy - radius - 1.0, cx + radius + 1.0, cy + radius + 1.0)

    def test(px, py):
        distance = ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5
        value = radius + 0.5 - distance
        return 0.0 if value <= 0.0 else (1.0 if value >= 1.0 else value)

    shapes.append((box, test))


def _icon_shapes(kind: str, s: float, stroke: float):
    """Line-art shapes in icon-local coordinates, as (bbox, coverage) pairs."""
    half = max(0.5, stroke / 2.0)
    shapes = []
    cx = s / 2.0
    if kind == "mouse":
        left, right = 0.24 * s, 0.76 * s
        top, bottom = 0.17 * s, 0.83 * s
        _add_box(shapes, left, top, right, bottom, min(4.0, 0.16 * s), half)
        _add_line(shapes, cx, top, cx, 0.45 * s, half)
        _add_line(shapes, left, 0.45 * s, right, 0.45 * s, half)
    elif kind == "keyboard":
        _add_box(shapes, 0.04 * s, 0.28 * s, 0.96 * s, 0.76 * s, 0.02 * s, half)
        if s >= 32:
            for fx in (0.18, 0.34, 0.50, 0.66, 0.82):
                _add_line(shapes, fx * s, 0.405 * s, fx * s, 0.450 * s, half)
            for fx in (0.27, 0.43, 0.59, 0.75):
                _add_line(shapes, fx * s, 0.500 * s, fx * s, 0.545 * s, half)
            bar_y = 0.635
        else:
            for fx in (0.19, 0.34, 0.50, 0.66, 0.81):
                _add_line(shapes, fx * s, 0.425 * s, fx * s, 0.475 * s, half)
            bar_y = 0.615
        _add_line(shapes, 0.36 * s, bar_y * s, 0.64 * s, bar_y * s, half)
    elif kind == "headset":
        _add_arc(shapes, 0.12 * s, 0.12 * s, 0.88 * s, 0.88 * s, 10, 160, half)
        _add_oval(shapes, 0.05 * s, 0.46 * s, 0.27 * s, 0.80 * s, half)
        _add_oval(shapes, 0.73 * s, 0.46 * s, 0.95 * s, 0.80 * s, half)
        _add_line(shapes, 0.16 * s, 0.78 * s, 0.16 * s, 0.88 * s, half)
        _add_line(shapes, 0.16 * s, 0.88 * s, 0.34 * s, 0.89 * s, half)
        _add_dot(shapes, 0.35 * s, 0.885 * s, 0.03 * s)
    else:
        _add_box(shapes, 0.08 * s, 0.25 * s, 0.82 * s, 0.75 * s, 0.08 * s, half)
        _add_line(shapes, 0.86 * s, 0.40 * s, 0.86 * s, 0.60 * s, max(half, stroke))
        _add_line(shapes, 0.22 * s, 0.40 * s, 0.22 * s, 0.60 * s, half)
    return shapes


_ICON_CACHE: dict[tuple, tk.PhotoImage] = {}


def _icon_image(master, kind: str, size: float, color: str, bg: str,
                stroke: float) -> tk.PhotoImage:
    """Render an anti-aliased device icon blended onto the taskbar colour.

    Tk's canvas primitives have no anti-aliasing, which left visible stair
    steps on the rounded mouse body.  Rendering the same line art as a
    per-pixel coverage bitmap keeps the strokes smooth at any DPI.
    """
    s = max(8, int(round(size)))
    key = (kind, s, color, bg, round(float(stroke), 2))
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached
    if len(_ICON_CACHE) > 64:
        _ICON_CACHE.clear()
    shapes = _icon_shapes(kind, float(s), float(stroke))
    ink = _rgb(color)
    back = _rgb(bg)
    rows = []
    for y in range(s):
        cells = []
        for x in range(s):
            px, py = x + 0.5, y + 0.5
            cover = 0.0
            for box, test in shapes:
                if px < box[0] or px > box[2] or py < box[1] or py > box[3]:
                    continue
                value = test(px, py)
                if value > cover:
                    cover = value
                    if cover >= 1.0:
                        break
            cells.append(bg if cover <= 0.0 else _blend(back, ink, cover))
        rows.append("{" + " ".join(cells) + "}")
    image = tk.PhotoImage(master=master, width=s, height=s)
    image.put(" ".join(rows))
    _ICON_CACHE[key] = image
    return image


def _icon_kind(kind: str) -> str:
    text = (kind or "").lower()
    if text in ("headset", "headphone", "headphones", "earphone", "earphones", "audio"):
        return "headset"
    if text in ("keyboard", "key"):
        return "keyboard"
    if text in ("mouse", "mice"):
        return "mouse"
    return text


class TaskbarStrip:
    def __init__(self, root: tk.Tk, on_right_click=None):
        self.root = root
        self.on_right_click = on_right_click
        self.light, self.bg, self.fg = _taskbar_theme()
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.configure(bg=self.bg)
        self.canvas = tk.Canvas(self.win, bg=self.bg, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        # Colour-key the background so the taskbar shows through; the icon and
        # text pixels remain opaque and clickable.
        try:
            self.win.attributes("-transparentcolor", self.bg)
        except tk.TclError:
            pass
        self.canvas.bind("<Button-3>", self._right_clicked)
        self.win.bind("<Button-3>", self._right_clicked)
        self.canvas.bind("<Configure>", self._on_configure)
        self.win.update_idletasks()
        self.win.deiconify()
        self.win.update_idletasks()
        child = int(self.win.winfo_id())
        wrapper = int(user32.GetParent(child) or 0)
        self.hwnd = wrapper or child
        self._embedded = False
        self._hidden = True
        self._after_id = None
        self._views: list = []
        self._cells: list[tuple[str, str, int | None, float]] = []
        self._font: tkfont.Font | None = None
        self._font_size = 0
        self._dpi = max(96, int(round(get_dpi_for_system())))
        self._width = max(40, int(round(56 * self._dpi / 96)))
        self._last_height = 0
        self._shown = False
        self._embed()
        self._layout()
        self._after_id = self.root.after(1000, self._tick)

    def _embed(self) -> None:
        if self._embedded:
            return
        shell = int(user32.FindWindowW("Shell_TrayWnd", None) or 0)
        if not shell or not self.hwnd:
            self.win.withdraw()
            return
        user32.SetParent(self.hwnd, shell)
        style = int(user32.GetWindowLongW(self.hwnd, GWL_STYLE) or 0)
        style = (style | WS_CHILD | WS_VISIBLE | WS_CLIPSIBLINGS) & ~0x00C00000
        user32.SetWindowLongW(self.hwnd, GWL_STYLE, style)
        self._embedded = True
        self.win.deiconify()
        self._shown = True

    def _place(self) -> None:
        if not self._embedded or not self.hwnd:
            return
        if self._hidden:
            # Hide through Tk as well: showing a window that was hidden with a
            # raw ShowWindow() leaves its layered surface unpainted (all black).
            if getattr(self, "_shown", False):
                try:
                    self.win.withdraw()
                except tk.TclError:
                    pass
                self._shown = False
            self._repaint_taskbar()
            return
        just_shown = False
        if not getattr(self, "_shown", False):
            try:
                self.win.deiconify()
            except tk.TclError:
                pass
            self._shown = True
            just_shown = True
        shell = int(user32.FindWindowW("Shell_TrayWnd", None) or 0)
        if not shell:
            return
        tray = int(user32.FindWindowExW(shell, None, "TrayNotifyWnd", None) or 0)
        bar = RECT()
        notify = RECT()
        if not user32.GetWindowRect(shell, ctypes.byref(bar)):
            return
        if tray and not user32.GetWindowRect(tray, ctypes.byref(notify)):
            tray = 0
        dpi = max(96, int(round(get_dpi_for_system())))
        height = min(bar.bottom - bar.top, max(32, int(round(40 * dpi / 96))))
        width = max(40, int(self._width))
        if bar.right - bar.left >= bar.bottom - bar.top:  # horizontal taskbar
            right = notify.left if tray else bar.right
            x_abs = right - width
            tm = _trafficmonitor_rect()
            if (
                tm is not None
                and tm.left < right
                and tm.right > x_abs
                and tm.top < bar.bottom
                and tm.bottom > bar.top
            ):
                avoid = tm.left - width - 4
                if avoid >= bar.left:
                    x_abs = avoid
            x = max(0, x_abs - bar.left)
            y = max(0, (bar.bottom - bar.top - height) // 2)
        else:  # vertical taskbar
            bottom = notify.top if tray else bar.bottom
            x = max(0, (bar.right - bar.left - width) // 2)
            y = max(0, bottom - height - bar.top)
        # HWND_TOP: the taskbar re-creates/re-orders its children (task list,
        # tray) on layout changes; without raising the strip it can slip under
        # MSTaskListWClass and disappear even though it is still "visible".
        user32.SetWindowPos(self.hwnd, 0, x, y, width, height,
                            SWP_NOACTIVATE | SWP_SHOWWINDOW)
        if just_shown:
            # Tk missed the map while the window was hidden; repaint now that
            # it is visible again, otherwise the surface stays black.
            self._draw()
            try:
                user32.InvalidateRect(self.hwnd, None, True)
                user32.UpdateWindow(self.hwnd)
            except Exception:
                pass

    def _repaint_taskbar(self) -> None:
        """Make the taskbar repaint the area the strip just vacated.

        The strip is a child of Shell_TrayWnd; hiding it does not make Explorer
        erase the rect, which otherwise stays as an unpainted black hole.
        """
        shell = int(user32.FindWindowW("Shell_TrayWnd", None) or 0)
        if not shell:
            return
        rect = RECT()
        if not user32.GetWindowRect(self.hwnd, ctypes.byref(rect)):
            return
        try:
            user32.RedrawWindow.restype = wintypes.BOOL
            user32.RedrawWindow.argtypes = [
                wintypes.HWND, ctypes.POINTER(RECT), wintypes.HANDLE, wintypes.UINT
            ]
            user32.RedrawWindow(
                shell,
                ctypes.byref(rect),
                None,
                0x0001 | 0x0004 | 0x0080 | 0x0100,  # INVALIDATE|ERASE|ALLCHILDREN|UPDATENOW
            )
        except Exception:
            pass

    def _sync_theme(self) -> bool:
        light, bg, fg = _taskbar_theme()
        if (light, bg, fg) == (self.light, self.bg, self.fg):
            return False
        self.light, self.bg, self.fg = light, bg, fg
        self.win.configure(bg=bg)
        self.canvas.configure(bg=bg)
        try:
            self.win.attributes("-transparentcolor", bg)
        except tk.TclError:
            pass
        return True

    def _make_cells(self, views) -> list[tuple[str, str, int | None, float]]:
        cells: list[tuple[str, str, int | None, float]] = []
        for view in views:
            kind = _icon_kind(str(getattr(getattr(view, "target", None), "kind", "")))
            level = getattr(view, "level", None)
            text = "--%" if level is None else f"{level}%"
            cells.append((kind, text, level, 0.0))
        return cells

    def _layout(self) -> None:
        self._sync_theme()
        if not self._views:
            self._cells = []
            self._width = 1
            self._hidden = True
            self._place()
            return
        self._hidden = False
        dpi = max(96, int(round(get_dpi_for_system())))
        scale = dpi / 96.0
        height = self.canvas.winfo_height()
        if height <= 1:
            height = max(30, int(round(40 * scale)))
        font_size = 11 if height >= 42 else 10
        font = tkfont.Font(root=self.root, family="Microsoft YaHei UI",
                           size=font_size, weight="normal")
        icon_size = max(16.0, min(round(25 * scale), round(height * 0.64)))
        pad = max(4.0, round(6 * scale))
        gap = max(4.0, round(7 * scale))
        raw = self._make_cells(self._views)
        cells: list[tuple[str, str, int | None, float]] = []
        total = 0.0
        for kind, text, level, _old in raw:
            text_width = float(font.measure(text))
            cell_width = pad + icon_size + gap + text_width + pad
            cells.append((kind, text, level, cell_width))
            total += cell_width
        self._cells = cells
        self._font = font
        self._font_size = font_size
        self._dpi = dpi
        self._width = max(40, int(round(total)) + 1)
        self._draw(icon_size, pad, gap, height)
        self._place()

    def _draw(self, icon_size: float | None = None, pad: float | None = None,
              gap: float | None = None, height: float | None = None) -> None:
        if self._font is None:
            return
        if icon_size is None or pad is None or gap is None or height is None:
            scale = self._dpi / 96.0
            height = float(self.canvas.winfo_height())
            if height <= 1:
                height = max(30.0, 40 * scale)
            icon_size = max(16.0, min(round(25 * scale), round(height * 0.64)))
            pad = max(4.0, round(6 * scale))
            gap = max(4.0, round(7 * scale))
        self.canvas.delete("all")
        if not self._cells:
            return
        stroke = max(1, min(2, int(round(0.8 * self._dpi / 96.0))))
        divider = COLOR_LIGHT_DIVIDER if self.light else COLOR_DARK_DIVIDER
        x = 0.0
        icon_top = (height - icon_size) / 2.0
        for index, (kind, text, level, cell_width) in enumerate(self._cells):
            if index:
                self.canvas.create_line(x, height * 0.22, x, height * 0.78,
                                        fill=divider, width=1, capstyle=tk.BUTT)
            icon_x = x + pad
            image = _icon_image(self.canvas, kind, icon_size, self.fg, self.bg, stroke)
            self.canvas.create_image(
                int(round(icon_x)), int(round(icon_top)), anchor="nw", image=image
            )
            text_x = icon_x + icon_size + gap
            self.canvas.create_text(
                text_x,
                height / 2.0,
                text=text,
                anchor="w",
                fill=_battery_color(level, self.light),
                font=self._font,
            )
            x += cell_width

    def _on_configure(self, event) -> None:
        if event.widget is not self.canvas:
            return
        if int(event.height) == self._last_height:
            return
        self._last_height = int(event.height)
        # A height change (DPI/taskbar resize) can alter icon and font metrics.
        self._layout()

    def _tick(self) -> None:
        try:
            if not self._embedded:
                self._embed()
            dpi = max(96, int(round(get_dpi_for_system())))
            if abs(dpi - self._dpi) >= 1:
                self._layout()
            elif self._sync_theme():
                self._layout()
            self._place()
            self._after_id = self.root.after(1000, self._tick)
        except tk.TclError:
            self._after_id = None

    def _right_clicked(self, _event=None) -> None:
        if self.on_right_click is None:
            return
        try:
            self.on_right_click()
        except Exception:
            pass

    def update(self, views) -> None:
        self._views = list(views)
        self._layout()

    def destroy(self) -> None:
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        try:
            self.win.destroy()
        except tk.TclError:
            pass
