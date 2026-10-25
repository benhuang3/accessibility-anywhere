"""Minimal RFC 6455 WebSocket client (ws:// and wss://), standard library only.

Client frames are masked (ally.websocket.encode_frame); server frames must be unmasked.
Text and binary messages, fragmentation, ping/pong and close are handled. No extensions are
negotiated. recv() can time out without losing data: frames are only consumed once complete.
"""

from __future__ import annotations

import base64
import os
import select
import socket
import ssl
import struct
import threading
import time
import urllib.parse

from .websocket import OP_BIN, OP_CLOSE, OP_CONT, OP_PING, OP_PONG, OP_TEXT, accept_key, encode_frame


class WSError(Exception):
    """The WebSocket failed (could not connect, protocol error, connection lost)."""


class WSHandshakeError(WSError):
    """The server did not upgrade. status is the HTTP status (0 if there was no valid answer)."""

    def __init__(self, message: str, status: int = 0, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


class WSTimeout(WSError):
    """No message arrived in time. The connection is still usable."""


class WSClosed(WSError):
    """The connection is closed. code is the close code, or None if it dropped without one."""

    def __init__(self, message: str, code: int | None = None, reason: str = ""):
        super().__init__(message)
        self.code = code
        self.reason = reason


def _read_headers(sock: socket.socket, timeout: float, limit: int = 65536) -> tuple[bytes, bytes]:
    """Read up to the blank line. Returns (head, bytes already read past it)."""
    sock.settimeout(timeout)
    buf = b""
    while b"\r\n\r\n" not in buf:
        try:
            chunk = sock.recv(4096)
        except (socket.timeout, OSError) as e:
            raise WSHandshakeError(f"no handshake answer: {e}")
        if not chunk:
            raise WSHandshakeError("connection closed during the handshake")
        buf += chunk
        if len(buf) > limit:
            raise WSHandshakeError("handshake answer too long")
    head, rest = buf.split(b"\r\n\r\n", 1)
    return head, rest


def _parse_head(head: bytes) -> tuple[int, dict[str, str]]:
    lines = head.decode("latin-1").split("\r\n")
    parts = lines[0].split(" ", 2)
    try:
        status = int(parts[1])
    except (IndexError, ValueError):
        raise WSHandshakeError(f"bad status line: {lines[0][:80]!r}")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return status, headers


def _error_body(sock: socket.socket, rest: bytes, headers: dict[str, str]) -> str:
    """Best-effort read of a rejected handshake's body, for the error message."""
    try:
        want = min(int(headers.get("content-length") or 0), 4096)
        sock.settimeout(2)
        while len(rest) < want:
            chunk = sock.recv(4096)
            if not chunk:
                break
            rest += chunk
    except (ValueError, OSError):
        pass
    return rest[:4096].decode("utf-8", "replace")


def connect(url: str, headers: dict[str, str] | None = None, timeout: float = 10.0,
            ssl_ctx: ssl.SSLContext | None = None, max_size: int = 16 * 1024 * 1024) -> "WSClient":
    """Open a WebSocket. Raises WSHandshakeError (with .status) or WSError."""
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("ws", "wss") or not u.hostname:
        raise WSError(f"not a ws:// or wss:// URL: {url}")
    secure = u.scheme == "wss"
    port = u.port or (443 if secure else 80)
    try:
        sock = socket.create_connection((u.hostname, port), timeout=timeout)
    except OSError as e:
        raise WSHandshakeError(f"can't connect to {u.hostname}:{port}: {e}")
    try:
        if secure:
            if ssl_ctx is None:
                from .claude import ssl_context
                ssl_ctx = ssl_context()
            sock = ssl_ctx.wrap_socket(sock, server_hostname=u.hostname)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        host = u.hostname if u.port is None else f"{u.hostname}:{u.port}"
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        lines = [f"GET {path} HTTP/1.1", f"Host: {host}", "Upgrade: websocket", "Connection: Upgrade",
                 f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13"]
        lines += [f"{k}: {v}" for k, v in (headers or {}).items()]
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
        head, rest = _read_headers(sock, timeout)
        status, resp = _parse_head(head)
        if status != 101:
            body = _error_body(sock, rest, resp)
            raise WSHandshakeError(f"server refused the upgrade: HTTP {status} {body[:200]}".strip(), status, body)
        if resp.get("upgrade", "").lower() != "websocket":
            raise WSHandshakeError("server did not upgrade to websocket", status)
        if resp.get("sec-websocket-accept", "") != accept_key(key):
            raise WSHandshakeError("Sec-WebSocket-Accept does not match", status)
    except WSError:
        _close_quietly(sock)
        raise
    except (OSError, ssl.SSLError) as e:
        _close_quietly(sock)
        raise WSHandshakeError(f"handshake failed: {e}")
    return WSClient(sock, rest, timeout, max_size)


def _close_quietly(sock: socket.socket) -> None:
    try:
        sock.close()
    except OSError:
        pass


class WSClient:
    """An open client connection. One thread may recv() while others send()."""

    def __init__(self, sock: socket.socket, buffered: bytes = b"", send_timeout: float = 10.0,
                 max_size: int = 16 * 1024 * 1024):
        self.sock = sock
        self.max_size = max_size
        self.closed = False
        self.close_code: int | None = None
        self.close_reason = ""
        self._buf = bytearray(buffered)
        self._parts: list[bytes] = []
        self._first_op: int | None = None
        self._send_lock = threading.Lock()
        self._sent_close = False
        sock.settimeout(send_timeout)  # bounds sendall; recv waits with select()

    # -- sending
    def _send(self, opcode: int, payload: bytes) -> None:
        with self._send_lock:
            if self.closed or self._sent_close:
                raise WSClosed("connection closed", self.close_code, self.close_reason)
            try:
                self.sock.sendall(encode_frame(opcode, payload, mask=os.urandom(4)))
            except OSError as e:
                self._mark_closed()
                raise WSClosed(f"send failed: {e}")
            if opcode == OP_CLOSE:
                self._sent_close = True

    def send(self, message: str | bytes) -> None:
        if isinstance(message, str):
            self._send(OP_TEXT, message.encode("utf-8"))
        else:
            self._send(OP_BIN, bytes(message))

    def ping(self, data: bytes = b"") -> None:
        self._send(OP_PING, data[:125])

    # -- receiving
    def _wait_readable(self, deadline: float | None) -> None:
        if isinstance(self.sock, ssl.SSLSocket) and self.sock.pending():
            return
        while True:
            left = None if deadline is None else deadline - time.monotonic()
            if left is not None and left <= 0:
                raise WSTimeout("no message in time")
            try:
                ready, _, _ = select.select([self.sock], [], [], left)
            except (OSError, ValueError) as e:
                self._mark_closed()
                raise WSClosed(f"connection lost: {e}")
            if ready:
                return

    def _fill(self, deadline: float | None) -> None:
        self._wait_readable(deadline)
        try:
            chunk = self.sock.recv(65536)
        except ssl.SSLWantReadError:
            return
        except socket.timeout:
            return
        except OSError as e:
            self._mark_closed()
            raise WSClosed(f"connection lost: {e}")
        if not chunk:
            self._mark_closed()
            raise WSClosed("connection dropped without a close frame")
        self._buf += chunk

    def _take_frame(self) -> tuple[bool, int, bytes] | None:
        """One complete frame from the buffer, or None if it hasn't all arrived yet."""
        buf = self._buf
        if len(buf) < 2:
            return None
        b0, b1 = buf[0], buf[1]
        if b0 & 0x70:
            raise WSError("unexpected reserved bits")
        if b1 & 0x80:
            raise WSError("server frames must not be masked")
        n, pos = b1 & 0x7F, 2
        if n == 126:
            if len(buf) < 4:
                return None
            (n,) = struct.unpack("!H", buf[2:4])
            pos = 4
        elif n == 127:
            if len(buf) < 10:
                return None
            (n,) = struct.unpack("!Q", buf[2:10])
            pos = 10
        if n > self.max_size:
            raise WSError("message too big")
        if len(buf) < pos + n:
            return None
        payload = bytes(buf[pos:pos + n])
        del buf[:pos + n]
        return bool(b0 & 0x80), b0 & 0x0F, payload

    def recv(self, timeout: float | None = None) -> str | bytes:
        """The next text (str) or binary (bytes) message.

        Raises WSTimeout (connection still fine), WSClosed (closed; .code set if the server sent a
        close frame) or WSError (protocol error; the connection is then closed)."""
        if self.closed:
            raise WSClosed("connection closed", self.close_code, self.close_reason)
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            try:
                frame = self._take_frame()
            except WSError:
                self.close(1002)
                raise
            if frame is None:
                self._fill(deadline)
                continue
            message = self._handle(*frame)
            if message is not None:
                return message

    def _handle(self, fin: bool, op: int, data: bytes) -> str | bytes | None:
        if op == OP_PING:
            try:
                self._send(OP_PONG, data[:125])
            except WSClosed:
                pass
            return None
        if op == OP_PONG:
            return None
        if op == OP_CLOSE:
            self.close_code = struct.unpack("!H", data[:2])[0] if len(data) >= 2 else 1005
            self.close_reason = data[2:].decode("utf-8", "replace")
            if not self._sent_close:
                try:
                    self._send(OP_CLOSE, data[:2])
                except WSClosed:
                    pass
            self._mark_closed()
            raise WSClosed(f"server closed the connection ({self.close_code})", self.close_code, self.close_reason)
        if op in (OP_TEXT, OP_BIN):
            if self._first_op is not None:
                self.close(1002)
                raise WSError("new message before the last one finished")
            self._first_op = op
        elif op != OP_CONT or self._first_op is None:
            self.close(1002)
            raise WSError(f"bad opcode {op}")
        self._parts.append(data)
        if sum(map(len, self._parts)) > self.max_size:
            self.close(1009)
            raise WSError("message too big")
        if not fin:
            return None
        payload, first = b"".join(self._parts), self._first_op
        self._parts, self._first_op = [], None
        if first == OP_TEXT:
            try:
                return payload.decode("utf-8")
            except UnicodeDecodeError:
                self.close(1007)
                raise WSError("text message is not UTF-8")
        return payload

    # -- closing
    def _mark_closed(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        _close_quietly(self.sock)

    def abort(self) -> None:
        """Drop the socket now (safe while another thread is inside recv())."""
        self._mark_closed()

    def close(self, code: int = 1000, reason: str = "", timeout: float = 2.0) -> None:
        """Send a close frame, wait briefly for the server's, then drop the socket.
        Don't call while another thread is inside recv(); use abort() then."""
        if self.closed:
            return
        try:
            self._send(OP_CLOSE, struct.pack("!H", code) + reason.encode("utf-8")[:120])
            deadline = time.monotonic() + timeout
            while not self.closed and time.monotonic() < deadline:
                frame = self._take_frame()
                if frame is None:
                    self._fill(deadline)
                elif frame[1] == OP_CLOSE:
                    self._handle(*frame)
        except WSError:
            pass
        self._mark_closed()

    def __enter__(self) -> "WSClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
