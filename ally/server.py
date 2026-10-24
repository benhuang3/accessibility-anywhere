"""Local web server: dashboard, JSON API, and the WebSocket for the Chrome extension.

Security (it only listens on 127.0.0.1, but web pages can still try to reach it):
- Host header must be 127.0.0.1/localhost:<port>  -> blocks DNS-rebinding attacks.
- /api/* needs the secret token (embedded in the dashboard page, stored in state/token for the CLI)
  and, when an Origin header is sent, it must be the dashboard itself.
- /ws only accepts chrome-extension:// origins (optionally only listed extension IDs).
"""

from __future__ import annotations

import hmac
import http.server
import json
import logging
import mimetypes
import socket
import sys
import threading
import urllib.parse

from .registry import ToolError
from .websocket import WebSocket, accept_key

log = logging.getLogger("ally.server")
STATIC = {"/app.js": "app.js", "/style.css": "style.css", "/favicon.svg": "favicon.svg"}
MAX_BODY = 1_000_000


class PortBusy(Exception):
    pass


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = sys.platform != "win32"  # on Windows SO_REUSEADDR would allow port hijacking

    def server_bind(self):
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError)):
            log.debug("connection dropped: %s", exc)  # browsers close sockets all the time
        else:
            log.exception("request from %s failed", client_address)


def make_handler(agent):
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "Ally"
        sys_version = ""
        protocol_version = "HTTP/1.1"
        timeout = 120  # idle keep-alive connections close after this

        def log_message(self, fmt, *args):
            log.debug("%s %s", self.address_string(), fmt % args)

        # ---- helpers
        @property
        def port(self) -> int:
            return self.server.server_address[1]

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").lower()
            return host in {f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"[::1]:{self.port}"}

        def _dashboard_origin_ok(self) -> bool:
            origin = self.headers.get("Origin")
            return origin is None or origin in {f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"}

        def _token_ok(self) -> bool:
            return hmac.compare_digest(self.headers.get("X-Ally-Token", ""), agent.token)

        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ValueError("request too large")
            raw = self.rfile.read(n) if n else b"{}"
            data = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(data, dict):
                raise ValueError("expected a JSON object")
            return data

        def _guard_api(self) -> bool:
            if not self._host_ok() or not self._dashboard_origin_ok():
                self._json(403, {"error": "forbidden origin"})
                return False
            if not self._token_ok():
                self._json(401, {"error": "missing or wrong X-Ally-Token"})
                return False
            return True

        # ---- routes
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if not self._host_ok():
                return self._json(403, {"error": "bad host"})
            if path == "/ws":
                return self._websocket()
            if path == "/health":
                return self._json(200, agent.health())
            if path in ("/", "/index.html"):
                return self._dashboard()
            if path in STATIC:
                return self._static(STATIC[path])
            if path.startswith("/api/"):
                if not self._guard_api():
                    return
                q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
                if path == "/api/status":
                    return self._json(200, agent.status())
                if path == "/api/tools":
                    return self._json(200, {"tools": agent.tools_info()})
                if path == "/api/config":
                    return self._json(200, agent.config_info())
                if path == "/api/voice":
                    return self._json(200, agent.confirm.info())
                if path == "/api/events":
                    try:
                        after = int(q.get("after", "0"))
                    except ValueError:
                        after = 0
                    return self._json(200, {"events": agent.events.since(after)})
            return self._json(404, {"error": "not found"})

        def do_POST(self):
            path = urllib.parse.urlsplit(self.path).path
            if not path.startswith("/api/"):
                return self._json(404, {"error": "not found"})
            if not self._guard_api():
                return
            try:
                data = self._body()
            except (ValueError, UnicodeDecodeError) as e:
                return self._json(400, {"error": str(e)})
            if path == "/api/command":
                text = str(data.get("text", ""))[:2000]
                reply = agent.handle_text(text, source=str(data.get("source", "dashboard"))[:20],
                                          speak=bool(data.get("speak", True)))
                return self._json(200, reply.to_dict())
            if path == "/api/tool":
                out = agent.call_tool(str(data.get("name", "")), data.get("args") or {}, source="dashboard")
                if data.get("speak") and out.ok:
                    agent.speak_result(out)
                return self._json(200 if out.ok else 400, {"ok": out.ok, "text": out.text(), "error": out.error})
            if path == "/api/config":
                ok, errors, warnings = agent.save_config(str(data.get("text", "")))
                return self._json(200 if ok else 400, {"ok": ok, "errors": errors, "warnings": warnings})
            if path == "/api/stop":
                agent.speaker.stop()
                return self._json(200, {"ok": True})
            if path.startswith("/api/voice/"):
                return self._voice(path[len("/api/voice/"):], data)
            if path == "/api/shutdown":
                self._json(200, {"ok": True})
                agent.request_exit(0)
                return
            if path == "/api/restart":
                self._json(200, {"ok": True})
                agent.request_exit(75)
                return
            return self._json(404, {"error": "not found"})

        def _voice(self, action: str, data: dict):
            """Spoken questions: say, ask (waits for the answer), answer (buttons), warn, cancel."""
            vc = agent.confirm
            try:
                if action == "say":
                    ok = vc.say(str(data.get("text", ""))[:4000], str(data.get("style", "calm")),
                                wait=bool(data.get("wait", False)))
                    return self._json(200, {"ok": ok, "engine": agent.speaker.last_engine})
                if action == "ask":
                    timeout = data.get("timeout")
                    ans = vc.ask(str(data.get("prompt", ""))[:300], rephrased=data.get("rephrased") or None,
                                 timeout=float(timeout) if isinstance(timeout, (int, float)) else None,
                                 kind=str(data.get("kind", "offer"))[:20])
                    return self._json(200, ans.to_dict())
                if action == "answer":
                    ok = vc.answer(str(data.get("answer", "")), by=str(data.get("by", "button"))[:20])
                    return self._json(200, {"ok": ok, "answered": ok})
                if action == "warn":
                    ans = vc.warn({"kind": str(data.get("kind", "")), "severity": str(data.get("severity", "high"))},
                                  ask=data.get("ask"))
                    return self._json(200, ans.to_dict() if ans is not None else {"value": "said"})
                if action == "cancel":
                    return self._json(200, {"ok": vc.cancel()})
            except ToolError as e:
                return self._json(400, {"error": str(e)})
            return self._json(404, {"error": "not found"})

        def _dashboard(self):
            try:
                html = (agent.paths.dashboard_dir / "index.html").read_text(encoding="utf-8")
            except OSError:
                return self._json(500, {"error": "dashboard files missing"})
            html = html.replace("__ALLY_TOKEN__", agent.token).replace("__ALLY_NAME__",
                                                                       agent.config.data["agent"]["name"])
            csp = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                   "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8",
                       {"Content-Security-Policy": csp, "X-Frame-Options": "DENY"})

        def _static(self, name: str):
            try:
                body = (agent.paths.dashboard_dir / name).read_bytes()
            except OSError:
                return self._json(404, {"error": "not found"})
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self._send(200, body, ctype)

        def _websocket(self):
            origin = self.headers.get("Origin", "")
            allowed = agent.config.data.get("security", {}).get("allowed_extension_ids") or []
            ok_origin = origin.startswith("chrome-extension://") and (
                not allowed or origin[len("chrome-extension://"):].strip("/") in allowed)
            key = self.headers.get("Sec-WebSocket-Key", "")
            upgrade = (self.headers.get("Upgrade") or "").lower() == "websocket"
            if not ok_origin:
                return self._json(403, {"error": "only the Ally Chrome extension may connect"})
            if not upgrade or not key or self.headers.get("Sec-WebSocket-Version") != "13":
                return self._json(400, {"error": "bad websocket request"})
            self.send_response(101, "Switching Protocols")
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept_key(key))
            self.end_headers()
            self.wfile.flush()
            self.close_connection = True
            ws = WebSocket(self.connection, self.rfile)
            agent.bridge.serve(ws, origin)

    return Handler


def start_server(agent, port: int) -> Server:
    try:
        httpd = Server(("127.0.0.1", port), make_handler(agent))
    except OSError as e:
        raise PortBusy(f"port {port} is in use ({e})")
    t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.5}, name="http", daemon=True)
    t.start()
    return httpd
