"""Full HID debugger: enumerate, dump, probe, send, calibrate."""
from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from .config import load_settings, save_settings
from .hid import (
    HidDevice,
    HidInterface,
    enumerate_hid,
)
from .providers import read_calibration

BG = "#11151A"
SURFACE = "#1B2129"
SURFACE_2 = "#222A34"
TEXT = "#E8EDF4"
MUTED = "#8B97A8"
BORDER = "#2A3340"
ACCENT = "#5AA9FF"


def _hex(data: bytes, limit: int = 256) -> str:
    data = data[:limit]
    text = data.hex(" ").upper()
    if len(data) > limit:
        text += " …"
    return text


def _parse_hex(text: str) -> bytes:
    cleaned = (
        text.replace("0x", "")
        .replace("0X", "")
        .replace(",", " ")
        .replace("-", " ")
        .replace("\n", " ")
        .replace("\t", " ")
    )
    cleaned = "".join(cleaned.split())
    if len(cleaned) % 2:
        raise ValueError("十六进制字节数必须是偶数")
    return bytes.fromhex(cleaned)


class DebugWindow:
    def __init__(
        self,
        root: tk.Tk,
        *,
        on_close: Callable[[], None],
        on_settings_changed: Callable[[], None],
    ):
        self.root = root
        self.on_close_cb = on_close
        self.on_settings_changed = on_settings_changed
        self.win = tk.Toplevel(root)
        self.win.title("电量托盘 · 完整调试版")
        self.win.geometry("1120x760")
        self.win.minsize(900, 620)
        self.win.configure(bg=BG)
        self.win.protocol("WM_DELETE_WINDOW", self._close)
        self.win.bind("<Escape>", lambda _e: self._close())
        self._queue: queue.Queue = queue.Queue()
        self.interfaces: list[HidInterface] = []
        self.selected: HidInterface | None = None
        self._items: dict[str, HidInterface] = {}
        self._busy = False
        self.last_report: bytes = b""
        self._closed = False

        self._style()
        self._build()
        self._poll()
        self.refresh_devices()

    # ---- construction ----------------------------------------------------
    def _style(self) -> None:
        style = ttk.Style(self.win)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Debug.Treeview",
            background=SURFACE,
            fieldbackground=SURFACE,
            foreground=TEXT,
            rowheight=24,
            font=("Microsoft YaHei UI", 9),
        )
        style.configure(
            "Debug.Treeview.Heading",
            background=SURFACE_2,
            foreground=TEXT,
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.map("Debug.Treeview", background=[("selected", "#2C4A66")], foreground=[("selected", "#FFFFFF")])
        style.configure("TScrollbar", background=SURFACE_2, troughcolor=SURFACE)

    def _button(self, master, text, command, accent=False, danger=False):
        bg = ACCENT if accent else (("#3A2226") if danger else SURFACE_2)
        fg = "#07111C" if accent else (("#FFB4B4") if danger else TEXT)
        return tk.Button(
            master,
            text=text,
            command=command,
            bg=bg,
            fg=fg,
            activebackground="#7DBBFF" if accent else SURFACE_2,
            activeforeground=fg,
            relief="flat",
            bd=0,
            padx=11,
            pady=4,
            cursor="hand2",
            font=("Microsoft YaHei UI", 9),
            highlightthickness=0,
        )

    def _build(self) -> None:
        top = tk.Frame(self.win, bg=BG)
        top.pack(fill="x", padx=18, pady=(14, 8))
        tk.Label(top, text="HID 调试台", bg=BG, fg=TEXT, font=("Microsoft YaHei UI", 16, "bold")).pack(side="left")
        self.status = tk.Label(top, text="就绪", bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 9))
        self.status.pack(side="left", padx=14)
        self._button(top, "刷新设备", self.refresh_devices, accent=True).pack(side="right")
        self._button(top, "关闭", self._close, danger=True).pack(side="right", padx=(0, 8))

        paned = ttk.Panedwindow(self.win, orient="vertical")
        paned.pack(fill="both", expand=True, padx=16, pady=(0, 12))

        upper = tk.Frame(paned, bg=BG)
        lower = tk.Frame(paned, bg=BG)
        paned.add(upper, weight=3)
        paned.add(lower, weight=4)

        # Interface tree.
        tree_wrap = tk.Frame(upper, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        tree_wrap.pack(side="left", fill="both", expand=True)
        columns = ("vidpid", "usage", "reports", "product")
        self.tree = ttk.Treeview(
            tree_wrap,
            columns=columns,
            show="headings",
            style="Debug.Treeview",
            selectmode="browse",
        )
        headings = {
            "vidpid": ("VID:PID", 90),
            "usage": ("Usage", 145),
            "reports": ("In/Out/Feat", 116),
            "product": ("产品 / 接口", 265),
        }
        for col, (label, width) in headings.items():
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, anchor="w", stretch=(col == "product"))
        ysb = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ysb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        ysb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._selection_changed)

        # Details + action buttons on the right.
        right = tk.Frame(upper, bg=BG, width=390)
        right.pack(side="right", fill="y", padx=(12, 0))
        right.pack_propagate(False)
        self.detail = tk.Text(
            right,
            height=9,
            bg=SURFACE,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            bd=0,
            padx=10,
            pady=8,
            font=("Consolas", 8),
            wrap="word",
        )
        self.detail.pack(fill="x")
        self.detail.insert("1.0", "选择一个 HID 接口查看能力表和路径。")
        self.detail.configure(state="disabled")
        buttons = tk.Frame(right, bg=BG)
        buttons.pack(fill="x", pady=(8, 0))
        row_a = tk.Frame(buttons, bg=BG)
        row_a.pack(fill="x", pady=(0, 5))
        self._button(row_a, "转储 Feature", self.dump_features).pack(side="left", padx=(0, 5))
        self._button(row_a, "探测 Report ID", self.probe_ids).pack(side="left")
        row_b = tk.Frame(buttons, bg=BG)
        row_b.pack(fill="x")
        self._button(row_b, "监听输入", self.listen_input).pack(side="left", padx=(0, 5))
        self._button(row_b, "试发命令", self.send_command, accent=True).pack(side="left")

        # Lower output + calibration.
        output_wrap = tk.Frame(lower, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        output_wrap.pack(side="left", fill="both", expand=True)
        self.output = tk.Text(
            output_wrap,
            bg=SURFACE,
            fg="#C9D4E2",
            insertbackground=TEXT,
            relief="flat",
            bd=0,
            padx=10,
            pady=8,
            font=("Consolas", 9),
            wrap="none",
        )
        ysb2 = ttk.Scrollbar(output_wrap, orient="vertical", command=self.output.yview)
        xsb2 = ttk.Scrollbar(output_wrap, orient="horizontal", command=self.output.xview)
        self.output.configure(yscrollcommand=ysb2.set, xscrollcommand=xsb2.set)
        self.output.grid(row=0, column=0, sticky="nsew")
        ysb2.grid(row=0, column=1, sticky="ns")
        xsb2.grid(row=1, column=0, sticky="ew")
        output_wrap.grid_rowconfigure(0, weight=1)
        output_wrap.grid_columnconfigure(0, weight=1)

        cal = tk.Frame(lower, bg=BG, width=360)
        cal.pack(side="right", fill="y", padx=(12, 0))
        cal.pack_propagate(False)
        tk.Label(cal, text="手动标定", bg=BG, fg=TEXT, font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
        tk.Label(
            cal,
            text="选中接口后填写；保存到 settings.json，按 VID:PID 匹配。",
            bg=BG,
            fg=MUTED,
            font=("Microsoft YaHei UI", 8),
            wraplength=340,
            justify="left",
        ).pack(anchor="w", pady=(2, 6))

        grid = tk.Frame(cal, bg=BG)
        grid.pack(fill="x")
        self.cal_vars = {
            "name": tk.StringVar(value="手动标定"),
            "report": tk.StringVar(value="0"),
            "index": tk.StringVar(value="0"),
            "scale": tk.StringVar(value="1"),
            "offset": tk.StringVar(value="0"),
            "charging": tk.StringVar(value=""),
        }
        fields = [
            ("名称", "name", 18),
            ("Report ID (hex)", "report", 8),
            ("电量字节下标", "index", 8),
            ("倍率", "scale", 8),
            ("偏移", "offset", 8),
            ("充电字节下标", "charging", 8),
        ]
        for row, (label, key, width) in enumerate(fields):
            tk.Label(grid, text=label, bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 8)).grid(
                row=row, column=0, sticky="w", pady=2
            )
            tk.Entry(
                grid,
                textvariable=self.cal_vars[key],
                width=width,
                bg=SURFACE_2,
                fg=TEXT,
                insertbackground=TEXT,
                relief="flat",
                font=("Consolas", 9),
            ).grid(row=row, column=1, sticky="ew", pady=2, padx=(8, 0))
        grid.columnconfigure(1, weight=1)

        self.cal_mode = tk.StringVar(value="input")
        mode_row = tk.Frame(cal, bg=BG)
        mode_row.pack(fill="x", pady=(4, 0))
        tk.Label(mode_row, text="来源", bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 8)).pack(side="left")
        for text, value in (("Input Report", "input"), ("Feature Report", "feature")):
            tk.Radiobutton(
                mode_row,
                text=text,
                variable=self.cal_mode,
                value=value,
                bg=BG,
                fg=TEXT,
                activebackground=BG,
                activeforeground=TEXT,
                selectcolor=SURFACE_2,
                font=("Microsoft YaHei UI", 8),
                bd=0,
                highlightthickness=0,
            ).pack(side="left", padx=(8, 0))

        cal_buttons = tk.Frame(cal, bg=BG)
        cal_buttons.pack(fill="x", pady=(8, 0))
        self._button(cal_buttons, "读取一次报文", self.read_one_report).pack(side="left", padx=(0, 5))
        self._button(cal_buttons, "测试标定", self.test_calibration).pack(side="left")
        self._button(cal, "保存标定", self.save_calibration, accent=True).pack(fill="x", pady=(6, 0))
        self.cal_last = tk.Label(
            cal,
            text="最后报文：—",
            bg=BG,
            fg=MUTED,
            font=("Consolas", 8),
            wraplength=340,
            justify="left",
            anchor="w",
        )
        self.cal_last.pack(fill="x", pady=(6, 0))

    # ---- helpers ---------------------------------------------------------
    def _set_status(self, text: str) -> None:
        self.status.configure(text=text)

    def _append(self, text: str) -> None:
        self.output.insert("end", text.rstrip() + "\n")
        self.output.see("end")

    def _set_detail(self, text: str) -> None:
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")

    def _selected(self) -> HidInterface | None:
        sel = self.tree.selection()
        if not sel:
            return None
        return self._items.get(sel[0])

    def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        self.on_close_cb()

    def _selection_changed(self, _event=None) -> None:
        iface = self._selected()
        self.selected = iface
        if not iface:
            self._set_detail("选择一个 HID 接口查看能力表和路径。")
            return
        lines = [
            f"path: {iface.path}",
            f"instance: {iface.instance_id}",
            f"VID:PID: {iface.vid:04X}:{iface.pid:04X}  version={iface.version:04X}",
            f"usage: {iface.usage_label}  input={iface.input_len} output={iface.output_len} feature={iface.feature_len}",
            f"product: {iface.product}",
            f"manufacturer: {iface.manufacturer}",
            f"serial: {iface.serial}",
            f"system-reserved-top-level: {iface.is_system_reserved}",
            f"error: {iface.error or '-'}",
            "feature values:",
        ]
        for cap in iface.feature_value_caps:
            lines.append(
                f"  {cap.usage_label} rid=0x{cap.report_id:02X} bits={cap.bit_size} "
                f"logical={cap.logical_min}..{cap.logical_max}"
            )
        if not iface.feature_value_caps:
            lines.append("  (none)")
        lines.append("input values:")
        for cap in iface.input_value_caps:
            lines.append(f"  {cap.usage_label} rid=0x{cap.report_id:02X} logical={cap.logical_min}..{cap.logical_max}")
        if not iface.input_value_caps:
            lines.append("  (none)")
        self._set_detail("\n".join(lines))

    def _run(self, label: str, func: Callable[[], object]) -> None:
        if self._busy:
            return
        self._busy = True
        self._set_status(f"{label} 运行中…")

        def worker():
            try:
                result = func()
                self._queue.put(("ok", label, result))
            except Exception as exc:
                self._queue.put(("error", label, f"{type(exc).__name__}: {exc}"))

        threading.Thread(target=worker, name="debug-worker", daemon=True).start()

    # ---- actions ---------------------------------------------------------
    def refresh_devices(self) -> None:
        self._run("刷新设备", enumerate_hid)

    def _install_interfaces(self, items: list[HidInterface]) -> None:
        selected_path = self.selected.path if self.selected else ""
        self.interfaces = items
        self.tree.delete(*self.tree.get_children())
        self._items.clear()
        for idx, item in enumerate(items):
            iid = f"i{idx}"
            self._items[iid] = item
            self.tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    item.vid_pid,
                    item.usage_label,
                    f"{item.input_len}/{item.output_len}/{item.feature_len}",
                    f"{item.product or item.display_name}  MI={item.mi or '-'}",
                ),
            )
            if selected_path and item.path == selected_path:
                self.tree.selection_set(iid)
                self.tree.focus(iid)
        self._append(f"已枚举 {len(items)} 个 HID 接口。")
        if selected_path:
            self._selection_changed()
        elif items:
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)
            self.tree.focus(first)
            self._selection_changed()

    def dump_features(self) -> None:
        iface = self._selected()
        if not iface:
            messagebox.showinfo("提示", "请先选择一个接口", parent=self.win)
            return
        if not iface.feature_len:
            messagebox.showinfo("提示", "该接口没有 Feature Report", parent=self.win)
            return

        def work():
            ids = {cap.report_id for cap in iface.feature_value_caps}
            ids.update(range(0, 0x21))
            lines = [f"Feature Report 转储：{iface.path}", f"length={iface.feature_len}"]
            with HidDevice(iface) as dev:
                for rid in sorted(ids):
                    try:
                        raw = dev.get_feature(rid)
                    except Exception:
                        continue
                    if any(raw[1:]):
                        lines.append(f"rid=0x{rid:02X}: {_hex(raw, 256)}")
                    else:
                        lines.append(f"rid=0x{rid:02X}: (all zero) {len(raw)} bytes")
            return "\n".join(lines)

        self._run("转储 Feature", work)

    def probe_ids(self) -> None:
        iface = self._selected()
        if not iface:
            messagebox.showinfo("提示", "请先选择一个接口", parent=self.win)
            return

        def work():
            lines = [f"Report ID 探测：{iface.path}", "说明：只读探测，不向设备写入。"]
            with HidDevice(iface) as dev:
                if iface.feature_len:
                    candidates = sorted({cap.report_id for cap in iface.feature_value_caps} | set(range(0, 0x21)))
                    found_ids = []
                    for rid in candidates:
                        try:
                            raw = dev.get_feature(rid)
                        except Exception:
                            continue
                        if any(raw):
                            found_ids.append(rid)
                    lines.append("Feature IDs: " + (", ".join(f"0x{x:02X}" for x in found_ids) or "none"))
                if iface.input_len:
                    found = {}
                    deadline = time.monotonic() + 2.5
                    while time.monotonic() < deadline:
                        try:
                            raw = dev.read_input(220)
                        except Exception as exc:
                            lines.append(f"Input 读取失败：{exc}")
                            break
                        if not raw:
                            continue
                        found[raw[0]] = len(raw)
                    lines.append(
                        "Input report IDs: "
                        + (", ".join(f"0x{x:02X}(len {n})" for x, n in sorted(found.items())) or "none (动一下设备再试)")
                    )
            return "\n".join(lines)

        self._run("探测 Report ID", work)

    def listen_input(self) -> None:
        iface = self._selected()
        if not iface:
            messagebox.showinfo("提示", "请先选择一个接口", parent=self.win)
            return
        if not iface.input_len:
            messagebox.showinfo("提示", "该接口没有 Input Report", parent=self.win)
            return

        def work():
            lines = [f"监听 Input Report 3 秒：{iface.path}"]
            reports = 0
            with HidDevice(iface) as dev:
                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline:
                    try:
                        raw = dev.read_input(250)
                    except Exception as exc:
                        lines.append(f"读取失败：{exc}")
                        break
                    if not raw:
                        continue
                    reports += 1
                    if reports <= 16:
                        lines.append(f"[{reports:02d}] rid=0x{raw[0]:02X} len={len(raw)}  {_hex(raw, 96)}")
                    if reports == 1:
                        self.last_report = raw
            if reports == 0:
                lines.append("没有收到报文（设备可能休眠、系统独占或没有主动上报）。")
            else:
                lines.append(f"共收到 {reports} 条。")
            return "\n".join(lines)

        self._run("监听输入", work)

    def send_command(self) -> None:
        iface = self._selected()
        if not iface:
            messagebox.showinfo("提示", "请先选择一个接口", parent=self.win)
            return
        _SendDialog(self.win, iface, self._send_worker)

    def _send_worker(self, iface: HidInterface, data: bytes, mode: str, wait_ms: int) -> None:
        def work():
            lines = [f"发送 {mode}: {_hex(data, 512)}", f"length={len(data)}"]
            with HidDevice(iface) as dev:
                if mode == "feature":
                    if iface.feature_len and len(data) < iface.feature_len:
                        data = data + b"\x00" * (iface.feature_len - len(data))
                    dev.set_feature(data)
                    time.sleep(wait_ms / 1000.0)
                    try:
                        raw = dev.get_feature(data[0], len(data))
                        lines.append(f"Feature reply: {_hex(raw, 512)}")
                    except Exception as exc:
                        lines.append(f"Feature reply 读取失败：{exc}")
                else:
                    written = dev.write_output(data)
                    lines.append(f"WriteFile: {written} bytes")
                    deadline = time.monotonic() + wait_ms / 1000.0
                    count = 0
                    while time.monotonic() < deadline:
                        try:
                            raw = dev.read_input(150)
                        except Exception as exc:
                            lines.append(f"读取失败：{exc}")
                            break
                        if raw:
                            count += 1
                            if count <= 16:
                                lines.append(f"[{count:02d}] {_hex(raw, 256)}")
                    if not count:
                        lines.append("没有收到 Input Report。")
            return "\n".join(lines)

        self._run("试发命令", work)

    def read_one_report(self) -> None:
        iface = self._selected()
        if not iface:
            messagebox.showinfo("提示", "请先选择一个接口", parent=self.win)
            return
        mode = self.cal_mode.get()
        report_id = 0
        try:
            report_id = int(self.cal_vars["report"].get().strip() or "0", 16)
        except ValueError:
            messagebox.showerror("参数错误", "Report ID 不是有效的十六进制数", parent=self.win)
            return

        def work():
            with HidDevice(iface) as dev:
                if mode == "feature":
                    raw = dev.get_feature(report_id)
                else:
                    deadline = time.monotonic() + 1.0
                    raw = b""
                    while time.monotonic() < deadline and not raw:
                        raw = dev.read_input(250) or b""
            return raw

        self._run("读取一次报文", work)

    def _calibration_dict(self) -> dict | None:
        iface = self._selected()
        if not iface:
            messagebox.showinfo("提示", "请先选择一个接口", parent=self.win)
            return None
        try:
            report_id = int(self.cal_vars["report"].get().strip() or "0", 16)
            index = int(self.cal_vars["index"].get().strip() or "0", 10)
            scale = float(self.cal_vars["scale"].get().strip() or "1")
            offset = float(self.cal_vars["offset"].get().strip() or "0")
            charging_text = self.cal_vars["charging"].get().strip()
            charging = int(charging_text, 10) if charging_text else None
        except ValueError as exc:
            messagebox.showerror("参数错误", f"标定参数无效：{exc}", parent=self.win)
            return None
        return {
            "name": self.cal_vars["name"].get().strip() or "手动标定",
            "report_id": f"{report_id:02X}",
            "index": index,
            "scale": scale,
            "offset": offset,
            "charging_index": charging,
            "mode": self.cal_mode.get(),
        }

    def test_calibration(self) -> None:
        iface = self._selected()
        cal = self._calibration_dict()
        if not iface or not cal:
            return

        def work():
            reading = read_calibration(iface, cal)
            if not reading:
                return "测试失败：没有匹配到报文或数值超出 0..100。"
            return (
                f"测试结果：{reading.level}%  来源={reading.source}\n"
                f"详情={reading.detail}\n原始报文={_hex(reading.raw, 256)}"
            )

        self._run("测试标定", work)

    def save_calibration(self) -> None:
        iface = self._selected()
        cal = self._calibration_dict()
        if not iface or not cal:
            return
        settings = load_settings()
        calibrations = dict(settings.get("calibrations", {}))
        key = f"{iface.vid:04X}:{iface.pid:04X}"
        calibrations[key] = cal
        settings["calibrations"] = calibrations
        try:
            save_settings(settings)
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=self.win)
            return
        self._append(f"标定已保存：{key} -> {cal}")
        self.on_settings_changed()

    # ---- worker polling --------------------------------------------------
    def _poll(self) -> None:
        if self._closed:
            return
        try:
            while True:
                kind, label, payload = self._queue.get_nowait()
                self._busy = False
                self._set_status(f"{label} 完成" if kind == "ok" else f"{label} 失败")
                if kind == "ok":
                    if label == "刷新设备":
                        self._install_interfaces(payload)
                    elif label == "读取一次报文":
                        self.last_report = payload
                        self.cal_last.configure(text="最后报文：" + (_hex(payload, 96) or "—"))
                        self._append(f"报文（{len(payload)} 字节）：{_hex(payload, 256)}")
                        if payload:
                            index = self.cal_vars["index"].get()
                            if index in ("", "0"):
                                # No guessing: the user still chooses the index.
                                pass
                    else:
                        self._append(str(payload))
                else:
                    self._append(f"{label} 错误：{payload}")
        except queue.Empty:
            pass
        try:
            self.win.after(120, self._poll)
        except tk.TclError:
            pass


class _SendDialog:
    def __init__(self, parent, iface: HidInterface, callback):
        self.iface = iface
        self.callback = callback
        self.win = tk.Toplevel(parent)
        self.win.title("试发命令")
        self.win.configure(bg=BG)
        self.win.geometry("620x330")
        self.win.transient(parent)
        self.win.grab_set()
        self.mode = tk.StringVar(value="output")
        tk.Label(self.win, text=f"{iface.vid_pid}  {iface.usage_label}", bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=14, pady=(12, 4))
        tk.Label(
            self.win,
            text="完整报文（含 Report ID；可空格分隔）。默认按 Output Report 发送。",
            bg=BG,
            fg=TEXT,
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", padx=14)
        self.text = tk.Text(self.win, height=7, bg=SURFACE, fg=TEXT, insertbackground=TEXT, relief="flat", font=("Consolas", 10), padx=8, pady=6)
        self.text.pack(fill="x", padx=14, pady=6)
        self.text.insert("1.0", "00 20 01 00 00 00 00 00")
        row = tk.Frame(self.win, bg=BG)
        row.pack(fill="x", padx=14, pady=(2, 6))
        for label, value in (("Output Report", "output"), ("Feature Report", "feature")):
            tk.Radiobutton(
                row,
                text=label,
                variable=self.mode,
                value=value,
                bg=BG,
                fg=TEXT,
                activebackground=BG,
                activeforeground=TEXT,
                selectcolor=SURFACE_2,
                font=("Microsoft YaHei UI", 9),
                bd=0,
            ).pack(side="left", padx=(0, 12))
        tk.Label(row, text="等待回复(ms)", bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 8)).pack(side="left", padx=(12, 4))
        self.wait = tk.StringVar(value="400")
        tk.Entry(row, textvariable=self.wait, width=6, bg=SURFACE_2, fg=TEXT, insertbackground=TEXT, relief="flat", font=("Consolas", 9)).pack(side="left")
        actions = tk.Frame(self.win, bg=BG)
        actions.pack(fill="x", padx=14, pady=(4, 12))
        tk.Button(actions, text="取消", command=self.win.destroy, bg=SURFACE_2, fg=TEXT, relief="flat", bd=0, padx=14, pady=5).pack(side="right")
        tk.Button(actions, text="发送", command=self._send, bg=ACCENT, fg="#07111C", relief="flat", bd=0, padx=14, pady=5).pack(side="right", padx=(0, 8))
        tk.Label(
            self.win,
            text="只对选中的厂商接口发送。系统独占的键盘/鼠标顶层集合不能写。",
            bg=BG,
            fg=MUTED,
            font=("Microsoft YaHei UI", 8),
        ).pack(anchor="w", padx=14)

    def _send(self) -> None:
        try:
            data = _parse_hex(self.text.get("1.0", "end"))
            if not data:
                raise ValueError("报文为空")
            wait = int(self.wait.get().strip() or "400")
        except ValueError as exc:
            messagebox.showerror("参数错误", str(exc), parent=self.win)
            return
        self.win.destroy()
        self.callback(self.iface, data, self.mode.get(), max(50, min(5000, wait)))
