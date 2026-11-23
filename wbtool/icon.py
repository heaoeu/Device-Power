"""The application icon shipped as ``wbtool/app.ico``.

Tk loads the file, and Windows picks the matching size (16/32/48/256) from the
multi-size .ico.  A missing file is not fatal: the window simply keeps Tk's
icon instead of the tool crashing.
"""
from __future__ import annotations

from .config import APP_ICON_PATH


def apply_icon(window, make_default: bool = False) -> bool:
    """Set the .ico as a Tk window's icon; quiet when the file is missing."""
    try:
        if make_default:
            window.iconbitmap(default=str(APP_ICON_PATH))
        else:
            window.iconbitmap(str(APP_ICON_PATH))
    except Exception:
        return False
    return True
