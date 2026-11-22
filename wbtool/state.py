"""Small last-reading cache.

This is deliberately not a log: it stores only the latest successful reading
per target, so a sleeping keyboard can keep showing its last known percentage
across app restarts.  Failed reads never touch this file.
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import Any

from .config import PROJECT_DIR

STATE_PATH = PROJECT_DIR / "last_state.json"


def load() -> dict[str, Any]:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def save(data: dict[str, Any]) -> None:
    if not data:
        return
    try:
        fd, tmp = tempfile.mkstemp(prefix=".last_state-", suffix=".tmp", dir=str(PROJECT_DIR))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.replace(tmp, STATE_PATH)
        finally:
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except OSError:
                pass
    except OSError:
        # A read-only project directory must not break battery refresh.
        pass
