"""Link to the Chrome extension over a local WebSocket.

Protocol (JSON text messages):
  extension -> agent  {"type":"hello", "version":..., "focused":bool, "tab":{...}}
                      {"type":"ping"}                         (every 20 s keeps both sides alive)
                      {"type":"result", "id":n, "ok":bool, "result"|"error":...}
                      {"type":"event", "name":"focus"|"tab", ...}
                      {"type":"command", "id":n, "text":"..."} (typed in the popup)
  agent -> extension  {"type":"call", "id":n, "action":"setZoom", "args":{...}}
                      {"type":"reply", "id":n, "text":"...", "ok":bool}
                      {"type":"pong"}
"""

from __future__ import annotations

import itertools
import json
import logging
import threading
import time

from .registry import ToolError
from .websocket import ConnectionClosed, WebSocket

log = logging.getLogger("ally.bridge")


class Client:
    def __init__(self, ws: WebSocket, origin: str):
        self.ws = ws
        self.origin = origin
        self.connected_at = time.time()
        self.last_seen = time.time()
        self.version = ""
        self.focused = False
        self.focus_changed = 0.0
        self.tab: dict = {}


class Bridge:
    def __init__(self, on_command=None, on_connect=None):
        self.on_command = on_command  # (text) -> reply text
        self.on_connect = on_connect  # (client) -> None
        self.on_page = None  # (page dict) -> None; set by ally.risk.wire
        self.clients: list[Client] = []
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._pending: dict[int, dict] = {}

    # ---- status
    @property
    def connected(self) -> bool:
        with self._lock:
            return any(not c.ws.closed for c in self.clients)

    def best(self) -> Client | None:
        with self._lock:
            live = [c for c in self.clients if not c.ws.closed]
        if not live:
            return None
        return max(live, key=lambda c: (c.focused, c.focus_changed, c.last_seen))

    @property
    def chrome_focused(self) -> bool:
        c = self.best()
        return bool(c and c.focused)

    @property
    def tab(self) -> dict:
        c = self.best()
        return dict(c.tab) if c else {}

    def status(self) -> dict:
        c = self.best()
        return {"connected": c is not None, "clients": len(self.clients), "focused": bool(c and c.focused),
                "tab": dict(c.tab) if c else {}, "version": c.version if c else ""}

    # ---- connection loop (runs on the HTTP handler thread)
    def serve(self, ws: WebSocket, origin: str) -> None:
        client = Client(ws, origin)
        with self._lock:
            self.clients.append(client)
        log.info("Chrome extension connected (%s)", origin)
        try:
            while True:
                raw = ws.recv()
                if raw is None:
                    break
                client.last_seen = time.time()
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(msg, dict):
                    self._handle(client, msg)
        finally:
            ws.close()
            with self._lock:
                if client in self.clients:
                    self.clients.remove(client)
            log.info("Chrome extension disconnected")

    def _handle(self, client: Client, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "ping":
            self._send(client, {"type": "pong"})
        elif kind == "hello":
            client.version = str(msg.get("version", ""))
            self._apply_focus(client, msg)
            if self.on_connect:
                threading.Thread(target=self._safe_connect, args=(client,), daemon=True).start()
        elif kind == "result":
            box = self._pending.get(msg.get("id"))
            if box is not None:
                box["msg"] = msg
                box["event"].set()
        elif kind == "event":
            if msg.get("name") == "page":
                if self.on_page:  # page text from extension/risk.js
                    threading.Thread(target=self._safe_page, args=(msg,), daemon=True).start()
                return
            self._apply_focus(client, msg)
        elif kind == "command" and self.on_command:
            threading.Thread(target=self._run_command, args=(client, msg), daemon=True).start()

    def _apply_focus(self, client: Client, msg: dict) -> None:
        if "focused" in msg:
            focused = bool(msg["focused"])
            if focused != client.focused or focused:
                client.focus_changed = time.time()
            client.focused = focused
        if isinstance(msg.get("tab"), dict):
            client.tab = {k: msg["tab"].get(k) for k in ("title", "url", "zoom") if k in msg["tab"]}

    def _safe_connect(self, client: Client) -> None:
        try:
            self.on_connect(client)
        except Exception as e:
            log.warning("on_connect failed: %s", e)

    def _safe_page(self, msg: dict) -> None:
        try:
            self.on_page(msg)
        except Exception as e:
            log.warning("on_page failed: %s", e)

    def _run_command(self, client: Client, msg: dict) -> None:
        try:
            text = self.on_command(str(msg.get("text", "")))
            self._send(client, {"type": "reply", "id": msg.get("id"), "ok": True, "text": text})
        except Exception as e:
            self._send(client, {"type": "reply", "id": msg.get("id"), "ok": False, "text": str(e)})

    def _send(self, client: Client, msg: dict) -> bool:
        try:
            client.ws.send(json.dumps(msg))
            return True
        except ConnectionClosed:
            return False

    # ---- calls into the browser
    def call(self, action: str, args: dict | None = None, timeout: float = 8.0, client: Client | None = None):
        client = client or self.best()
        if client is None:
            raise ToolError("Chrome isn't connected. Open Chrome with the Ally extension installed "
                            "(chrome://extensions > Load unpacked > the 'extension' folder).")
        cid = next(self._ids)
        box = {"event": threading.Event()}
        self._pending[cid] = box
        try:
            if not self._send(client, {"type": "call", "id": cid, "action": action, "args": args or {}}):
                raise ToolError("Lost the connection to Chrome. Try again in a moment.")
            if not box["event"].wait(timeout):
                raise ToolError("Chrome didn't answer in time.")
            msg = box["msg"]
        finally:
            self._pending.pop(cid, None)
        if not msg.get("ok"):
            raise ToolError(str(msg.get("error") or "The browser action failed."))
        return msg.get("result")

    def close_all(self) -> None:
        """Drop every extension connection (they reconnect on their own)."""
        with self._lock:
            clients = list(self.clients)
        for c in clients:
            c.ws.close(1001, "agent stopping")

    def broadcast(self, action: str, args: dict | None = None) -> None:
        with self._lock:
            clients = [c for c in self.clients if not c.ws.closed]
        for c in clients:
            threading.Thread(target=self._quiet_call, args=(c, action, args), daemon=True).start()

    def _quiet_call(self, client: Client, action: str, args: dict | None) -> None:
        try:
            self.call(action, args, client=client)
        except ToolError as e:
            log.debug("broadcast %s failed: %s", action, e)
