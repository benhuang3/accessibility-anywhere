"""Opt-in asynchronous POST sink for validated struggle events."""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request

from .senses.contract import to_event

log = logging.getLogger("ally.struggle_sink")


class StruggleSink:
    def __init__(self, agent, url: str):
        self.agent, self.url = agent, url
        self._lock = threading.Lock()
        self._last_failure = -1e9

    def __call__(self, struggle: dict) -> None:
        threading.Thread(target=self._post, args=(dict(struggle),), daemon=True,
                         name="struggle-post").start()

    def _post(self, struggle: dict) -> None:
        try:
            token = self.agent.paths.token_file.read_text(encoding="utf-8").strip()
            body = json.dumps(to_event(struggle)).encode("utf-8")
            request = urllib.request.Request(self.url, data=body, method="POST",
                                             headers={"X-Ally-Token": token, "Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=2) as response:
                response.read(1)
        except Exception as exc:
            now = time.monotonic()
            with self._lock:
                if now - self._last_failure >= 60:
                    self._last_failure = now
                    log.warning("struggle event POST failed: %s", exc)
