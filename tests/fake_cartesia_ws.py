"""A stand-in for Cartesia's streaming STT WebSocket (GET /stt/turns/websocket). Standard library only.

Speaks the documented turn protocol: "connected" on open, binary audio in, {"type": "close"} to
finish, JSON turn.* events out, then a close frame. Set .mode (or call set()) before connecting:

  mode = ok           turn.start on the first audio; on close: turn.update, turn.eager_end, turn.end
                      with .text, then a close frame.
         script       .script is a list of {"at": "audio" | "close", "event": {...}, "delay": s};
                      "audio" events go out after the first audio block, "close" ones on close.
         slow         like ok, but waits .delay seconds before answering close.
         drop         drops the TCP connection (no close frame) after the first audio block.
         reject_auth  every connection gets HTTP 401 (also happens for a wrong X-API-Key).
         error        sends an error message (.error) after the first audio, then closes.
  Raw modes for WebSocket client tests:
         bad_accept   upgrades with a wrong Sec-WebSocket-Accept.
         echo         echoes each message; text "fragment" gets a 3-part fragmented reply;
                      text "ping" makes the server ping (the pong lands in .frames).

.frames records every client frame as (masked, opcode, payload); .requests records each
handshake's path, query and headers.
"""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import socketserver
import struct
import sys
import threading
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ally.websocket import OP_BIN, OP_CLOSE, OP_CONT, OP_PING, OP_PONG, OP_TEXT, accept_key, encode_frame, xor_mask  # noqa: E402

KEY = "test-key"


def read_frame(rfile) -> tuple[bool, int, bool, bytes]:
    head = rfile.read(2)
    if len(head) < 2:
        raise EOFError
    fin, op, masked, n = bool(head[0] & 0x80), head[0] & 0x0F, bool(head[1] & 0x80), head[1] & 0x7F
    if n == 126:
        (n,) = struct.unpack("!H", rfile.read(2))
    elif n == 127:
        (n,) = struct.unpack("!Q", rfile.read(8))
    mask = rfile.read(4) if masked else b""
    data = rfile.read(n)
    if len(data) < n:
        raise EOFError
    return fin, op, masked, xor_mask(data, mask) if masked else data


class FakeCartesiaWS:
    def __init__(self, key: str = KEY):
        self.key = key
        self.mode = "ok"
        self.text = "hello there"
        self.script: list[dict] = []
        self.delay = 0.0
        self.error = {"status_code": 400, "title": "Bad request", "message": "model not found",
                      "error_code": "model_not_found"}
        self.frames: list[tuple[bool, int, bytes]] = []
        self.requests: list[dict] = []
        self.audio_bytes = 0
        self.lock = threading.Lock()
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                outer._serve(self.connection, self.rfile)

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.ws_url = f"ws://127.0.0.1:{self.port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def set(self, **kw) -> None:
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    # -- handshake
    def _serve(self, conn: socket.socket, rfile) -> None:
        line = rfile.readline().decode("latin-1").strip()
        headers = {}
        while True:
            h = rfile.readline().decode("latin-1").strip()
            if not h:
                break
            k, _, v = h.partition(":")
            headers[k.strip().lower()] = v.strip()
        target = line.split(" ")[1] if " " in line else "/"
        u = urllib.parse.urlsplit(target)
        query = urllib.parse.parse_qs(u.query)
        with self.lock:
            self.requests.append({"path": u.path, "query": query, "headers": headers})
        mode = self.mode
        if mode == "reject_auth" or (mode not in ("echo", "bad_accept") and headers.get("x-api-key") != self.key):
            return self._http(conn, 401, {"error": "invalid API key"})
        if mode not in ("echo", "bad_accept"):
            missing = [p for p in ("model", "encoding", "sample_rate", "cartesia_version") if p not in query]
            if u.path != "/stt/turns/websocket" or missing:
                return self._http(conn, 400, {"error": f"bad request, missing {missing}"})
        key = headers.get("sec-websocket-key", "")
        accept = accept_key(key)
        if mode == "bad_accept":
            accept = base64.b64encode(hashlib.sha1(b"wrong").digest()).decode()
        conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
        try:
            if mode == "echo":
                self._echo(conn, rfile)
            elif mode != "bad_accept":
                self._turns(conn, rfile, mode)
        except (EOFError, OSError):
            pass

    @staticmethod
    def _http(conn: socket.socket, code: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        conn.sendall(f"HTTP/1.1 {code} Error\r\nContent-Type: application/json\r\nContent-Length: {len(raw)}\r\n"
                     f"Connection: close\r\n\r\n".encode() + raw)

    # -- frames
    def _recv(self, rfile) -> tuple[int, bytes]:
        fin, op, masked, data = read_frame(rfile)
        with self.lock:
            self.frames.append((masked, op, data))
        return op, data

    @staticmethod
    def _send(conn: socket.socket, obj) -> None:
        if isinstance(obj, dict):
            conn.sendall(encode_frame(OP_TEXT, json.dumps(obj).encode()))
        else:
            conn.sendall(encode_frame(OP_TEXT, obj.encode()) if isinstance(obj, str) else encode_frame(OP_BIN, obj))

    @staticmethod
    def _close(conn: socket.socket, rfile, code: int = 1000) -> None:
        conn.sendall(encode_frame(OP_CLOSE, struct.pack("!H", code)))
        try:
            conn.settimeout(1)
            while read_frame(rfile)[1] != OP_CLOSE:
                pass
        except (EOFError, OSError):
            pass

    def _echo(self, conn: socket.socket, rfile) -> None:
        while True:
            op, data = self._recv(rfile)
            if op == OP_CLOSE:
                conn.sendall(encode_frame(OP_CLOSE, data[:2]))
                return
            if op == OP_TEXT and data == b"fragment":
                conn.sendall(encode_frame(OP_TEXT, b"one ", fin=False) + encode_frame(OP_PING, b"mid")
                             + encode_frame(OP_CONT, b"two ", fin=False) + encode_frame(OP_CONT, b"three"))
            elif op == OP_TEXT and data == b"ping":
                conn.sendall(encode_frame(OP_PING, b"are-you-there"))
                conn.sendall(encode_frame(OP_TEXT, b"pinged"))
            elif op == OP_TEXT and data == b"close-me":
                conn.sendall(encode_frame(OP_CLOSE, struct.pack("!H", 1001) + b"bye"))
            elif op in (OP_TEXT, OP_BIN):
                conn.sendall(encode_frame(op, data))

    def _turns(self, conn: socket.socket, rfile, mode: str) -> None:
        rid = f"req-{len(self.requests)}"

        def emit(event: dict) -> None:
            self._send(conn, {**event, "request_id": rid})

        emit({"type": "connected"})
        started = False
        while True:
            op, data = self._recv(rfile)
            if op == OP_BIN:
                with self.lock:
                    self.audio_bytes += len(data)
                if started:
                    continue
                started = True
                if mode == "drop":
                    conn.shutdown(socket.SHUT_RDWR)
                    conn.close()
                    return
                if mode == "error":
                    emit({"type": "error", **self.error})
                    return self._close(conn, rfile, 1008)
                if mode == "script":
                    self._play(emit, "audio")
                else:
                    emit({"type": "turn.start"})
            elif op == OP_TEXT and json.loads(data).get("type") == "close":
                if mode == "slow":
                    time.sleep(self.delay)
                if mode == "script":
                    self._play(emit, "close")
                else:
                    if not started:
                        emit({"type": "turn.start"})
                    emit({"type": "turn.update", "transcript": self.text})
                    emit({"type": "turn.eager_end", "transcript": self.text})
                    emit({"type": "turn.end", "transcript": self.text})
                return self._close(conn, rfile)
            elif op == OP_CLOSE:
                conn.sendall(encode_frame(OP_CLOSE, data[:2]))
                return
            elif op == OP_PING:
                conn.sendall(encode_frame(OP_PONG, data))

    def _play(self, emit, at: str) -> None:
        for item in self.script:
            if item.get("at", "audio") == at:
                time.sleep(float(item.get("delay", 0)))
                if item.get("event") == "drop":
                    raise EOFError
                emit(item["event"])


if __name__ == "__main__":
    f = FakeCartesiaWS()
    print(f.ws_url, flush=True)
    threading.Event().wait()
