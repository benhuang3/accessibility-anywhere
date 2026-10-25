"""Agent-side eye tracking, with fakes. No camera and no microphone."""

from __future__ import annotations

import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from helpers import ROOT  # noqa: F401  (puts the repo on sys.path)

from ally import gaze_element
from ally.doctor import describe_calibration
from ally.gaze import RETUNE_OFFER, GazeManager, choice_index, ongoing_choice
from ally.gaze_helper import RecentFeatures, adopt_model, apply_click, classify_command, nearest_click_frame
from ally.registry import ToolError
from ally.senses import clickable
from ally.senses.manager import Senses
from ally.platforms.base import ease_pan, edge_speed
from ally.rules import match
from ally.tools.gaze_tools import (calibration_words, check_eye_tracker, check_words, choose_by_looking,
                                   eye_follow_speed, looking_text, replace_toml_key, resolve_pan_speed,
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

    def test_retune_offer_after_ten_seconds_then_waits(self):
        self.assertEqual(RETUNE_OFFER, "Your position changed. Say check my eye tracker to re-tune it.")
        gaze, agent = manager()
        said, added = [], []
        agent.events = types.SimpleNamespace(add=lambda kind, text: added.append((kind, text)))
        agent.speaker = types.SimpleNamespace(say=lambda text: said.append(text))
        now = {"t": 1_000.0}

        def fake_time():
            return now["t"]

        def unsure():
            gaze.face_at = now["t"]
            gaze.points.clear()
            _cluster(gaze, now["t"] - 1.0, 200, 180, radius=400)
            return gaze.target()

        with mock.patch("ally.gaze.time.time", fake_time):
            got = unsure()
            self.assertEqual(got["source"], "mouse")
            self.assertIn("isn't sure", got["why"])
            self.assertEqual(said, [])
            now["t"] = 1_009.0
            unsure()
            self.assertEqual(said, [])
            now["t"] = 1_010.0
            unsure()
            self.assertEqual(said, [RETUNE_OFFER])
            self.assertEqual(added, [("gaze", RETUNE_OFFER)])
            now["t"] = 1_010.0 + 299.0
            unsure()
            self.assertEqual(len(said), 1)
            now["t"] = 1_010.0 + 300.0
            unsure()
            self.assertEqual(len(said), 2)
            self.assertEqual(len(added), 2)

            # A steady look, or a fallback that is not high uncertainty, starts the wait over.
            gaze.points.clear()
            _cluster(gaze, now["t"] - 1.0, 200, 180, radius=40)
            self.assertEqual(gaze.target()["source"], "eyes")
            now["t"] += 30.0
            unsure()
            self.assertEqual(len(said), 2)
            gaze.face_at = 0
            gaze.target()
            now["t"] += 30.0
            gaze.face_at = now["t"]
            unsure()
            self.assertEqual(len(said), 2)


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

    def test_check_eye_tracker_retunes_and_saves(self):
        import numpy as np

        from ally import gaze_model
        from ally.gaze_helper import retune_check

        gaze, agent = manager()
        screen = gaze.screen
        rnd = np.random.default_rng(2)
        dots = [(fx * screen[0], fy * screen[1]) for fy in (0.1, 0.5, 0.9) for fx in (0.08, 0.5, 0.92)]
        trained = []
        for tx, ty in dots:
            for _ in range(8):
                u, v = tx / screen[0] - 0.5, ty / screen[1] - 0.5
                feat = np.array([0.3 * u, 0.2 * v, 0.8 * u, 0.6 * v, 0, 0, 0, 0, 0.4], dtype=float)
                feat = (feat + rnd.normal(0, 0.001, 9)).tolist()
                trained.append((feat, (tx, ty)))
        model = gaze_model.fit(trained, screen)
        model["camera"] = 0
        gaze_model.save(model, gaze.calibration_path)
        samples = []
        for feat, _target in trained[::8][:5]:
            pred = gaze_model.predict(model, feat)
            shifted = (pred[0] + 400.0, pred[1] - 150.0)
            for _ in range(4):
                samples.append((list(feat), shifted))
        sent = []
        gaze.send = lambda line: sent.append(line) or True
        gaze.check = lambda timeout=40: {"calibrated": True, "check": True, "samples": samples,
                                         "cause": "Sit still, with light on your face, and try again."}

        class _Os:
            def view(self):
                return (1.0, 0, 0)

            def other_magnifier_open(self):
                return False

            def magnify(self, _level):
                raise AssertionError("the check dots are already unmagnified")

        ctx = types.SimpleNamespace(agent=agent, os=_Os(), say=lambda _text: None)
        with mock.patch("ally.gaze_model.retune", wraps=gaze_model.retune) as retuned:
            text = check_eye_tracker(ctx)
        retuned.assert_called_once()
        self.assertEqual(sent, ["reload"])
        saved = gaze_model.load(gaze.calibration_path, screen)
        self.assertIsNotNone(saved)
        self.assertNotEqual(saved["offset"], [0.0, 0.0])
        preview = retune_check(model, samples)
        self.assertGreater(preview["error_before"], preview["error_after"])
        self.assertIn(str(preview["error_before"]), text)
        self.assertIn(str(preview["error_after"]), text)
        self.assertIn("re-tuning", text.lower())
        self.assertEqual(saved["validation"]["error_px"], preview["error_after"])
        loaded, corrector = adopt_model(gaze.calibration_path, screen, 0, True)
        self.assertEqual(loaded["offset"], saved["offset"])
        self.assertIsNotNone(corrector)
        self.assertEqual(classify_command("reload"), ("reload", None))


class PanSpeedTests(unittest.TestCase):
    def test_the_edge_ramp_is_quadratic(self):
        self.assertEqual(edge_speed(0.5), 0.0)
        self.assertAlmostEqual(edge_speed(0.0), -1.0)
        self.assertAlmostEqual(edge_speed(1.0), 1.0)
        # Halfway into the 18% band used to be half speed. It is now a quarter.
        self.assertAlmostEqual(edge_speed(0.09), -0.25)
        self.assertGreater(edge_speed(0.17), -0.02)
        self.assertLess(abs(edge_speed(0.17)), abs((0.18 - 0.17) / 0.18))

    def test_panning_waits_then_eases(self):
        held, vel = (0.0, 0.0), (0.0, 0.0)
        for _ in range(2):
            vx, vy, held = ease_pan(0.0, 0.5, held, vel, 0.1)
            vel = (vx, vy)
            self.assertEqual((vx, vy), (0.0, 0.0))
        self.assertLess(held[0], 0.3)
        vx, vy, held = ease_pan(0.0, 0.5, held, vel, 0.1)
        self.assertGreaterEqual(held[0], 0.3)
        self.assertLess(vx, 0.0)
        self.assertGreater(vx, -1.0)  # eased in, not a jump to full speed
        self.assertEqual(vy, 0.0)
        prev = vx
        for _ in range(30):
            vx, vy, held = ease_pan(0.0, 0.5, held, (vx, vy), 0.05)
        self.assertLess(vx, prev)
        self.assertAlmostEqual(vx, -1.0, places=2)
        # Leaving the edge eases back to rest. It does not stop dead.
        stopped, vy, held = ease_pan(0.5, 0.5, held, (vx, vy), 0.05)
        self.assertEqual(held, (0.0, 0.0))
        self.assertLess(stopped, 0.0)
        self.assertGreater(stopped, vx)

    def test_speed_words_and_a_saved_live_change(self):
        self.assertEqual(resolve_pan_speed("slow", 0.35), 0.2)
        self.assertEqual(resolve_pan_speed("medium", 1.0), 0.35)
        self.assertEqual(resolve_pan_speed("fast", 0.2), 1.0)
        self.assertEqual(resolve_pan_speed("slower", 0.35), 0.2)
        self.assertEqual(resolve_pan_speed("faster", 0.35), 0.5)
        self.assertEqual(resolve_pan_speed("0.5", 0.35), 0.5)
        self.assertEqual(resolve_pan_speed(2, 0.35), 2.0)
        self.assertEqual(resolve_pan_speed("slower", 0.1), 0.1)
        self.assertEqual(resolve_pan_speed("faster", 2.0), 2.0)
        with self.assertRaises(ToolError):
            resolve_pan_speed("very fast", 0.35)
        with self.assertRaises(ToolError):
            resolve_pan_speed(3, 0.35)

        text = (
            "# keep me\n[agent]\nname = \"Ally\"\n\n[gaze]\n"
            "enabled = true\n"
            "pan_speed = 1.0            # how fast the view moves\n"
            "camera = 0\n\n[speech]\nrate = 1.0\n"
        )
        updated = replace_toml_key(text, "gaze", "pan_speed", "0.25")
        self.assertIn("pan_speed = 0.25            # how fast the view moves", updated)
        self.assertIn('name = "Ally"', updated)
        self.assertIn("camera = 0", updated)
        self.assertIn("rate = 1.0", updated)
        self.assertIn("# keep me", updated)
        self.assertEqual(updated.count("pan_speed"), 1)

        gaze, agent = manager()
        saved = {}

        def save_config(body):
            saved["text"] = body
            agent.config.data["gaze"]["pan_speed"] = 0.25
            return True, [], []

        applied = {}
        agent.config.read_text = lambda: text
        agent.save_config = save_config
        agent.platform = types.SimpleNamespace(
            set_gaze_source=lambda latest, pan_speed=1.0: applied.update(speed=pan_speed, latest=latest))
        agent.config.data["gaze"]["pan_speed"] = 0.35
        said = eye_follow_speed(types.SimpleNamespace(agent=agent), "slower")
        self.assertIn("0.2", said)
        self.assertIn("pan_speed = 0.2            # how fast the view moves", saved["text"])
        self.assertEqual(applied["speed"], 0.2)
        self.assertIs(applied["latest"].__self__, gaze)
        self.assertEqual(match("follow my eyes slower").actions, [("eye_follow_speed", {"speed": "slower"})])
        self.assertEqual(match("follow my eyes faster").actions, [("eye_follow_speed", {"speed": "faster"})])
        self.assertEqual(match("follow my eyes").actions, [("magnifier_follow", {"target": "eyes"})])


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
        status, detail = describe_calibration(
            {"created": now - 30, "features": "v5", "camera": 0}, camera=0, features="v5", now=now)
        self.assertIn("features v5", detail)

    def test_gaze_model_file_and_onnxruntime(self):
        from ally.doctor import describe_gaze_cnn
        from ally.gaze_helper import cnn_readiness

        state = Path(tempfile.mkdtemp())
        status, name, detail = describe_gaze_cnn(state)
        self.assertEqual((status, name), ("warn", "Gaze model"))
        self.assertIn("mobileone_s0_gaze.onnx", detail)
        self.assertIn("python scripts/get_gaze_models.py", detail)
        self.assertIn("pip install onnxruntime", detail)
        models = state / "models"
        models.mkdir()
        (models / "mobileone_s0_gaze.onnx").write_bytes(b"not-a-real-model")
        status, _name, detail = describe_gaze_cnn(state)
        ready = cnn_readiness(state)
        self.assertTrue(ready["file"])
        if ready["onnxruntime"]:
            self.assertEqual(status, "ok")
            self.assertIn("present", detail)
            self.assertIn("onnxruntime imports", detail)
        else:
            self.assertEqual(status, "warn")
            self.assertIn("pip install onnxruntime", detail)
            self.assertNotIn("is missing", detail)


class LiveCnnTests(unittest.TestCase):
    def test_fake_cnn_calibrates_v5_and_a_missing_model_uses_v4(self):
        import math

        import numpy as np

        from ally import gaze_model as gm
        from ally.gaze_helper import (frame_features, mention_fallback, read_cnn_angles,
                                      resolve_live_features)

        class FakeCNN:
            def __init__(self, yaw, pitch):
                self.yaw, self.pitch = yaw, pitch
                self.frame = None

            def angles(self, frame, landmarks):
                self.frame = frame
                return self.yaw, self.pitch

        screen, mm = (1920, 1080), gm.DEFAULT_SCREEN_MM
        mat = np.eye(4)
        mat[2, 3] = 55.0
        coef = np.zeros((10, 2))
        coef[0, 0], coef[7, 0], coef[9, 0] = 0.4, 0.3, 0.01
        coef[1, 1], coef[8, 1], coef[9, 1] = -0.2, 0.35, -0.02
        shy, shp = gm._V4_SIGNS[2], gm._V4_SIGNS[3]
        rng = np.random.default_rng(2)
        samples = []
        while len(samples) < 30:
            h, v = float(rng.uniform(-0.2, 0.2)), float(rng.uniform(-0.2, 0.2))
            yaw_deg, pitch_deg = float(rng.uniform(-10, 10)), float(rng.uniform(-6, 6))
            landmarks = np.zeros((478, 3))
            for ids, side in ((gm.RIGHT_EYE, -1), (gm.LEFT_EYE, 1)):
                landmarks[ids["inner"], :2] = [0.4, 0.5]
                landmarks[ids["outer"], :2] = [0.6, 0.5]
                landmarks[ids["top"], :2] = [0.5, 0.48]
                landmarks[ids["bottom"], :2] = [0.5, 0.52]
                landmarks[ids["iris"], :2] = [0.5 + (h if side < 0 else v) * 0.2, 0.5]
            fake = FakeCNN(yaw_deg, pitch_deg)
            frame = np.zeros((12, 16, 3), dtype=np.uint8)
            angles = read_cnn_angles(fake, frame, landmarks)
            self.assertEqual(angles, (yaw_deg, pitch_deg))
            self.assertIs(fake.frame, frame)
            feat = frame_features({"features": "v5", "screen_mm": mm}, landmarks, {}, mat, 640, 480, cnn=angles)
            self.assertEqual(len(feat[0]), 11)
            self.assertEqual(feat[0][9:], [yaw_deg, pitch_deg])
            eye = gm.hybrid_terms(feat[0][:9], yaw_deg, pitch_deg) @ coef
            point = gm.project_gaze(feat[0], float(eye[0] + shy * feat[0][4]),
                                    float(eye[1] + shp * feat[0][5]), screen, mm)
            if point is None or not (40 < point[0] < 1880 and 40 < point[1] < 1040):
                continue
            samples.append((feat[0], point))
        model = gm.fit(samples, screen, "v5", screen_mm=mm)
        self.assertEqual(model["features"], "v5")
        self.assertEqual(model["cnn_model"], "mobileone_s0")
        self.assertEqual(model["fallback"]["features"], "v4")
        err = np.median([math.hypot(*(np.asarray(gm.predict(model, f)) - np.asarray(t))) for f, t in samples])
        self.assertLess(float(err), 20)

        state = Path(tempfile.mkdtemp())
        feature_set, runner, note = resolve_live_features("v5", state)
        self.assertEqual((feature_set, runner), ("v4", None))
        self.assertIn("Using v4", note)
        self.assertIn("python scripts/get_gaze_models.py", note)
        result, lines = mention_fallback({"calibrated": True, "lines": ["Good."]}, ["Good."], note)
        self.assertEqual(result["cnn_fallback"], note)
        self.assertIn(note, lines)
        self.assertIn(note, result["lines"])
        plain, idle, quiet = resolve_live_features("v4", state)
        self.assertEqual((plain, idle, quiet), ("v4", None, None))


class UnknownElementTests(unittest.TestCase):
    def test_unnamed_client_and_pane_are_not_controls(self):
        info = {"region": "middle", "source": "eyes", "why": ""}
        for role in ("client", "pane", "control", "Client", "Pane"):
            text = looking_text(info, {"role": role, "name": "  "})
            self.assertEqual(text, "Region: middle.")
            self.assertNotIn("control", text.lower())
            self.assertNotIn(role.lower(), text.lower())
        self.assertIn("Button: Save", looking_text(info, {"role": "button", "name": "Save"}))
        gaze, agent = manager()
        gaze.target = lambda t0=None, t1=None: {
            "x": 8, "y": 9, "radius_px": 40, "confidence": 0.8,
            "region": "top left", "source": "eyes", "why": ""}
        ctx = types.SimpleNamespace(agent=agent)
        with mock.patch("ally.tools.gaze_tools._element_at", return_value={"role": "pane", "name": ""}):
            text = where_am_i_looking(ctx)
        self.assertEqual(text, "Region: top left.")
        self.assertNotIn("pane", text.lower())
        self.assertNotIn("control", text.lower())

    def test_client_and_pane_stay_unknown_to_clickable(self):
        self.assertIsNone(clickable.hint_for_role(0x0A))
        self.assertIsNone(clickable.hint_for_role(0x10))
        self.assertIs(clickable.hint_for_role(0x2B), True)
        # The older role map still marks them non-clickable; the point lookup uses the hint.
        self.assertIs(clickable._role_result(0x0A), False)
        self.assertIs(clickable._role_result(0x10), False)

    def test_lookups_initialize_com_on_the_worker_thread(self):
        seen = {}

        def co_initialize(_reserved, mode):
            seen["mode"] = int(mode)
            return 0

        def co_uninitialize():
            seen["uninit"] = True

        windll = types.SimpleNamespace(ole32=types.SimpleNamespace(
            CoInitializeEx=co_initialize, CoUninitialize=co_uninitialize))
        with mock.patch("ctypes.windll", windll, create=True):
            self.assertTrue(gaze_element._com_init())
            gaze_element._com_uninit()
            self.assertTrue(clickable._com_init())
            clickable._com_uninit()
        self.assertEqual(seen["mode"], 0x2)
        self.assertTrue(seen["uninit"])

        def changed(_reserved, _mode):
            return 0x80010106

        blocked = types.SimpleNamespace(ole32=types.SimpleNamespace(CoInitializeEx=changed))
        with mock.patch("ctypes.windll", blocked, create=True):
            self.assertFalse(gaze_element._com_init())
            self.assertFalse(clickable._com_init())

        order = []

        def init():
            order.append("init")
            return True

        def uninit():
            order.append("uninit")

        def describe_at(x, y):
            order.append(("lookup", x, y))
            return {"role": "button", "name": "Ok"}

        with mock.patch("ally.gaze_element.sys.platform", "win32"), \
                mock.patch("ally.gaze_element._com_init", init), \
                mock.patch("ally.gaze_element._com_uninit", uninit), \
                mock.patch("ally.gaze_element._describe", describe_at):
            got = gaze_element.describe(3, 4, timeout=1)
        self.assertEqual(got, {"role": "button", "name": "Ok"})
        self.assertEqual(order, ["init", ("lookup", 3, 4), "uninit"])

        clicked = []

        def click_init():
            clicked.append("init")
            return True

        def click_uninit():
            clicked.append("uninit")

        def lookup(x, y):
            clicked.append(("lookup", x, y))
            return None

        with mock.patch("ally.senses.clickable._com_init", click_init), \
                mock.patch("ally.senses.clickable._com_uninit", click_uninit), \
                mock.patch("ally.senses.clickable._lookup", lookup):
            self.assertIsNone(clickable.is_clickable(5, 6, timeout=1))
        self.assertEqual(clicked, ["init", ("lookup", 5, 6), "uninit"])


class OfferByLookingTests(unittest.TestCase):
    """The proactive layer's offers (ally.contracts) can be answered by looking at Yes or No."""

    def setup(self, answer_offers=True, look_x=100):
        from ally.contracts import EventBus, Offer

        gaze, agent = manager()
        agent.config.data["gaze"]["answer_offers"] = answer_offers
        agent.events = types.SimpleNamespace(add=lambda *a, **k: None)
        bus = EventBus()
        gaze.attach_bus(bus)
        now = time.time()
        gaze.face_at = now
        for i in range(30):  # 1.5 s on one half of the screen (left = Yes, right = No)
            gaze.points.append((now - 1.5 + i * 0.05, look_x, 500, 40))
        return gaze, bus, Offer("Want me to make this bigger?", timeout=2.0)

    def test_dwell_counts_with_real_frame_timing(self):
        # Irregular 15-20 fps timestamps: a 1 s look must count; 0.5 s must not; a look away resets it.
        t, pts = 100.0, []
        for i in range(18):
            t += 0.05 + (i % 3) * 0.01
            pts.append((t, 300, 400, 40))
        self.assertEqual(ongoing_choice(pts, (1920, 1080), 2, t), 0)
        self.assertIsNone(ongoing_choice(pts[-8:], (1920, 1080), 2, t))
        mixed = pts[:-4] + [(p[0], 1500, 400, 40) for p in pts[-4:-2]] + pts[-2:]
        self.assertIsNone(ongoing_choice(mixed, (1920, 1080), 2, t))

    def test_looking_at_yes_answers_the_offer(self):
        gaze, bus, offer = self.setup()
        bus.publish("offer", offer)
        self.assertEqual(offer.wait(3.0), "yes")
        self.assertEqual(offer.answered_by, "eyes")

    def test_off_when_answer_offers_is_false(self):
        gaze, bus, offer = self.setup(answer_offers=False)
        bus.publish("offer", offer)
        self.assertEqual(offer.wait(0.6), "timeout")

    def test_an_earlier_answer_wins(self):
        gaze, bus, offer = self.setup(look_x=1500)
        offer.resolve("no", by="voice")  # answered out loud before the eyes settle
        bus.publish("offer", offer)
        time.sleep(0.3)
        self.assertEqual((offer.choice, offer.answered_by), ("no", "voice"))

    def test_struggle_points_at_the_gaze_when_the_eyes_are_sure(self):
        gaze, agent = manager()
        now = time.time()
        gaze.face_at = now
        _cluster(gaze, now - 1.0, 640, 300, n=20)
        agent.platform = types.SimpleNamespace(frontmost_app=lambda: "Notes")
        agent.bridge = types.SimpleNamespace(chrome_focused=False, tab={})
        agent.events = types.SimpleNamespace(add=lambda *a, **k: None)
        agent.config.data["senses"] = dict(__import__("ally.config", fromlist=["DEFAULTS"]).DEFAULTS["senses"],
                                          vision=True, input=False, cooldown_seconds=0)
        s = Senses(agent)
        s.simulate("squint", tier="weak")
        d = s.simulate("lean", tier="weak")
        self.assertEqual((d["target"]["x"], d["target"]["y"]), (640, 300))


class GazePhraseTests(unittest.TestCase):
    """Offline mode only has these phrase rules, so everyday wordings must reach the gaze tools."""

    def test_everyday_wordings(self):
        from ally import rules

        cases = {
            "show where I'm looking at": "show_dot", "show me where I'm looking": "show_dot",
            "show where I am looking": "show_dot", "show the eye dot": "show_dot",
            "hide the eye dot": "hide_dot", "stop showing where I'm looking": "hide_dot",
            "follow my eyes a bit slower": "follow_slower", "slow down the panning": "follow_slower",
            "follow my eyes faster": "follow_faster", "speed up the panning": "follow_faster",
            "magnify what I'm looking at": "look_magnify", "follow my eyes": "follow_eyes",
        }
        for text, rule in cases.items():
            m = rules.match(text)
            self.assertEqual(m.rule if m else None, rule, text)


if __name__ == "__main__":
    unittest.main()
