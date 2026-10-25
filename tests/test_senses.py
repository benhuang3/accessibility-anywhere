"""Struggle sensing: detectors, fusion, the agent-side manager, and the camera privacy rules.
No camera, microphone, mouse hook or numpy needed (face.py tests skip without numpy)."""

import ast
import copy
import io
import tempfile
import threading
import time
import types
import unittest
from unittest import mock
from pathlib import Path

from helpers import ROOT, temp_home

from ally import config as config_mod
from ally.gaze import GazeManager
from ally.log import EventLog
from ally.senses.detectors import (Baseline, HesitationDetector, LeanDetector, RageClickDetector, RetypeDetector,
                                   Signal, SquintDetector, Tuning, gate)
from ally.senses.fusion import KINDS, Fuser
from ally.senses.contract import to_event, validate_struggle
from ally.senses import clickable
from ally.senses.manager import Senses

try:
    import numpy as np

    from ally.senses import face
except ImportError:  # numpy is optional
    np = face = None

BASE = Baseline(squint=0.10, width=0.30)


def frame(squint=0.10, l=None, r=None, smile=0.0, blink=0.0, yaw=0.0, pitch=0.0, width=0.30, light=120.0):
    return {"squint_l": squint if l is None else l, "squint_r": squint if r is None else r, "smile": smile,
            "blink": blink, "yaw": yaw, "pitch": pitch, "width": width, "light": light}


def run(detector, frames, t0=100.0, fps=10, base=BASE):
    """Feed (count, frame) pairs at fps; returns the signals that fired."""
    out, t = [], t0
    for n, f in frames:
        for _ in range(n):
            t += 1.0 / fps
            s = detector.feed(f, t, base)
            if s:
                out.append(s)
    return out


class BaselineTests(unittest.TestCase):
    def test_median_of_good_frames(self):
        frames = [frame(0.1)] * 40 + [frame(0.9, blink=0.9)] * 20  # blinks are left out
        b = Baseline.from_samples(frames, 1.0)
        self.assertAlmostEqual(b.squint, 0.1)
        self.assertAlmostEqual(b.width, 0.3)
        self.assertEqual(b.samples, 40)

    def test_calibration_marks_unsteady_squint_unreliable(self):
        samples = [frame(0.1 if i % 2 else 0.35) for i in range(40)]
        b = Baseline.from_samples(samples, 1.0)
        self.assertFalse(b.squint_reliable)
        self.assertTrue(b.squint_why)
        self.assertFalse(Baseline.from_dict(b.to_dict()).squint_reliable)

    def test_rejects_bad_setups(self):
        with self.assertRaisesRegex(ValueError, "only 5 frames"):
            Baseline.from_samples([frame()] * 5, 1.0)
        with self.assertRaisesRegex(ValueError, "narrowed"):
            Baseline.from_samples([frame(0.7)] * 40, 1.0)
        with self.assertRaisesRegex(ValueError, "smiling"):
            Baseline.from_samples([frame(smile=0.8)] * 40, 1.0)
        with self.assertRaisesRegex(ValueError, "too far or too close"):
            Baseline.from_samples([frame(width=0.01)] * 40, 1.0)

    def test_round_trip_and_bounds(self):
        b = Baseline.from_dict(BASE.to_dict())
        self.assertEqual((b.squint, b.width), (0.10, 0.30))
        self.assertIsNone(Baseline.from_dict({"squint": 0.9, "width": 0.3}))
        self.assertIsNone(Baseline.from_dict({"width": 0.3}))

    def test_learns_the_persons_left_right_difference(self):
        b = Baseline.from_samples([frame(l=0.45, r=0.36) for _ in range(40)], 0.0)
        self.assertAlmostEqual(b.asym, 0.09, places=3)
        self.assertAlmostEqual(Baseline.from_dict(b.to_dict()).asym, 0.09, places=3)

    def test_drift_follows_slow_changes_only(self):
        b = Baseline(0.10, 0.30)
        b.drift(frame(0.14), 60.0)  # small change: follows a little
        self.assertGreater(b.squint, 0.10)
        self.assertLess(b.squint, 0.14)
        b2 = Baseline(0.10, 0.30)
        b2.drift(frame(0.40), 60.0)  # a squint in progress: ignored
        self.assertEqual(b2.squint, 0.10)


class SquintTests(unittest.TestCase):
    def setUp(self):
        self.tune = Tuning()

    def test_strong_squint_fires_once(self):
        sigs = run(SquintDetector(self.tune), [(20, frame()), (40, frame(0.35))])
        self.assertEqual(len(sigs), 1)
        self.assertEqual((sigs[0].kind, sigs[0].tier), ("squint", "strong"))
        self.assertGreaterEqual(sigs[0].confidence, 0.6)

    def test_weak_squint(self):
        sigs = run(SquintDetector(self.tune), [(30, frame(0.25))])
        self.assertEqual([(s.tier) for s in sigs], ["weak"])
        self.assertLess(sigs[0].confidence, 0.6)

    def test_short_squint_ignored(self):
        self.assertEqual(run(SquintDetector(self.tune), [(10, frame(0.4)), (30, frame())]), [])

    def test_smile_wink_dark_and_turned_are_gated(self):
        for f in (frame(0.4, smile=0.6), frame(l=0.6, r=0.2), frame(0.4, light=10), frame(0.4, yaw=35)):
            self.assertEqual(run(SquintDetector(self.tune), [(40, f)]), [], f)
        self.assertEqual(gate(frame(0.4, smile=0.6), self.tune), "smile")
        self.assertEqual(gate(frame(), self.tune), "")

    def test_blinks_dont_reset_the_hold(self):
        f, b = frame(0.4), frame(0.4, blink=0.9)
        sigs = run(SquintDetector(self.tune), [(6, f), (1, b), (6, f), (1, b), (6, f)])
        self.assertEqual(len(sigs), 1)

    def test_must_relax_before_next_squint(self):
        d = SquintDetector(self.tune)
        self.assertEqual(len(run(d, [(20, frame(0.4))])), 1)
        self.assertEqual(run(d, [(150, frame(0.4))], t0=103), [])  # still squinting: latched...
        self.assertEqual(len(run(d, [(100, frame(0.4))], t0=118)), 1)  # ...until rearm_seconds pass
        self.assertEqual(len(run(d, [(5, frame()), (20, frame(0.4))], t0=160)), 1)  # relaxed, then again

    def test_uses_the_persons_baseline(self):
        narrow = Baseline(squint=0.35, width=0.3)  # someone whose eyes always read narrow
        self.assertEqual(run(SquintDetector(self.tune), [(40, frame(0.40))], base=narrow), [])

    def test_held_squint_counts_though_its_blink_score_is_high(self):
        # A hard squint closes the eyes partway: a real recording read blink 0.57 for the whole squint.
        sigs = run(SquintDetector(self.tune), [(20, frame()), (40, frame(0.36, blink=0.57))])
        self.assertEqual([s.kind for s in sigs], ["squint"])

    def test_brief_blinks_and_shut_eyes_are_still_dropped(self):
        b = frame(0.36, blink=0.6)  # 0.3 s blinks between normal frames
        self.assertEqual(run(SquintDetector(self.tune), [(3, b), (5, frame()), (3, b), (5, frame()), (3, b)]), [])
        self.assertEqual(run(SquintDetector(self.tune), [(40, frame(0.5, blink=0.9))]), [])  # eyes shut

    def test_wink_is_judged_against_the_persons_usual_difference(self):
        # Numbers from a real face whose left eye always reads about 0.10 narrower than the right.
        uneven = Baseline(squint=0.41, width=0.3, asym=0.10)
        rest = (20, frame(l=0.46, r=0.36))
        # A hard squint widens the raw gap to 0.11: still a squint, not a wink.
        sigs = run(SquintDetector(self.tune), [rest, (40, frame(l=0.72, r=0.61))], base=uneven)
        self.assertEqual([s.kind for s in sigs], ["squint"])
        # A right wink reads 0.48 vs 0.63: 0.25 off this person's usual gap, though the raw gap is
        # small. It stays a wink while the eye slowly reopens (0.12 off, squint score still raised).
        wink = [rest, (15, frame(l=0.48, r=0.63)), (40, frame(l=0.58, r=0.63)), (40, frame(l=0.57, r=0.59))]
        self.assertEqual(run(SquintDetector(self.tune), wink, base=uneven), [])



class LeanTests(unittest.TestCase):
    def setUp(self):
        self.tune = Tuning()

    def test_lean_in(self):
        sigs = run(LeanDetector(self.tune), [(10, frame()), (30, frame(width=0.36))])  # 1.2x wider
        self.assertEqual([(s.kind, s.tier) for s in sigs], [("lean", "strong")])
        self.assertEqual(sigs[0].detail["closer_pct"], 17)

    def test_leaning_alone_isnt_a_squint(self):
        # Leaning in close raised one real person's squint score +0.16 with no squinting; while
        # leaning only a strong squint counts, and a real one (+0.26 or more) still does.
        lean_only = run(SquintDetector(self.tune), [(10, frame()), (60, frame(0.26, width=0.50))])
        self.assertEqual(lean_only, [])
        squinting = run(SquintDetector(self.tune), [(10, frame()), (40, frame(0.36, width=0.50))])
        self.assertEqual([(s.kind, s.tier) for s in squinting], [("squint", "strong")])

    def test_lean_counts_while_squinting(self):
        # Leaning in and squinting at once keeps the blink score high; face width doesn't depend on it.
        sigs = run(LeanDetector(self.tune), [(10, frame()), (30, frame(0.36, blink=0.6, width=0.36))])
        self.assertEqual([(s.kind, s.tier) for s in sigs], [("lean", "strong")])

    def test_weak_then_strong(self):
        sigs = run(LeanDetector(self.tune), [(25, frame(width=0.335)), (25, frame(width=0.36))])
        self.assertEqual([s.tier for s in sigs], ["weak", "strong"])

    def test_brief_lean_and_hysteresis(self):
        self.assertEqual(run(LeanDetector(self.tune), [(15, frame(width=0.36)), (10, frame())]), [])
        d = LeanDetector(self.tune)
        self.assertEqual(len(run(d, [(30, frame(width=0.36))])), 1)
        # Easing back into the band between release and weak doesn't re-arm it.
        self.assertEqual(run(d, [(10, frame(width=0.33)), (30, frame(width=0.36))], t0=104), [])

    def test_turned_head_isnt_judged(self):
        self.assertEqual(run(LeanDetector(self.tune), [(40, frame(width=0.36, yaw=30))]), [])


class InputTests(unittest.TestCase):
    def setUp(self):
        self.tune = Tuning()

    def clicks(self, times, xy=(100, 100), jitter=0):
        d, out = RageClickDetector(self.tune), []
        for i, t in enumerate(times):
            s = d.click(t, xy[0] + (i % 2) * jitter, xy[1])
            if s:
                out.append(s)
        return out

    def test_rage_click(self):
        sigs = self.clicks([0, 0.3, 0.6, 0.9], jitter=10)
        self.assertEqual([(s.kind, s.tier) for s in sigs], [("rage_click", "strong")])
        self.assertEqual((sigs[0].x, sigs[0].y), (110, 100))  # where the last click landed

    def test_double_and_triple_clicks_are_normal(self):
        self.assertEqual(self.clicks([0, 0.15]), [])
        self.assertEqual(self.clicks([0, 0.15, 0.3]), [])  # triple-click selects a paragraph

    def test_three_slower_clicks_are_weak(self):
        self.assertEqual([s.tier for s in self.clicks([0, 0.5, 1.0])], ["weak"])

    def test_clicks_far_apart_dont_count(self):
        d = RageClickDetector(self.tune)
        self.assertFalse(any(d.click(t, 100 + 200 * i, 100) for i, t in enumerate([0, 0.3, 0.6, 0.9])))

    def test_retype(self):
        d = RetypeDetector(self.tune)
        t = 0.0
        for _ in range(6):
            t += 0.2
            self.assertIsNone(d.key(t, True))
        self.assertIsNone(d.key(t + 0.5, False))
        s = d.key(t + 0.8, False)
        self.assertEqual(s.kind, "retype")
        self.assertEqual(s.detail["backspaces"], 6)

    def test_a_few_backspaces_are_normal(self):
        d = RetypeDetector(self.tune)
        sigs = [d.key(i * 0.3, i % 3 == 0) for i in range(30)]
        self.assertFalse(any(sigs))

    def test_long_normal_backspace_share_never_fires(self):
        for share in (0.12, 0.20):
            d, signals = RetypeDetector(self.tune), []
            for i in range(2000):
                # Distributed ordinary edits, with typing between deletions.
                backspace = i % round(1 / share) == 0
                sig = d.key(i * 0.2, backspace)
                if sig:
                    signals.append(sig)
            self.assertEqual(signals, [], f"FP count at {share:.0%} backspaces: {len(signals)}")

    def test_hesitation(self):
        d = HesitationDetector(self.tune)
        d.move(0, 10, 10)
        d.move(1, 300, 200)  # moved somewhere, then rests
        self.assertIsNone(d.tick(3))
        s = d.tick(5.2)
        self.assertEqual((s.kind, s.tier, s.x, s.y), ("hesitation", "weak", 300, 200))
        self.assertIsNone(d.tick(6))  # once per rest

    def test_parked_pointer_or_clicking_isnt_hesitation(self):
        d = HesitationDetector(self.tune)
        d.move(0, 10, 10)
        self.assertIsNone(d.tick(10))  # never moved anywhere
        d.move(11, 300, 200)
        d.click(12, 300, 200)
        self.assertIsNone(d.tick(17))

    def test_hesitation_requires_clickable_when_known_false(self):
        d = HesitationDetector(self.tune)
        d.move(0, 10, 10)
        d.move(1, 300, 200)
        d.clickable = False
        self.assertIsNone(d.tick(10))
        d.clickable = None
        self.assertIsNotNone(d.tick(5.2))

    def test_hesitation_adapts_to_the_person(self):
        d = HesitationDetector(self.tune)
        t = 0.0
        for _ in range(25):  # this person always pauses ~5 s before clicking
            d.move(t, 0, 0)
            d.move(t + 0.1, 400, 400)
            d.click(t + 5.1, 400, 400)
            t += 10
        self.assertGreater(d.threshold(), 5.0)


class FusionTests(unittest.TestCase):
    def sig(self, kind, t, conf=0.4, tier="weak", source="vision"):
        return Signal(kind, conf, t, tier, source)

    def test_a_lean_still_going_on_pairs_with_a_later_squint(self):
        f = Fuser()
        self.assertIsNone(f.add(self.sig("lean", 0.0, 0.95, "strong")))
        for i in range(1, 43):  # still leaning in; the squint fires 4.3 s after the lean did
            f.hold("lean", i / 10)
        self.assertEqual(f.add(self.sig("squint", 4.3, 0.72, "strong")).kind, "fused")
        g = Fuser()  # without the hold, the lean is too old to pair
        g.add(self.sig("lean", 0.0, 0.95, "strong"))
        self.assertIsNone(g.add(self.sig("squint", 4.3, 0.72, "strong")))

    def test_hold_never_creates_or_revives_a_signal(self):
        f = Fuser()
        f.hold("lean", 1.0)
        self.assertIsNone(f.add(self.sig("squint", 1.0, 0.9, "strong")))
        f.add(self.sig("lean", 10.0, 0.9, "strong"))
        f.hold("lean", 20.0)  # long expired
        self.assertIsNone(f.add(self.sig("squint", 20.0, 0.9, "strong")))

    def test_one_signal_is_not_enough(self):
        f = Fuser()
        self.assertIsNone(f.add(self.sig("squint", 1, 0.9, "strong")))

    def test_two_weak_signals_fuse(self):
        f = Fuser()
        f.add(self.sig("squint", 1))
        s = f.add(Signal("rage_click", 0.4, 2, "weak", "input", 50, 60))
        self.assertEqual(s.kind, "fused")
        self.assertAlmostEqual(s.confidence, 0.64)
        self.assertEqual(s.source, "senses")
        self.assertEqual({p["kind"] for p in s.detail["signals"]}, {"squint", "rage_click"})
        self.assertEqual((s.target["x"], s.target["y"]), (50, 60))
        self.assertIn(s.kind, KINDS)

    def test_window_and_cooldown(self):
        f = Fuser(window=4, cooldown=30)
        f.add(self.sig("squint", 1))
        self.assertIsNone(f.add(self.sig("lean", 6)))  # too far apart
        self.assertIsNotNone(f.add(self.sig("squint", 7)))
        f.add(self.sig("squint", 10))
        self.assertIsNone(f.add(self.sig("lean", 11)))  # cooling down
        f.add(self.sig("squint", 40))
        self.assertIsNotNone(f.add(self.sig("lean", 41)))

    def test_solo_kinds(self):
        f = Fuser(solo=["rage_click"])
        self.assertIsNone(f.add(self.sig("rage_click", 1, 0.4, "weak", "input")))
        f2 = Fuser(solo=["rage_click"])
        s = f2.add(self.sig("rage_click", 1, 0.7, "strong", "input"))
        self.assertEqual((s.kind, s.source, s.detail["tier"]), ("rage_click", "senses.input", "strong"))

    def test_low_confidence_pairs_are_dropped(self):
        f = Fuser(min_confidence=0.5)
        f.add(self.sig("hesitation", 1, 0.2))
        self.assertIsNone(f.add(self.sig("lean", 2, 0.2)))


@unittest.skipIf(np is None, "numpy not installed")
class FaceSignalTests(unittest.TestCase):
    def test_signals_from_landmarks(self):
        pts = np.full((478, 3), 0.5)
        pts[face.CHEEK_R, :2] = (0.35, 0.5)
        pts[face.CHEEK_L, :2] = (0.65, 0.5)
        bs = {"eyeSquintLeft": 0.3, "eyeSquintRight": 0.2, "mouthSmileLeft": 0.1, "mouthSmileRight": 0.3,
              "eyeBlinkLeft": 0.0, "eyeBlinkRight": 0.2}
        s = face.signals(pts.tolist(), bs, np.eye(4), 640, 480, light=99.0)
        self.assertEqual(set(s), set(face.FIELDS))
        self.assertAlmostEqual(s["width"], 0.3, places=3)
        self.assertAlmostEqual(s["smile"], 0.2)
        self.assertAlmostEqual(s["blink"], 0.1)
        self.assertEqual((s["yaw"], s["pitch"], s["light"]), (0.0, 0.0, 99.0))
        self.assertIsNone(face.signals([(0, 0, 0)] * 10, bs, None, 640, 480))

    def test_head_turn(self):
        a = np.radians(30)
        m = np.eye(4)
        m[:3, :3] = [[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]]
        self.assertAlmostEqual(face.head_angles(m)[0], 30.0, places=3)

    def test_brightness(self):
        gray = np.zeros((100, 100), dtype=np.uint8)
        gray[20:60, 20:60] = 200
        self.assertAlmostEqual(face.brightness(gray, (20, 20, 60, 60)), 200.0)
        self.assertEqual(face.brightness(gray, (0, 0, 2, 2)), -1.0)


def fake_agent(**senses_cfg):
    cfg = copy.deepcopy(config_mod.DEFAULTS["senses"])
    cfg.update({"vision": True, "input": False, "cooldown_seconds": 0})
    cfg.update(senses_cfg)
    got = []
    agent = types.SimpleNamespace(
        config=types.SimpleNamespace(data={"senses": cfg, "gaze": {"enabled": False}}),
        paths=types.SimpleNamespace(state_dir=Path(tempfile.mkdtemp())), events=EventLog(),
        platform=types.SimpleNamespace(frontmost_app=lambda: "Mail"),
        bridge=types.SimpleNamespace(chrome_focused=True, tab={"url": "https://mail.example.com/inbox",
                                                               "title": "Inbox"}),
        on_struggle=got.append)
    return agent, got


def wait_for(cond, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end and not cond():
        time.sleep(0.01)
    return cond()


class SensesManagerTests(unittest.TestCase):
    def test_clickable_msaa_role_mapping(self):
        self.assertEqual(clickable._role_result(0x0C), True)  # menu item
        self.assertEqual(clickable._role_result(0x39), True)  # button menu
        self.assertEqual(clickable._role_result(0x38), True)  # button drop-down
        self.assertIsNone(clickable._role_result(0x0B))  # menu popup is not itself a control
        for role in clickable.CLICKABLE_ROLES:
            self.assertIs(clickable._role_result(role), True, f"clickable role {role:#x}")
        for role in clickable.NONCLICKABLE_ROLES:
            self.assertIs(clickable._role_result(role), False, f"nonclickable role {role:#x}")
        self.assertIsNone(clickable._role_result(0xFFFF))

    def _quiet_face(self, label, make_frame):
        agent, got = fake_agent()
        senses = Senses(agent)
        senses.baseline = Baseline(0.1, 0.3)
        for i in range(6000):
            senses.on_face(1000 + i / 10, make_frame(i))
        fp = len(got) + sum(senses.counts.values())
        self.assertEqual(fp, 0, f"FP count for {label}: {fp}")

    def test_ten_minute_reading_with_blinks(self):
        self._quiet_face("reading/blinks", lambda i: frame(blink=0.9 if i % 37 == 0 else 0))

    def test_ten_minute_smiling_bouts(self):
        self._quiet_face("smiling", lambda i: frame(0.4 if i % 600 < 250 else 0.1,
                                                       smile=0.7 if i % 600 < 250 else 0))

    def test_ten_minute_laughing_bouts(self):
        self._quiet_face("laughing", lambda i: frame(0.4 if i % 500 < 180 else 0.1,
                                                       smile=0.8 if i % 500 < 180 else 0,
                                                       blink=0.9 if i % 43 == 0 else 0))

    def test_ten_minute_winks(self):
        self._quiet_face("winks", lambda i: frame(l=0.6 if i % 400 < 100 else 0.1))

    def test_ten_minute_head_turns(self):
        self._quiet_face("30 degree yaw", lambda i: frame(0.1, yaw=30 if i % 500 < 250 else 0))

    def test_ten_minute_phone_looking(self):
        self._quiet_face("30 degree pitch", lambda i: frame(0.1, pitch=30 if i % 450 < 160 else 0))

    def test_ten_minute_dim_light(self):
        self._quiet_face("dim light/high squint", lambda i: frame(0.7, light=20))

    def test_ten_minute_small_posture_shifts(self):
        self._quiet_face("posture shifts", lambda i: frame(width=0.3 * (1.07 if i % 300 < 100 else 1)))

    def test_ten_minute_normal_typing(self):
        agent, got = fake_agent()
        senses = Senses(agent)
        for i in range(2400):
            senses.on_input({"e": "b" if i % 25 < 3 else "k", "t": 1000 + i * 0.5})
        fp = len(got) + sum(senses.counts.values())
        self.assertEqual(fp, 0, f"FP count for normal typing: {fp}")

    def test_ten_minute_double_and_triple_clicks(self):
        agent, got = fake_agent()
        senses = Senses(agent)
        for i in range(300):
            x, y = (i * 97) % 1920, (i * 53) % 1080
            for j in range(2 + i % 2):
                senses.on_input({"e": "c", "x": x, "y": y, "t": 1000 + i * 2 + j * 0.12})
        fp = len(got) + sum(senses.counts.values())
        self.assertEqual(fp, 0, f"FP count for double/triple clicks: {fp}")

    def test_ten_minute_parked_pointer_reading(self):
        for clickable in (False, None):
            agent, got = fake_agent()
            senses = Senses(agent)
            senses.on_input({"e": "m", "t": 999, "x": 10, "y": 10})
            senses.on_input({"e": "m", "t": 1000, "x": 300, "y": 300})
            senses.on_input({"e": "r", "t": 1001, "x": 300, "y": 300, "clickable": clickable})
            for i in range(600):
                signal = senses.hesitation.tick(1002 + i)
                if signal:
                    senses._signal(signal)
            fp = len(got)
            strong = 1 if senses.last_signal and senses.last_signal["tier"] == "strong" else 0
            self.assertEqual((fp, strong), (0, 0),
                             f"FP count for parked pointer clickable={clickable}: struggles {fp}, strong {strong}")

    def test_face_regression_gates_and_two_signal_positive(self):
        """Synthetic at 10 fps: ordinary expressions/poses stay quiet; paired signals fuse once."""
        negatives = [
            (frame(0.4, smile=0.7), "smile"),
            (frame(l=0.6, r=0.2), "wink"),
            (frame(0.4, yaw=30), "yaw"),
            (frame(0.4, pitch=30), "pitch"),
            (frame(0.4, light=10), "dark"),
        ]
        false_positives = 0
        for face_sig, label in negatives:
            agent, got = fake_agent()
            senses = Senses(agent)
            senses.baseline = Baseline(0.1, 0.3)
            for i in range(6000):
                senses.on_face(1000 + i / 10, face_sig)
            false_positives += len(got) + sum(senses.counts.values())
            self.assertEqual(senses.counts, {}, f"FP count for {label}: {sum(senses.counts.values())}")
        self.assertEqual(false_positives, 0, f"total FP count: {false_positives}")

        agent, got = fake_agent()
        senses = Senses(agent)
        senses.baseline = Baseline(0.1, 0.3)
        t = 1000.0
        # Weak squint held, then a sustained weak lean: two agreeing signals.
        for i in range(40):
            t += 0.1
            senses.on_face(t, frame(0.35 if i >= 2 else 0.1,
                                    width=0.336 if i >= 15 else 0.3))
        self.assertTrue(wait_for(lambda: got), "FN count: expected one fused struggle, got 0")
        self.assertEqual(len(got), 1, f"FP count: expected 1 event, got {len(got)}")
        self.assertEqual(got[0]["kind"], "fused")

    def test_tiny_text_squint_plus_weak_lean_fuses_once_within_three_seconds(self):
        agent, got = fake_agent()
        senses = Senses(agent)
        senses.baseline = Baseline(0.1, 0.3)
        onset = 2000.0
        for i in range(30):
            t = onset + i / 10
            senses.on_face(t, frame(0.35 if i >= 1 else 0.1,
                                   width=0.336 if i >= 1 else 0.3))
        self.assertTrue(wait_for(lambda: got), "FN count for tiny text: expected one fused event, got 0")
        self.assertEqual(len(got), 1, f"FP count for tiny text: expected 1 event, got {len(got)}")
        self.assertEqual(got[0]["kind"], "fused")
        self.assertLessEqual(got[0]["ts"] - onset, 3.0, "FN count for tiny text after 3 seconds: 1")

    def test_rage_clicks_plus_squint_fuses_once_within_three_seconds(self):
        agent, got = fake_agent()
        senses = Senses(agent)
        senses.baseline = Baseline(0.1, 0.3)
        onset = 3000.0
        for i in range(20):
            senses.on_face(onset + i / 10, frame(0.4 if i >= 1 else 0.1))
        for i in range(4):
            senses.on_input({"e": "c", "t": onset + 1 + i * 0.3, "x": 400, "y": 300})
        self.assertTrue(wait_for(lambda: got), "FN count for rage clicks plus squint: expected one, got 0")
        self.assertEqual(len(got), 1, f"FP count for rage clicks plus squint: expected 1, got {len(got)}")
        self.assertEqual(got[0]["kind"], "fused")
        self.assertLessEqual(got[0]["ts"] - onset, 3.0,
                             "FN count for rage clicks plus squint after 3 seconds: 1")

    def test_desktop_struggle_reaches_the_bus_as_a_desktop_app(self):
        # A struggle in Word, with Chrome not in front: unknown text must be "", not None (the shared
        # contract turned None into "None", so the coordinator picked the web-page fix).
        from ally.contracts import Struggle as BusStruggle
        from ally.proactive import pick_accommodation

        agent, got = fake_agent()
        agent.platform.frontmost_app = lambda: "Report.docx - Word"
        agent.bridge.chrome_focused = False
        s = Senses(agent)
        s.simulate("squint", tier="weak")
        d = s.simulate("lean", tier="weak")
        self.assertEqual((d["target"]["app"], d["target"]["site"], d["target"]["window"]),
                         ("Word", "", "Report.docx - Word"))
        self.assertEqual(validate_struggle(d), [])
        bus = BusStruggle.from_dict(d)
        self.assertEqual((bus.target.app, bus.target.site), ("Word", ""))
        self.assertEqual(pick_accommodation(bus.kind, bool(bus.target.site)).tool, "magnify_screen")

    def test_simulated_demo_reaches_the_brain(self):
        agent, got = fake_agent()
        s = Senses(agent)
        self.assertIsNone(s.simulate("demo"))
        self.assertTrue(s.info()["simulated"])
        s.tick(time.time() + 1.5)
        d = s.last_struggle
        self.assertEqual(d["kind"], "fused")
        self.assertEqual(set(d), {"kind", "confidence", "target", "source", "detail", "ts"})
        self.assertEqual(set(d["target"]), {"app", "site", "window", "x", "y", "box"})
        self.assertEqual((d["target"]["app"], d["target"]["site"], d["target"]["window"]),
                         ("Mail", "mail.example.com", "Inbox"))
        self.assertEqual(d["source"], "simulated")
        self.assertTrue(d["detail"]["simulated"])
        self.assertEqual(validate_struggle(d), [])
        self.assertEqual(to_event(d)["type"], "struggle")
        self.assertTrue(wait_for(lambda: got))
        self.assertEqual(got[0]["kind"], "fused")
        kinds = [e["kind"] for e in agent.events.since(0)]
        self.assertIn("struggle", kinds)
        with self.assertRaises(Exception):
            s.simulate("sneeze")

    def test_replay_tick_uses_supplied_timestamp(self):
        agent, _ = fake_agent()
        s = Senses(agent)
        s.input.proc = types.SimpleNamespace(poll=lambda: None)
        s.hesitation.move(10.0, 10, 10)
        s.hesitation.move(11.0, 300, 200)
        s.tick(16.1)
        self.assertEqual(s.last_signal["kind"], "hesitation")
        self.assertAlmostEqual(s.last_signal["ts"], 16.1)

    def test_struggle_sink_posts_contract_event_on_worker(self):
        from ally.struggle_sink import StruggleSink
        agent, _ = fake_agent()
        token_file = agent.paths.state_dir / "token"
        token_file.write_text("secret-token", encoding="utf-8")
        agent.paths.token_file = token_file
        sink = StruggleSink(agent, "http://localhost/api/event")
        seen = threading.Event()
        def urlopen(req, timeout):
            self.assertEqual(timeout, 2)
            self.assertEqual(req.get_header("X-ally-token"), "secret-token")
            self.assertEqual(__import__("json").loads(req.data)["type"], "struggle")
            seen.set()
            response = mock.MagicMock()
            response.read.return_value = b""
            return response
        with mock.patch("ally.struggle_sink.urllib.request.urlopen", side_effect=urlopen):
            event = {"kind": "fused", "confidence": 0.8,
                     "target": {"app": None, "site": None, "window": None, "x": None, "y": None, "box": None},
                     "source": "senses", "detail": {}, "ts": 1.0}
            sink(event)
            self.assertTrue(seen.wait(2))

    def test_declined_struggle_snoozes_its_signal_kinds(self):
        agent, _ = fake_agent(decline_seconds=90)
        s = Senses(agent)
        d = {"kind": "fused", "ts": 100.0, "detail": {"signals": [{"kind": "squint"}, {"kind": "lean"}]}}
        s.answer(d, "no")
        self.assertEqual(s.snoozed_until, {"fused": 190.0, "squint": 190.0, "lean": 190.0})
        s._signal(Signal("squint", 0.8, 150, "strong"))
        self.assertNotIn("squint", s.counts)

    def test_subscribers_and_a_failing_handler(self):
        agent, got = fake_agent()
        agent.on_struggle = lambda d: 1 / 0  # a broken brain must not break sensing
        s = Senses(agent)
        seen = []
        s.subscribe(seen.append)
        with self.assertLogs("ally.senses", "ERROR"):
            s.simulate("demo")
            s.tick(time.time() + 1.5)
            self.assertTrue(wait_for(lambda: seen))
            time.sleep(0.1)  # let the failing handler's thread log

    def test_auto_baseline_then_real_squint_and_lean(self):
        agent, got = fake_agent(fps=10)
        s = Senses(agent)
        t = 1000.0
        for _ in range(200):  # 20 s of a resting face
            t += 0.1
            s.on_face(t, frame())
        self.assertIsNotNone(s.baseline)
        self.assertEqual(s.baseline.source, "auto")
        self.assertTrue(s.baseline_path.exists())
        self.assertEqual(s.vision_status(), "watching")
        for _ in range(40):  # squinting and leaning in together
            t += 0.1
            s.on_face(t, frame(0.4, width=0.36))
        self.assertTrue(wait_for(lambda: got))
        self.assertEqual(got[0]["kind"], "fused")
        self.assertEqual({p["kind"] for p in got[0]["detail"]["signals"]}, {"squint", "lean"})
        info = s.info()
        self.assertEqual(info["signals"], {"squint": 1, "lean": 1})
        self.assertTrue(any("lean" in row[0] for row in s.hud_lines()))

    def test_lean_held_then_a_late_squint_is_one_struggle(self):
        agent, got = fake_agent(fps=10)
        s = Senses(agent)
        t = 1000.0
        for _ in range(200):  # 20 s of a resting face
            t += 0.1
            s.on_face(t, frame())
        for _ in range(45):  # leaning in without squinting: the lean fires, but alone it's no struggle
            t += 0.1
            s.on_face(t, frame(width=0.42))
        for _ in range(30):  # still leaning, now squinting: the squint fires over 4 s after the lean
            t += 0.1
            s.on_face(t, frame(0.4, width=0.42))
        self.assertTrue(wait_for(lambda: got))
        time.sleep(0.2)
        self.assertEqual([g["kind"] for g in got], ["fused"])

    def test_smiling_and_laughing_dont_trigger(self):
        agent, got = fake_agent()
        s = Senses(agent)
        s.baseline = Baseline(0.1, 0.3)
        t = 1000.0
        for _ in range(100):
            t += 0.1
            s.on_face(t, frame(0.45, smile=0.7))
        self.assertEqual(s.counts, {})
        self.assertEqual(s.info()["gate"], "smile")

    def test_calibration(self):
        agent, _ = fake_agent(calibrate_seconds=5)
        s = Senses(agent)
        stop = threading.Event()

        def camera():
            while not stop.is_set():
                s.on_face(time.time(), frame(0.12, width=0.28))
                time.sleep(0.02)

        threading.Thread(target=camera, daemon=True).start()
        try:
            result = s.calibrate(1.0)
        finally:
            stop.set()
        self.assertTrue(result["ok"], result)
        self.assertAlmostEqual(s.baseline.squint, 0.12)
        self.assertEqual(s.baseline.source, "calibrated")
        s2 = Senses(agent)  # loads the saved baseline
        self.assertAlmostEqual(s2.baseline.width, 0.28)

    def test_input_events(self):
        agent, got = fake_agent(solo=["rage_click"])
        s = Senses(agent)
        for i in range(4):
            s.on_input({"e": "c", "t": 50 + 0.3 * i, "x": 400, "y": 300})
        self.assertTrue(wait_for(lambda: got))
        self.assertEqual((got[0]["kind"], got[0]["target"]["x"]), ("rage_click", 400))

    def test_vision_off_ignores_frames(self):
        agent, _ = fake_agent(vision=False)
        s = Senses(agent)
        s.on_face(1.0, frame())
        self.assertIsNone(s.face)
        self.assertEqual(s.vision_status(), "off")


class CameraSharingTests(unittest.TestCase):
    def test_camera_runs_for_senses_alone(self):
        agent, _ = fake_agent()
        agent.senses = Senses(agent)
        g = GazeManager(agent)
        self.assertFalse(g.wanted())
        self.assertTrue(g.camera_wanted())
        agent.senses.vision_override = False
        self.assertFalse(g.camera_wanted())

    def test_face_messages_go_to_senses(self):
        agent, _ = fake_agent()
        agent.senses = Senses(agent)
        g = GazeManager(agent)
        lines = '{"ready": true, "calibrated": false}\n{"t": 5.0, "f": %s}\nnot json\n' % (
            __import__("json").dumps(frame()))
        g._read_stdout(types.SimpleNamespace(stdout=io.StringIO(lines)))
        self.assertEqual(agent.senses.face["width"], 0.30)
        self.assertEqual(agent.senses._auto[-1]["squint_l"], 0.10)


class PrivacyTests(unittest.TestCase):
    """Camera code must never touch the network or write images/video (the team's privacy claim)."""

    BANNED_MODULES = {"socket", "http", "ssl", "requests", "httpx", "aiohttp", "urllib.request", "websocket",
                      "ftplib", "smtplib"}
    BANNED_NAMES = {"imwrite", "VideoWriter", "imencode", "VideoWriter_fourcc"}

    def files(self):
        return sorted((ROOT / "ally" / "senses").glob("*.py")) + [ROOT / "ally" / "gaze_helper.py"]

    def test_no_network_or_image_writing(self):
        for path in self.files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    mods = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
                for m in mods:
                    self.assertFalse(m in self.BANNED_MODULES or m.split(".")[0] in self.BANNED_MODULES - {"urllib"},
                                     f"{path.name} imports {m}")
                if isinstance(node, ast.Attribute):
                    self.assertNotIn(node.attr, self.BANNED_NAMES, f"{path.name} uses {node.attr}")

    def test_input_helper_never_sends_keys(self):
        src = (ROOT / "ally" / "senses" / "input_helper.py").read_text(encoding="utf-8")
        self.assertNotIn(".char", src)
        self.assertNotIn("vk", src)


class SensesConfigTests(unittest.TestCase):
    def test_defaults_are_valid_and_tunable(self):
        errors, warnings = config_mod.validate(config_mod.DEFAULTS)
        self.assertEqual([e for e in errors if "senses" in e], [])
        self.assertEqual([w for w in warnings if "senses" in w], [])
        tune = Tuning.from_config({"squint_delta": 0.3, "rage_clicks": 5, "lean_hold": "x"})
        self.assertEqual((tune.squint_delta, tune.rage_clicks, tune.lean_hold), (0.3, 5, 2.0))

    def test_every_threshold_is_a_real_key_in_the_default_config(self):
        # A lost line break once folded pose_gate into a comment, so it silently stopped being settable.
        senses = config_mod.DEFAULTS["senses"]
        for name, field in Tuning.__dataclass_fields__.items():
            self.assertIn(name, senses, f"{name} is missing from [senses] in default_config.toml")
            self.assertEqual(senses[name], field.default, name)

    def test_test_homes_keep_senses_off(self):
        text = (temp_home() / "config.toml").read_text(encoding="utf-8")
        self.assertIn("[senses]\nvision = false\ninput = false", text)


if __name__ == "__main__":
    unittest.main()
