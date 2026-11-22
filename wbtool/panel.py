"""The compact two-card Tk panel."""
from __future__ import annotations

import re
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import Callable

from .service import DeviceView

BG = "#11151A"
SURFACE = "#1B2129"
SURFACE_2 = "#222A34"
TEXT = "#E8EDF4"
MUTED = "#8B97A8"
BORDER = "#2A3340"
ACCENT = "#5AA9FF"
GREEN = "#35C46A"


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


class BatteryCard(tk.Canvas):
    WIDTH = 252
    HEIGHT = 236

    def __init__(self, master, kind: str, tray_selected: bool = True,
                 on_tray_toggle: Callable[[str, bool], None] | None = None,
                 title: str | None = None, show_tray: bool = True, **kwargs):
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
        self.show_tray = show_tray
        self.on_tray_toggle = on_tray_toggle
        self._last_key = None
        self._source_font = tkfont.Font(family="Microsoft YaHei UI", size=8)
        self.tray_var = tk.BooleanVar(value=bool(tray_selected))
        if self.show_tray:
            self.bind("<Button-1>", self._card_clicked)
        self.draw(None)

    def _card_clicked(self, _event=None) -> None:
        self.tray_var.set(not self.tray_var.get())
        self._draw_tray_marker()
        if self.on_tray_toggle:
            self.on_tray_toggle(self.kind, bool(self.tray_var.get()))

    def set_tray_selected(self, selected: bool) -> None:
        self.tray_var.set(bool(selected))
        self._draw_tray_marker()

    def _draw_tray_marker(self) -> None:
        self.delete("tray_marker")
        if not self.show_tray or not self.tray_var.get():
            return
        self.create_polygon(
            self.WIDTH - 30, self.HEIGHT - 2,
            self.WIDTH - 2, self.HEIGHT - 2,
            self.WIDTH - 2, self.HEIGHT - 30,
            fill=GREEN,
            outline="",
            tags="tray_marker",
        )

    def _kind_label(self) -> str:
        if self.kind == "placeholder":
            return "占位"
        if self.title:
            return self.title
        return "键盘" if self.kind == "keyboard" else "鼠标"

    def draw(self, view: DeviceView | None) -> None:
        self.delete("all")
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
        source = view.source if view and view.source else (
            "临时测试卡片" if self.kind == "placeholder" else "等待读取"
        )
        updated = view.updated_text if view else "尚未读取"
        detail = view.detail if view and view.detail else ""
        stale = bool(view and view.stale)

        _rounded_rect(self, 1, 1, self.WIDTH - 1, self.HEIGHT - 1, 15, fill=SURFACE, outline=BORDER, width=1)
        # Accent strip on the left edge.
        _rounded_rect(self, 1, 1, 6, self.HEIGHT - 1, 3, fill=color, outline="")
        self.create_text(22, 30, text=self._kind_label(), anchor="w", fill=MUTED, font=("Microsoft YaHei UI", 9))
        self.create_text(
            self.WIDTH - 22,
            30,
            text=("更新 " + updated) if view else updated,
            anchor="e",
            fill=MUTED,
            font=("Segoe UI", 8),
        )
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
        self.create_text(22, 184, text="数据来源", anchor="w", fill=MUTED, font=("Microsoft YaHei UI", 8))
        source_text = source
        if level is None and detail:
            # Keep the card readable: the full error/detail lives in the
            # status area, while the card gets one short actionable line.
            if "未检测到" in detail:
                source_text = "未检测到设备，请检查连接"
            elif "没有可用" in detail or "休眠" in detail or "唤醒" in detail:
                source_text = "等待设备唤醒（可手动标定）"
            else:
                source_text = detail.split("；")[0]
                if len(source_text) > 26:
                    source_text = source_text[:25] + "…"
        self.create_text(
            22,
            206,
            text=self._ellipsize(re.sub(r"[（(][^）)]*[）)]", "", source_text).strip(), self.WIDTH - 62),
            anchor="w",
            fill=TEXT if not stale else MUTED,
            font=self._source_font,
            width=0,
        )
        self._draw_tray_marker()
        self._last_key = (level, name, status, source, updated, stale, color)

    def _ellipsize(self, text: str, max_width: int) -> str:
        if self._source_font.measure(text) <= max_width:
            return text
        ellipsis = "…"
        while text and self._source_font.measure(text + ellipsis) > max_width:
            text = text[:-1]
        return (text + ellipsis) if text else ellipsis

    @staticmethod
    def _status_badge(canvas: tk.Canvas, text: str, color: str, x: int, y: int) -> None:
        pass


class PanelWindow:
    """A single reusable Toplevel; never create a second copy."""

    def __init__(
        self,
        root: tk.Tk,
        *,
        tray_enabled: bool,
        refresh_seconds: int,
        startup_enabled: bool,
        startup_hint: str,
        tray_icon_visible: bool,
        taskbar_visible: bool,
        tray_devices: list[str],
        on_refresh: Callable[[], None],
        on_interval: Callable[[int], None],
        on_startup: Callable[[bool], None],
        on_tray_icon: Callable[[bool], None],
        on_taskbar: Callable[[bool], None],
        on_tray_devices: Callable[[list[str]], None],
        on_close: Callable[[], None],
        on_quit: Callable[[], None],
    ):
        self.root = root
        self.tray_enabled = tray_enabled
        self.tray_icon_visible = tray_icon_visible
        self.taskbar_visible = taskbar_visible
        self.tray_devices = list(tray_devices)
        self.on_refresh = on_refresh
        self.on_interval = on_interval
        self.on_startup = on_startup
        self.on_tray_icon = on_tray_icon
        self.on_taskbar = on_taskbar
        self.on_tray_devices = on_tray_devices
        self.on_close = on_close
        self.on_quit = on_quit

        self.win = tk.Toplevel(root)
        self.win.title("电量托盘")
        self.win.configure(bg=BG)
        self.win.geometry("584x514")
        self.win.minsize(560, 492)
        self.win.protocol("WM_DELETE_WINDOW", self._close_clicked)
        self.win.bind("<Escape>", lambda _e: self._close_clicked())
        try:
            self.win.attributes("-alpha", 1.0)
        except tk.TclError:
            pass

        self._build_header()
        self._build_footer(refresh_seconds, startup_enabled, startup_hint)
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
        self._card_columns = 0
        self._card_gap = -1

        self.card_mouse = BatteryCard(
            self.cards_inner, "mouse",
            tray_selected="mouse" in self.tray_devices,
            on_tray_toggle=self._device_toggled,
        )
        self.card_keyboard = BatteryCard(
            self.cards_inner, "keyboard",
            tray_selected="keyboard" in self.tray_devices,
            on_tray_toggle=self._device_toggled,
        )
        self._cards = [self.card_mouse, self.card_keyboard]

        self.cards_inner.bind("<Configure>", self._on_cards_inner_configure)
        self.cards_canvas.bind("<Configure>", self._on_cards_canvas_configure)
        self.win.bind("<MouseWheel>", self._on_cards_mousewheel, add="+")
        self.win.after_idle(self._layout_cards)

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
        if columns == self._card_columns and gap == self._card_gap:
            return
        columns_changed = columns != self._card_columns
        self._card_columns = columns
        self._card_gap = gap
        for index, card in enumerate(self._cards):
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

    def _build_footer(self, refresh_seconds: int, startup_enabled: bool, startup_hint: str) -> None:
        footer = tk.Frame(self.win, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        footer.pack(fill="x", side="bottom", padx=18, pady=(8, 16))

        row1 = tk.Frame(footer, bg=SURFACE)
        row1.pack(fill="x", padx=14, pady=(11, 4))
        self.startup_var = tk.BooleanVar(value=startup_enabled)
        self.startup_check = tk.Checkbutton(
            row1,
            text="开机自启",
            variable=self.startup_var,
            command=self._startup_clicked,
            bg=SURFACE,
            fg=TEXT,
            activebackground=SURFACE,
            activeforeground=TEXT,
            selectcolor=SURFACE_2,
            font=("Microsoft YaHei UI", 9),
            bd=0,
            highlightthickness=0,
        )
        self.startup_check.pack(side="left")
        self.startup_hint = tk.Label(
            row1,
            text=startup_hint,
            bg=SURFACE,
            fg=MUTED,
            font=("Microsoft YaHei UI", 8),
        )
        self.startup_hint.pack(side="left", padx=(10, 0))

        self.interval_var = tk.StringVar()
        self.interval_box = ttk.Combobox(
            row1,
            textvariable=self.interval_var,
            values=[label for _, label in REFRESH_CHOICES],
            width=8,
            state="readonly",
            font=("Microsoft YaHei UI", 9),
        )
        self.interval_box.pack(side="right", padx=(0, 32))
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
        self.quit_btn = self._button(row2, "退出托盘" if self.tray_enabled else "退出", self._quit_clicked, danger=True)
        self.quit_btn.pack(side="right")
        if self.tray_enabled:
            self._button(row2, "隐藏到托盘", self._close_clicked).pack(side="right", padx=(0, 8))



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

    def _device_toggled(self, kind: str, selected: bool) -> None:
        chosen = set(self.tray_devices)
        if selected:
            chosen.add(kind)
        else:
            chosen.discard(kind)
        self.tray_devices = [item for item in ("mouse", "keyboard") if item in chosen]
        self.on_tray_devices(self.tray_devices)

    def _interval_selected(self, _event=None) -> None:
        label = self.interval_var.get()
        for seconds, text in REFRESH_CHOICES:
            if text == label:
                self.on_interval(seconds)
                return

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

    def set_startup_state(self, enabled: bool, hint: str) -> None:
        self.startup_var.set(bool(enabled))
        self.startup_hint.configure(text=hint)

    def set_visibility_state(self, tray_icon: bool, taskbar: bool) -> None:
        self.tray_icon_visible = bool(tray_icon)
        self.taskbar_visible = bool(taskbar)
        self.tray_icon_var.set(self.tray_icon_visible)
        self.taskbar_var.set(self.taskbar_visible)

    def set_tray_devices(self, devices: list[str]) -> None:
        self.tray_devices = list(devices)
        self.card_mouse.set_tray_selected("mouse" in self.tray_devices)
        self.card_keyboard.set_tray_selected("keyboard" in self.tray_devices)

    def set_status(self, text: str) -> None:
        text = str(text)
        if len(text) > 24:
            text = text[:23] + "…"
        self.status.configure(text=text)

    def update_views(self, views: list[DeviceView], elapsed: float | None = None) -> None:
        self.views = list(views)
        by_kind = {v.target.kind: v for v in views}
        mouse = by_kind.get("mouse") or (views[0] if views else None)
        keyboard = by_kind.get("keyboard") or (views[1] if len(views) > 1 else None)
        self.card_mouse.draw(mouse)
        self.card_keyboard.draw(keyboard)
        if views:
            connected = sum(1 for v in views if v.connected)
            self.subtitle.configure(text=f"已连接 {connected}/2 台")
            errors = [v.detail for v in views if v.level is None and v.detail]
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
