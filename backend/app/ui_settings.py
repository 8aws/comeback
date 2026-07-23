"""Persistent UI settings stored in /backups/.settings.json.

These are user-configurable values that survive container recreations
(stored on the backups volume). Unlike env-var settings, they can be
changed from the web UI without restarting the container.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .config import settings

logger = logging.getLogger("comeback.ui_settings")

DEFAULTS: dict = {
    "max_bind_mount_backup_gb": 10,   # bind mounts larger than this are unchecked by default
}


def _store() -> Path:
    return settings.backup_dir / ".settings.json"


def load() -> dict:
    try:
        if _store().exists():
            data = json.loads(_store().read_text())
            return {**DEFAULTS, **data}
    except Exception as e:
        logger.warning("Could not load UI settings: %s", e)
    return dict(DEFAULTS)


def save(data: dict):
    try:
        _store().parent.mkdir(parents=True, exist_ok=True)
        current = load()
        current.update({k: v for k, v in data.items() if k in DEFAULTS})
        _store().write_text(json.dumps(current, indent=2))
    except Exception as e:
        logger.warning("Could not save UI settings: %s", e)
