"""Small persistent JSON store (atomic writes) and the local API token."""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
from pathlib import Path

log = logging.getLogger("ally.state")


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp, path)


class State:
    """Key/value settings that Ally changes at runtime (kept across restarts)."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._data: dict = {}
        try:
            self._data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(self._data, dict):
                self._data = {}
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            log.warning("state file unreadable, starting fresh: %s", e)
            self._data = {}

    def get(self, key: str, default=None):
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        with self._lock:
            self._data[key] = value
            self._save()

    def update(self, **values) -> None:
        with self._lock:
            self._data.update(values)
            self._save()

    def snapshot(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._data))

    def _save(self) -> None:
        try:
            atomic_write(self.path, json.dumps(self._data, indent=2, sort_keys=True))
        except OSError as e:
            log.warning("could not save state: %s", e)


def load_or_create_token(path: Path) -> str:
    try:
        token = path.read_text(encoding="utf-8").strip()
        if len(token) >= 20:
            return token
    except OSError:
        pass
    token = secrets.token_urlsafe(32)
    atomic_write(path, token)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return token
