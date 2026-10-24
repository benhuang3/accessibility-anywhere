"""Minimal RFC 6455 WebSocket (server side) for the local Chrome extension link.

Text and binary messages, fragmentation, ping/pong and close are supported.
No extensions are negotiated, so frames are never compressed.
"""

from __future__ import annotations

import base64
import hashlib
import socket
import struct
import threading

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OP_CONT, OP_TEXT, OP_BIN, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


class ConnectionClosed(Exception):
    pass


def accept_key(client_key: str) -> str:
    digest = hashlib.sha1((client_key.strip() + GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def encode_frame(opcode: int, payload: bytes, fin: bool = True, mask: bytes | None = None) -> bytes:
    head = bytearray([(0x80 if fin else 0) | opcode])
    n = len(payload)
    mbit = 0x80 if mask else 0
    if n < 126:
        head.append(mbit | n)
    elif n < 65536:
        head.append(mbit | 126)
        head += struct.pack("!H", n)
    else:
        head.append(mbit | 127)
        head += struct.pack("!Q", n)
    if mask:
        head += mask
        payload = xor_mask(payload, mask)
    return bytes(head) + payload


def xor_mask(data: bytes, mask: bytes) -> bytes:
    n = len(data)
    if n == 0:
        return b""
    key = (mask * (n // 4 + 1))[:n]
    return (int.from_bytes(data, "big") ^ int.from_bytes(key, "big")).to_bytes(n, "big")


class WebSocket:
    def __init__(self, sock: socket.socket, rfile, max_size: int = 32 * 1024 * 1024, idle_timeout: float = 90.0):
        self.sock = sock
        self.rfile = rfile
        self.max_size = max_size
        self.closed = False
        self._send_lock = threading.Lock()
        sock.settimeout(idle_timeout)  # the extension pings every 20 s; silence means the link is dead

    def _read(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            try:
                chunk = self.rfile.read(n - len(buf))
            except (socket.timeout, OSError) as e:
                raise ConnectionClosed(str(e))
            if not chunk:
                raise ConnectionClosed("peer closed")
            buf += chunk
        return buf

    def _read_frame(self) -> tuple[bool, int, bytes]:
        b0, b1 = self._read(2)
        fin, rsv, opcode = bool(b0 & 0x80), b0 & 0x70, b0 & 0x0F
        masked, n = bool(b1 & 0x80), b1 & 0x7F
        if rsv:
            raise ConnectionClosed("unexpected reserved bits")
        if n == 126:
            (n,) = struct.unpack("!H", self._read(2))
        elif n == 127:
            (n,) = struct.unpack("!Q", self._read(8))
        if n > self.max_size:
            self.close(1009, "message too big")
            raise ConnectionClosed("message too big")
        if not masked:
            self.close(1002, "client frames must be masked")
            raise ConnectionClosed("unmasked client frame")
        mask = self._read(4)
        return fin, opcode, xor_mask(self._read(n), mask)

    def recv(self) -> str | bytes | None:
        """Next message, or None once the connection is closed."""
        parts: list[bytes] = []
        first_op = None
        try:
            while True:
                fin, op, data = self._read_frame()
                if op == OP_PING:
                    self._send(OP_PONG, data[:125])
                    continue
                if op == OP_PONG:
                    continue
                if op == OP_CLOSE:
                    code = struct.unpack("!H", data[:2])[0] if len(data) >= 2 else 1000
                    self.close(code if code in (1000, 1001) else 1000)
                    return None
                if op in (OP_TEXT, OP_BIN):
                    if first_op is not None:
                        raise ConnectionClosed("new message before the last one finished")
                    first_op = op
                elif op != OP_CONT or first_op is None:
                    raise ConnectionClosed(f"bad opcode {op}")
                parts.append(data)
                if sum(map(len, parts)) > self.max_size:
                    self.close(1009, "message too big")
                    return None
                if fin:
                    payload = b"".join(parts)
                    return payload.decode("utf-8") if first_op == OP_TEXT else payload
        except (ConnectionClosed, UnicodeDecodeError, struct.error, ValueError):
            self.closed = True
            return None

    def _send(self, opcode: int, payload: bytes) -> None:
        with self._send_lock:
            if self.closed:
                raise ConnectionClosed("closed")
            try:
                self.sock.sendall(encode_frame(opcode, payload))
            except OSError as e:
                self.closed = True
                raise ConnectionClosed(str(e))

    def send(self, message: str | bytes) -> None:
        if isinstance(message, str):
            self._send(OP_TEXT, message.encode("utf-8"))
        else:
            self._send(OP_BIN, message)

    def close(self, code: int = 1000, reason: str = "") -> None:
        if self.closed:
            return
        try:
            self._send(OP_CLOSE, struct.pack("!H", code) + reason.encode("utf-8")[:120])
        except ConnectionClosed:
            pass
        self.closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
