import io
import json
import random
import threading
import unittest
import urllib.error
from types import SimpleNamespace
from unittest.mock import Mock, patch

from helpers import ROOT
from ally.config import DEFAULTS, merge, validate
from ally.contracts import EventBus, Risk
from ally.risk.screenshare import ScreenShare
from ally.risk.wire import RiskWire, attach

try:
    from PIL import Image
except ImportError:
    Image = None

ROOM = "private-room-0123456789"
TOKEN = "private-identity-token"
BASE = "https://calls.example/ally/"
RISK = Risk("scam_popup", "high", "A fake warning.")


class Response(io.BytesIO):
    pass


class Clock:
    def __init__(self):
        self.now = 0.0
        self.waits = []
        self.stopped = False

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def wait(self, seconds):
        self.waits.append(seconds)
        self.now += seconds
        return self.stopped


def rejection(message):
    return urllib.error.HTTPError("https://db.example/" + ROOM, 530, message, {},
                                  io.BytesIO(message.encode()))


@unittest.skipUnless(Image, "Pillow is optional; install it to verify screenshot encoding")
class SenderTests(unittest.TestCase):
    def png(self, noisy=False):
        size = (2400, 1500) if noisy else (1600, 900)
        image = (Image.frombytes("RGB", size, random.Random(12).randbytes(size[0] * size[1] * 3))
                 if noisy else Image.new("RGB", size, "white"))
        out = io.BytesIO()
        image.save(out, "PNG")
        return out.getvalue()

    def run_sender(self, handler=None, duration=3):
        clock = Clock()
        capture = Mock(return_value=self.png())
        share = ScreenShare("https://db.example", "call database", ROOM, capture)
        share._stop = clock
        share.MAX_SECONDS = duration
        requests = []

        def opener(request, timeout):
            self.assertEqual(timeout, 5)
            requests.append((clock.now, request))
            if request.full_url.endswith("/v1/identity"):
                return Response(json.dumps({"token": TOKEN}).encode())
            self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)
            if handler:
                handler(request, clock, share)
            return Response(b"")

        with patch("urllib.request.urlopen", opener), patch("ally.risk.screenshare.time.monotonic", lambda: clock.now):
            with self.assertNoLogs("ally", level="DEBUG"):
                share._run()
        return share, capture, clock, requests

    def test_rate_identity_once_and_json_arguments(self):
        share, capture, clock, requests = self.run_sender()
        frames = [(at, req) for at, req in requests if req.full_url.endswith("/send_screen_frame")]
        self.assertEqual([at for at, _ in frames], [0, 1, 2])
        self.assertEqual(capture.call_count, 3)
        self.assertEqual(len([r for _, r in requests if r.full_url.endswith("/v1/identity")]), 1)
        for seq, (_, req) in enumerate(frames):
            self.assertIn("/call%20database/call/", req.full_url)
            room, actual_seq, jpeg, width, height = json.loads(req.data)
            self.assertEqual((room, actual_seq, width, height), (ROOM, seq, 1280, 720))
            self.assertLess(len(jpeg), 150000)
            self.assertEqual(bytes(jpeg[:2]), b"\xff\xd8")
        self.assertTrue(share._stop.is_set())
        self.assertEqual(clock.waits, [1, 1, 1])

    def test_noisy_frames_resize_and_stay_under_size_cap(self):
        share = ScreenShare("https://db.example", "db", ROOM, lambda: self.png(noisy=True))
        jpeg, width, height = share._frame()
        self.assertLessEqual(len(jpeg), 150000)
        self.assertLessEqual(width, 1280)
        with Image.open(io.BytesIO(jpeg)) as decoded:
            self.assertEqual(decoded.size, (width, height))

    def test_rejection_ends_sender_without_leaking_private_response(self):
        def reject(req, clock, share):
            if req.full_url.endswith("/send_screen_frame"):
                raise rejection("Call ended " + TOKEN + ROOM)
        _, capture, _, requests = self.run_sender(reject)
        self.assertEqual(capture.call_count, 1)
        self.assertEqual(len(requests), 3)

    def test_paused_watcher_takes_no_screenshots_and_can_resume(self):
        def paused(req, clock, share):
            if req.full_url.endswith("/screen_share_ready") and clock.now < 2:
                raise rejection("Screen sharing paused")
        _, capture, _, requests = self.run_sender(paused, duration=4)
        self.assertEqual(capture.call_count, 2)
        self.assertEqual([at for at, r in requests if r.full_url.endswith("/send_screen_frame")], [2, 3])

    def test_room_creation_race_waits_before_capture_and_times_out(self):
        def missing(req, clock, share):
            raise rejection("This call is not available")
        _, capture, clock, _ = self.run_sender(missing, duration=60)
        self.assertEqual(capture.call_count, 0)
        self.assertEqual(clock.now, 30)

    def test_stop_after_capture_drops_frame_and_returns(self):
        share = ScreenShare("https://db.example", "db", ROOM, lambda: b"")
        share._capture = lambda: (share.stop(), self.png())[1]
        with patch("urllib.request.urlopen", return_value=Response(json.dumps({"token": TOKEN}).encode())):
            # Use a separate response for each request.
            with patch.object(share, "_post", side_effect=[json.dumps({"token": TOKEN}).encode(), b""]) as post:
                share._run()
        self.assertEqual(post.call_count, 2, "no send after stop during capture")

    def test_start_is_daemon_and_stop_interrupts_wait(self):
        sent = threading.Event()
        share = ScreenShare("https://db.example", "db", ROOM, lambda: self.png())

        def post(path, args, token=""):
            if path.endswith("/v1/identity"):
                return json.dumps({"token": TOKEN}).encode()
            if path.endswith("/send_screen_frame"):
                sent.set()
            return b""

        with patch.object(share, "_post", post):
            share.start()
            share.start()
            self.assertTrue(sent.wait(3))
            self.assertTrue(share._thread.daemon)
            share.stop()
            share._thread.join(1)
            self.assertFalse(share._thread.is_alive())


class WiringTests(unittest.TestCase):
    def wire(self, service="spacecall", enabled=True, opened=True):
        wire = RiskWire(EventBus(), snapshot_fn=lambda: [],
                        contact=("Priya", lambda *args: True, "https://meet.google.com/abc-defg-hij",
                                 False, service, BASE),
                        call=lambda *args: {"opened": True} if opened else (_ for _ in ()).throw(RuntimeError()),
                        screen_capture=lambda: b"screen", screen_share=enabled)
        self.addCleanup(wire.stop)
        return wire

    def test_only_successful_enabled_spacecall_taps_start_sharing(self):
        for service, enabled, opened in [("meet", True, True), ("spacecall", False, True),
                                         ("spacecall", True, False), ("spacecall", True, True)]:
            with self.subTest(service=service, enabled=enabled, opened=opened):
                wire = self.wire(service, enabled, opened)
                with patch("ally.risk.wire.ScreenShare") as factory:
                    wire._tell(RISK)
                    self.assertEqual(factory.call_count, int(service == "spacecall" and enabled and opened))
                    if factory.called:
                        factory.return_value.start.assert_called_once()

    def test_second_call_ends_old_sender_and_window_close_and_wire_stop_end_current(self):
        wire = self.wire()
        first, second, third = Mock(), Mock(), Mock()
        with patch("ally.risk.wire.ScreenShare", side_effect=[first, second, third]):
            wire._tell(RISK)
            wire._tell(RISK)
            first.stop.assert_called_once()
            wire.on_call_ended({"service": "spacecall"})
            second.stop.assert_called_once()
            wire._tell(RISK)
            wire.stop()
            third.stop.assert_called_once()

    def test_disabled_link_tells_client_that_sharing_is_off(self):
        wire = self.wire(enabled=False)
        self.assertIn("screen=off", wire._call_urls(RISK)[0])

    def test_config_key_validates_and_attach_reuses_platform_capture(self):
        self.assertTrue(DEFAULTS["risk"]["screen_share"])
        self.assertEqual(validate(merge({"risk": {"screen_share": False}})), ([], []))
        self.assertTrue(validate(merge({"risk": {"screen_share": "yes"}}))[0])
        agent = SimpleNamespace(bus=EventBus(), config=SimpleNamespace(data={"risk": {"screen_share": False}}),
                                platform=SimpleNamespace(screenshot_png=Mock(return_value=b"png")))
        with patch("ally.risk.wire._make_guardian", return_value=(None, None)), patch.object(RiskWire, "start"):
            wire = attach(agent)
        self.assertFalse(wire.screen_share)
        self.assertEqual(wire.screen_capture(), b"png")
        wire.stop()

    def test_sender_errors_and_thread_start_failure_never_escape_or_log_secrets(self):
        share = ScreenShare("https://db.example", "db", ROOM, lambda: b"")
        with self.assertNoLogs("ally", level="DEBUG"):
            with patch.object(share, "_post", side_effect=RuntimeError(TOKEN + ROOM)):
                share._run()
            with patch("threading.Thread", side_effect=RuntimeError(TOKEN + ROOM)):
                ScreenShare("https://db.example", "db", ROOM, lambda: b"").start()


if __name__ == "__main__":
    unittest.main()
