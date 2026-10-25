"""Reading cards: big replacement text on top of any app, shown by ally/card_helper.py in its own process."""

from __future__ import annotations

import json
import logging
import subprocess
import threading

from ..paths import CODE_ROOT
from ..registry import ToolError
from .base import NO_WINDOW

log = logging.getLogger("ally.os.cards")


class ReadingCards:
    """Starts the card helper on first use (and again if it died), and sends it show/clear commands."""

    def __init__(self, argv: list[str] | None = None, start_timeout: float = 8.0):
        self.argv = argv
        self.start_timeout = start_timeout
        self.proc: subprocess.Popen | None = None
        self.visible = False
        self.error = ""
        self._ready = threading.Event()
        self._lock = threading.Lock()

    def _command(self) -> list[str]:
        if self.argv:
            return list(self.argv)
        from ..hotkeys import python_for_children

        return [python_for_children(), "-m", "ally.card_helper"]

    def _start(self) -> subprocess.Popen:
        if self.proc is not None and self.proc.poll() is None:
            return self.proc
        self._ready.clear()
        self.error = ""
        try:
            proc = subprocess.Popen(self._command(), cwd=str(CODE_ROOT), stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                    encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        except OSError as e:
            raise ToolError(f"Couldn't start the reading card: {e}")
        threading.Thread(target=self._read, args=(proc,), daemon=True, name="cards-out").start()
        if not self._ready.wait(self.start_timeout) or self.error:
            self._kill(proc)
            raise ToolError(f"The reading card couldn't open: {self.error or 'it took too long to start'}.")
        self.proc = proc
        return proc

    def _read(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            line = line.strip()
            if line == "READY":
                self._ready.set()
            elif line == "CLOSED":
                self.visible = False
            elif line.startswith("ERROR"):
                self.error = line[6:] or "unknown error"
                log.warning("reading card: %s", self.error)
                self._ready.set()
        proc.stdout.close()

    def _send(self, msg: dict) -> None:
        with self._lock:
            for attempt in range(2):  # a helper that died since the last card is replaced once
                proc = self._start()
                try:
                    proc.stdin.write(json.dumps(msg) + "\n")
                    proc.stdin.flush()
                    return
                except (OSError, ValueError):
                    self._kill(proc)
                    if attempt:
                        raise ToolError("The reading card stopped working.")

    def show(self, box, text: str) -> str:
        text = (text or "").strip()
        if not text:
            raise ToolError("There's no text to show on the reading card.")
        self._send({"op": "show", "text": text, "box": list(box) if box else None})
        self.visible = True
        return f'Showing "{text[:80]}" on a reading card. Say restore to close it.'

    def clear(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self._send({"op": "clear"})
        self.visible = False

    def close(self) -> None:
        with self._lock:
            if self.proc is not None:
                try:
                    self.proc.stdin.close()  # the helper exits when its stdin closes
                    self.proc.wait(timeout=3)
                except Exception:
                    self._kill(self.proc)
                self.proc = None
        self.visible = False

    def _kill(self, proc: subprocess.Popen) -> None:
        try:
            proc.kill()
            proc.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:
            proc.stdin.close()
        except (OSError, ValueError):
            pass
        if proc is self.proc:
            self.proc = None
