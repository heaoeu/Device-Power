"""The compact two-card Tk panel."""
from __future__ import annotations

import math
import re
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import Callable

from .icon import apply_icon
from .service import DeviceView
from .win32 import foreground_window, toplevel_hwnd

BG = "#11151A"
SURFACE = "#1B2129"
SURFACE_2 = "#222A34"
TEXT = "#E8EDF4"
MUTED = "#8B97A8"
BORDER = "#2A3340"
ACCENT = "#5AA9FF"
TRANSPORT_COLORS = {
    "2.4G": "#2E9E5B",      # green: 2.4G dongle
    "蓝牙": "#2F6FD0",      # blue: Bluetooth
    "有线": "#3C4654",      # grey: plain USB cable
}


REFRESH_CHOICES = [
    (30, "30 秒"),
    (60, "1 分钟"),
    (120, "2 分钟"),
    (300, "5 分钟"),
    (600, "10 分钟"),
    (1800, "30 分钟"),
]

def _rounded_rect(canvas: tk.Canvas, x1, y1, x2, y2, radius, **kwargs):
    points = [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


def _rgb(color: str) -> tuple[int, int, int]:
    return (int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16))


# Tk's canvas never anti-aliases polygons or ovals, so the pill switch is drawn
# as a supersampled bitmap instead: render at 6x, average each 6x6 block down.
_SWITCH_SS = 6
_SWITCH_CACHE: dict[bool, tk.PhotoImage] = {}


def _downsample(master, samples, width: int, height: int, scale: int) -> tk.PhotoImage:
    """Box-filter a ``scale``x supersampled colour grid into a PhotoImage."""
    image = tk.PhotoImage(master=master, width=width, height=height)
    lines = []
    block = scale * scale
    for y in range(height):
        cells = []
        for x in range(width):
            red = green = blue = 0
            for dy in range(scale):
                sample_row = samples[y * scale + dy]
                for dx in range(scale):
                    r, g, b = sample_row[x * scale + dx]
                    red += r
                    green += g
                    blue += b
            cells.append("#%02x%02x%02x" % (red // block, green // block, blue // block))
        lines.append("{" + " ".join(cells) + "}")
    image.put(" ".join(lines))
    return image


def _switch_image(master, on: bool, width: int, height: int) -> tk.PhotoImage:
    """Crisp pill switch: capsule track + round knob, anti-aliased on the CPU."""
    cached = _SWITCH_CACHE.get(on)
    if cached is not None:
        return cached
    ss = _SWITCH_SS
    hi_w, hi_h = width * ss, height * ss
    radius = hi_h / 2.0
    center_y = hi_h / 2.0
    straight_a, straight_b = radius, hi_w - radius
    knob_radius = 5.5 * ss
    knob_center = (hi_w - radius) if on else radius
    track = _rgb(ACCENT) if on else _rgb(SURFACE_2)
    border = _rgb(BORDER)
    knob = _rgb("#FFFFFF") if on else _rgb(MUTED)
    background = _rgb(SURFACE)
    border_px = float(ss)          # the 1 px outline, in supersampled pixels

    samples = []
    for y in range(hi_h):
        py = y + 0.5
        row = []
        for x in range(hi_w):
            px = x + 0.5
            nearest = straight_a if px < straight_a else (straight_b if px > straight_b else px)
            distance = math.hypot(px - nearest, py - center_y) - radius
            if distance <= 0.0:
                color = border if (not on and distance > -border_px) else track
            else:
                color = background
            if math.hypot(px - knob_center, py - center_y) <= knob_radius:
                color = knob
            row.append(color)
        samples.append(row)

    image = _downsample(master, samples, width, height, ss)
    _SWITCH_CACHE[on] = image
    return image


_SHAPE_SS = 6
_SHAPE_CACHE: dict[tuple, tk.PhotoImage] = {}


def _rounded_image(master, width: int, height: int, radius: float, fill: str,
                   outline: str = "", outline_width: float = 0.0) -> tk.PhotoImage:
    """Anti-aliased rounded rectangle blended onto the card surface colour.

    Used for the small transport chip: at this size a Tk polygon shows stair
    steps on every corner, while a supersampled bitmap keeps the capsule edge
    soft.  The shape is opaque, so the corners are blended into ``SURFACE``.
    """
    key = (width, height, round(radius, 1), fill, outline, round(outline_width, 1))
    cached = _SHAPE_CACHE.get(key)
    if cached is not None:
        return cached
    if len(_SHAPE_CACHE) > 512:
        _SHAPE_CACHE.clear()

    scale = _SHAPE_SS
    hi_w, hi_h = width * scale, height * scale
    half_w, half_h = hi_w / 2.0, hi_h / 2.0
    radius = max(0.0, min(float(radius), min(width, height) / 2.0)) * scale
    inner_w, inner_h = half_w - radius, half_h - radius
    fill_rgb = _rgb(fill)
    outline_rgb = _rgb(outline) if outline else fill_rgb
    edge = float(outline_width) * scale if outline else 0.0
    backdrop = _rgb(SURFACE)

    samples = []
    for y in range(hi_h):
        py = y + 0.5 - half_h
        row = []
        for x in range(hi_w):
            px = x + 0.5 - half_w
            dx = abs(px) - inner_w
            dy = abs(py) - inner_h
            distance = (
                math.hypot(max(dx, 0.0), max(dy, 0.0))
                + min(max(dx, dy), 0.0)
                - radius
            )
            if distance <= 0.0:
                color = outline_rgb if (edge and distance > -edge) else fill_rgb
            else:
                color = backdrop
            row.append(color)
        samples.append(row)

    image = _downsample(master, samples, width, height, scale)
    _SHAPE_CACHE[key] = image
    return image


_TRI_SS = 4
_TRI_CACHE: dict[tuple, tk.PhotoImage] = {}


def _triangle_image(master, size: int, fill: str, ink: str) -> tk.PhotoImage:
    """Corner ribbon (right angle top-right) with an anti-aliased bin icon.

    The bin is rasterised into the same supersampled bitmap as the triangle,
    so both the diagonal and the icon strokes come out smooth instead of the
    one-pixel stair steps a Tk polygon plus 1 px canvas lines would give.
    """
    key = (size, fill, ink)
    cached = _TRI_CACHE.get(key)
    if cached is not None:
        return cached
    scale = _TRI_SS
    hi = size * scale
    fill_rgb = _rgb(fill)
    ink_rgb = _rgb(ink)
    back = _rgb(SURFACE)

    cx, cy = size * 0.70, size * 0.31      # bin centre, tucked into the corner
    half = 0.85                            # stroke half-width in pixels
    segments = (
        ((cx - 6, cy - 4), (cx + 6, cy - 4)),   # lid
        ((cx - 2, cy - 7), (cx + 2, cy - 7)),   # handle
        ((cx - 4, cy - 2), (cx - 3, cy + 7)),   # left wall
        ((cx + 4, cy - 2), (cx + 3, cy + 7)),   # right wall
        ((cx - 3, cy + 7), (cx + 3, cy + 7)),   # bottom
    )
    bx0, bx1 = cx - 8, cx + 8
    by0, by1 = cy - 9, cy + 9

    def near_ink(px: float, py: float) -> bool:
        if px < bx0 or px > bx1 or py < by0 or py > by1:
            return False
        for (ax, ay), (bx, by) in segments:
            vx, vy = bx - ax, by - ay
            wx, wy = px - ax, py - ay
            length2 = vx * vx + vy * vy
            t = 0.0 if length2 == 0 else max(0.0, min(1.0, (wx * vx + wy * vy) / length2))
            dx, dy = wx - vx * t, wy - vy * t
            if dx * dx + dy * dy <= half * half:
                return True
        return False

    samples = []
    for y in range(hi):
        py = (y + 0.5) / scale
        row = []
        for x in range(hi):
            px = (x + 0.5) / scale
            if py <= px:                    # right triangle: right angle top-right
                row.append(ink_rgb if near_ink(px, py) else fill_rgb)
            else:
                row.append(back)
        samples.append(row)
    image = _downsample(master, samples, size, size, scale)
    _TRI_CACHE[key] = image
    return image


class BatteryCard(tk.Canvas):
    WIDTH = 252
    HEIGHT = 236          # data block + the two surface switches
    SWITCH_WIDTH = 30
    SWITCH_HEIGHT = 16
    BADGE_HEIGHT = 15
    SOURCE_WIDTH = 104     # left half of the bottom row; the switches own the right half

    def __init__(self, master, kind: str, taskbar_on: bool = True, tray_on: bool = True,
                 on_surface_toggle: Callable[[str, str, bool], None] | None = None,
                 title: str | None = None, device_id: str = "",
                 on_delete: Callable[[str], None] | None = None, **kwargs):
        super().__init__(
            master,
            width=self.WIDTH,
            height=self.HEIGHT,
            bg=BG,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
            **kwargs,
        )
        self.kind = kind
        self.title = title
        self.device_id = device_id
        self.on_delete = on_delete
        self.transport = ""
        self.on_surface_toggle = on_surface_toggle
        self.taskbar_on = bool(taskbar_on)
        self.tray_on = bool(tray_on)
        self._last_key = None
        self._badge_font = tkfont.Font(family="Microsoft YaHei UI", size=8)
        self._source_font = tkfont.Font(family="Microsoft YaHei UI", size=8)
        # Tag bindings survive the redraws in _draw_switches().
        self.tag_bind("sw_taskbar", "<Button-1>", lambda _e: self._toggle_surface("taskbar"))
        self.tag_bind("sw_tray", "<Button-1>", lambda _e: self._toggle_surface("tray"))
        self.tag_bind("card_delete", "<Button-1>", lambda _e: self._delete_clicked())
        self.draw(None)

    def _delete_clicked(self) -> None:
        if self.on_delete and self.device_id:
            self.on_delete(self.device_id)

    def _draw_delete(self) -> None:
        """Corner ribbon with a bin icon; asks before deleting the device."""
        size = 44
        self.create_image(
            self.WIDTH - 8 - size,
            8,
            image=_triangle_image(self, size, "#4A2A30", "#FFB4B4"),
            anchor="nw",
            tags=("card_delete",),
        )

    def _toggle_surface(self, surface: str) -> None:
        if surface == "taskbar":
            self.taskbar_on = not self.taskbar_on
            value = self.taskbar_on
        else:
            self.tray_on = not self.tray_on
            value = self.tray_on
        self._draw_switches()
        if self.on_surface_toggle:
            self.on_surface_toggle(self.kind, surface, value)

    def set_surfaces(self, taskbar_on: bool, tray_on: bool) -> None:
        self.taskbar_on = bool(taskbar_on)
        self.tray_on = bool(tray_on)
        self._draw_switches()

    def _draw_switch(self, center_y: int, on: bool, tag: str, label: str) -> None:
        x2 = self.WIDTH - 22
        x1 = x2 - self.SWITCH_WIDTH
        y1 = center_y - self.SWITCH_HEIGHT / 2
        image = _switch_image(self, on, self.SWITCH_WIDTH, self.SWITCH_HEIGHT)
        self.create_image(x1, y1, image=image, anchor="nw", tags=(tag, "surface_switch"))
        self.create_text(
            x1 - 9,
            center_y,
            text=label,
            anchor="e",
            fill=TEXT if on else MUTED,
            font=("Microsoft YaHei UI", 9),
            tags=(tag, "surface_switch"),
        )

    def _draw_switches(self) -> None:
        """Per-device visibility switches, stacked in the bottom-right corner."""
        self.delete("surface_switch")
        self._draw_switch(190, self.taskbar_on, "sw_taskbar", "任务栏")
        self._draw_switch(214, self.tray_on, "sw_tray", "托盘")

    def _kind_label(self) -> str:
        if self.kind == "placeholder":
            return "占位"
        if self.title:
            return self.title
        return {
            "keyboard": "键盘",
            "mouse": "鼠标",
            "headset": "耳机",
        }.get(self.kind, "外设")

    def _draw_transport(self, anchor_item: int) -> int:
        """Coloured chip right after the device type: green 2.4G, blue BT.

        Returns the chip's right edge so the caller can keep the timestamp
        clear of it, or 0 when the link is unknown.
        """
        label = self.transport
        if not label:
            return 0
        color = TRANSPORT_COLORS.get(label, TRANSPORT_COLORS["有线"])
        width = self._badge_font.measure(label) + 14
        height = self.BADGE_HEIGHT
        bbox = self.bbox(anchor_item)
        left = int((bbox[2] if bbox else 22 + 30) + 7)
        top = int(30 - height / 2) + 1
        self.create_image(
            left,
            top,
            image=_rounded_image(self, width, height, height / 2, color),
            anchor="nw",
            tags=("transport",),
        )
        self.create_text(
            left + width / 2,
            top + height / 2,
            text=label,
            fill="#FFFFFF",
            font=self._badge_font,
            tags=("transport",),
        )
        # Tk centres a text item on its font box, not on the glyph ink; the CJK
        # fallback leaves the label about a pixel high inside the chip, so align
        # the rendered bounding box with the chip centre instead.
        chip_center = top + height / 2
        for item in self.find_withtag("transport"):
            if self.type(item) != "text":
                continue
            box = self.bbox(item)
            if box:
                offset = chip_center - (box[1] + box[3]) / 2
                if abs(offset) >= 0.5:
                    self.move(item, 0, offset)
        return left + width

    def draw(self, view: DeviceView | None) -> None:
        self.delete("all")
        self.transport = view.transport if view else ""
        level = view.level if view else None
        color = view.color if view else MUTED
        percent = view.display_percent if view else "--"
        if view:
            name = view.target.name
        elif self.title:
            name = self.title
        else:
            name = "AULA F87 Pro V2 键盘" if self.kind == "keyboard" else "无线鼠标"
        status = view.status_text if view else ""
        updated = view.updated_text if view else "尚未读取"
        stale = bool(view and view.stale)

        _rounded_rect(self, 1, 1, self.WIDTH - 1, self.HEIGHT - 1, 15, fill=SURFACE, outline=BORDER, width=1)
        # Accent strip on the left edge.
        _rounded_rect(self, 1, 1, 6, self.HEIGHT - 1, 3, fill=color, outline="")
        kind_item = self.create_text(
            22, 30, text=self._kind_label(), anchor="w", fill=MUTED,
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        badge_right = self._draw_transport(kind_item)
        update_text = ("更新 " + updated) if view else updated
        update_item = self.create_text(
            self.WIDTH - 22 - 34,
            30,
            text=update_text,
            anchor="e",
            fill=MUTED,
            font=("Segoe UI", 8),
        )
        self._avoid_badge_overlap(update_item, updated, badge_right)
        self.create_text(
            22,
            58,
            text=name,
            anchor="w",
            fill=TEXT,
            font=("Microsoft YaHei UI", 11, "bold"),
            width=self.WIDTH - 44,
        )
        self.create_text(
            22,
            110,
            text=percent,
            anchor="w",
            fill=color,
            font=("Segoe UI Semibold", 34, "bold"),
        )
        if status:
            badge_color = color if not stale else MUTED
            self.create_text(
                self.WIDTH - 22,
                110,
                text=status,
                anchor="e",
                fill=badge_color,
                font=("Microsoft YaHei UI", 9, "bold"),
            )
        # Progress track.
        bar_x1, bar_y1, bar_x2, bar_y2 = 22, 145, self.WIDTH - 22, 153
        _rounded_rect(self, bar_x1, bar_y1, bar_x2, bar_y2, 4, fill=SURFACE_2, outline="")
        if level is not None and level > 0:
            fill_x2 = bar_x1 + (bar_x2 - bar_x1) * max(0, min(100, level)) / 100.0
            if fill_x2 > bar_x1 + 4:
                _rounded_rect(self, bar_x1, bar_y1, fill_x2, bar_y2, 4, fill=color, outline="")
        source = view.source if view and view.source else ""
        source_text = self._clean_source(source)
        if source_text:
            self.create_text(
                22,
                190,
                text="数据来源",
                anchor="w",
                fill=MUTED,
                font=("Microsoft YaHei UI", 8),
                tags=("source",),
            )
            self.create_text(
                22,
                212,
                text=self._ellipsize(source_text, self.SOURCE_WIDTH * 2 - 10),
                anchor="w",
                fill=TEXT if not stale else MUTED,
                font=self._source_font,
                width=self.SOURCE_WIDTH,
                tags=("source",),
            )
        self._draw_switches()
        self._draw_delete()
        self._last_key = (level, name, status, updated, stale, color, self.transport, source_text)

    @staticmethod
    def _clean_source(source: str) -> str:
        """Shorten a provider label to fit the half-width column.

        Parenthesised report details (report ids, byte offsets) belong in the
        debug UI, and the link type (``2.4G``) is already shown by the chip
        after the device type, so both are dropped here.
        """
        if not source:
            return ""
        text = re.sub(r"[（(][^）)]*[）)]", " ", source)
        text = re.sub(r"(?i)(?<![0-9a-z])2\.4\s*g(?![0-9a-z])", " ", text)
        return " ".join(text.split())

    def _ellipsize(self, text: str, max_width: int) -> str:
        if self._source_font.measure(text) <= max_width:
            return text
        ellipsis = "…"
        while text and self._source_font.measure(text + ellipsis) > max_width:
            text = text[:-1]
        return (text + ellipsis) if text else ellipsis

    def _avoid_badge_overlap(self, item: int, short_text: str, badge_right: int) -> None:
        """Drop the ``更新`` prefix when the chip leaves no room for it."""
        if not badge_right:
            return
        box = self.bbox(item)
        if box and box[0] < badge_right + 8:
            self.itemconfigure(item, text=short_text)


class PanelWindow:
    """A single reusable Toplevel; never create a second copy."""

    def __init__(
        self,
        root: tk.Tk,
        *,
        tray_enabled: bool,
        refresh_seconds: int,
        startup_enabled: bool,
        hide_to_tray: bool,
        tray_icon_visible: bool,
        taskbar_visible: bool,
        taskbar_devices: list[str],
        tray_devices: list[str],
        on_refresh: Callable[[], None],
        on_interval: Callable[[int], None],
        on_startup: Callable[[bool], None],
        on_tray_icon: Callable[[bool], None],
        on_taskbar: Callable[[bool], None],
        on_device_surface: Callable[[str, str, bool], None],
        on_scan: Callable[[], None],
        on_delete: Callable[[str], None],
        on_close: Callable[[], None],
        on_quit: Callable[[], None],
        on_hide_pref: Callable[[bool], None],
    ):
        self.root = root
        self.tray_enabled = tray_enabled
        self.tray_icon_visible = tray_icon_visible
        self.taskbar_visible = taskbar_visible
        self.taskbar_devices = list(taskbar_devices)
        self.tray_devices = list(tray_devices)
        self.on_refresh = on_refresh
        self.on_interval = on_interval
        self.on_startup = on_startup
        self.on_tray_icon = on_tray_icon
        self.on_taskbar = on_taskbar
        self.on_device_surface = on_device_surface
        self.on_scan = on_scan
        self.on_delete = on_delete
        self.on_close = on_close
        self.on_quit = on_quit
        self.on_hide_pref = on_hide_pref

        self.win = tk.Toplevel(root)
        self.win.title("电量托盘")
        apply_icon(self.win)
        self.win.configure(bg=BG)
        self.win.geometry("584x510")
        self.win.minsize(560, 488)
        self.win.protocol("WM_DELETE_WINDOW", self._close_clicked)
        self.win.bind("<Escape>", lambda _e: self._close_clicked())
        try:
            self.win.attributes("-alpha", 1.0)
        except tk.TclError:
            pass

        self._build_header()
        self._build_footer(refresh_seconds, startup_enabled, hide_to_tray)
        self._build_cards()
        self.views: list[DeviceView] = []

    def _build_header(self) -> None:
        header = tk.Frame(self.win, bg=BG)
        header.pack(fill="x", padx=24, pady=(20, 8))
        left = tk.Frame(header, bg=BG)
        left.pack(side="left", fill="x", expand=True)
        tk.Label(left, text="无线外设电量", bg=BG, fg=TEXT, font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w")
        self.subtitle = tk.Label(
            left,
            text="正在读取设备…",
            bg=BG,
            fg=MUTED,
            font=("Microsoft YaHei UI", 9),
        )
        self.subtitle.pack(anchor="w", pady=(3, 0))
        self.refresh_btn = self._button(header, "立即刷新", self.on_refresh, accent=True)
        self.refresh_btn.pack(side="right", padx=(8, 0))
        self.status = tk.Label(
            header,
            text="",
            bg=BG,
            fg=MUTED,
            font=("Microsoft YaHei UI", 8),
            anchor="e",
        )
        self.status.pack(side="right", padx=(8, 0))

    def _build_cards(self) -> None:
        area = tk.Frame(self.win, bg=BG)
        area.pack(fill="both", expand=True, padx=(18, 10), pady=(8, 4))
        self.cards_canvas = tk.Canvas(
            area, bg=BG, highlightthickness=0, bd=0, width=1, height=1
        )
        self.cards_canvas.pack(side="left", fill="both", expand=True)
        self.cards_inner = tk.Frame(self.cards_canvas, bg=BG)
        self._cards_window = self.cards_canvas.create_window(
            (0, 0), window=self.cards_inner, anchor="nw"
        )
        self._cards: dict[str, BatteryCard] = {}
        self._card_order: list[str] = []
        self._card_columns = 0
        self._card_gap = -1
        self._card_count = 0

        self.cards_inner.bind("<Configure>", self._on_cards_inner_configure)
        self.cards_canvas.bind("<Configure>", self._on_cards_canvas_configure)
        self.win.bind("<MouseWheel>", self._on_cards_mousewheel, add="+")
        self.win.after_idle(self._layout_cards)

    def _ensure_card(self, device_id: str, kind: str, name: str | None = None) -> BatteryCard:
        """Create the card for a device the first time it shows up."""
        card = self._cards.get(device_id)
        if card is not None:
            # The device type can be corrected after a refresh (HID data wins
            # over a stale settings entry), so redraw with the fresh kind.
            if kind and card.kind != kind:
                card.kind = kind
            return card
        card = BatteryCard(
            self.cards_inner,
            kind,
            taskbar_on=device_id in self.taskbar_devices,
            tray_on=device_id in self.tray_devices,
            on_surface_toggle=(
                lambda _kind, surface, enabled, _id=device_id:
                self._device_surface_toggled(_id, surface, enabled)
            ),
            device_id=device_id,
            on_delete=self.on_delete,
        )
        self._cards[device_id] = card
        self._card_order.append(device_id)
        return card

    def _layout_cards(self, width: int | None = None) -> None:
        if not getattr(self, "_cards", None):
            return
        width = width or self.cards_canvas.winfo_width()
        row_width = max(BatteryCard.WIDTH, width)
        min_gap = 12
        columns = max(
            1,
            (row_width + min_gap) // (BatteryCard.WIDTH + min_gap),
        )
        if columns <= 1:
            gap = 0
        else:
            # (panel width - horizontal padding - cards' widths) / gaps in a row
            gap = int(round((row_width - columns * BatteryCard.WIDTH) / (columns - 1)))
            gap = max(min_gap, gap)
        count = len(self._card_order)
        if (columns, gap, count) == (self._card_columns, self._card_gap, self._card_count):
            return
        columns_changed = columns != self._card_columns
        self._card_columns = columns
        self._card_gap = gap
        self._card_count = count
        for index, device_id in enumerate(self._card_order):
            card = self._cards[device_id]
            row, column = divmod(index, columns)
            card.grid(
                row=row,
                column=column,
                padx=(0, gap),
                pady=(0, min_gap),
                sticky="nw",
            )
        self.cards_inner.update_idletasks()
        self._update_cards_scroll()
        if columns_changed:
            # Re-wrapping changes the row count; keep the first row in view.
            self.cards_canvas.yview_moveto(0.0)

    def _on_cards_canvas_configure(self, event) -> None:
        self.cards_canvas.itemconfigure(self._cards_window, width=event.width)
        self._layout_cards(event.width)

    def _on_cards_inner_configure(self, _event) -> None:
        self._update_cards_scroll()

    def _update_cards_scroll(self) -> None:
        if not hasattr(self, "cards_canvas"):
            return
        self.cards_canvas.configure(scrollregion=self.cards_canvas.bbox("all"))

    def _on_cards_mousewheel(self, event):
        widget = event.widget
        while widget is not None:
            if widget in (self.cards_canvas, self.cards_inner):
                break
            widget = getattr(widget, "master", None)
        else:
            return None
        self.cards_canvas.yview_scroll(int(-event.delta / 120), "units")
        return "break"

    def _build_footer(self, refresh_seconds: int, startup_enabled: bool,
                      hide_to_tray: bool) -> None:
        footer = tk.Frame(self.win, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        footer.pack(fill="x", side="bottom", padx=18, pady=(8, 16))

        row1 = tk.Frame(footer, bg=SURFACE)
        row1.pack(fill="x", padx=14, pady=(11, 4))
        self.startup_var = tk.BooleanVar(value=startup_enabled)
        self.startup_check = self._checkbox(row1, "开机自启", self.startup_var, self._startup_clicked)
        self.startup_check.pack(side="left")
        # Only decides what the window's X button does: checked hides the
        # panel to the tray, unchecked closes the program.
        self.hide_var = tk.BooleanVar(value=bool(hide_to_tray))
        self.hide_check = self._checkbox(row1, "隐藏到托盘", self.hide_var, self._hide_clicked)
        self.hide_check.pack(side="left", padx=(12, 0))

        self.interval_var = tk.StringVar()
        self.interval_box = ttk.Combobox(
            row1,
            textvariable=self.interval_var,
            values=[label for _, label in REFRESH_CHOICES],
            width=8,
            state="readonly",
            font=("Microsoft YaHei UI", 9),
        )
        self.interval_box.pack(side="right", padx=(0, 0))
        tk.Label(row1, text="刷新间隔", bg=SURFACE, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(side="right", padx=(8, 4))
        self.interval_box.bind("<<ComboboxSelected>>", self._interval_selected)
        self.set_interval(refresh_seconds)

        row2 = tk.Frame(footer, bg=SURFACE)
        row2.pack(fill="x", padx=14, pady=(4, 10))
        self.tray_icon_var = tk.BooleanVar(value=self.tray_icon_visible)
        self.tray_icon_check = self._checkbox(row2, "显示托盘图标", self.tray_icon_var, self._tray_icon_clicked)
        self.tray_icon_check.pack(side="left")
        self.taskbar_var = tk.BooleanVar(value=self.taskbar_visible)
        self.taskbar_check = self._checkbox(row2, "显示任务栏图标", self.taskbar_var, self._taskbar_clicked)
        self.taskbar_check.pack(side="left", padx=(8, 0))
        self.scan_btn = self._button(row2, "扫描设备", self._scan_clicked)
        self.scan_btn.pack(side="right")

    def _checkbox(self, master, text: str, variable, command) -> tk.Checkbutton:
        return tk.Checkbutton(
            master,
            text=text,
            variable=variable,
            command=command,
            bg=SURFACE,
            fg=TEXT,
            activebackground=SURFACE,
            activeforeground=TEXT,
            selectcolor=SURFACE_2,
            font=("Microsoft YaHei UI", 9),
            bd=0,
            highlightthickness=0,
        )

    def _button(self, master, text: str, command, accent: bool = False, danger: bool = False) -> tk.Button:
        bg = ACCENT if accent else (("#3A2226") if danger else SURFACE_2)
        fg = "#07111C" if accent else (("#FFB4B4") if danger else TEXT)
        active = "#7DBBFF" if accent else (("#4A2A30") if danger else "#2C3744")
        return tk.Button(
            master,
            text=text,
            command=command,
            bg=bg,
            fg=fg,
            activebackground=active,
            activeforeground=fg,
            relief="flat",
            bd=0,
            padx=12,
            pady=5,
            cursor="hand2",
            font=("Microsoft YaHei UI", 9),
            highlightthickness=0,
        )

    # ---- interactions ---------------------------------------------------
    def _startup_clicked(self) -> None:
        self.on_startup(bool(self.startup_var.get()))

    def _tray_icon_clicked(self) -> None:
        self.on_tray_icon(bool(self.tray_icon_var.get()))

    def _taskbar_clicked(self) -> None:
        self.on_taskbar(bool(self.taskbar_var.get()))

    def _device_surface_toggled(self, device_id: str, surface: str, enabled: bool) -> None:
        """One card switch flipped: taskbar cells or tray icon for that device."""
        target = self.taskbar_devices if surface == "taskbar" else self.tray_devices
        if enabled:
            if device_id not in target:
                target.append(device_id)
        elif device_id in target:
            target.remove(device_id)
        self.on_device_surface(device_id, surface, enabled)

    def _interval_selected(self, _event=None) -> None:
        label = self.interval_var.get()
        for seconds, text in REFRESH_CHOICES:
            if text == label:
                self.on_interval(seconds)
                return

    def _hide_clicked(self) -> None:
        self.on_hide_pref(bool(self.hide_var.get()))

    def _scan_clicked(self) -> None:
        self.on_scan()

    def _close_clicked(self) -> None:
        self.on_close()

    def _quit_clicked(self) -> None:
        self.on_quit()

    def set_interval(self, seconds: int) -> None:
        for value, label in REFRESH_CHOICES:
            if value == seconds:
                self.interval_var.set(label)
                return
        self.interval_var.set(f"{max(1, seconds // 60)} 分钟")

    def set_startup_state(self, enabled: bool) -> None:
        self.startup_var.set(bool(enabled))

    def set_visibility_state(self, tray_icon: bool, taskbar: bool) -> None:
        self.tray_icon_visible = bool(tray_icon)
        self.taskbar_visible = bool(taskbar)
        self.tray_icon_var.set(self.tray_icon_visible)
        self.taskbar_var.set(self.taskbar_visible)

    def set_surfaces(self, taskbar_devices: list[str], tray_devices: list[str]) -> None:
        self.taskbar_devices = list(taskbar_devices)
        self.tray_devices = list(tray_devices)
        for device_id, card in self._cards.items():
            card.set_surfaces(
                device_id in self.taskbar_devices, device_id in self.tray_devices
            )

    def set_status(self, text: str) -> None:
        text = str(text)
        if len(text) > 24:
            text = text[:23] + "…"
        self.status.configure(text=text)

    def update_views(self, views: list[DeviceView], elapsed: float | None = None) -> None:
        self.views = list(views)
        active: list[str] = []
        for view in views:
            device_id = view.target.id
            active.append(device_id)
            self._ensure_card(device_id, view.target.kind, view.target.name).draw(view)
        for device_id in list(self._card_order):
            if device_id in active:
                continue
            card = self._cards.pop(device_id)
            self._card_order.remove(device_id)
            try:
                card.destroy()
            except tk.TclError:
                pass
        self._layout_cards()
        if views:
            connected = sum(1 for view in views if view.connected)
            self.subtitle.configure(text=f"已连接 {connected}/{len(views)} 台")
            errors = [view.detail for view in views if view.level is None and view.detail]
            if errors:
                self.set_status(errors[0][:110])
            elif elapsed is not None:
                self.set_status(f"上次刷新 {elapsed:.1f} 秒")
        else:
            self.subtitle.configure(text="没有配置设备")

    def show(self) -> None:
        try:
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
        except tk.TclError:
            pass
        # A click on the taskbar strip/tray leaves Explorer in front; Tk alone
        # would not bring the panel above the active application.
        try:
            foreground_window(toplevel_hwnd(int(self.win.winfo_id())))
        except Exception:
            pass

    def hide(self) -> None:
        try:
            self.win.withdraw()
        except tk.TclError:
            pass

    def destroy(self) -> None:
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    def exists(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def is_viewable(self) -> bool:
        try:
            return bool(self.win.winfo_viewable())
        except tk.TclError:
            return False
