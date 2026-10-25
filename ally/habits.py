"""Habit memory: remembers which accommodations a person accepts in which app/site, so next time
Ally applies them without asking ("I made Chase bigger, like last time").

Stored in state/habits.json:
    {"chase.com": {"zoom_in": {"accepted": 2, "declined": 0, "last": 1729800000,
                               "accommodation": {...}}}}
Owner: Ben H.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from .contracts import Accommodation

log = logging.getLogger("ally")


def context_key(app: str = "", site: str = "") -> str:
    """Site wins over app (a site is more specific). Lower-cased, 'www.' dropped."""
    site = (site or "").strip().lower()
    if site.startswith("www."):
        site = site[4:]
    if site:
        return site
    return (app or "").strip().lower() or "unknown"


class HabitMemory:
    def __init__(self, path: Path, auto_after: int = 2):
        self.path = Path(path)
        self.auto_after = auto_after
        self._lock = threading.Lock()
        self._data: dict = self._load()

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(self._data, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            log.exception("could not save habits")

    def record(self, context: str, acc: Accommodation, accepted: bool) -> dict:
        with self._lock:
            entry = self._data.setdefault(context, {}).setdefault(
                acc.key, {"accepted": 0, "declined": 0, "accommodation": acc.to_dict()})
            entry["accepted" if accepted else "declined"] += 1
            entry["last"] = time.time()
            entry["accommodation"] = acc.to_dict()
            self._save()
            return dict(entry)

    def lookup(self, context: str) -> list[dict]:
        """All remembered accommodations for a context, best first."""
        with self._lock:
            items = [dict(v, key=k) for k, v in self._data.get(context, {}).items()]
        return sorted(items, key=lambda e: (e["accepted"] - e["declined"], e.get("last", 0)), reverse=True)

    def auto_apply(self, context: str) -> list[Accommodation]:
        """Accommodations accepted often enough here to apply without asking."""
        out = []
        for e in self.lookup(context):
            if e["accepted"] >= self.auto_after and e["accepted"] > e["declined"]:
                a = e["accommodation"]
                out.append(Accommodation(a["key"], a["label"], a["tool"], dict(a.get("args") or {})))
        return out

    def declined_recently(self, context: str, key: str, within_s: float = 600) -> bool:
        with self._lock:
            e = self._data.get(context, {}).get(key)
        return bool(e and e["declined"] > e["accepted"] and time.time() - e.get("last", 0) < within_s)

    def forget(self, context: str | None = None) -> None:
        with self._lock:
            if context is None:
                self._data = {}
            else:
                self._data.pop(context, None)
            self._save()

    def snapshot(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._data))
