#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wireless battery tool for Windows 10/11.

Examples:
    python my_devices.py --once
    python my_devices.py --panel
    python my_devices.py --tray
    python my_devices.py --list
    python my_devices.py --install
    python my_devices.py --quit

Only the Python standard library and ctypes are used.  No log file is written;
``--debug`` prints diagnostics to the console.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Keep the project directory free of __pycache__ litter (the tool writes no
# logs at all; this is purely optional housekeeping).
sys.dont_write_bytecode = True

# Direct execution must work both from the project directory and from a link
# that resolves to it.  The Desktop file entry uses a tiny launcher rather than
# copying this file, so this path is normally the project itself.
BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from wbtool.app import main  # noqa: E402


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # pythonw has no console; surface a real error
        import traceback

        if sys.stderr is not None:
            traceback.print_exc()
        else:
            try:
                import ctypes

                ctypes.windll.user32.MessageBoxW(
                    0,
                    f"电量托盘启动失败：\n\n{type(exc).__name__}: {exc}",
                    "电量托盘",
                    0x00000010,
                )
            except Exception:
                pass
        raise SystemExit(1)
