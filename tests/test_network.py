import base64
import hashlib
import http.client
import json
import os
import socket
import struct
import threading
import time
import unittest

from helpers import EnvPatch, fake_screen, temp_home

from ally.agent import Agent
from ally.websocket import OP_BIN, OP_CLOSE, OP_CONT, OP_PING, OP_PONG, OP_TEXT, WebSocket, accept_key, encode_frame


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def read_frame(sock) -> tuple[int, bytes]:
    def read(n):
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise EOFError
            buf += chunk
        return buf

    b0, b1 = read(2)
    n = b1 & 0x7F
    if n == 126:
        (n,) = struct.unpack("!H", read(2))
    elif n == 127:
        (n,) = struct.unpack("!Q", read(8))
    return b0 & 0x0F, read(n)


class WebSocketFrameTests(unittest.TestCase):
    def setUp(self):
        self.a, self.b = socket.socketpair()
        self.ws = WebSocket(self.a, self.a.makefile("rb"))

    def tearDown(self):
        self.a.close()
        self.b.close()

    def send_client(self, op, payload, fin=True):
        self.b.sendall(encode_frame(op, payload, fin=fin, mask=os.urandom(4)))

    def test_accept_key_rfc_example(self):
        # Example from RFC 6455 section 1.3
        self.assertEqual(accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_sizes_roundtrip(self):
        for n in (0, 1, 125, 126, 65535, 65536, 200_000):
            text = "x" * n
            self.send_client(OP_TEXT, text.encode())
            self.assertEqual(self.ws.recv(), text, n)

    def test_binary_and_fragments(self):
        self.send_client(OP_BIN, b"\x00\x01")
        self.assertEqual(self.ws.recv(), b"\x00\x01")
        self.send_client(OP_TEXT, "hel".encode(), fin=False)
        self.send_client(OP_PING, b"p")  # control frame in the middle is allowed
        self.send_client(OP_CONT, "lo".encode(), fin=True)
        self.assertEqual(self.ws.recv(), "hello")
        op, data = read_frame(self.b)
        self.assertEqual((op, data), (OP_PONG, b"p"))

    def test_server_send_and_close(self):
        self.ws.send("hi")
        self.assertEqual(read_frame(self.b), (OP_TEXT, b"hi"))
        self.send_client(OP_CLOSE, struct.pack("!H", 1000))
        self.assertIsNone(self.ws.recv())
        self.assertTrue(self.ws.closed)
        self.assertEqual(read_frame(self.b)[0], OP_CLOSE)

    def test_unmasked_client_frame_rejected(self):
        self.b.sendall(encode_frame(OP_TEXT, b"no mask"))
        self.assertIsNone(self.ws.recv())


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        cls.env = EnvPatch(ANTHROPIC_API_KEY=None)
        cls.env.__enter__()
        cls.agent = Agent(temp_home(f"[agent]\nport = {cls.port}\nannounce_start = false\n"), start_hotkeys=False)
        fake_screen(cls.agent.platform)
        cls.agent.start()

    @classmethod
    def tearDownClass(cls):
        cls.agent.close()
        cls.env.__exit__()

    def request(self, method, path, body=None, headers=None, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.putrequest(method, path, skip_host=True)
        conn.putheader("Host", host or f"127.0.0.1:{self.port}")
        data = json.dumps(body).encode() if body is not None else b""
        for k, v in (headers or {}).items():
            conn.putheader(k, v)
        if body is not None:
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(len(data)))
        conn.endheaders(data if body is not None else None)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        return resp.status, raw

    def test_health_is_public(self):
        status, raw = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["name"], "ally")

    def test_dns_rebinding_blocked(self):
        status, _ = self.request("GET", "/", host=f"evil.example:{self.port}")
        self.assertEqual(status, 403)

    def test_api_needs_token_and_good_origin(self):
        tok = {"X-Ally-Token": self.agent.token}
        self.assertEqual(self.request("POST", "/api/command", {"text": "help"})[0], 401)
        self.assertEqual(self.request("POST", "/api/command", {"text": "help"},
                                      {**tok, "Origin": "https://evil.example"})[0], 403)
        status, raw = self.request("POST", "/api/command", {"text": "help"},
                                   {**tok, "Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(status, 200)
        self.assertIn("You can say", json.loads(raw)["text"] + "You can say")

    def test_dashboard_has_token_and_csp(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", "/")
        resp = conn.getresponse()
        html = resp.read().decode()
        self.assertIn(self.agent.token, html)
        self.assertIn("frame-ancestors 'none'", resp.getheader("Content-Security-Policy"))
        conn.close()

    def test_config_api_validates(self):
        tok = {"X-Ally-Token": self.agent.token}
        status, raw = self.request("POST", "/api/config", {"text": "[agent]\nport = 5\n"}, tok)
        self.assertEqual(status, 400)
        self.assertTrue(json.loads(raw)["errors"])

    def ws_connect(self, origin):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        s.sendall((f"GET /ws HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                   f"Origin: {origin}\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            head += s.recv(1)
        return s, key, head.decode()

    def test_websocket_rejects_web_pages(self):
        s, _key, head = self.ws_connect("https://evil.example")
        self.assertIn(" 403 ", head.splitlines()[0])
        s.close()

    def test_websocket_extension_round_trip(self):
        s, key, head = self.ws_connect("chrome-extension://abcdefghijklmnop")
        self.assertIn(" 101 ", head.splitlines()[0])
        expect = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        self.assertIn(f"Sec-WebSocket-Accept: {expect}", head)

        def send(obj):
            s.sendall(encode_frame(OP_TEXT, json.dumps(obj).encode(), mask=os.urandom(4)))

        def recv_json():
            while True:
                op, data = read_frame(s)
                if op == OP_TEXT:
                    return json.loads(data)

        send({"type": "hello", "version": "test", "focused": True, "tab": {"title": "T", "url": "https://a.b/c"}})
        send({"type": "ping"})
        # The agent pushes the profile on hello; answer it, then expect our pong.
        msgs = [recv_json(), recv_json()]
        kinds = sorted(m["type"] for m in msgs)
        self.assertEqual(kinds, ["call", "pong"])
        call = next(m for m in msgs if m["type"] == "call")
        self.assertEqual(call["action"], "setProfile")
        send({"type": "result", "id": call["id"], "ok": True, "result": True})
        self.assertTrue(self.agent.bridge.connected)
        self.assertTrue(self.agent.bridge.chrome_focused)

        box = {}
        th = threading.Thread(target=lambda: box.setdefault("v", self.agent.bridge.call("getTab", {})))
        th.start()
        call = recv_json()
        self.assertEqual(call["action"], "getTab")
        send({"type": "result", "id": call["id"], "ok": True, "result": {"zoom": 1.25}})
        th.join(5)
        self.assertEqual(box["v"], {"zoom": 1.25})

        # Errors from the browser come back as ToolError text.
        out = {}
        th = threading.Thread(target=lambda: out.setdefault("o", self.agent.call_tool("set_page_zoom", {"zoom": 2})))
        th.start()
        call = recv_json()
        send({"type": "result", "id": call["id"], "ok": False, "error": "Chrome doesn't let extensions change this page"})
        th.join(5)
        self.assertIn("Chrome doesn't let", out["o"].error)
        s.close()
        self.assertTrue(wait_until(lambda: not self.agent.bridge.connected, 5))


def wait_until(fn, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.05)
    return False


if __name__ == "__main__":
    unittest.main()
