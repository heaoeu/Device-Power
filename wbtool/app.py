"""Application entry: CLI, Tk main loop, tray lifecycle and refresh scheduling."""
from __future__ import annotations

import argparse
import queue
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from tkinter import messagebox, ttk

from .config import (
    MAX_REFRESH_SECONDS,
    MIN_REFRESH_SECONDS,
    PROJECT_DIR,
    load_settings,
    save_settings,
    set_refresh_seconds,
)
from .debugui import DebugWindow
from .hid import enumerate_hid
from .installer import install, uninstall
from .keyboard_watch import KeyboardWatcher
from .panel import SURFACE_2, TEXT, PanelWindow
from .service import BatteryService, DeviceView, RefreshResult
from .shortcuts import startup_state
from .taskbar_strip import TaskbarStrip
from .tray import SingleInstance, TrayThread, TrayView, find_tray_window
from .win32 import set_dpi_aware, user32


def _print_views(result: RefreshResult) -> None:
    """The acceptance-facing --once output: exactly two device lines."""
    for view in result.views:
        name = view.target.name
        if view.level is None:
            reason = (view.error or view.detail or "未读取").split("；")[0]
            print(f"{name}：未读取（{reason}）")
        else:
            charge = f"，{view.status_text}" if view.status_text else ""
            stale = "（上次）" if view.stale else ""
            print(f"{name}：{view.level}%{stale}{charge}  [{view.source}]")
    # Guarantee two lines even if settings are malformed and contain fewer devices.
    if len(result.views) < 2:
        print("第二台设备：未配置")


def _print_list() -> None:
    items = enumerate_hid()
    print(f"HID interfaces: {len(items)}")
    for item in items:
        print(
            f"{item.vid_pid}  {item.usage_label}  "
            f"in={item.input_len:<3} out={item.output_len:<3} feat={item.feature_len:<4} "
            f"{item.product or item.display_name}  {item.path}"
        )


PANEL_MUTEX_NAME = "Local\\WirelessBatteryPanel_v1"


def _focus_existing_panel(timeout: float = 2.5) -> bool:
    """Bring an already-running panel-only instance to the foreground."""
    user32.FindWindowW.restype = wintypes.HWND
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hwnd = int(user32.FindWindowW(None, "电量托盘") or 0)
        if hwnd:
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            return True
        time.sleep(0.1)
    return False


class Application:
    def __init__(self, mode: str = "panel", debug: bool = False, open_debug: bool = False,
                 single_instance: SingleInstance | None = None):
        self.mode = mode
        self.debug = debug
        self.single_instance = single_instance
        self.settings = load_settings()
        self._show_tray_icon = bool(self.settings.get("show_tray_icon", True))
        self._show_taskbar_strip = bool(self.settings.get("show_taskbar_strip", True))
        raw_devices = self.settings.get("tray_devices", ["mouse", "keyboard"])
        wanted_devices = {str(item) for item in raw_devices} if isinstance(raw_devices, list) else set()
        self._tray_devices = [kind for kind in ("mouse", "keyboard") if kind in wanted_devices]
        self.service = BatteryService(self.settings)
        self.keyboard_watcher: KeyboardWatcher | None = KeyboardWatcher()
        self._last_watch_ts = 0.0
        self.queue: queue.Queue = queue.Queue()
        self.tray_events: queue.Queue = queue.Queue()
        self.tray: TrayThread | None = None
        self.panel: PanelWindow | None = None
        self.taskbar_strip: TaskbarStrip | None = None
        self.debug_window: DebugWindow | None = None
        self.views: list[DeviceView] = self.service.cached_views()
        self._refreshing = False
        self._closing = False
        self._startup_poll_id: str | None = None
        self._refresh_timer: str | None = None
        self._pump_id: str | None = None
        self._after_ids: list[str] = []
        self._startup_hint = ""
        self._last_result: RefreshResult | None = None

        set_dpi_aware()
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("电量托盘 Host")
        self._configure_tk()
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self._apply_ttk_style()

        if self.mode == "tray":
            self._start_tray()
        else:
            self.open_panel()
        if open_debug:
            self.open_debug()
        if self._show_taskbar_strip:
            self._create_taskbar_strip()
        self._pump_id = self._schedule(100, self._pump)
        self._schedule(150, self.request_refresh)
        self._schedule_refresh_timer()
        self._schedule(300, self._read_startup_state)
        self._schedule(3000, self._start_keyboard_watcher)

    def _start_keyboard_watcher(self) -> None:
        if self._closing or self.keyboard_watcher is None:
            return
        if not self.keyboard_watcher.is_alive():
            self.keyboard_watcher.start()
        self.keyboard_watcher.nudge()

    def _schedule(self, delay_ms: int, callback) -> str:
        after_id = self.root.after(delay_ms, callback)
        self._after_ids.append(after_id)
        return after_id

    def _cancel_scheduled(self) -> None:
        for after_id in self._after_ids:
            try:
                self.root.after_cancel(after_id)
            except tk.TclError:
                pass
        self._after_ids.clear()

    def _configure_tk(self) -> None:
        try:
            # Tk's default scaling is based on 72 dpi points; with per-monitor
            # DPI awareness this keeps text readable on scaled displays.
            from .win32 import get_dpi_for_system

            dpi = get_dpi_for_system()
            self.root.tk.call("tk", "scaling", max(1.0, dpi / 72.0))
        except Exception:
            pass

    def _apply_ttk_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "TCombobox",
            fieldbackground=SURFACE_2,
            background=SURFACE_2,
            foreground=TEXT,
            arrowcolor=TEXT,
            bordercolor=SURFACE_2,
            lightcolor=SURFACE_2,
            darkcolor=SURFACE_2,
            selectbackground=SURFACE_2,
            selectforeground=TEXT,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", SURFACE_2)],
            foreground=[("readonly", TEXT)],
            selectbackground=[("readonly", SURFACE_2)],
            selectforeground=[("readonly", TEXT)],
        )

    # ---- tray -----------------------------------------------------------
    def _start_tray(self) -> None:
        self.tray = TrayThread(
            self.tray_events,
            show_icons=self._show_tray_icon,
            strip_visible=self._show_taskbar_strip,
            shown_kinds=self._tray_devices,
        )
        if not self.tray.start_and_wait(5.0):
            error = self.tray.error or "未知错误"
            self.tray.stop(1.0)
            self.tray = None
            self.mode = "panel"
            self.open_panel()
            self._log(f"tray failed: {error}")
            messagebox.showerror("电量托盘", f"托盘启动失败：{error}")
            return
        self.tray.set_menu_state(self._show_tray_icon, self._show_taskbar_strip)
        self.tray.update([self._tray_view(v) for v in self.views])

    # ---- visibility toggles ---------------------------------------------
    def _create_taskbar_strip(self) -> None:
        if self._closing or self.taskbar_strip is not None:
            return
        try:
            self.taskbar_strip = TaskbarStrip(self.root, self.open_panel, self._show_tray_menu)
            self.taskbar_strip.update(self._taskbar_views())
        except Exception as exc:
            self.taskbar_strip = None
            self._log(f"taskbar strip unavailable: {exc}")

    def _taskbar_views(self, views=None) -> list[DeviceView]:
        """Only selected device cards appear in the taskbar strip."""
        selected = set(self._tray_devices)
        source = self.views if views is None else views
        return [v for v in source if getattr(v.target, "kind", "") in selected]

    def _show_tray_menu(self) -> None:
        if self.tray:
            self.tray.show_menu()

    def _destroy_taskbar_strip(self) -> None:
        if self.taskbar_strip is not None:
            self.taskbar_strip.destroy()
            self.taskbar_strip = None

    def _set_show_tray_icon(self, visible: bool) -> None:
        visible = bool(visible)
        self._show_tray_icon = visible
        self.settings["show_tray_icon"] = visible
        try:
            save_settings(self.settings)
        except OSError as exc:
            self._log(f"save settings failed: {exc}")
        if self.tray:
            self.tray.set_icon_visible(visible)
            self.tray.set_menu_state(visible, self._show_taskbar_strip)
        self._sync_panel_visibility()

    def _set_show_taskbar_strip(self, visible: bool) -> None:
        visible = bool(visible)
        self._show_taskbar_strip = visible
        self.settings["show_taskbar_strip"] = visible
        try:
            save_settings(self.settings)
        except OSError as exc:
            self._log(f"save settings failed: {exc}")
        if visible:
            self._create_taskbar_strip()
        else:
            self._destroy_taskbar_strip()
        if self.tray:
            self.tray.set_menu_state(self._show_tray_icon, visible)
        self._sync_panel_visibility()

    def _set_tray_devices(self, devices: list[str]) -> None:
        wanted = {str(item) for item in devices}
        self._tray_devices = [kind for kind in ("mouse", "keyboard") if kind in wanted]
        self.settings["tray_devices"] = list(self._tray_devices)
        try:
            save_settings(self.settings)
        except OSError as exc:
            self._log(f"save settings failed: {exc}")
        if self.tray:
            self.tray.set_shown_kinds(self._tray_devices)
        if self.taskbar_strip:
            self.taskbar_strip.update(self._taskbar_views())
        self._sync_panel_visibility()

    def _sync_panel_visibility(self) -> None:
        if self.panel is None:
            return
        try:
            self.panel.set_visibility_state(self._show_tray_icon, self._show_taskbar_strip)
            self.panel.set_tray_devices(self._tray_devices)
        except tk.TclError:
            pass

    def _tray_view(self, view: DeviceView) -> TrayView:
        return TrayView(
            id=view.target.id,
            name=view.target.name,
            level=view.level,
            charging=view.charging,
            updated_text=view.updated_text if view.updated else "尚未读取",
            status_text=view.status_text,
            kind=view.target.kind,
        )

    def _handle_tray_event(self, event: str) -> None:
        if event == "refresh":
            self.request_refresh(manual=True)
        elif event == "open_panel":
            self.open_panel()
        elif event == "tray_icon_on":
            self._set_show_tray_icon(True)
        elif event == "tray_icon_off":
            self._set_show_tray_icon(False)
        elif event == "strip_on":
            self._set_show_taskbar_strip(True)
        elif event == "strip_off":
            self._set_show_taskbar_strip(False)
        elif event == "restore_tray_icons":
            self._set_show_tray_icon(True)
        elif event == "quit":
            self.quit()
        elif event == "tray_closed":
            if not self._closing:
                self.quit()

    # ---- panel ----------------------------------------------------------
    def open_panel(self) -> None:
        if self._closing:
            return
        if self.panel and self.panel.exists():
            self.panel.set_visibility_state(self._show_tray_icon, self._show_taskbar_strip)
            self.panel.set_tray_devices(self._tray_devices)
            self.panel.show()
            self._read_startup_state()
            self.request_refresh(manual=False)
            return
        self._read_startup_state()
        self.panel = PanelWindow(
            self.root,
            tray_enabled=self.tray is not None and (self._show_tray_icon or self._show_taskbar_strip),
            refresh_seconds=int(self.settings.get("refresh_seconds", 60)),
            startup_enabled=bool(self._startup_state().get("enabled")),
            startup_hint=self._startup_hint,
            tray_icon_visible=self._show_tray_icon,
            taskbar_visible=self._show_taskbar_strip,
            tray_devices=self._tray_devices,
            on_refresh=lambda: self.request_refresh(manual=True),
            on_interval=self.on_interval,
            on_startup=self.on_startup_toggle,
            on_tray_icon=self._set_show_tray_icon,
            on_taskbar=self._set_show_taskbar_strip,
            on_tray_devices=self._set_tray_devices,
            on_close=self.on_panel_close,
            on_quit=self.quit,
        )
        self.panel.update_views(self.views, None)
        self.panel.show()
        self._start_startup_poll()
        self.request_refresh(manual=False)

    def _start_startup_poll(self) -> None:
        if self._closing or self._startup_poll_id is not None:
            return
        if self.panel is None:
            return
        self._startup_poll_id = self.root.after(3000, self._poll_startup_state)

    def _poll_startup_state(self) -> None:
        self._startup_poll_id = None
        if self._closing:
            return
        if self.panel and self.panel.exists():
            try:
                if self.panel.is_viewable():
                    self._read_startup_state()
            except tk.TclError:
                pass
            self._startup_poll_id = self.root.after(3000, self._poll_startup_state)

    def on_panel_close(self) -> None:
        if self._closing:
            return
        if self.tray is not None and (self._show_tray_icon or self._show_taskbar_strip):
            self.panel.hide()
        else:
            self.quit()

    def _startup_state(self) -> dict:
        try:
            return startup_state()
        except Exception:
            return {"enabled": False, "exists": False, "approved": "unknown", "target_ok": False, "args_ok": False}

    def _read_startup_state(self) -> None:
        state = self._startup_state()
        if not state.get("exists"):
            hint = "启动文件夹未创建"
        elif not state.get("enabled"):
            hint = "任务管理器可见 · 已禁用"
        elif not state.get("target_ok") or not state.get("args_ok"):
            hint = "任务管理器可见 · 目标异常"
        else:
            hint = "任务管理器可见 · 已启用"
        self._startup_hint = hint
        if self.panel:
            self.panel.set_startup_state(bool(state.get("enabled")), hint)

    def on_startup_toggle(self, enabled: bool) -> None:
        # This is only reached from a real user click.  The startup path never
        # calls it, so a stale/disabled Task Manager state is never "corrected".
        try:
            from .shortcuts import set_startup_enabled

            set_startup_enabled(bool(enabled), str(PROJECT_DIR), str(PROJECT_DIR / "my_devices.py"))
        except Exception as exc:
            messagebox.showerror("开机自启", f"更新启动文件夹失败：{exc}")
        self._read_startup_state()

    def on_interval(self, seconds: int) -> None:
        seconds = max(MIN_REFRESH_SECONDS, min(MAX_REFRESH_SECONDS, int(seconds)))
        self.settings = set_refresh_seconds(self.settings, seconds)
        try:
            save_settings(self.settings)
        except Exception as exc:
            messagebox.showerror("设置", f"保存刷新间隔失败：{exc}")
        self.service.update_settings(self.settings)
        if self.panel:
            self.panel.set_interval(seconds)
            self.panel.set_status(f"刷新间隔已设为 {seconds} 秒")
        self._schedule_refresh_timer()

    # ---- debug ----------------------------------------------------------
    def open_debug(self) -> None:
        if self._closing:
            return
        if self.debug_window:
            try:
                if self.debug_window.win.winfo_exists():
                    self.debug_window.win.deiconify()
                    self.debug_window.win.lift()
                    self.debug_window.win.focus_force()
                    return
            except tk.TclError:
                self.debug_window = None
        self.debug_window = DebugWindow(
            self.root,
            on_close=self._debug_closed,
            on_settings_changed=self.reload_settings,
        )

    def _debug_closed(self) -> None:
        self.debug_window = None

    def reload_settings(self) -> None:
        self.settings = load_settings()
        self.service.update_settings(self.settings)
        if self.panel:
            self.panel.set_interval(int(self.settings.get("refresh_seconds", 60)))
            self._read_startup_state()
        self._schedule_refresh_timer()
        self.request_refresh(manual=True)

    # ---- refresh --------------------------------------------------------
    def request_refresh(self, manual: bool = False) -> None:
        if self._closing or self._refreshing:
            return
        if self.keyboard_watcher is not None and self.keyboard_watcher.is_alive():
            self.keyboard_watcher.nudge()
        self._refreshing = True
        if self.panel:
            self.panel.set_status("正在读取设备…" if manual else "后台刷新中…")
        threading.Thread(target=self._refresh_worker, name="battery-refresh", daemon=True).start()

    def _refresh_worker(self) -> None:
        try:
            skip = (
                {"keyboard"}
                if self.keyboard_watcher is not None and self.keyboard_watcher.is_alive()
                else set()
            )
            result = self.service.refresh(debug=self.debug, log=self._log, skip_kinds=skip)
        except Exception as exc:
            result = RefreshResult([], 0.0, f"刷新异常: {exc}")
        self.queue.put(("refresh", result))

    def _handle_refresh(self, result: RefreshResult) -> None:
        self._refreshing = False
        self._last_result = result
        self.views = result.views
        if result.error:
            self._log(result.error)
        if self.panel and self.panel.exists():
            self.panel.update_views(result.views, result.elapsed_s)
            if result.error:
                self.panel.set_status(result.error)
            elif any(v.level is None for v in result.views):
                missing = next((v for v in result.views if v.level is None), None)
                self.panel.set_status(missing.detail if missing and missing.detail else f"更新完成 · 用时 {result.elapsed_s:.1f} 秒")
            else:
                self.panel.set_status(f"更新完成 · 用时 {result.elapsed_s:.1f} 秒")
        if self.tray:
            self.tray.update([self._tray_view(v) for v in result.views])
        if self.taskbar_strip:
            self.taskbar_strip.update(self._taskbar_views(result.views))
        self._schedule_refresh_timer()

    # ---- automatic refresh ----------------------------------------------
    def _schedule_refresh_timer(self, delay_ms: int | None = None) -> None:
        if self._closing:
            return
        if self._refresh_timer is not None:
            try:
                self.root.after_cancel(self._refresh_timer)
            except tk.TclError:
                pass
            self._refresh_timer = None
        seconds = int(self.settings.get("refresh_seconds", 60))
        delay = delay_ms if delay_ms is not None else max(MIN_REFRESH_SECONDS, seconds) * 1000
        self._refresh_timer = self.root.after(delay, self._auto_refresh)

    def _auto_refresh(self) -> None:
        self._refresh_timer = None
        if self._closing:
            return
        if self._refreshing:
            self._schedule_refresh_timer(5000)
            return
        self.request_refresh(manual=False)

    # ---- pump -----------------------------------------------------------
    def _pump(self) -> None:
        if self._closing:
            return
        while True:
            try:
                kind, payload = self.queue.get_nowait()
            except queue.Empty:
                break
            if kind == "refresh":
                self._handle_refresh(payload)
        while True:
            try:
                event = self.tray_events.get_nowait()
            except queue.Empty:
                break
            self._handle_tray_event(str(event))
        self._poll_keyboard_watcher()
        try:
            self._pump_id = self.root.after(100, self._pump)
        except tk.TclError:
            pass

    def _poll_keyboard_watcher(self) -> None:
        if self.keyboard_watcher is None:
            return
        reading, seen_at = self.keyboard_watcher.snapshot()
        if reading is None or seen_at <= self._last_watch_ts:
            return
        self._last_watch_ts = seen_at
        view = self.service.apply_keyboard_reading(reading)
        if view is None:
            return
        if self.debug:
            self._log(f"keyboard push: {reading.level}% source={reading.source}")
        for index, current in enumerate(self.views):
            if current.target.kind == "keyboard":
                self.views[index] = view
                break
        else:
            self.views.append(view)
        if self.panel:
            try:
                if self.panel.exists():
                    self.panel.update_views(self.views, 0.0)
            except tk.TclError:
                pass
        if self.tray:
            self.tray.update([self._tray_view(v) for v in self.views])
        if self.taskbar_strip:
            self.taskbar_strip.update(self._taskbar_views())

    def _log(self, text: str) -> None:
        if self.debug:
            print(f"[debug] {text}", flush=True)

    # ---- shutdown -------------------------------------------------------
    def quit(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._cancel_scheduled()
        if self._pump_id is not None:
            try:
                self.root.after_cancel(self._pump_id)
            except tk.TclError:
                pass
            self._pump_id = None
        if self._refresh_timer is not None:
            try:
                self.root.after_cancel(self._refresh_timer)
            except tk.TclError:
                pass
            self._refresh_timer = None
        if self._startup_poll_id is not None:
            try:
                self.root.after_cancel(self._startup_poll_id)
            except tk.TclError:
                pass
            self._startup_poll_id = None
        if self.tray:
            self.tray.stop(3.0)
            self.tray = None
        if self.taskbar_strip:
            self.taskbar_strip.destroy()
            self.taskbar_strip = None
        if self.keyboard_watcher:
            self.keyboard_watcher.stop()
            self.keyboard_watcher = None
        if self.debug_window:
            try:
                self.debug_window._closed = True
                self.debug_window.win.destroy()
            except tk.TclError:
                pass
            self.debug_window = None
        if self.panel:
            self.panel.destroy()
            self.panel = None
        if self.single_instance:
            self.single_instance.close()
            self.single_instance = None
        try:
            self.root.quit()
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self) -> int:
        try:
            self.root.mainloop()
        finally:
            if self.tray:
                self.tray.stop(3.0)
                self.tray = None
            if self.taskbar_strip:
                self.taskbar_strip.destroy()
                self.taskbar_strip = None
            if self.keyboard_watcher:
                self.keyboard_watcher.stop()
                self.keyboard_watcher = None
            if self.single_instance:
                self.single_instance.close()
                self.single_instance = None
        return 0


def _cmd_quit() -> int:
    from .tray import WM_CLOSE
    from .win32 import user32

    hwnd = find_tray_window()
    if not hwnd:
        print("没有正在运行的托盘实例。")
        return 1
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    for _ in range(30):
        if not find_tray_window():
            print("已发送退出请求。")
            return 0
        time.sleep(0.1)
    print("已发送退出请求，但托盘窗口仍未消失。")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Windows 10/11 无线外设电量小工具（纯 ctypes + tkinter）",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="读取一次并打印两台设备后退出")
    mode.add_argument("--panel", action="store_true", help="打开简洁面板（默认）")
    mode.add_argument("--tray", action="store_true", help="托盘常驻（两个数字图标）")
    mode.add_argument("--list", action="store_true", help="列出所有 HID 接口")
    mode.add_argument("--install", action="store_true", help="创建启动快捷方式和桌面链接")
    mode.add_argument("--uninstall", action="store_true", help="删除本工具的启动项和桌面链接")
    mode.add_argument("--quit", action="store_true", help="让正在运行的托盘实例退出")
    parser.add_argument("--debug", action="store_true", help="输出调试信息到控制台（不写日志文件）")
    parser.add_argument("--debug-ui", action="store_true", help="打开面板并同时打开完整调试版")
    parser.add_argument("--version", action="store_true", help="显示版本")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if args.version:
        from . import __version__

        print(__version__)
        return 0

    if args.once:
        settings = load_settings()
        service = BatteryService(settings)
        result = service.refresh(debug=args.debug, log=lambda s: print(f"[debug] {s}", flush=True) if args.debug else None)
        _print_views(result)
        return 0

    if args.list:
        _print_list()
        return 0

    if args.install:
        result = install(startup_enabled=True)
        from .installer import describe_install

        ok = bool(result.get("startup", {}).get("created")) and all(
            item.get("ok") for item in result.get("links", {}).values()
        )
        print("安装完成。" if ok else "安装未完全成功，请检查下面的错误。")
        print(describe_install(result))
        return 0 if ok else 1

    if args.uninstall:
        result = uninstall(remove_links=True)
        print("已删除启动项和桌面链接。" if not result["errors"] else "\n".join(result["errors"]))
        return 0 if not result["errors"] else 1

    if args.quit:
        return _cmd_quit()

    set_dpi_aware()
    # If the tray is already resident, a plain `python my_devices.py` should
    # foreground its panel instead of creating a second copy.
    if not args.tray and not args.debug_ui and find_tray_window():
        if notify_existing():
            print("已有托盘实例在运行，已请求它打开面板。")
            return 0
    single = None
    if args.tray:
        single = SingleInstance()
        if single.already_exists:
            if notify_existing():
                print("已有实例在运行，已请求它打开面板。")
                single.close()
                return 0
            print("检测到已有实例，但找不到它的托盘窗口。")
            single.close()
            return 1
    elif not args.debug_ui:
        single = SingleInstance(PANEL_MUTEX_NAME)
        if single.already_exists:
            if _focus_existing_panel():
                print("已有面板在运行，已将其前置。")
                single.close()
                return 0
            print("检测到已有面板实例，但找不到面板窗口。")
            single.close()
            return 1
    mode_name = "tray" if args.tray else "panel"
    app = Application(mode=mode_name, debug=args.debug, open_debug=args.debug_ui, single_instance=single)
    return app.run()


def notify_existing() -> bool:
    from .tray import notify_existing_instance

    return notify_existing_instance(2.5)


if __name__ == "__main__":
    raise SystemExit(main())
