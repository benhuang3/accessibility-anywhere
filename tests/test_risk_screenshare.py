import http.client
import io
import json
import random
import sys
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
    return b"\xff\xd8\xff\xd9", 1280 + value, 720


class TransportTests(unittest.TestCase):
    def test_two_sender_threads_reuse_separate_keepalive_connections(self):
        share = ScreenShare("https://db.example", "db", ROOM, Mock())
        barrier = threading.Barrier(2)
        connections, failures = [], []

        def connection(*args, **kwargs):
            result = Mock()
            result.getresponse.return_value = SimpleNamespace(status=200, read=Mock(return_value=b"{}"))
            connections.append(result)
            return result

        def send():
            try:
                for _ in range(3):
                    barrier.wait(timeout=2)
                    share._post("/frames", [], TOKEN)
            except Exception as error:
                failures.append(error)
            finally:
                share._close()

        with patch("ally.risk.screenshare.http.client.HTTPSConnection", side_effect=connection):
            workers = [threading.Thread(target=send) for _ in range(2)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(3)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(failures, [])
        self.assertEqual(len(connections), 2)
        for connection in connections:
            self.assertEqual(connection.request.call_count, 3)
            connection.close.assert_called_once()

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

    def test_every_unchanged_frame_is_sent_without_a_bandwidth_budget(self):
        share = self.share()
        large = b"\xff" * 150_000
        with patch.object(share, "_post") as post, \
                patch("ally.risk.screenshare.time.monotonic", return_value=0):
            for _ in range(30):
                self.assertTrue(share._send_frame((large, 1280, 720), TOKEN))
        self.assertEqual(post.call_count, 30)
        self.assertEqual([call.args[1][1] for call in post.call_args_list], list(range(30)))
        self.assertEqual((share._quality, share._width), (60, 1280))

    def test_slow_posts_reduce_quality_and_width_without_reducing_fps(self):
        for duration in (0.085, 1, 1.001):
            with self.subTest(duration=duration):
                share, clock = self.share(), Clock()
                def post(*args):
                    clock.now += duration
                with patch.object(share, "_post", post), \
                        patch("ally.risk.screenshare.time.monotonic", lambda: clock.now):
                    share._send_frame(frame(), TOKEN)
                expected = (45, 960) if duration > 1 else (60, 1280)
                self.assertEqual((share._quality, share._width), expected)
                self.assertEqual(share._interval, 1 / 15)

    def test_failed_posts_step_down_and_continue_with_fresh_sequence(self):
        share = self.share()
        with patch.object(share, "_post", side_effect=[OSError(TOKEN + ROOM), b""]) as post, \
                self.assertNoLogs("ally", level="DEBUG"):
            self.assertFalse(share._send_frame(frame(), TOKEN))
            self.assertEqual((share._quality, share._width), (45, 960))
            self.assertTrue(share._send_frame(frame(), TOKEN))
        self.assertEqual([call.args[1][1] for call in post.call_args_list], [0, 1])
        self.assertFalse(share._stop.is_set())
        self.assertFalse(share._pending)

    def test_capture_rate_is_capped_at_fifteen_fps_with_a_fake_clock(self):
        for capture_seconds in (0, 0.02, 0.103):
            with self.subTest(capture_seconds=capture_seconds):
                share, clock, times = self.share(), Clock(), []
                share._stop = clock
                share._interval = 0
                share._set_enabled(True)
                def capture():
                    times.append(clock.now)
                    clock.now += capture_seconds
                    if len(times) == 4:
                        share.stop()
                    return frame()
                with patch.object(share, "_frame", capture), \
                        patch("ally.risk.screenshare.time.monotonic", lambda: clock.now):
                    share._produce(10)
                interval = max(1 / 15, capture_seconds)
                for index, actual in enumerate(times):
                    self.assertAlmostEqual(actual, index * interval)

    def test_three_persistent_connections_overlap_capture_and_drop_busy_frames(self):
        share = self.share()
        three_busy, captured_while_busy, release = (threading.Event() for _ in range(3))
        lock = threading.Lock()
        captured, sent, completed, connections, failures = [], [], [], [], []
        active = peak = 0
        def capture():
            captured.append(len(captured) + 1)
            if len(captured) >= 6 and three_busy.is_set():
                captured_while_busy.set()
            return frame(captured[-1])
        class Connection:
            def request(inner, method, path, body, headers):
                nonlocal active, peak
                inner.path = path
                inner.args = json.loads(body)
                if path.endswith("/send_screen_frame"):
                    with lock:
                        active += 1
                        peak = max(peak, active)
                        sent.append((inner.args[1], inner.args[3]))
                        if active == 3:
                            three_busy.set()
            def getresponse(inner):
                nonlocal active
                if inner.path.endswith("/identity"):
                    body = json.dumps({"token": TOKEN}).encode()
                else:
                    body = b""
                if inner.path.endswith("/send_screen_frame"):
                    if not release.wait(3):
                        failures.append("senders were not released")
                    with lock:
                        active -= 1
                        completed.append(inner.args[1])
                        if len(completed) >= 6:
                            share.stop()
                return SimpleNamespace(status=200, read=lambda limit: body)
            def close(inner):
                pass
        def connect(*args, **kwargs):
            connection = Connection()
            connections.append(connection)
            return connection
        try:
            with patch.object(share, "_frame", capture), \
                    patch("ally.risk.screenshare.http.client.HTTPSConnection", connect):
                share.start()
                self.assertTrue(three_busy.wait(2), "three HTTPS posts must overlap")
                self.assertTrue(captured_while_busy.wait(2), "capture must continue during blocked posts")
                with share._condition:
                    self.assertIsNone(share._waiting, "busy senders must leave no queued frame")
                    last_dropped = captured[-1]
                release.set()
                share._thread.join(3)
        finally:
            release.set()
            share.stop()
            if share._thread:
                share._thread.join(2)
        self.assertFalse(share._thread.is_alive())
        self.assertFalse(failures)
        self.assertEqual(peak, 3)
        self.assertEqual(len(connections), 3)
        self.assertGreaterEqual(len(sent), 6)
        self.assertEqual(sorted(seq for seq, _ in sent), list(range(len(sent))))
        self.assertTrue(all(width > 1280 + last_dropped for _, width in sent[3:]))
        self.assertFalse(share._pending)

    def test_rejection_stops_without_capture_or_resume(self):
        for message in ("Screen sharing paused", "Call ended " + TOKEN + ROOM):
            with self.subTest(message=message):
                share = self.share()
                with patch.object(share, "_post", side_effect=[
                        json.dumps({"token": TOKEN}).encode(), rejection(message)]) as post, \
                        patch.object(share, "_frame") as capture, self.assertNoLogs("ally", level="DEBUG"):
                    share._run()
                self.assertTrue(share._stop.is_set())
                self.assertEqual(post.call_count, 2)
                capture.assert_not_called()

    def test_missing_room_waits_thirty_seconds_before_any_capture(self):
        share, clock = self.share(), Clock()
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

    def test_frame_rejection_stops_all_senders_without_logging(self):
        for message in ("Screen sharing paused", "This call is not available", "Call ended " + TOKEN + ROOM):
            with self.subTest(message=message):
                share = self.share()
                def post(path, args, token=""):
                    if path.endswith("/identity"):
                        return json.dumps({"token": TOKEN}).encode()
                    if path.endswith("/send_screen_frame"):
                        raise rejection(message)
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
                self.assertFalse(share._pending)

    def test_deadline_stops_capture_and_sender(self):
        share = self.share()
        share.MAX_SECONDS = 0.06
        def post(path, args, token=""):
            return json.dumps({"token": TOKEN}).encode() if path.endswith("/identity") else b""
        with patch.object(share, "_frame", return_value=frame()), patch.object(share, "_post", post):
            share.start()
            share._thread.join(2)
        self.assertFalse(share._thread.is_alive())
        self.assertTrue(share._stop.is_set())
        self.assertEqual(ScreenShare.MAX_SECONDS, 1800)

    def test_stop_interrupts_idle_capture_and_network_wait(self):
        share, sent = self.share(), threading.Event()
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

    def test_stopped_or_expired_sender_posts_no_more_frames(self):
        for stopped in (True, False):
            share = self.share()
            if stopped:
                share.stop()
            else:
                share._deadline = 0
            with patch.object(share, "_post") as post:
                self.assertFalse(share._send_frame(frame(), TOKEN))
            post.assert_not_called()

    def test_capture_thread_start_failure_never_escapes_or_logs_private_details(self):
        share = self.share()
        with patch.object(share, "_post", side_effect=[json.dumps({"token": TOKEN}).encode(), b""]), \
                patch("threading.Thread.start", side_effect=RuntimeError(TOKEN + ROOM)), \
                self.assertNoLogs("ally", level="DEBUG"):
            share._run()
        self.assertTrue(share._stop.is_set())

    def test_capture_failure_stops_all_threads_quietly(self):
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


class CaptureTests(unittest.TestCase):
    def test_fast_capture_returns_primary_screen_image_without_calling_fallback(self):
        image = object()
        grab = Mock(return_value=image)
        fallback = Mock()
        with patch.dict(sys.modules, {"PIL": SimpleNamespace(ImageGrab=SimpleNamespace(grab=grab))}):
            self.assertIs(ScreenShare.capture_screen(fallback), image)
        grab.assert_called_once_with(all_screens=False)
        fallback.assert_not_called()

    def test_fast_capture_falls_back_when_imagegrab_raises(self):
        fallback = Mock(return_value=b"png")
        grab = Mock(side_effect=OSError(TOKEN + ROOM))
        with patch.dict(sys.modules, {"PIL": SimpleNamespace(ImageGrab=SimpleNamespace(grab=grab))}), \
                self.assertNoLogs("ally", level="DEBUG"):
            self.assertEqual(ScreenShare.capture_screen(fallback), b"png")
        fallback.assert_called_once_with()

    def test_fast_capture_falls_back_when_imagegrab_is_unavailable(self):
        fallback = Mock(return_value=b"png")
        with patch.dict(sys.modules, {"PIL": None}):
            self.assertEqual(ScreenShare.capture_screen(fallback), b"png")
        fallback.assert_called_once_with()


@unittest.skipUnless(Image, "Pillow is optional; install it to verify screenshot encoding")
class EncodingTests(unittest.TestCase):
    def test_pil_capture_encodes_directly_without_png_decode(self):
        image = Image.new("RGB", (1600, 900), "white")
        capture = Mock(return_value=image)
        share = ScreenShare("https://db.example", "db", ROOM, capture)
        with patch.object(Image, "open", side_effect=AssertionError("PNG decode is unnecessary")):
            jpeg, width, height = share._frame()
        capture.assert_called_once_with()
        self.assertEqual((width, height), (1280, 720))
        with Image.open(io.BytesIO(jpeg)) as decoded:
            self.assertEqual(decoded.size, (width, height))

    def png(self, noisy=False):
        size = (2400, 1500) if noisy else (1600, 900)
        image = (Image.frombytes("RGB", size, random.Random(12).randbytes(size[0] * size[1] * 3))
                 if noisy else Image.new("RGB", size, "white"))
        out = io.BytesIO()
        image.save(out, "PNG")
        return out.getvalue()

    def test_jpeg_dimensions_quality_and_thumbnail(self):
        share = ScreenShare("https://db.example", "db", ROOM, lambda: self.png())
        jpeg, width, height = share._frame()
        self.assertEqual((width, height), (1280, 720))
        with Image.open(io.BytesIO(jpeg)) as decoded:
            self.assertEqual(decoded.size, (width, height))
        share._step_down()
        share._step_down()
        self.assertEqual(share._frame()[1:3], (960, 540))

    def test_noisy_frames_stay_under_size_cap(self):
        share = ScreenShare("https://db.example", "db", ROOM, lambda: self.png(noisy=True))
        jpeg, width, height = share._frame()
        self.assertLessEqual(len(jpeg), 150000)
        self.assertLessEqual(width, 1280)
        with Image.open(io.BytesIO(jpeg)) as decoded:
            self.assertEqual(decoded.size, (width, height))

    def test_quality_and_width_recover_after_healthy_period(self):
        share = ScreenShare("https://db.example", "db", ROOM,
                            lambda: Image.new("RGB", (1600, 900), "white"))
        clock = Clock()
        qualities = []
        save = Image.Image.save

        def record_save(image, output, format, **options):
            qualities.append(options["quality"])
            return save(image, output, format, **options)

        with patch("ally.risk.screenshare.time.monotonic", lambda: clock.now), \
                patch.object(Image.Image, "save", record_save):
            self.assertEqual(share._frame()[1:], (1280, 720))
            share._step_down()
            self.assertEqual(share._frame()[1:], (960, 540))
            clock.now = share.RECOVERY_SECONDS
            self.assertEqual(share._frame()[1:], (1280, 720))
        self.assertEqual(qualities, [60, 45, 60])


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
        with patch.object(ScreenShare, "capture_screen", side_effect=lambda fallback: fallback()) as capture:
            self.assertEqual(wire.screen_capture(), b"png")
        capture.assert_called_once()
        agent.platform.screenshot_png.assert_called_once()
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
