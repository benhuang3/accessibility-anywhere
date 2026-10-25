"""Face setup: steps, the scan ring, labelled senses samples, lean, phrases, restore. No camera."""

from __future__ import annotations

import math
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from helpers import ROOT  # noqa: F401

from ally.gaze import FACE_SETUP_OFFER, GazeManager, restore_face_profile, save_face_profile
from ally.gaze_helper import (CHECK_PROMPT, EYES_PROMPT, FACE_ABOUT, GLARE_FIX, LEAN_IN, PROMPT_SECONDS,
                              READ_PHASES, READ_PROMPT, SCAN_PROMPT, SCAN_SECONDS, SCAN_SEGMENTS,
                              SIT_BACK, FaceSetup, classify_command, face_plan_seconds, labelled_line,
                              scan_issue, segment_index)
from ally.rules import match
from ally.senses.manager import Senses
from ally.tools.gaze_tools import face_setup, face_setup_words


def _feat():
    return ([0.01, 0.02, 0, 0, 0, -0.1, 0, -2, -30, 0.5], 0.5)


def _face(**kw):
    base = {"present": True, "width": 0.30, "light": 120, "eye_light": 40,
            "yaw": 0.0, "pitch": 0.0, "cx": 0.5, "cy": 0.5, "distance": 1.0}
    base.update(kw)
    return base


def _pose_face(logic: FaceSetup) -> dict:
    setup = logic.setup
    if setup.stage == "settle":
        pose = getattr(setup, "_settle_pose", "normal")
    else:
        cur = setup.current
        pose = cur.pose if cur is not None else "normal"
    width = {"normal": 0.30, "back": 0.24, "close": 0.39}.get(pose, 0.30)
    dist = {"normal": 1.0, "back": 1.25, "close": 0.80}.get(pose, 1.0)
    return _face(width=width, distance=dist)


def _reading_sample(phase: str) -> dict:
    squint = 0.42 if phase == "squint" else 0.12
    smile = 0.2 if phase == "smile" else 0.0
    aperture = 0.22 if phase == "squint" else 0.40
    lower = 0.18 if phase == "squint" else 0.10
    return {
        "squint_l": squint, "squint_r": squint, "width": 0.30, "yaw": 0.0, "pitch": 0.0,
        "blink": 0.0, "smile": smile, "light": 120.0,
        "aperture": aperture, "aperture_r": aperture, "aperture_l": aperture,
        "lower_raise": lower, "lower_raise_r": lower, "lower_raise_l": lower,
        "iris_vertical": 0.5, "eyeLookDown": 0.0, "mouthSmile": smile,
        "eyeSquint": squint, "head_yaw": 0.0, "head_pitch": 0.0, "head_distance": 50.0,
        "iris_diameter": 12.0, "face_width": 180.0,
    }


class ScanTests(unittest.TestCase):
    def test_the_ring_fills_when_the_head_turns_in_a_circle(self):
        setup = FaceSetup((1920, 1080), ready_seconds=0)
        face = _face()
        setup.step(0.0, _feat(), face, 0.0)
        self.assertEqual(setup.segments, [False] * SCAN_SEGMENTS)
        self.assertIsNone(segment_index(0, 0))
        for k in range(SCAN_SEGMENTS):
            ang = -math.pi + (k + 0.5) * (2.0 * math.pi / SCAN_SEGMENTS)
            yaw = 25.0 * math.cos(ang)
            pitch = 25.0 * math.sin(ang)
            self.assertEqual(segment_index(yaw, pitch), k)
            setup.step(0.05 * (k + 1), _feat(), _face(yaw=yaw, pitch=pitch), 0.05 * (k + 1))
        self.assertEqual(setup.segments, [True] * SCAN_SEGMENTS)
        self.assertEqual(setup.caption(), "Step 1 of 6")
        self.assertLess(setup.progress(), 0.2)

    def test_scan_names_light_distance_and_glare(self):
        self.assertEqual(scan_issue(None), "I can't see your face. Look at the screen.")
        self.assertEqual(scan_issue({"present": False, "two": True}), "I see two faces. Only one person, please.")
        self.assertEqual(scan_issue(_face(width=0.10)), "move a little closer")
        self.assertEqual(scan_issue(_face(width=0.60)), "move a little farther back")
        self.assertEqual(scan_issue(_face(light=10)), "turn on a light in front of you")
        self.assertEqual(scan_issue(_face(eye_light=240)), GLARE_FIX)
        self.assertEqual(GLARE_FIX, "Move the light off to the side.")
        # A turned head is the scan itself, not an error.
        self.assertIsNone(scan_issue(_face(yaw=30, pitch=-15)))
        setup = FaceSetup((800, 600))
        setup.step(0.0, _feat(), _face(light=5), 0.0)
        setup.step(1.0, _feat(), _face(light=5), 1.0)
        self.assertEqual(setup.issue, "turn on a light in front of you")
        self.assertAlmostEqual(setup._scan_good, 0.0)

    def test_a_labelled_line_is_numbers_only(self):
        line = labelled_line("neutral", 1.25, {"width": 0.3, "note": "nope", "ok": True, "yaw": 1})
        self.assertEqual(set(line), {"label", "t", "f"})
        self.assertEqual(line["f"], {"width": 0.3, "yaw": 1.0})
        self.assertNotIn("note", line["f"])


class FlowTests(unittest.TestCase):
    def test_steps_timings_prompts_labels_and_lean(self):
        self.assertGreaterEqual(face_plan_seconds(), 75)
        self.assertIn("90", FACE_ABOUT)
        self.assertEqual([row[1] for row in READ_PHASES], [8.0, 4.0, 4.0, 3.0, 3.0, 1.5, 3.0])
        setup = FaceSetup((1920, 1080))
        labels = []
        seen = []
        t = 0.0
        state = "running"
        fit_at = None
        for _ in range(4000):
            if setup.step_n and (not seen or seen[-1] != setup.step_n):
                seen.append(setup.step_n)
            sample = _reading_sample(setup.phase) if setup.wants_sample else None
            state = setup.step(t, _feat(), _pose_face(setup), t, sample)
            if setup.step_n and (not seen or seen[-1] != setup.step_n):
                seen.append(setup.step_n)
            labels.extend(setup.pull_labels())
            if state == "fit":
                fit_at = t
                break
            t += 0.05
        else:
            self.fail(f"did not reach the check ({setup.stage}, step {setup.step_n})")
        self.assertEqual(seen, [1, 2, 3, 4, 5, 6])
        self.assertEqual(setup.announcements, [
            SCAN_PROMPT, EYES_PROMPT, SIT_BACK, LEAN_IN, READ_PROMPT, CHECK_PROMPT])
        self.assertGreaterEqual(setup.prompt_at[2] - setup.prompt_at[1], SCAN_SECONDS - 0.1)
        back = [event for event in setup.setup.events if event.get("pose") == "back"]
        close = [event for event in setup.setup.events if event.get("pose") == "close"]
        self.assertGreaterEqual(back[0]["shown"] - setup.prompt_at[3], PROMPT_SECONDS - 0.06)
        self.assertGreaterEqual(close[0]["shown"] - setup.prompt_at[4], PROMPT_SECONDS - 0.06)
        self.assertGreaterEqual(fit_at - setup.prompt_at[6], PROMPT_SECONDS - 0.06)
        names = [name for name, _stamp in setup.phase_log]
        self.assertEqual(names, ["neutral", "top", "bottom", "smile", "squint", "rest", "squint"])
        for i in range(len(setup.phase_log) - 1):
            gap = setup.phase_log[i + 1][1] - setup.phase_log[i][1]
            self.assertAlmostEqual(gap, READ_PHASES[i][1], delta=0.06)
        got = {}
        for line in labels:
            self.assertEqual(set(line), {"label", "t", "f"})
            self.assertTrue(all(isinstance(v, float) for v in line["f"].values()))
            got.setdefault(line["label"], 0)
            got[line["label"]] += 1
        self.assertGreater(got.get("neutral", 0), 40)
        self.assertGreater(got.get("top", 0), 20)
        self.assertGreater(got.get("bottom", 0), 20)
        self.assertGreater(got.get("smile", 0), 15)
        self.assertGreater(got.get("squint", 0), 30)
        self.assertNotIn("rest", got)
        lean = setup.lean()
        self.assertAlmostEqual(lean["width"], 0.39 / 0.30, places=2)
        self.assertAlmostEqual(lean["distance"], 0.80 / 1.0, places=2)
        normal = [event for event in setup.setup.events if event.get("pose") == "normal"]
        self.assertGreaterEqual(len(normal), 9)
        setup.accept_fit({"version": 1, "screen": [1920, 1080]})
        self.assertEqual(setup.caption(), "Step 6 of 6")
        t += 0.05
        for _ in range(800):
            state = setup.step(t, _feat(), _face(), t)
            if state == "score":
                break
            t += 0.05
        else:
            self.fail(setup.setup.stage)
        checks = [event for event in setup.setup.events if event.get("phase") == "validation"]
        self.assertEqual(len(checks), 5)


def _agent():
    return types.SimpleNamespace(
        config=types.SimpleNamespace(data={"senses": {"vision": True, "input": False, "fps": 10,
                                                       "squint_method": "v1", "lean_method": "v1",
                                                       "window_seconds": 4, "cooldown_seconds": 30,
                                                       "solo": [], "min_confidence": 0.5},
                                             "gaze": {"enabled": True}}),
        paths=types.SimpleNamespace(state_dir=Path(tempfile.mkdtemp(prefix="ally-face-"))),
        events=types.SimpleNamespace(add=lambda *a, **k: None),
        gaze=None,
    )


class SensesIntakeTests(unittest.TestCase):
    def _feed(self, senses, phase, frame, n):
        for _ in range(n):
            senses.intake_label(phase, frame, 1.0)

    def test_labelled_phases_fit_and_the_lean_ratio_is_saved(self):
        agent = _agent()
        senses = Senses(agent)
        senses.begin_setup_intake()
        self._feed(senses, "neutral", _reading_sample("neutral"), 40)
        self._feed(senses, "top", _reading_sample("top"), 15)
        self._feed(senses, "bottom", _reading_sample("bottom"), 15)
        self._feed(senses, "smile", _reading_sample("smile"), 12)
        self._feed(senses, "squint", _reading_sample("squint"), 20)
        senses.intake_label("nope", {"width": 1}, 1.0)
        lean = {"width": 1.28, "distance": 0.8, "normal_width": 0.30, "near_width": 0.384,
                "normal_distance": 1.0, "near_distance": 0.8}
        result = senses.finish_setup_intake(lean)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["squint_enabled"], result)
        self.assertEqual(senses.baseline.feature_stats["lean_ratio"]["width"], 1.28)
        self.assertAlmostEqual(senses.tune.lean_ratio, 1.28)
        saved = senses.baseline_path.read_text(encoding="utf-8")
        self.assertIn("lean_ratio", saved)
        self.assertIn("personal_model", saved)
        again = Senses(agent)
        self.assertAlmostEqual(again.tune.lean_ratio, 1.28)
        self.assertEqual(again.squint.__class__.__name__, "PersonalSquintDetector")

    def test_a_weak_squint_turns_sensing_off_and_says_so(self):
        agent = _agent()
        senses = Senses(agent)
        senses.begin_setup_intake()
        frame = _reading_sample("neutral")
        self._feed(senses, "neutral", frame, 40)
        self._feed(senses, "squint", frame, 20)
        result = senses.finish_setup_intake({"width": 1.2, "distance": 0.9})
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["squint_enabled"])
        self.assertFalse(senses.baseline.squint_reliable)
        words = face_setup_words(
            {"calibrated": True, "validation": {"error_px": 120, "region_hits": 5, "region_total": 5},
             "grade": "good", "lines": ["Good.", "About 120 pixels off, and 5 of 5 areas right."]},
            result)
        self.assertIn("120", words)
        self.assertIn("Squint sensing is off for you", words)
        self.assertIn("Lean and the other signals still work", words)


class PhraseTests(unittest.TestCase):
    def test_face_phrases_and_the_eye_alias(self):
        for text in ("set up my face", "face setup", "scan my face", "please scan my face"):
            self.assertEqual(match(text).actions, [("face_setup", {})], text)
        for text in ("set up my eyes", "calibrate my eyes", "calibrate"):
            self.assertEqual(match(text).actions, [("calibrate_eye_tracker", {})], text)
        self.assertEqual(match("quick calibrate").actions, [("check_eye_tracker", {})])
        self.assertEqual(classify_command("face"), ("face", None))
        self.assertEqual(classify_command("calibrate"), ("calibrate", None))
        self.assertEqual(classify_command("check"), ("check", None))


class ProfileTests(unittest.TestCase):
    def test_startup_restores_a_profile_when_nothing_local_is_saved(self):
        agent = _agent()
        said = []
        agent.speaker = types.SimpleNamespace(
            say=lambda text, interrupt=True: said.append((text, interrupt)))
        applied = {}

        class Store:
            def __init__(self, paths, cfg):
                self.paths = paths

            def load(self):
                return {"gaze": {"version": 1}}

            def save(self, profile):
                applied["saved"] = profile

        face_mod = types.ModuleType("ally.face_profile")
        face_mod.build = lambda **kw: {"built": kw}
        face_mod.apply = lambda agent, profile: applied.setdefault("applied", profile) or ["ok"]
        store_mod = types.ModuleType("ally.profile_store")
        store_mod.ProfileStore = Store
        with mock.patch.dict(sys.modules, {"ally.face_profile": face_mod, "ally.profile_store": store_mod}):
            self.assertTrue(restore_face_profile(agent))
        self.assertEqual(said, [("Welcome back. I restored your face setup.", False)])
        self.assertEqual(applied["applied"], {"gaze": {"version": 1}})
        (agent.paths.state_dir / "gaze_calibration.json").write_text("{}", encoding="utf-8")
        with mock.patch.dict(sys.modules, {"ally.face_profile": face_mod, "ally.profile_store": store_mod}):
            self.assertFalse(restore_face_profile(agent))

    def test_save_and_setup_still_work_when_the_profile_modules_are_missing(self):
        agent = _agent()
        agent.gaze = types.SimpleNamespace(calibration_path=agent.paths.state_dir / "gaze_calibration.json")
        senses = Senses(agent)
        agent.senses = senses
        with mock.patch.dict(sys.modules, {"ally.face_profile": None, "ally.profile_store": None}):
            self.assertFalse(save_face_profile(agent))
            self.assertFalse(restore_face_profile(agent))
        said = []
        agent.speaker = types.SimpleNamespace(say=lambda text, interrupt=True: said.append(text) or True)
        agent.gaze = types.SimpleNamespace(
            calibrate=lambda timeout=240: {
                "calibrated": True,
                "validation": {"error_px": 100, "region_hits": 5, "region_total": 5},
                "grade": "good",
                "lines": ["Good.", "About 100 pixels off, and 5 of 5 areas right."],
                "lean": {"width": 1.2, "distance": 0.85},
            })
        agent.os = None

        class OS:
            def view(self):
                return (1.0, 0, 0)

            def other_magnifier_open(self):
                return False

            def magnify(self, level):
                return "ok"

        ctx = types.SimpleNamespace(agent=agent, os=OS(), say=lambda text: said.append(text))
        senses.begin_setup_intake()
        for _ in range(40):
            senses.intake_label("neutral", _reading_sample("neutral"), 1.0)
        for _ in range(20):
            senses.intake_label("squint", _reading_sample("squint"), 2.0)
        with mock.patch.dict(sys.modules, {"ally.face_profile": None, "ally.profile_store": None}):
            words = face_setup(ctx)
        self.assertIn("100", words)
        self.assertIn("Squint sensing is on for you", words)
        self.assertTrue(senses.baseline_path.exists())


class OfferTests(unittest.TestCase):
    def test_the_suggestion_is_once_and_calibrate_runs_the_face_setup(self):
        agent = _agent()
        said = []
        agent.speaker = types.SimpleNamespace(say=lambda text, interrupt=True: said.append(text))
        agent.events = types.SimpleNamespace(add=lambda *a, **k: None)
        gaze = GazeManager(agent)
        agent.gaze = gaze
        gaze.maybe_offer_face_setup()
        gaze.maybe_offer_face_setup()
        self.assertEqual(said, [FACE_SETUP_OFFER])
        gaze2 = GazeManager(agent)
        agent.gaze = gaze2
        gaze2.maybe_offer_face_setup()
        self.assertEqual(said, [FACE_SETUP_OFFER])
        sent = []

        def send(line):
            sent.append(line)
            gaze._calib_result = {"calibrated": True}
            gaze._calib_done.set()
            return True

        gaze.ready = True
        gaze.send = send
        gaze.calibrate(timeout=1)
        self.assertEqual(sent, ["face"])
        self.assertFalse(gaze.setting_up)


if __name__ == "__main__":
    unittest.main()
