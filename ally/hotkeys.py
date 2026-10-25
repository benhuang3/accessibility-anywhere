"""Global hotkeys. Runs ally.hotkey_helper as a child process and restarts it if it dies."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import keys
from .paths import CODE_ROOT
from .platforms.base import NO_WINDOW

log = logging.getLogger("ally.hotkeys")


def python_for_children() -> str:
    """On Windows, pythonw.exe has no console; use python.exe (with no window) so pipes work."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").exists():
        return str(exe.parent / "python.exe")
    return sys.executable


class HotkeyManager:
    def __init__(self, agent):
        self.agent = agent
        self.proc: subprocess.Popen | None = None
        self.bindings: list[tuple[str, str, object]] = []  # (label, pynput combo, action)
        self.status = "starting"
        self.error = ""
        self._crashes: list[float] = []
        self._lock = threading.Lock()
        self._wanted = True
        self._signature = None

    def compute_bindings(self) -> list[tuple[str, str, object]]:
        cfg = self.agent.config.data
        out = []
        for combo, action in (cfg.get("hotkeys") or {}).items():
            if action == "" or action is None:
                continue
            out.append((combo, keys.expand_mod(combo), action))
        for name, c in (cfg.get("commands") or {}).items():
            if isinstance(c, dict) and c.get("hotkey"):
                out.append((c["hotkey"], keys.expand_mod(c["hotkey"]), name))
        return out

    def apply(self, force: bool = False) -> None:
        """(Re)start the listener if the bindings changed."""
        if not self.agent.config.data["agent"].get("hotkeys_enabled", True):
            with self._lock:
                self._kill()
            self._signature = None
            self.bindings = self.compute_bindings()
            self.status, self.error = "off", "turned off in config (agent.hotkeys_enabled = false)"
            return
        bindings = self.compute_bindings()
        signature = json.dumps([(b[1], str(b[2])) for b in bindings])
        if not force and signature == self._signature and self.proc and self.proc.poll() is None:
            self.bindings = bindings
            return
        self._signature = signature
        self.bindings = bindings
        self._restart()

    def _restart(self) -> None:
        with self._lock:
            self._kill()
            if not self.bindings:
                self.status, self.error = "off", "no hotkeys configured"
                return
            argv = [python_for_children(), "-m", "ally.hotkey_helper", json.dumps([b[1] for b in self.bindings])]
            try:
                self.proc = subprocess.Popen(argv, cwd=str(CODE_ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                             stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                             errors="replace", creationflags=NO_WINDOW)
            except OSError as e:
                self.status, self.error = "error", f"couldn't start the hotkey helper: {e}"
                return
            self.status, self.error = "starting", ""
            proc = self.proc
        threading.Thread(target=self._read_stdout, args=(proc,), daemon=True, name="hotkeys-out").start()
        threading.Thread(target=self._read_stderr, args=(proc,), daemon=True, name="hotkeys-err").start()

    def _read_stdout(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            line = line.strip()
            if line == "READY":
                self.status, self.error = "on", ""
                log.info("hotkeys on: %s", ", ".join(keys.describe(b[0]) for b in self.bindings))
            elif line.startswith("HIT "):
                try:
                    label, _combo, action = self.bindings[int(line[4:])]
                except (ValueError, IndexError):
                    continue
                threading.Thread(target=self.agent.run_action, args=(action, f"hotkey {label}"),
                                 daemon=True).start()
            elif line.startswith("ERROR"):
                self.status, self.error = "error", line[6:]
                log.warning("hotkeys unavailable: %s", self.error)
        try:
            proc.stdout.close()
        except OSError:
            pass

    def _read_stderr(self, proc: subprocess.Popen) -> None:
        for line in proc.stderr:
            line = line.strip()
            if line:
                log.warning("hotkey helper: %s", line[:300])
        try:
            proc.stderr.close()
        except OSError:
            pass

    def ensure_running(self) -> None:
        """Called every few seconds: restart a crashed helper (with limits)."""
        if not self._wanted or not self.bindings or self.status == "off":
            return
        proc = self.proc
        if proc is None or proc.poll() is None:
            return
        code = proc.returncode
        if code in (2, 3, 4):  # configuration problems: restarting won't help
            self.status = "error"
            return
        now = time.time()
        self._crashes = [t for t in self._crashes if now - t < 120] + [now]
        if len(self._crashes) > 5:
            self.status = "error"
            self.error = "the hotkey helper keeps crashing; hotkeys are off (see logs/ally.log)"
            return
        log.warning("hotkey helper exited (%s); restarting", code)
        self._restart()

    def _kill(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            proc.stdin.close()  # the helper exits when its stdin closes
        except Exception:
            pass
        try:
            proc.wait(timeout=1.5)
        except Exception:
            proc.kill()
            try:
                proc.wait(timeout=2)
            except Exception:
                pass

    def stop(self) -> None:
        self._wanted = False
        with self._lock:
            self._kill()
        self.status = "off"

    def info(self) -> dict:
        return {"status": self.status, "error": self.error,
                "keys": [{"keys": keys.describe(b[0]), "action": str(b[2])} for b in self.bindings]}
