"""Filesystem paths for Kosha.

All persistent data lives under ``%APPDATA%\\Kosha`` so it survives app updates
and reinstalls. Nothing here reaches the network.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "Kosha"

DB_FILENAME = "kosha.db"
SALT_FILENAME = "kosha.salt"


def data_dir() -> Path:
    """Return the per-user data directory, creating it if needed.

    Uses ``%APPDATA%`` on Windows; falls back to ``~/.kosha`` elsewhere so the
    module is importable (and testable) on non-Windows CI.
    """
    override = os.environ.get("KOSHA_DATA_DIR")
    if override:
        base = Path(override)
    else:
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) / APP_NAME if appdata else Path.home() / f".{APP_NAME.lower()}"
    base.mkdir(parents=True, exist_ok=True)
    return base


def db_path() -> Path:
    return data_dir() / DB_FILENAME


def salt_path() -> Path:
    return data_dir() / SALT_FILENAME
