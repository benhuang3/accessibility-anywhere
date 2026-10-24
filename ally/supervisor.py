"""Keeps the agent running: restarts it if it crashes or stops answering.

- exit 0  -> the user asked Ally to stop: the supervisor stops too
- exit 75 -> restart requested: restart right away
- exit 98 -> port busy: if another Ally is healthy, step aside
- anything else -> crash: restart with backoff (1 s, 2 s, 4 s ... max 30 s)
- /health fails 3 times in a row (after a 20 s grace period) -> kill and restart
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request

from .config import ConfigManager
from .log import setup_logging
from .paths import CODE_ROOT, Paths
from .platforms.base import NO_WINDOW

log = logging.getLogger("ally.supervisor")
EXIT_OK, EXIT_RESTART, EXIT_PORT_BUSY = 0, 75, 98


def listening(port: int, timeout: float = 0.5) -> bool:
    """Quick check that something accepts connections on 127.0.0.1:port.

    Windows takes about 2 s to refuse a connection to a closed localhost port; a live agent
    accepts in well under a millisecond, so a short timeout answers "not running" fast.
    """
    try:
        socket.create_connection(("127.0.0.1", port), timeout=timeout).close()
        return True
    except OSError:
        return False


def health(port: int, timeout: float = 3.0) -> dict | None:
    if not listening(port):
        return None
    req = urllib.request.Request(f"http://127.0.0.1:{port}/health")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never send localhost via a proxy
    try:
        with opener.open(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data if data.get("name") == "ally" else None
    except Exception:
        return None


def current_port(paths: Paths) -> int:
    cm = ConfigManager(paths.config)
    cm.reload()
    return int(cm.data["agent"]["port"])


def agent_command(paths: Paths) -> list[str]:
    return [sys.executable, str(CODE_ROOT / "run.py"), "agent"]


def supervise(home=None, check_every: float = 5.0, grace: float = 20.0, max_backoff: float = 30.0,
              command: list[str] | None = None) -> int:
    paths = Paths(home)
    paths.ensure()
    setup_logging(paths.log_dir, filename="supervisor.log")
    env = dict(os.environ, ALLY_HOME=str(paths.home))
    port = current_port(paths)
    if health(port):
        log.info("Ally is already running on port %s", port)
        print(f"Ally is already running: http://127.0.0.1:{port}/")
        return EXIT_OK

    stop = threading.Event()
    proc_box: dict = {}

    def on_signal(*_):
        stop.set()
        p = proc_box.get("proc")
        if p and p.poll() is None:
            p.terminate()

    for sig in ("SIGTERM", "SIGINT", "SIGBREAK", "SIGHUP"):
        if hasattr(signal, sig):
            try:
                signal.signal(getattr(signal, sig), on_signal)
            except (ValueError, OSError):
                pass

    backoff = 1.0
    try:
        while not stop.is_set():
            port = current_port(paths)
            argv = command or agent_command(paths)
            started = time.monotonic()
            log.info("starting agent: %s", " ".join(argv))
            try:
                proc = subprocess.Popen(argv, cwd=str(CODE_ROOT), env=env, creationflags=NO_WINDOW)
            except OSError as e:
                log.error("couldn't start the agent: %s", e)
                if stop.wait(backoff):
                    break
                backoff = min(backoff * 2, max_backoff)
                continue
            proc_box["proc"] = proc
            fails = 0
            while True:
                try:
                    proc.wait(timeout=check_every)  # returns the moment the agent exits
                    break
                except subprocess.TimeoutExpired:
                    pass
                if stop.is_set():
                    break
                if time.monotonic() - started < grace:
                    continue
                if health(port):
                    fails = 0
                    continue
                fails += 1
                log.warning("agent didn't answer the health check (%s/3)", fails)
                if fails >= 3:
                    log.error("agent is stuck; restarting it")
                    proc.kill()
                    break
            if stop.is_set():
                break
            code = proc.wait()
            uptime = time.monotonic() - started
            if code == EXIT_OK:
                log.info("agent stopped normally")
                return EXIT_OK
            if code == EXIT_PORT_BUSY and health(port):
                log.info("another Ally is already running on port %s", port)
                return EXIT_OK
            if code == EXIT_RESTART:
                log.info("restart requested")
                backoff = 1.0
                continue
            if uptime > 60:
                backoff = 1.0
            log.warning("agent exited with code %s after %.0fs; restarting in %.0fs", code, uptime, backoff)
            if stop.wait(backoff):
                break
            backoff = min(backoff * 2, max_backoff)
    except KeyboardInterrupt:
        pass
    p = proc_box.get("proc")
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(timeout=8)
        except subprocess.TimeoutExpired:
            p.kill()
    log.info("supervisor stopped")
    return EXIT_OK
