"""Agent-side eye tracking, with fakes. No camera and no microphone."""

from __future__ import annotations

import random
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from helpers import ROOT  # noqa: F401  (puts the repo on sys.path)

from ally import gaze_element
from ally.doctor import describe_calibration
from ally.gaze import RETUNE_OFFER, GazeManager, choice_center, choice_index, ongoing_choice
from ally.gaze_helper import (FEATURE_MEMORY, LearningStore, RecentFeatures, adopt_model, apply_choice,
                              apply_click, classify_command, learning_path, load_learning,
                              nearest_click_frame, restore_corrector, save_learning)
from ally.registry import ToolError
from ally.senses import clickable
from ally.senses.manager import Senses
from ally.platforms.base import ReadingPan, ease_pan, edge_speed
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
        # The buffer covers a dwell (a few seconds), so these close frames all stay.
        self.assertGreaterEqual(FEATURE_MEMORY, 2.0)
        self.assertEqual([frame[0] for frame in recent.frames], [0.0, 0.50, 0.85, 1.00, 1.20])
        recent.add(0.0 + FEATURE_MEMORY + 0.1, ["later"])
        self.assertNotIn(0.0, [frame[0] for frame in recent.frames])
        picked = nearest_click_frame([(0.0, ["old"]), (0.85, ["look"]), (1.0, ["click"])], 1.0)
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

    def test_a_settled_look_sends_a_choice_sample(self):
        screen = (1920, 1080)
        self.assertEqual(choice_center(screen, 2, 0), (480.0, 540.0))
        self.assertEqual(choice_center(screen, 2, 1), (1440.0, 540.0))
        self.assertEqual(choice_center(screen, 4, 3), (1440.0, 810.0))
        gaze, _agent = manager()
        stdin = _Stdin()
        gaze.proc = types.SimpleNamespace(poll=lambda: None, stdin=stdin)
        for i in range(10):
            gaze.points.append((10.0 + i * 0.1, 100, 400, 40))
        clock = {"now": 10.9}

        def sleep(_dt):
            clock["now"] += 1.0

        self.assertEqual(gaze.choose(["Yes", "No"], timeout=2, clock=lambda: clock["now"], sleep=sleep), "Yes")
        line = [item for item in stdin.buf.splitlines() if item.startswith("choice ")]
        self.assertEqual(line, ["choice 10.000 10.900 480.0 540.0"])
        kind, payload = classify_command(line[0])
        self.assertEqual(kind, "choice")
        self.assertEqual(payload, (10.0, 10.9, 480.0, 540.0))

        class _Choice:
            def __init__(self):
                self.got = None

            def add(self, features, target, t, source="click"):
                self.got = (list(features), tuple(target), t, source)
                return True

        corrector = _Choice()
        frames = [(10.0, ["start"]), (10.45, ["middle"]), (10.9, ["end"]), (12.0, ["later"])]
        self.assertTrue(apply_choice(corrector, frames, *payload))
        self.assertEqual(corrector.got, (["middle"], (480.0, 540.0), 10.45, "choice"))
        self.assertFalse(apply_choice(corrector, [(1.0, ["old"])], *payload))

        class _NoSource:
            def __init__(self):
                self.got = None

            def add(self, features, target, t):
                self.got = (list(features), tuple(target), t)
                return True

        plain = _NoSource()
        self.assertTrue(apply_choice(plain, frames, *payload))
        self.assertEqual(plain.got[0], ["middle"])
        self.assertEqual(plain.got[1], (480.0, 540.0))


class _Learned:
    """Stand-in for Corrector when to_dict and from_dict are not on the branch yet."""

    def __init__(self, model, samples=None):
        self.model = model
        self.samples = list(samples or [])

    def to_dict(self):
        return {"samples": [dict(sample) for sample in self.samples]}

    @classmethod
    def from_dict(cls, model, data):
        return cls(model, (data or {}).get("samples"))


class _BareCorrector:
    def __init__(self, model):
        self.model = model


class LearningTests(unittest.TestCase):
    def _model(self):
        return {"created": 20.5, "dots": 9, "offset": [0.0, 0.0]}

    def _sample(self):
        return {"features": [0.1, -0.2, 0.3], "target": [80.0, 120.0], "t": 4.5, "source": "click"}

    def test_save_and_restore_round_trip(self):
        from ally import gaze_model

        model = self._model()
        sample = self._sample()
        with tempfile.TemporaryDirectory() as tmp:
            path = learning_path(Path(tmp) / "gaze_calibration.json")
            learned = _Learned(model, [sample])
            self.assertTrue(save_learning(path, learned, model))
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("png", text)
            self.assertNotIn("image", text.lower())
            loaded = load_learning(path, model)
            self.assertEqual(loaded["samples"], [sample])
            restored = restore_corrector(model, loaded, True, _Learned)
            self.assertEqual(restored.samples, [sample])
            # A new calibration has a new stamp, so the old file does not come back.
            self.assertIsNone(load_learning(path, {"created": 99.0, "dots": 9}))
            self.assertIsNone(restore_corrector(model, loaded, False, _Learned))
            store = LearningStore(path)
            store.clear()
            self.assertFalse(path.exists())
            fresh = restore_corrector(model, None, True, _Learned)
            self.assertEqual(fresh.samples, [])

            if hasattr(gaze_model.Corrector, "to_dict") and hasattr(gaze_model.Corrector, "from_dict"):
                real = gaze_model.Corrector(model)
                self.assertTrue(hasattr(real, "to_dict"))
            else:
                bare = restore_corrector(model, loaded, True, _BareCorrector)
                self.assertIsInstance(bare, _BareCorrector)
                self.assertFalse(hasattr(gaze_model.Corrector, "to_dict"))
                self.assertFalse(hasattr(gaze_model.Corrector, "from_dict"))

    def test_helper_does_not_import_the_network_or_write_pictures(self):
        text = (ROOT / "ally" / "gaze_helper.py").read_text(encoding="utf-8")
        for word in ("urllib", "socket", "requests", "imwrite", "VideoWriter", "imencode"):
            self.assertNotIn(word, text)

    def test_pictures_are_not_written(self):
        model = self._model()

        class _Pictured:
            def to_dict(self):
                return {"rows": [[0] * 80 for _ in range(80)]}

        class _Named:
            def to_dict(self):
                return {"png": [1, 2, 3]}

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gaze.learning.json"
            self.assertFalse(save_learning(path, _Pictured(), model))
            self.assertFalse(save_learning(path, _Named(), model))
            self.assertFalse(path.exists())

    def test_saves_at_most_once_a_minute_and_on_exit(self):
        model = self._model()
        learned = _Learned(model, [self._sample()])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gaze.learning.json"
            store = LearningStore(path, interval=60)
            store.mark()
            self.assertTrue(store.maybe_save(learned, model, 1000))
            learned.samples.append({"features": [1.0], "target": [2.0, 3.0], "t": 9.0, "source": "click"})
            store.mark()
            self.assertFalse(store.maybe_save(learned, model, 1030))
            self.assertNotIn("9.0", path.read_text(encoding="utf-8"))
            self.assertTrue(store.save(learned, model, 1030))
            self.assertIn("9.0", path.read_text(encoding="utf-8"))
            self.assertFalse(store.save(learned, model, 1031))

    def test_adopt_model_restores_the_sidecar(self):
        import numpy as np

        from ally import gaze_model

        gaze, _agent = manager()
        screen = gaze.screen
        rnd = np.random.default_rng(3)
        dots = [(fx * screen[0], fy * screen[1]) for fy in (0.06, 0.5, 0.94) for fx in (0.08, 0.5, 0.92)]
        trained = []
        for tx, ty in dots:
            for _ in range(8):
                u, v = tx / screen[0] - 0.5, ty / screen[1] - 0.5
                feat = [0.3 * u, 0.2 * v, 0.8 * u, 0.6 * v, 0, 0, 0, 0, 0.4]
                feat = (np.array(feat) + rnd.normal(0, 0.001, 9)).tolist()
                trained.append((feat, (tx, ty)))
        model = gaze_model.fit(trained, screen)
        model["camera"] = 0
        gaze_model.save(model, gaze.calibration_path)
        sample = self._sample()
        self.assertTrue(save_learning(learning_path(gaze.calibration_path), _Learned(model, [sample]), model))

        def from_dict(loaded, data):
            made = gaze_model.Corrector(loaded)
            made.restored = data
            return made

        with mock.patch.object(gaze_model.Corrector, "from_dict", from_dict, create=True):
            loaded, corrector = adopt_model(gaze.calibration_path, screen, 0, True)
        self.assertEqual(loaded["created"], model["created"])
        self.assertEqual(corrector.restored["samples"], [sample])
        _loaded, quiet = adopt_model(gaze.calibration_path, screen, 0, False)
        self.assertIsNone(quiet)


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
        dots = [(fx * screen[0], fy * screen[1]) for fy in (0.06, 0.5, 0.94) for fx in (0.08, 0.5, 0.92)]
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
        for _ in range(5):  # about half a second at the edge before anything moves
            vx, vy, held = ease_pan(0.0, 0.5, held, vel, 0.1)
            vel = (vx, vy)
            self.assertEqual((vx, vy), (0.0, 0.0))
        self.assertLess(held[0], 0.6)
        vx, vy, held = ease_pan(0.0, 0.5, held, vel, 0.11)
        self.assertGreaterEqual(held[0], 0.6)
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
        self.assertEqual(resolve_pan_speed("slow", 0.35), 0.4)
        self.assertEqual(resolve_pan_speed("medium", 1.0), 0.85)
        self.assertEqual(resolve_pan_speed("fast", 0.2), 1.5)
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
            set_gaze_source=lambda latest, pan_speed=1.0, max_speed=None: applied.update(
                speed=pan_speed, latest=latest, max_speed=max_speed))
        agent.config.data["gaze"]["pan_speed"] = 0.35
        said = eye_follow_speed(types.SimpleNamespace(agent=agent), "slower")
        self.assertIn("0.2", said)
        self.assertIn("pan_speed = 0.2            # how fast the view moves", saved["text"])
        self.assertEqual(applied["speed"], 0.2)
        self.assertIsNone(applied["max_speed"])  # a speed change keeps the saved top speed
        self.assertIs(applied["latest"].__self__, gaze)
        self.assertEqual(match("follow my eyes slower").actions, [("eye_follow_speed", {"speed": "slower"})])
        self.assertEqual(match("follow my eyes faster").actions, [("eye_follow_speed", {"speed": "faster"})])
        self.assertEqual(match("follow my eyes").actions, [("magnifier_follow", {"target": "eyes"})])


class ReadingPanTests(unittest.TestCase):
    """The magnifier as a reading aid, driven by a simulated reader. Screen 1920x1080."""

    W, H = 1920, 1080

    def run_reader(self, level=2.0, rate=150, bias=(80, 40), jitter=10, speed=0.85, lines=3,
                   start=150, end=1750, max_speed=None):
        """A reader moves along each line in 0.25 s fixations at `rate` px/s (unmagnified), then
        looks for the start of the next line. The tracker adds a fixed bias and jitter."""
        rnd = random.Random(7)
        vw, vh = self.W / level, self.H / level
        pan = ReadingPan()
        x, y = max(0.0, start + 100 - vw / 2), max(0.0, 300 - vh / 2)
        cx, line, fix, t = float(start), 0, 0.0, 0.0
        seeking = False
        out = {"frames": 0, "outside": 0, "returns": 0, "starts": [], "pace": [], "fastest": 0.0,
               "fastest_return": 0.0}
        while line < lines and t < 60:
            if not seeking:
                fix += 1 / 30
                if fix >= 0.25:
                    fix, cx = 0.0, cx + rate * 0.25
                if cx >= end:
                    line, seeking = line + 1, True
            if seeking:
                if start >= x:
                    cx, seeking = float(start), False
                    out["starts"].append(x)
                else:
                    cx = x + 0.08 * vw  # waits at the left of the view
            if not seeking:
                out["frames"] += 1
                out["outside"] += not 0.1 <= (cx - x) / vw <= 0.92
            gx = (cx - x) * level + bias[0] + rnd.gauss(0, jitter)
            gy = (300 + 28 * line - y) * level + bias[1] + rnd.gauss(0, jitter)
            was = pan.returning
            x2, y2 = pan.step((gx / self.W, gy / self.H), (x, y), (vw, vh), (self.W - vw, self.H - vh), 1 / 30, speed,
                              max_speed)
            out["returns"] += was is None and pan.returning is not None
            views_per_s = max(abs(x2 - x) / vw, abs(y2 - y) / vh) * 30
            key = "fastest" if was is None and pan.returning is None else "fastest_return"
            out[key] = max(out[key], views_per_s)
            if not seeking and pan.returning is None and 0.45 <= (cx - start) / (end - start) <= 0.75:
                out["pace"].append((x2 - x) * 30)
            x, y, t = x2, y2, t + 1 / 30
        return out

    def test_follows_a_reader_at_their_pace(self):
        for level, rate in ((1.5, 100), (2.0, 150), (3.0, 200)):
            got = self.run_reader(level=level, rate=rate)
            self.assertLess(got["outside"] / got["frames"], 0.02, (level, rate))
            pace = sorted(got["pace"])[len(got["pace"]) // 2]
            self.assertAlmostEqual(pace, rate, delta=0.25 * rate)

    def test_tracker_bias_and_speed_setting_do_not_lose_the_reader(self):
        for bias in ((0, 0), (150, 80), (-150, -60)):
            got = self.run_reader(bias=bias, jitter=30)
            self.assertLess(got["outside"] / got["frames"], 0.02, bias)
        for speed in (0.1, 0.85, 2.0):
            got = self.run_reader(level=3.0, rate=200, speed=speed)
            self.assertLess(got["outside"] / got["frames"], 0.02, speed)

    def test_the_top_speed_caps_every_pan(self):
        for level, rate in ((1.5, 150), (2.0, 150)):
            got = self.run_reader(level=level, rate=rate, max_speed=0.35)
            self.assertLessEqual(got["fastest"], 0.35 + 1e-6)
            self.assertLessEqual(got["fastest_return"], 2 * 0.35 + 0.02)  # the trip back may go twice as fast
            self.assertLess(got["outside"] / got["frames"], 0.02, (level, rate))
            self.assertEqual(got["returns"], 2)
        dx, _ = self.step_many(ReadingPan(), [(0.02, 0.5)] * 90, max_speed=0.1)
        self.assertGreaterEqual(dx, -0.1 * 960 * 3 - 1)  # 3 s at an edge, never past the cap

    def test_looking_back_left_returns_to_the_start_of_the_line(self):
        got = self.run_reader(lines=3)
        self.assertEqual(got["returns"], 2)
        first = got["starts"][0] if got["starts"] else None
        self.assertIsNotNone(first)

    def test_the_return_eases_in(self):
        pan = ReadingPan()
        pan.line_start, pan.read_at, pan.t = 0.0, 0.0, 0.5
        pan.returning = (900.0, 300.0, 0.0, 300.0, pan.t)
        x, _ = pan.step((0.1, 0.5), (900.0, 300.0), (960, 540), (960, 540), 1 / 30)
        self.assertGreater(x, 890.0)  # barely moved in the first frame
        for _ in range(40):
            x, _ = pan.step((0.1, 0.5), (x, 300.0), (960, 540), (960, 540), 1 / 30)
        self.assertEqual(x, 0.0)
        self.assertIsNone(pan.returning)

    def step_many(self, pan, points, pos=(400.0, 200.0), level=2.0, max_speed=None):
        vw, vh = self.W / level, self.H / level
        x, y = pos
        for nx, ny in points:
            x, y = pan.step((nx, ny), (x, y), (vw, vh), (self.W - vw, self.H - vh), 1 / 30, 0.85, max_speed)
        return x - pos[0], y - pos[1]

    def test_staring_or_one_glance_right_does_not_pan(self):
        self.assertEqual(self.step_many(ReadingPan(), [(0.75, 0.5)] * 150), (0.0, 0.0))
        glance = [(0.4, 0.5)] * 20 + [(0.7, 0.5)] * 15 + [(0.4, 0.5)] * 40
        self.assertEqual(self.step_many(ReadingPan(), glance), (0.0, 0.0))

    def test_looking_around_without_reading_leaves_the_view_still(self):
        rnd = random.Random(1)
        moved = 0.0
        for seed in range(4):
            rnd.seed(seed)
            points = []
            while len(points) < 1800:  # one minute of fixations anywhere in the middle
                p = (rnd.uniform(0.2, 0.8), rnd.uniform(0.25, 0.75))
                points += [p] * rnd.randint(8, 27)
            dx, dy = self.step_many(ReadingPan(), points[:1800])
            moved += abs(dx) + abs(dy)
        self.assertLess(moved, 200)  # four minutes: at most one brief, gentle nudge

    def test_an_edge_still_pans(self):
        dx, dy = self.step_many(ReadingPan(), [(0.02, 0.5)] * 60)
        self.assertLess(dx, -50)
        self.assertEqual(dy, 0.0)

    def test_help_me_read_magnifies_where_i_look(self):
        for text in ("help me read", "help me read this", "Ally, help me read the page"):
            self.assertEqual(match(text).actions, [("magnify_where_i_look", {})], text)


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
            self.assertEqual(len(feat[0]), 12)
            self.assertEqual(feat[0][9:11], [yaw_deg, pitch_deg])
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
