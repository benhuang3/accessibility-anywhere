"""The stdlib WebSocket client (ally/ws_client.py) and Cartesia streaming STT (ally/stt_stream.py)."""

import os
import sys
import threading
import time
import unittest

from helpers import EnvPatch

from ally import speech, stt_stream, ws_client
from ally.registry import ToolError
from ally.websocket import OP_BIN, OP_CLOSE, OP_PONG, OP_TEXT

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fake_cartesia_ws import KEY, FakeCartesiaWS  # noqa: E402

BLOCK = b"\x00\x01" * 1600  # 100 ms of 16 kHz int16


class FakeTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeCartesiaWS()
        self.addCleanup(self.fake.close)


class ClientTests(FakeTest):
    def setUp(self):
        super().setUp()
        self.fake.set(mode="echo")

    def connect(self, **kw):
        ws = ws_client.connect(self.fake.ws_url + "/echo", {"X-Test": "yes"}, timeout=5, **kw)
        self.addCleanup(ws.abort)
        return ws

    def test_handshake_sends_headers_and_echoes(self):
        ws = self.connect()
        req = self.fake.requests[0]
        self.assertEqual(req["path"], "/echo")
        self.assertEqual(req["headers"]["x-test"], "yes")
        self.assertEqual(req["headers"]["upgrade"].lower(), "websocket")
        ws.send("hello")
        self.assertEqual(ws.recv(timeout=2), "hello")
        ws.send(b"\x00\xff")
        self.assertEqual(ws.recv(timeout=2), b"\x00\xff")

    def test_large_message(self):
        ws = self.connect()
        big = os.urandom(70000)  # 64-bit length form
        ws.send(big)
        self.assertEqual(ws.recv(timeout=5), big)

    def test_accept_key_mismatch(self):
        self.fake.set(mode="bad_accept")
        with self.assertRaises(ws_client.WSHandshakeError) as cm:
            ws_client.connect(self.fake.ws_url + "/x", timeout=3)
        self.assertIn("Accept", str(cm.exception))

    def test_http_refusal_has_status(self):
        self.fake.set(mode="reject_auth")
        with self.assertRaises(ws_client.WSHandshakeError) as cm:
            ws_client.connect(self.fake.ws_url + "/x", timeout=3)
        self.assertEqual(cm.exception.status, 401)
        self.assertIn("invalid API key", cm.exception.body)

    def test_unreachable(self):
        port = self.fake.port
        self.fake.close()
        with self.assertRaises(ws_client.WSError):
            ws_client.connect(f"ws://127.0.0.1:{port}/x", timeout=2)

    def test_bad_scheme(self):
        with self.assertRaises(ws_client.WSError):
            ws_client.connect("http://127.0.0.1:1/x")

    def test_client_frames_are_masked(self):
        ws = self.connect()
        ws.send("abc")
        ws.recv(timeout=2)
        self.assertTrue(self.fake.frames)
        self.assertTrue(all(masked for masked, _, _ in self.fake.frames))
        self.assertEqual(self.fake.frames[0][1:], (OP_TEXT, b"abc"))

    def test_fragmented_message_with_ping_in_between(self):
        ws = self.connect()
        ws.send("fragment")
        self.assertEqual(ws.recv(timeout=2), "one two three")
        time.sleep(0.1)
        self.assertIn((True, OP_PONG, b"mid"), self.fake.frames)

    def test_ping_gets_pong(self):
        ws = self.connect()
        ws.send("ping")
        self.assertEqual(ws.recv(timeout=2), "pinged")
        time.sleep(0.1)
        self.assertIn((True, OP_PONG, b"are-you-there"), self.fake.frames)

    def test_timeout_keeps_connection_usable(self):
        ws = self.connect()
        with self.assertRaises(ws_client.WSTimeout):
            ws.recv(timeout=0.2)
        ws.send("still here")
        self.assertEqual(ws.recv(timeout=2), "still here")

    def test_client_close(self):
        ws = self.connect()
        ws.close(1000, "done")
        self.assertTrue(ws.closed)
        self.assertEqual(ws.close_code, 1000)
        self.assertEqual(self.fake.frames[-1][1], OP_CLOSE)
        with self.assertRaises(ws_client.WSClosed):
            ws.send("late")

    def test_server_close(self):
        ws = self.connect()
        ws.send("close-me")
        with self.assertRaises(ws_client.WSClosed) as cm:
            ws.recv(timeout=2)
        self.assertEqual((cm.exception.code, cm.exception.reason), (1001, "bye"))
        time.sleep(0.1)
        self.assertEqual(self.fake.frames[-1][1], OP_CLOSE)  # we answered the close

    def test_abort_wakes_a_blocked_reader(self):
        ws = self.connect()
        got = []

        def reader():
            try:
                ws.recv()
            except ws_client.WSError as e:
                got.append(e)

        t = threading.Thread(target=reader)
        t.start()
        time.sleep(0.1)
        ws.abort()
        t.join(2)
        self.assertFalse(t.is_alive())
        self.assertIsInstance(got[0], ws_client.WSClosed)


class StreamTests(FakeTest):
    def setUp(self):
        super().setUp()
        self.env = EnvPatch(CARTESIA_API_KEY=KEY)
        self.env.__enter__()
        self.addCleanup(self.env.__exit__, None, None, None)
        old = speech.CARTESIA_URL
        speech.CARTESIA_URL = self.fake.url
        self.addCleanup(setattr, speech, "CARTESIA_URL", old)

    def test_url(self):
        url = stt_stream.stream_url("https://api.cartesia.ai", turn={"end_timeout_ms": 4500}, keyterms=["Ally bot"])
        self.assertTrue(url.startswith("wss://api.cartesia.ai/stt/turns/websocket?model=ink-2&encoding=pcm_s16le"
                                       "&sample_rate=16000&cartesia_version="))
        self.assertIn("turn_end_timeout_ms=4500", url)
        self.assertIn("keyterm=Ally%20bot", url)

    def test_ok_returns_final_and_sends_audio_and_auth(self):
        events = []
        stt = stt_stream.StreamingSTT(on_event=events.append).open()
        self.addCleanup(stt.close)
        for _ in range(3):
            stt.feed(BLOCK)
        self.assertEqual(stt.finish(timeout=5), "hello there")
        req = self.fake.requests[0]
        self.assertEqual(req["headers"]["x-api-key"], KEY)
        self.assertEqual(req["query"]["model"], ["ink-2"])
        self.assertEqual(req["query"]["sample_rate"], ["16000"])
        self.assertEqual(req["query"]["cartesia_version"], [speech.CARTESIA_VERSION])
        self.assertEqual(self.fake.audio_bytes, 3 * len(BLOCK))
        self.assertTrue(all(m for m, op, _ in self.fake.frames if op == OP_BIN))
        kinds = [e["type"] for e in events]
        self.assertEqual(kinds, ["connected", "turn.start", "turn.update", "turn.eager_end", "turn.end"])
        self.assertEqual(stt.request_id, "req-1")

    def test_eager_is_separate_from_final(self):
        self.fake.set(mode="script", script=[
            {"at": "audio", "event": {"type": "turn.start"}},
            {"at": "audio", "event": {"type": "turn.update", "transcript": "call my"}},
            {"at": "audio", "event": {"type": "turn.eager_end", "transcript": "call my"}},
            {"at": "audio", "event": {"type": "turn.resume"}},
            {"at": "close", "event": {"type": "turn.update", "transcript": "call my daughter"}},
            {"at": "close", "event": {"type": "turn.end", "transcript": "call my daughter"}},
        ])
        stt = stt_stream.StreamingSTT().open()
        self.addCleanup(stt.close)
        stt.feed(BLOCK)
        deadline = time.time() + 2
        seen = []
        while time.time() < deadline and "turn.resume" not in seen:
            seen.append(stt.events.get(timeout=2)["type"])
        self.assertIn("turn.eager_end", seen)
        self.assertEqual(stt.finals, [])  # eager_end is not a final
        self.assertEqual(stt.eager, "")  # cleared by turn.resume
        self.assertEqual(stt.finish(timeout=5), "call my daughter")

    def test_two_turns_join_and_wait_turn(self):
        self.fake.set(mode="script", script=[
            {"at": "audio", "event": {"type": "turn.start"}},
            {"at": "audio", "event": {"type": "turn.eager_end", "transcript": "hello"}},
            {"at": "audio", "event": {"type": "turn.end", "transcript": "hello"}},
            {"at": "close", "event": {"type": "turn.start"}},
            {"at": "close", "event": {"type": "turn.end", "transcript": "how are you"}},
        ])
        stt = stt_stream.StreamingSTT().open()
        self.addCleanup(stt.close)
        stt.feed(BLOCK)
        self.assertEqual(stt.wait_turn(timeout=2), "hello")
        self.assertEqual(stt.finish(timeout=5), "hello how are you")

    def test_partial_kept_if_stream_ends_mid_turn(self):
        self.fake.set(mode="script", script=[
            {"at": "close", "event": {"type": "turn.start"}},
            {"at": "close", "event": {"type": "turn.update", "transcript": "open the"}},
        ])
        self.assertEqual(stt_stream.transcribe_blocks([BLOCK, BLOCK], timeout=5), "open the")

    def test_transcribe_blocks(self):
        self.fake.set(text="what time is it")
        self.assertEqual(stt_stream.transcribe_blocks([BLOCK] * 5, timeout=5), "what time is it")

    def test_timeout(self):
        self.fake.set(mode="slow", delay=2.0)
        stt = stt_stream.StreamingSTT().open()
        self.addCleanup(stt.close)
        stt.feed(BLOCK)
        t0 = time.monotonic()
        with self.assertRaises(stt_stream.StreamTimeout) as cm:
            stt.finish(timeout=0.3)
        self.assertLess(time.monotonic() - t0, 1.5)
        self.assertIsInstance(cm.exception, ToolError)

    def test_dropped_connection(self):
        self.fake.set(mode="drop")
        stt = stt_stream.StreamingSTT().open()
        self.addCleanup(stt.close)
        stt.feed(BLOCK)
        with self.assertRaises(stt_stream.StreamClosed) as cm:
            stt.finish(timeout=3)
        self.assertIsInstance(cm.exception, ToolError)
        with self.assertRaises(ToolError):
            stt.feed(BLOCK)

    def test_drop_mid_script(self):
        self.fake.set(mode="script", script=[
            {"at": "audio", "event": {"type": "turn.start"}},
            {"at": "audio", "event": "drop"},
        ])
        with self.assertRaises(stt_stream.StreamClosed):
            stt_stream.transcribe_blocks([BLOCK], timeout=3)

    def test_auth_rejected(self):
        with EnvPatch(CARTESIA_API_KEY="wrong"):
            with self.assertRaises(stt_stream.StreamAuthError) as cm:
                stt_stream.StreamingSTT().open()
        self.assertEqual(cm.exception.status, 401)
        self.assertIsInstance(cm.exception, ToolError)

    def test_missing_key(self):
        with EnvPatch(CARTESIA_API_KEY=None):
            with self.assertRaises(stt_stream.StreamAuthError):
                stt_stream.StreamingSTT().open()

    def test_error_message(self):
        self.fake.set(mode="error")
        with self.assertRaises(stt_stream.StreamError) as cm:
            stt_stream.transcribe_blocks([BLOCK, BLOCK], timeout=3)
        self.assertEqual(cm.exception.status, 400)
        self.assertIn("model not found", str(cm.exception))

    def test_error_message_401_is_auth(self):
        self.fake.set(mode="error", error={"status_code": 401, "title": "Unauthorized", "message": "bad key"})
        with self.assertRaises(stt_stream.StreamAuthError):
            stt_stream.transcribe_blocks([BLOCK, BLOCK], timeout=3)

    def test_unreachable(self):
        speech.CARTESIA_URL = "http://127.0.0.1:1"
        with self.assertRaises(stt_stream.StreamError) as cm:
            stt_stream.StreamingSTT(connect_timeout=2).open()
        self.assertEqual(cm.exception.status, 0)

    def test_close_without_finish(self):
        stt = stt_stream.StreamingSTT().open()
        stt.feed(BLOCK)
        stt.close()
        self.assertTrue(stt.done)
        self.assertIsNone(stt.error)


if __name__ == "__main__":
    unittest.main()
