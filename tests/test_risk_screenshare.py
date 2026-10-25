import http.client
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


def frame(value=0):
    return b"\xff\xd8\xff\xd9", 1280, 720, bytes([value]) * (64 * 36)


class TransportTests(unittest.TestCase):
    def test_one_persistent_connection_for_identity_and_n_frames(self):
        share = ScreenShare("https://db.example/prefix/", "call database", ROOM, Mock())
        connection = Mock()
        connection.getresponse.return_value = SimpleNamespace(status=200, read=Mock(return_value=b"{}"))
        with patch("ally.risk.screenshare.http.client.HTTPSConnection", return_value=connection) as factory:
            share._post("/v1/identity", [])
            for seq in range(5):
                share._post("/frames", [ROOM, seq], TOKEN)
        factory.assert_called_once_with("db.example", None, timeout=5)
        self.assertEqual(connection.request.call_count, 6)
        method, path = connection.request.call_args.args
        self.assertEqual((method, path), ("POST", "/prefix/frames"))
        self.assertEqual(connection.request.call_args.kwargs["headers"]["Authorization"], "Bearer " + TOKEN)
        self.assertEqual(json.loads(connection.request.call_args.kwargs["body"]), [ROOM, 4])
        share._close()
        connection.close.assert_called_once()

    def test_broken_keepalive_reconnects_once(self):
        share = ScreenShare("https://db.example", "db", ROOM, Mock())
        broken, healthy = Mock(), Mock()
        broken.request.side_effect = http.client.RemoteDisconnected()
        healthy.getresponse.return_value = SimpleNamespace(status=200, read=Mock(return_value=b"ok"))
        with patch("ally.risk.screenshare.http.client.HTTPSConnection", side_effect=[broken, healthy]) as factory:
            self.assertEqual(share._post("/frames", [], TOKEN), b"ok")
        self.assertEqual(factory.call_count, 2)
        broken.close.assert_called_once()
        share._close()

    def test_rejection_is_not_retried(self):
        share = ScreenShare("https://db.example", "db", ROOM, Mock())
        connection = Mock()
        connection.getresponse.return_value = SimpleNamespace(
            status=530, read=Mock(return_value=b"Call ended " + TOKEN.encode() + ROOM.encode()))
        with patch("ally.risk.screenshare.http.client.HTTPSConnection", return_value=connection) as factory:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                share._post("/frames", [], TOKEN)
            self.assertEqual(share._rejection(caught.exception), "ended")
        self.assertEqual(factory.call_count, 1)

    def test_stopped_sender_never_opens_a_connection(self):
        share = ScreenShare("https://db.example", "db", ROOM, Mock())
        share.stop()
        with patch("ally.risk.screenshare.http.client.HTTPSConnection") as factory:
            with self.assertRaises(OSError):
                share._post("/frames", [], TOKEN)
        factory.assert_not_called()

    def test_reconnect_cannot_send_after_deadline(self):
        share = ScreenShare("https://db.example", "db", ROOM, Mock())
        share._deadline = 1
        clock = Clock()
        broken = Mock()

        def fail(*args, **kwargs):
            clock.now = 1
            raise http.client.RemoteDisconnected()

        broken.request.side_effect = fail
        with patch("ally.risk.screenshare.http.client.HTTPSConnection", return_value=broken) as factory, \
                patch("ally.risk.screenshare.time.monotonic", lambda: clock.now):
            with self.assertRaises(OSError):
                share._post("/frames", [], TOKEN)
        factory.assert_called_once()


class SenderTests(unittest.TestCase):
    def share(self):
        return ScreenShare("https://db.example", "call database", ROOM, Mock())

    def test_unchanged_frames_skip_and_keyframes_every_five_seconds(self):
        share = self.share()
        clock = Clock()
        times = []
        with patch.object(share, "_post", side_effect=lambda *args: times.append(clock.now)), \
                patch("ally.risk.screenshare.time.monotonic", lambda: clock.now):
            for quarter in range(41):
                clock.now = quarter / 4
                share._send_frame(frame(), TOKEN)
        self.assertEqual(times, [0, 5, 10])
        self.assertEqual(share._seq, 3)

    def test_change_detection_compares_last_sent_not_last_captured(self):
        share = self.share()
        with patch.object(share, "_post") as post, patch("ally.risk.screenshare.time.monotonic", return_value=0):
            self.assertTrue(share._send_frame(frame(0), TOKEN))
            self.assertFalse(share._send_frame(frame(1), TOKEN))
            self.assertTrue(share._send_frame(frame(2), TOKEN))
        self.assertEqual(post.call_count, 2)
        self.assertEqual(share._last_fingerprint, frame(2)[3])

    def test_failed_send_does_not_advance_fingerprint_or_keyframe_clock(self):
        share = self.share()
        with patch.object(share, "_post", side_effect=rejection("Screen sharing paused")):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                share._send_frame(frame(), TOKEN)
            caught.exception.close()
        self.assertIsNone(share._last_fingerprint)
        self.assertEqual(share._last_sent, float("-inf"))
        self.assertEqual(share._seq, 0)

    def test_slow_posts_reduce_quality_then_width_before_fps(self):
        share = self.share()
        clock = Clock()

        def slow(*args):
            clock.now += 0.401

        with patch.object(share, "_post", slow), patch("ally.risk.screenshare.time.monotonic", lambda: clock.now):
            share._send_frame(frame(0), TOKEN)
            self.assertEqual((share._quality, share._width, share._interval), (45, 1280, 0.25))
            share._send_frame(frame(10), TOKEN)
            self.assertEqual((share._quality, share._width, share._interval), (45, 960, 0.25))
            share._send_frame(frame(20), TOKEN)
            self.assertGreater(share._interval, 0.25)

    def test_ten_second_budget_counts_json_bytes_and_reserves_camera_bandwidth(self):
        share = self.share()
        clock = Clock()
        large = b"\xff" * 150_000
        with patch.object(share, "_post"), patch("ally.risk.screenshare.time.monotonic", lambda: clock.now):
            for index in range(13):
                clock.now = index / 4
                share._send_frame((large, 1280, 720, frame(index * 2)[3]), TOKEN)
            self.assertEqual(share._quality, 45)
            clock.now = 14
            share._send_frame(frame(100), TOKEN)
        self.assertEqual(len(share._traffic), 1)

    def test_pipeline_overlaps_capture_with_post_and_keeps_only_latest_waiting_frame(self):
        share = self.share()
        share._interval = 0.002
        fourth = threading.Event()
        captured, sent, waiting = [], [], []

        def capture():
            value = len(captured) + 1
            captured.append(value)
            if value >= 4:
                fourth.set()
            return frame(value * 2)

        def post(path, args, token=""):
            if path.endswith("/identity"):
                return json.dumps({"token": TOKEN}).encode()
            if path.endswith("/send_screen_frame"):
                sent.append(args[3])
                if len(sent) == 1:
                    self.assertTrue(fourth.wait(2), "capture must run while a post is blocked")
                    with share._condition:
                        waiting.append(share._waiting[3][0])
                else:
                    share.stop()
            return b""

        # Use width as an observable frame number without inspecting private JPEG data.
        def numbered_capture():
            jpeg, _, height, fingerprint = capture()
            return jpeg, len(captured), height, fingerprint

        with patch.object(share, "_frame", numbered_capture), patch.object(share, "_post", post):
            share.start()
            share._thread.join(3)
        self.assertFalse(share._thread.is_alive())
        self.assertGreaterEqual(len(captured), 4)
        self.assertEqual(len(waiting), 1)
        self.assertGreaterEqual(waiting[0], 8)
        self.assertEqual(sent[0], 1)
        self.assertGreaterEqual(sent[1], 4, "old queued frames must be replaced")
        self.assertIsNone(share._waiting)

    def test_paused_watcher_never_captures_and_resumes(self):
        share = self.share()
        clock = Clock()
        share._stop = clock
        reads = []

        def post(path, args, token=""):
            if path.endswith("/identity"):
                return json.dumps({"token": TOKEN}).encode()
            if path.endswith("/screen_share_ready"):
                reads.append(clock.now)
                if clock.now < 2:
                    raise rejection("Screen sharing paused")
            if path.endswith("/send_screen_frame"):
                share.stop()
            return b""

        with patch.object(share, "_frame", return_value=frame()) as capture, \
                patch.object(share, "_post", post), \
                patch("ally.risk.screenshare.time.monotonic", lambda: clock.now):
            # A controlled producer avoids advancing a fake clock in two threads.
            def produce(deadline):
                self.assertEqual(clock.now, 2)
                with share._condition:
                    share._waiting = capture()
                    share._condition.notify_all()
            with patch.object(share, "_produce", produce):
                share._run()
        self.assertEqual(reads[:3], [0, 1, 2])
        capture.assert_called_once()

    def test_missing_room_waits_thirty_seconds_before_any_capture(self):
        share = self.share()
        clock = Clock()
        share._stop = clock

        def post(path, args, token=""):
            if path.endswith("/identity"):
                return json.dumps({"token": TOKEN}).encode()
            raise rejection("This call is not available")

        with patch.object(share, "_post", post), patch.object(share, "_frame") as capture, \
                patch("ally.risk.screenshare.time.monotonic", lambda: clock.now):
            share._run()
        self.assertEqual(clock.now, 30)
        capture.assert_not_called()

    def test_stop_during_capture_sends_nothing(self):
        share = self.share()

        def capture():
            share.stop()
            return frame()

        with patch.object(share, "_frame", capture), patch.object(share, "_post", side_effect=[
                json.dumps({"token": TOKEN}).encode(), b""]) as post:
            share.start()
            share._thread.join(2)
        self.assertFalse(share._thread.is_alive())
        self.assertEqual(post.call_count, 2)

    def test_rejection_clears_pipeline_and_stops_sender_without_logging(self):
        share = self.share()

        def post(path, args, token=""):
            if path.endswith("/identity"):
                return json.dumps({"token": TOKEN}).encode()
            if path.endswith("/send_screen_frame"):
                raise rejection("Call ended " + TOKEN + ROOM)
            return b""

        with patch.object(share, "_frame", return_value=frame()), patch.object(share, "_post", post), \
                self.assertNoLogs("ally", level="DEBUG"):
            share.start()
            share.start()
            share._thread.join(2)
        self.assertTrue(share._thread.daemon)
        self.assertFalse(share._thread.is_alive())
        self.assertTrue(share._stop.is_set())
        self.assertIsNone(share._waiting)

    def test_deadline_stops_capture_and_sender(self):
        share = self.share()
        share.MAX_SECONDS = 0.06
        share._interval = 0.005

        def post(path, args, token=""):
            return json.dumps({"token": TOKEN}).encode() if path.endswith("/identity") else b""

        with patch.object(share, "_frame", return_value=frame()), patch.object(share, "_post", post):
            share.start()
            share._thread.join(2)
        self.assertFalse(share._thread.is_alive())
        self.assertTrue(share._stop.is_set())
        self.assertEqual(ScreenShare.MAX_SECONDS, 1800)

    def test_stop_interrupts_idle_capture_and_network_wait(self):
        share = self.share()
        sent = threading.Event()

        def post(path, args, token=""):
            if path.endswith("/identity"):
                return json.dumps({"token": TOKEN}).encode()
            if path.endswith("/send_screen_frame"):
                sent.set()
            return b""

        with patch.object(share, "_frame", return_value=frame()), patch.object(share, "_post", post):
            share.start()
            self.assertTrue(sent.wait(2))
            share.stop()
            share._thread.join(1)
        self.assertFalse(share._thread.is_alive())

    def test_send_rejection_pauses_capture_and_same_room_can_resume(self):
        share = self.share()
        share._interval = 0.005
        paused, resume = threading.Event(), threading.Event()
        sends = []

        def post(path, args, token=""):
            if path.endswith("/identity"):
                return json.dumps({"token": TOKEN}).encode()
            if path.endswith("/screen_share_ready") and sends and not resume.is_set():
                paused.set()
                raise rejection("Screen sharing paused")
            if path.endswith("/send_screen_frame"):
                sends.append(args[1])
                if len(sends) == 1:
                    raise rejection("Screen sharing paused")
                share.stop()
            return b""

        with patch.object(share, "_frame", return_value=frame()) as capture, patch.object(share, "_post", post):
            share.start()
            self.assertTrue(paused.wait(2))
            count = capture.call_count
            self.assertIsNone(share._waiting)
            self.assertFalse(share._enabled)
            threading.Event().wait(0.03)
            self.assertEqual(capture.call_count, count, "paused watcher must take no screenshots")
            resume.set()
            share._thread.join(2)
        self.assertFalse(share._thread.is_alive())
        self.assertEqual(sends, [0, 0], "a rejected frame must not advance sequence")

    def test_capture_from_before_pause_is_discarded_even_if_resumed_during_encoding(self):
        share = self.share()
        share._set_enabled(True)
        captured = threading.Event()

        def capture():
            share._set_enabled(False)
            share._set_enabled(True)
            return frame()

        # End after this single iteration, observing the slot before stop clears it.
        def wait(seconds):
            self.assertIsNone(share._waiting)
            captured.set()
            share.stop()

        with patch.object(share, "_frame", capture), patch.object(share._stop, "wait", wait):
            share._produce(float("inf"))
        self.assertTrue(captured.is_set())

    def test_capture_thread_start_failure_never_escapes_or_logs_private_details(self):
        share = self.share()
        with patch.object(share, "_post", side_effect=[json.dumps({"token": TOKEN}).encode(), b""]), \
                patch("threading.Thread.start", side_effect=RuntimeError(TOKEN + ROOM)), \
                self.assertNoLogs("ally", level="DEBUG"):
            share._run()
        self.assertTrue(share._stop.is_set())

    def test_capture_failure_stops_both_threads_quietly(self):
        share = self.share()

        def post(path, args, token=""):
            return json.dumps({"token": TOKEN}).encode() if path.endswith("/identity") else b""

        with patch.object(share, "_post", post), \
                patch.object(share, "_frame", side_effect=RuntimeError(TOKEN + ROOM)), \
                self.assertNoLogs("ally", level="DEBUG"):
            share.start()
            share._thread.join(2)
        self.assertFalse(share._thread.is_alive())
        self.assertTrue(share._stop.is_set())


@unittest.skipUnless(Image, "Pillow is optional; install it to verify screenshot encoding")
class EncodingTests(unittest.TestCase):
    def png(self, noisy=False):
        size = (2400, 1500) if noisy else (1600, 900)
        image = (Image.frombytes("RGB", size, random.Random(12).randbytes(size[0] * size[1] * 3))
                 if noisy else Image.new("RGB", size, "white"))
        out = io.BytesIO()
        image.save(out, "PNG")
        return out.getvalue()

    def test_jpeg_dimensions_quality_and_thumbnail(self):
        share = ScreenShare("https://db.example", "db", ROOM, lambda: self.png())
        jpeg, width, height, fingerprint = share._frame()
        self.assertEqual((width, height), (1280, 720))
        self.assertEqual(len(fingerprint), 64 * 36)
        self.assertEqual(set(fingerprint), {255})
        with Image.open(io.BytesIO(jpeg)) as decoded:
            self.assertEqual(decoded.size, (width, height))
        share._step_down()
        share._step_down()
        self.assertEqual(share._frame()[1:3], (960, 540))

    def test_noisy_frames_stay_under_size_cap(self):
        share = ScreenShare("https://db.example", "db", ROOM, lambda: self.png(noisy=True))
        jpeg, width, height, _ = share._frame()
        self.assertLessEqual(len(jpeg), 150000)
        self.assertLessEqual(width, 1280)
        with Image.open(io.BytesIO(jpeg)) as decoded:
            self.assertEqual(decoded.size, (width, height))


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
