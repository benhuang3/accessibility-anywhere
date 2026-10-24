"""Logging to a rotating file, plus a small in-memory event feed for the dashboard."""

from __future__ import annotations

import collections
import logging
import logging.handlers
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger("ally")


def setup_logging(log_dir: Path, level: str = "INFO", filename: str = "ally.log") -> None:
    root = logging.getLogger("ally")
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    for h in list(root.handlers):
        root.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_dir / filename, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        pass
    # pythonw (Windows, no console) has no stderr.
    if sys.stderr is not None:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)


def set_level(level: str) -> None:
    logging.getLogger("ally").setLevel(getattr(logging, str(level).upper(), logging.INFO))


class EventLog:
    """Thread-safe ring buffer of recent events, read by the dashboard."""

    def __init__(self, size: int = 300):
        self._items: collections.deque = collections.deque(maxlen=size)
        self._lock = threading.Lock()
        self._next = 1

    def add(self, kind: str, text: str, **data) -> dict:
        with self._lock:
            item = {"id": self._next, "ts": time.time(), "kind": kind, "text": str(text)[:2000]}
            if data:
                item["data"] = data
            self._next += 1
            self._items.append(item)
        return item

    def since(self, after_id: int = 0, limit: int = 100) -> list[dict]:
        with self._lock:
            items = [i for i in self._items if i["id"] > after_id]
        return items[-limit:]
