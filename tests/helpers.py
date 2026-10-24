"""Shared test helpers (standard library only)."""

from __future__ import annotations

import http.server
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def temp_home(config: str = "") -> Path:
    home = Path(tempfile.mkdtemp(prefix="ally-test-"))
    base = '' if "[speech]" in config else '[speech]\nengine = "off"\n'
    (home / "config.toml").write_text(base + config, encoding="utf-8")
    (home / "plugins").mkdir()
    return home


class FakeClaude:
    """A tiny stand-in for the Claude Messages API. Queue responses; inspect requests."""

    def __init__(self):
        self.responses: list[tuple[int, dict]] = []
        self.requests: list[dict] = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                outer.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
                status, resp = outer.responses.pop(0) if outer.responses else (500, {"type": "error", "error": {"type": "api_error", "message": "no response queued"}})
                self._reply(status, resp)

            def do_GET(self):
                outer.requests.append({"path": self.path, "headers": dict(self.headers), "body": None})
                self._reply(200, {"data": [{"id": "claude-haiku-5-5"}]})

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def queue(self, status: int, body: dict) -> None:
        self.responses.append((status, body))

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def tool_use(name: str, args: dict, uid: str = "toolu_1", text: str = "") -> dict:
    content = ([{"type": "text", "text": text}] if text else []) + [
        {"type": "tool_use", "id": uid, "name": name, "input": args}]
    return {"id": "msg_1", "type": "message", "role": "assistant", "content": content,
            "stop_reason": "tool_use", "model": "claude-haiku-5-5"}


def final(text: str) -> dict:
    return {"id": "msg_2", "type": "message", "role": "assistant", "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn", "model": "claude-haiku-5-5"}


class EnvPatch:
    def __init__(self, **values):
        self.values = values
        self.old = {}

    def __enter__(self):
        for k, v in self.values.items():
            self.old[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return self

    def __exit__(self, *exc):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
