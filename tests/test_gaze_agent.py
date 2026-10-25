"""Agent-side eye tracking, with fakes. No camera and no microphone."""

from __future__ import annotations

import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from helpers import ROOT  # noqa: F401  (puts the repo on sys.path)

from ally.doctor import describe_calibration
from ally.gaze import GazeManager, choice_index, ongoing_choice
from ally.gaze_helper import RecentFeatures, apply_click, classify_command, nearest_click_frame
from ally.registry import ToolError
from ally.senses.manager import Senses
from ally.tools.gaze_tools import (calibration_words, check_words, choose_by_looking, looking_text,
                                   where_am_i_looking)


class _Stdin:
    def __init__(self):
        self.buf = ""

    def write(self, text):
        self.buf += text
        return len(text)

    def flush(self):
        return None


def manager():
    agent = types.SimpleNamespace(
        config=types.SimpleNamespace(data={
            "gaze": {"enabled": True, "camera": 0, "features": "v1", "click_learning": True,
                     "max_radius_px": 250},
            "senses": {"vision": False, "input": False},
        }),
        paths=types.SimpleNamespace(state_dir=Path(tempfile.mkdtemp(prefix="ally-gaze-agent-"))),
        last_utterance=None,
    )
    gaze = GazeManager(agent)
    gaze.ready = gaze.calibrated = True
    gaze.face_at = time.time()
    gaze.screen = (1920, 1080)
    agent.gaze = gaze
    agent.senses = types.SimpleNamespace(pointer=(15, 20))
    return gaze, agent


def _cluster(gaze, t0, x, y, n=12, step=0.05, radius=40):
    for i in range(n):
        gaze.points.append((t0 + i * step, x, y, radius))


class TargetTests(unittest.TestCase):
    def test_longest_fixation_in_the_request_window(self):
        gaze, agent = manager()
        now = time.time()
        gaze.face_at = now
        agent.last_utterance = (now - 3.0, now - 2.2)
        _cluster(gaze, now - 2.9, 200, 180, radius=60)
        _cluster(gaze, now - 1.0, 1500, 800, radius=60)
        got = gaze.target()
        self.assertEqual(got["source"], "eyes")
        self.assertEqual(got["why"], "")
        self.assertEqual((got["x"], got["y"]), (200, 180))
        self.assertEqual(got["region"], "top left")
        self.assertEqual(got["radius_px"], 60)
        self.assertGreater(got["confidence"], 0)

    def test_last_second_and_a_half_when_nobody_spoke(self):
        gaze, _agent = manager()
        now = time.time()
        gaze.face_at = now
        _cluster(gaze, now - 1.0, 200, 180)
        got = gaze.target()
        self.assertEqual((got["source"], got["x"], got["region"]), ("eyes", 200, "top left"))

    def test_fallbacks_say_why(self):
        gaze, agent = manager()
        now = time.time()
        _cluster(gaze, now - 1.0, 200, 180)
        gaze.calibrated = False
        got = gaze.target()
        self.assertEqual(got["source"], "mouse")
        self.assertIn("isn't set up", got["why"])
        self.assertEqual((got["x"], got["y"]), (15, 20))

        gaze.calibrated = True
        gaze.face_at = 0
        got = gaze.target()
        self.assertEqual(got["source"], "mouse")
        self.assertIn("can't see your eyes", got["why"])

        gaze.face_at = time.time()
        gaze.points.clear()
        for i in range(20):
            x, y = (100, 100) if i % 2 == 0 else (1000, 900)
            gaze.points.append((time.time() - 1.0 + i * 0.05, x, y, 40))
        got = gaze.target()
        self.assertEqual(got["source"], "mouse")
        self.assertIn("didn't stay", got["why"])

        gaze.points.clear()
        _cluster(gaze, time.time() - 1.0, 200, 180, radius=400)
        got = gaze.target()
        self.assertEqual(got["source"], "mouse")
        self.assertIn("isn't sure", got["why"])

        gaze.override = False
        got = gaze.target()
        self.assertIn("off", got["why"])


class ClickTests(unittest.TestCase):
    def test_input_click_becomes_a_helper_line(self):
        gaze, agent = manager()
        stdin = _Stdin()
        gaze.proc = types.SimpleNamespace(poll=lambda: None, stdin=stdin)
        senses = Senses(agent)
        senses.on_input({"e": "c", "t": 12.5, "x": 10, "y": 20})
        self.assertIn("click 12.500 10 20\n", stdin.buf)
        senses.on_input({"e": "m", "t": 13, "x": 11, "y": 21})
        self.assertEqual(stdin.buf.count("click"), 1)
        self.assertEqual(classify_command("click 12.500 10 20"), ("click", (12.5, 10.0, 20.0)))

    def test_click_learning_off_sends_nothing(self):
        gaze, _agent = manager()
        stdin = _Stdin()
        gaze.proc = types.SimpleNamespace(poll=lambda: None, stdin=stdin)
        gaze.cfg["click_learning"] = False
        self.assertFalse(gaze.on_click(1.0, 2, 3))
        self.assertEqual(stdin.buf, "")

    def test_buffer_picks_the_frame_nearest_a_tenth_before(self):
        recent = RecentFeatures()
        for t in (0.0, 0.50, 0.85, 1.00, 1.20):
            recent.add(t, [t])
        self.assertTrue(all(frame[0] >= 0.2 for frame in recent.frames))
        self.assertNotIn(0.0, [frame[0] for frame in recent.frames])
        picked = nearest_click_frame(recent.frames, 1.0)
        self.assertEqual(picked[0], 0.85)

        class Recorder:
            def __init__(self):
                self.got = None

            def add(self, features, target, t):
                self.got = (features, tuple(target), t)
                return True

        recorder = Recorder()
        frames = [(1.0, ["early"]), (1.9, ["look"]), (2.0, ["click"])]
        self.assertTrue(apply_click(recorder, frames, 2.0, 8, 9))
        self.assertEqual(recorder.got, (["look"], (8.0, 9.0), 2.0))
        self.assertIsNone(nearest_click_frame([(0.0, ["old"])], 2.0))


class ToolTests(unittest.TestCase):
    def test_where_am_i_looking_uses_a_fake_element(self):
        gaze, agent = manager()
        gaze.target = lambda t0=None, t1=None: {
            "x": 200, "y": 180, "radius_px": 40, "confidence": 0.8,
            "region": "top left", "source": "eyes", "why": ""}
        ctx = types.SimpleNamespace(agent=agent)
        with mock.patch("ally.tools.gaze_tools._element_at", return_value={"role": "button", "name": "Save"}) as looked:
            text = where_am_i_looking(ctx)
        looked.assert_called_once()
        self.assertIn("top left", text)
        self.assertIn("Button: Save", text)
        self.assertNotIn("mouse", text.lower())

        gaze.target = lambda t0=None, t1=None: {
            "x": 15, "y": 20, "radius_px": None, "confidence": 0,
            "region": "top left", "source": "mouse",
            "why": "I can't see your eyes, so this is the mouse pointer."}
        with mock.patch("ally.tools.gaze_tools._element_at", return_value=None):
            text = where_am_i_looking(ctx)
        self.assertIn("can't see your eyes", text)
        self.assertIn("Region: top left", text)
        self.assertNotIn("Button", text)
        self.assertEqual(looking_text({"region": "middle", "source": "eyes", "why": ""}, None), "Region: middle.")

    def test_choose_dwells_and_times_out(self):
        self.assertEqual(choice_index(100, 100, (1920, 1080), 2), 0)
        self.assertEqual(choice_index(1500, 100, (1920, 1080), 2), 1)
        gaze, _agent = manager()
        for i in range(10):
            gaze.points.append((10.0 + i * 0.1, 100, 400, 40))
        clock = {"now": 10.9}

        def now():
            return clock["now"]

        def sleep(_dt):
            clock["now"] += 1.0

        self.assertEqual(gaze.choose(["Yes", "No"], timeout=2, clock=now, sleep=sleep), "Yes")
        gaze.points.clear()
        for i in range(6):
            x = 100 if i < 3 else 1500
            gaze.points.append((20.0 + i * 0.1, x, 400, 40))
        clock["now"] = 20.5
        self.assertIsNone(ongoing_choice(list(gaze.points), gaze.screen, 2, clock["now"]))
        self.assertIsNone(gaze.choose(["Yes", "No"], timeout=2, clock=now, sleep=sleep))

        said = []
        ctx = types.SimpleNamespace(agent=types.SimpleNamespace(gaze=gaze), say=lambda text: said.append(text))
        gaze.choose = lambda options, timeout=8: "No"
        self.assertEqual(choose_by_looking(ctx, ["Yes", "No"], timeout=4), "They looked at No.")
        gaze.choose = lambda options, timeout=8: None
        self.assertIn("No choice", choose_by_looking(ctx, ["Yes", "No"]))
        with self.assertRaises(ToolError):
            choose_by_looking(ctx, ["Only one"])

    def test_calibration_and_check_words(self):
        good = {"calibrated": True, "validation": {"error_px": 140, "region_hits": 5, "region_total": 5}}
        text = calibration_words(good)
        self.assertIn("ready", text)
        self.assertIn("Good", text)
        self.assertIn("140", text)
        self.assertIn("5 of 5", text)
        nine = {"calibrated": True, "validation": {"error_px": 150, "region_hits": 8, "region_total": 9}}
        self.assertIn("Good", calibration_words(nine))
        okay = {"calibrated": True, "validation": {"error_px": 220, "region_hits": 4, "region_total": 5}}
        self.assertIn("Okay", calibration_words(okay))
        self.assertIn("Okay", check_words(okay))
        poor = {"calibrated": True, "validation": {"error_px": 400, "region_hits": 2, "region_total": 5},
                "cause": "The light on your face was too dim. Turn on a light in front of you."}
        text = calibration_words(poor)
        self.assertIn("poor", text.lower())
        self.assertIn("light", text.lower())
        self.assertIn("Poor", check_words(poor))
        self.assertEqual(calibration_words({"calibrated": False, "why": "cancelled"}), "Eye tracker setup cancelled.")
        with self.assertRaises(ToolError):
            check_words({"calibrated": False, "why": "The eye tracker needs a setup first."})


class DoctorTests(unittest.TestCase):
    def test_lines(self):
        now = 1_000_000.0
        model = {"created": now - 7200, "features": "v2", "camera": 0,
                 "validation": {"error_px": 140, "region_hits": 5, "region_total": 5}}
        status, detail = describe_calibration(model, camera=0, features="v1", now=now)
        self.assertEqual(status, "ok")
        self.assertIn("2 hours ago", detail)
        self.assertIn("140", detail)
        self.assertIn("5 of 5", detail)
        self.assertIn("features v2", detail)
        self.assertIn("camera 0", detail)
        status, detail = describe_calibration(model, camera=1, features="v1", now=now)
        self.assertEqual(status, "warn")
        self.assertIn("camera 0", detail)
        self.assertIn("gaze.camera is 1", detail)
        status, detail = describe_calibration(None, camera=0, features="v1", now=now)
        self.assertEqual(status, "warn")
        self.assertIn("not calibrated", detail)
        self.assertIn("features v1", detail)
        poor = {"created": now - 60, "features": "v1", "camera": 0,
                "validation": {"error_px": 400, "region_hits": 2, "region_total": 5}}
        status, detail = describe_calibration(poor, camera=0, features="v1", now=now)
        self.assertEqual(status, "warn")
        self.assertIn("400", detail)


if __name__ == "__main__":
    unittest.main()
