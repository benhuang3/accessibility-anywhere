"""Hands-free voice (wake word) and eye tracking. No microphone or camera needed."""

import math
import random
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path

from helpers import temp_home

from ally import config as config_mod
from ally import listener, rules
from ally.gaze import GazeManager
from ally.platforms.base import edge_speed
from ally.registry import ToolError

try:
    import numpy as np

    from ally import gaze_model
except ImportError:  # numpy is optional
    np = gaze_model = None


class WakeWordTests(unittest.TestCase):
    def test_split_wake(self):
        w = ["ally"]
        self.assertEqual(listener.split_wake("Hey Ally, make it bigger.", w), (True, "make it bigger"))
        self.assertEqual(listener.split_wake("Make it bigger, Ally.", w), (True, "Make it bigger"))
        self.assertEqual(listener.split_wake("alley zoom in", w), (True, "zoom in"))
        self.assertEqual(listener.split_wake("Okay, Allie. Stop.", w), (True, "Stop"))
        self.assertEqual(listener.split_wake("Ally.", w), (True, ""))
        self.assertFalse(listener.split_wake("I'm calling my friend Ally later", w)[0])
        self.assertFalse(listener.split_wake("Totally fine", w)[0])
        self.assertEqual(listener.split_wake("Computer, zoom in", ["computer"]), (True, "zoom in"))
        self.assertEqual(listener.split_wake("hey my helper read this", ["my helper"]), (True, "read this"))

    def test_stop_while_talking(self):
        self.assertTrue(listener.is_stop("and the article says ally stop the", ["ally"]))
        self.assertTrue(listener.is_stop("Ally, be quiet.", ["ally"]))
        self.assertFalse(listener.is_stop("stop the car", ["ally"]))


def blocks(levels):
    """Fake audio: one 100 ms block per level."""
    return [(b"\x00\x00" * 1600, lv) for lv in levels]


class SegmenterTests(unittest.TestCase):
    def run_levels(self, levels, speaking=False, **kw):
        seg, out, t = listener.Segmenter(**kw), [], 100.0
        for pcm, lv in blocks(levels):
            t += 0.1
            u = seg.feed(pcm, lv, t, speaking)
            if u:
                out.append(u)
        return out

    def test_one_sentence(self):
        out = self.run_levels([40] * 20 + [3000] * 15 + [40] * 12, end_silence=0.7)
        self.assertEqual(len(out), 1)
        u = out[0]
        self.assertAlmostEqual(u.t1 - u.t0, 1.5 + 0.4, delta=0.35)  # speech plus a little lead-in
        self.assertFalse(u.over_speech)

    def test_clicks_and_noise_are_ignored(self):
        self.assertEqual(self.run_levels([40] * 20 + [5000] + [40] * 20), [])  # 100 ms click
        self.assertEqual(self.run_levels([40] * 10 + [5000] * 3 + [40] * 20, min_seconds=0.4), [])

    def test_long_speech_is_cut(self):
        out = self.run_levels([40] * 10 + [3000] * 50, max_seconds=2.0)
        self.assertGreaterEqual(len(out), 2)

    def test_noise_floor_adapts(self):
        # A loud fan (level 400) isn't speech once the floor has learned it.
        self.assertEqual(self.run_levels([400] * 200), [])

    def test_marks_ally_talking(self):
        out = self.run_levels([40] * 10 + [3000] * 10 + [40] * 10, speaking=True)
        self.assertTrue(out and out[0].over_speech)


class FakeSpeaker:
    speaking = False

    def __init__(self):
        self.stopped = 0

    def stop(self):
        self.stopped += 1

    def beep(self, kind="start", wait=True):
        pass


class FakeAgent:
    def __init__(self, follow_up=6):
        self.config = types.SimpleNamespace(data={"voice": {
            "always_listen": True, "wake_words": ["ally"], "follow_up_seconds": follow_up, "beep": True,
            "whisper_model": "base.en", "language": "en", "silence_seconds": 1.2, "engine": "auto"}})
        self.speaker = FakeSpeaker()
        self.events = types.SimpleNamespace(add=lambda *a, **k: None)
        self.handled = []
        self.last_utterance = None
        self.done = threading.Event()

    def handle_text(self, text, source="text"):
        self.handled.append(text)
        self.done.set()


class ListenerTests(unittest.TestCase):
    def hear(self, L, a, text, over_speech=False):
        L.transcribe = lambda utt: text
        a.done.clear()
        req = L.handle(listener.Utterance(b"", time.time() - 1, time.time(), over_speech))
        if req and req != "stop":  # "stop" is handled right away, not sent on as a request
            self.assertTrue(a.done.wait(2))
        return req

    def test_wake_word_request(self):
        a = FakeAgent()
        L = listener.WakeListener(a)
        self.assertEqual(self.hear(L, a, "Ally, magnify what I'm looking at."), "magnify what I'm looking at")
        self.assertEqual(a.handled, ["magnify what I'm looking at"])
        self.assertIsNotNone(a.last_utterance)

    def test_chatter_is_ignored(self):
        a = FakeAgent()
        L = listener.WakeListener(a)
        self.assertIsNone(self.hear(L, a, "I think we should get lunch soon."))
        self.assertEqual(a.handled, [])

    def test_name_alone_then_request(self):
        a = FakeAgent()
        L = listener.WakeListener(a)
        self.assertEqual(self.hear(L, a, "Ally."), "")
        self.assertEqual(self.hear(L, a, "Please tell me what this big page here is all about"),
                         "Please tell me what this big page here is all about")

    def test_follow_ups(self):
        a = FakeAgent()
        L = listener.WakeListener(a)
        self.hear(L, a, "Ally, make it bigger")
        time.sleep(0.05)
        self.assertEqual(self.hear(L, a, "a bit more"), "a bit more")  # short: fine without the name
        time.sleep(0.05)
        self.assertIsNone(self.hear(L, a, "I think we should get lunch soon, honestly."))  # long chatter
        self.assertIsNone(self.hear(L, a, "Thank you."))  # what Whisper writes for noise

    def test_no_follow_ups_when_off(self):
        a = FakeAgent(follow_up=0)
        L = listener.WakeListener(a)
        self.hear(L, a, "Ally, make it bigger")
        time.sleep(0.05)
        self.assertIsNone(self.hear(L, a, "bigger"))

    def test_only_stop_while_ally_talks(self):
        a = FakeAgent()
        L = listener.WakeListener(a)
        self.assertIsNone(self.hear(L, a, "Ally is ready. Say Ally, then what you need.", over_speech=True))
        self.assertEqual(a.handled, [])
        self.assertEqual(self.hear(L, a, "and then the story goes Ally stop", over_speech=True), "stop")
        self.assertEqual(a.speaker.stopped, 1)


@unittest.skipIf(np is None, "numpy not installed")
class GazeModelTests(unittest.TestCase):
    def synthetic(self, n_per_dot=20, noise=0.002, seed=1):
        """Fake eye features that depend on the target in a curved way, plus head wobble."""
        rnd = np.random.default_rng(seed)
        screen = (1920, 1080)
        dots = [(fx * screen[0], fy * screen[1]) for fy in (0.1, 0.5, 0.9) for fx in (0.08, 0.5, 0.92)]
        samples = []
        for tx, ty in dots:
            for _ in range(n_per_dot):
                u, v = tx / screen[0] - 0.5, ty / screen[1] - 0.5
                head = rnd.normal(0, 0.01, 5)
                f = [0.3 * u + 0.05 * u * u, 0.2 * v + 0.04 * v * v, 0.8 * u, 0.6 * v, *head]
                f = (np.array(f) + rnd.normal(0, noise, 9)).tolist()
                samples.append((f, (tx, ty)))
        return samples, screen

    def test_fit_and_predict(self):
        samples, screen = self.synthetic()
        model = gaze_model.fit(samples, screen)
        self.assertLess(model["error_px"], 120)
        f, (tx, ty) = samples[5]
        x, y = gaze_model.predict(model, f)
        self.assertLess(math.hypot(x - tx, y - ty), 150)

    def test_needs_enough_dots(self):
        samples, screen = self.synthetic()
        few = [s for s in samples if s[1][1] < 600]  # only 6 dots...
        gaze_model.fit(few, screen)
        with self.assertRaises(ValueError):
            gaze_model.fit([s for s in samples if s[1][1] < 200], screen)  # ...3 is too few

    def test_save_and_load(self):
        samples, screen = self.synthetic()
        model = gaze_model.fit(samples, screen)
        path = Path(tempfile.mkdtemp()) / "cal.json"
        gaze_model.save(model, path)
        self.assertEqual(gaze_model.load(path, screen)["coef"], model["coef"])
        self.assertIsNone(gaze_model.load(path, (1280, 720)))  # another screen size: recalibrate
        self.assertIsNone(gaze_model.load(path.with_name("missing.json")))

    def test_features_from_landmarks(self):
        pts = [(0.5, 0.5, 0.0)] * 478
        pts = list(pts)
        for idx, x in ((33, 0.40), (133, 0.46), (362, 0.54), (263, 0.60)):
            pts[idx] = (x, 0.45, 0.0)
        pts[159], pts[145], pts[386], pts[374] = (0.43, 0.44, 0), (0.43, 0.46, 0), (0.57, 0.44, 0), (0.57, 0.46, 0)
        pts[468], pts[473] = (0.44, 0.45, 0), (0.58, 0.45, 0)  # both irises moved the same way
        f, openness = gaze_model.features(pts, {}, None, 640, 480)
        self.assertEqual(len(f), len(gaze_model.FEATURES))
        self.assertGreater(abs(f[0]), 0.05)  # the two eyes agree, so the shift adds up
        self.assertGreater(openness, 0.1)
        self.assertIsNone(gaze_model.features(pts[:400], {}, None, 640, 480))

    def test_one_euro_is_steady_and_quick(self):
        flt = gaze_model.OneEuro()
        t, out = 0.0, None
        for _ in range(30):  # jitter around a fixation
            t += 0.05
            out = flt(np.array([500 + random.uniform(-20, 20), 300.0]), t)
        self.assertLess(abs(out[0] - 500), 15)
        for _ in range(12):  # a jump to another spot gets there within ~0.6 s
            t += 0.05
            out = flt(np.array([1500.0, 300.0]), t)
        self.assertGreater(out[0], 1300)


class PanTests(unittest.TestCase):
    def test_edge_speed(self):
        self.assertEqual(edge_speed(0.5), 0.0)
        self.assertEqual(edge_speed(0.2), 0.0)
        self.assertLess(edge_speed(0.05), -0.5)
        self.assertAlmostEqual(edge_speed(1.0), 1.0)
        self.assertEqual(edge_speed(-0.2), -1.0)


class GazeManagerTests(unittest.TestCase):
    def manager(self):
        agent = types.SimpleNamespace(config=types.SimpleNamespace(data={"gaze": {"enabled": True}}),
                                      paths=types.SimpleNamespace(state_dir=Path(tempfile.mkdtemp())),
                                      last_utterance=None)
        g = GazeManager(agent)
        g.ready = g.calibrated = True
        return g, agent

    def test_median_point_while_speaking(self):
        g, agent = self.manager()
        now = time.time()
        for i in range(20):
            g.points.append((now - 2 + i * 0.1, 800 + (300 if i == 7 else 0), 400))  # one glance away
        g.face_at = now
        agent.last_utterance = (now - 1.8, now - 0.2)
        self.assertEqual(g.point_for_request(), (800, 400))

    def test_clear_problems(self):
        g, _agent = self.manager()
        g.calibrated = False
        g.face_at = time.time()
        with self.assertRaisesRegex(ToolError, "calibrate my eyes"):
            g.point_for_request()
        g.calibrated, g.face_at = True, 0
        with self.assertRaisesRegex(ToolError, "can't see your eyes"):
            g.point_for_request()
        g.override = False
        with self.assertRaisesRegex(ToolError, "turn on eye tracking"):
            g.point_for_request()


class GazeToolTests(unittest.TestCase):
    def test_maps_gaze_into_the_zoomed_view(self):
        from ally.tools.gaze_tools import magnify_where_i_look

        calls = []
        os_ = types.SimpleNamespace(view=lambda: (2.0, 100, 50), last_magnify=2.0,
                                    magnify_at=lambda *a, **k: calls.append((a, k)) or "ok")
        ctx = types.SimpleNamespace(agent=types.SimpleNamespace(gaze=types.SimpleNamespace(
            target=lambda t0=None, t1=None: {"x": 960, "y": 540, "radius_px": 40, "confidence": 0.8,
                                              "region": "middle", "source": "eyes", "why": ""},
            last_point=None)), os=os_, event=lambda *a, **k: None)
        magnify_where_i_look(ctx)
        (level, cx, cy), kw = calls[0]
        self.assertEqual((level, cx, cy, kw["follow"]), (2.0, 580.0, 320.0, "eyes"))  # 100 + 960/2, 50 + 540/2


class HandsFreeConfigTests(unittest.TestCase):
    def test_rules(self):
        for s in ("I want what I'm looking at magnified", "Zoom in on what I am looking at", "magnify here"):
            self.assertEqual(rules.match(s).actions, [("magnify_where_i_look", {})], s)
        self.assertEqual(rules.match("calibrate my eyes").actions, [("calibrate_eye_tracker", {})])
        self.assertEqual(rules.match("turn off the camera").actions, [("eye_tracking", {"on": False})])
        self.assertEqual(rules.match("follow my eyes").actions, [("magnifier_follow", {"target": "eyes"})])
        self.assertEqual(rules.match("make it bigger").actions, [("zoom_in", {})])

    def test_settings(self):
        errors, _w = config_mod.validate(config_mod.merge({"voice": {"wake_words": []}}))
        self.assertTrue(any("wake_words" in e for e in errors))
        errors, _w = config_mod.validate(config_mod.merge({"gaze": {"fps": 120}}))
        self.assertTrue(any("gaze.fps" in e for e in errors))
        errors, warnings = config_mod.validate(config_mod.merge({}))
        self.assertEqual((errors, warnings), ([], []))

    def test_test_agents_leave_mic_and_camera_alone(self):
        text = (temp_home() / "config.toml").read_text()
        self.assertIn("always_listen = false", text)
        self.assertIn("enabled = false", text)


if __name__ == "__main__":
    unittest.main()
