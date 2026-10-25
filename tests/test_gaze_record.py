"""Calibration layout, readiness, held-out score, and the recording file. No camera."""

from __future__ import annotations

import io
import json
import math
import random
import statistics
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from helpers import ROOT  # noqa: F401  (puts the repo on sys.path)

from ally.gaze_helper import (CAL_COLLECT, CAL_SETTLE, CLOSER, CENTER_HEAD, EYES_ONLY, FARTHER,
                              HEAD_MOVE_DEG, HEAD_RETRY_LINE, LEAN_IN, ABOUT_30, ABOUT_45, NEED_LIGHT,
                              NO_FACE, SIT_BACK, STEADY_SECONDS, TWO_FACES, VAL_COLLECT, Setup,
                              attach_screen_mm, calibration_points, cell_index, classify_command,
                              grid_points, intro_lines, kept_model, likely_cause, pose_count,
                              readiness_issue, result_grade, setup_duration, validation_points,
                              validation_score, with_check)
from ally.gaze_record import (BLENDSHAPES, READING_SECONDS, RunLog, _cnn_readings, _cnn_warned,
                              attach_cnn, click_record, frame_record, gaze_run_path, image_like,
                              meta_record, record, target_record)
from ally.paths import Paths

try:
    import numpy as np
    from ally import gaze_model
except ImportError:  # numpy is optional; the score test skips without it
    np = None
    gaze_model = None


def _face(**over):
    face = {"present": True, "width": 0.30, "light": 120.0, "cx": 0.5, "cy": 0.45, "yaw": 0.0, "pitch": 0.0}
    face.update(over)
    return face


def _feat(openness: float = 0.4):
    return ([0.0] * 9, openness)


def _drive(setup: Setup, feat_fn, face: dict, until: set[str], t: float = 0.0, dt: float = 0.05):
    for _ in range(20000):
        state = setup.step(t, feat_fn(setup), face, 1_000_000.0 + t)
        t += dt
        if state in until:
            return t, state
    raise AssertionError(setup.stage)


class LayoutTests(unittest.TestCase):
    def test_nine_grid_dots_cover_every_cell(self):
        dots = calibration_points()
        center = (0.50, 0.50)
        self.assertEqual(len(dots), 9)
        self.assertEqual(len(set(dots)), 9)
        self.assertEqual(set(dots), set(grid_points()))
        self.assertEqual(dots[0], center)
        self.assertEqual({cell_index(x, y) for x, y in dots}, set(range(9)))

    def test_validation_dots_use_different_cells(self):
        calib = calibration_points()
        for seed in range(30):
            dots = validation_points(random.Random(seed))
            self.assertEqual(len(dots), 5, seed)
            cells = [cell_index(x, y) for x, y in dots]
            self.assertEqual(len(set(cells)), 5, seed)
            for x, y in dots:
                for cx, cy in calib:
                    self.assertGreaterEqual(math.hypot(x - cx, y - cy), 0.03)


class ReadinessTests(unittest.TestCase):
    def test_one_instruction_until_it_is_fixed(self):
        good = _face()
        self.assertIsNone(readiness_issue(good))
        self.assertEqual(readiness_issue(None), NO_FACE)
        self.assertEqual(readiness_issue({"present": False, "two": True}), TWO_FACES)
        self.assertEqual(readiness_issue(_face(width=0.10)), CLOSER)
        self.assertEqual(readiness_issue(_face(width=0.60)), FARTHER)
        self.assertEqual(readiness_issue(_face(light=10)), NEED_LIGHT)
        self.assertEqual(readiness_issue(_face(yaw=30)), CENTER_HEAD)
        self.assertEqual(readiness_issue(_face(cx=0.05)), CENTER_HEAD)
        # Distance is named before light, so a far, dark face gets one instruction.
        self.assertEqual(readiness_issue(_face(width=0.10, light=5)), CLOSER)
        self.assertEqual(CLOSER, "move a little closer")
        self.assertEqual(NEED_LIGHT, "turn on a light in front of you")

    def test_the_wait_restarts_when_something_is_off(self):
        setup = Setup((1920, 1080), rng=random.Random(0), ready_seconds=3)
        face = _face()
        self.assertEqual(setup.step(0, _feat(), face, 0), "running")
        self.assertEqual(setup.stage, "ready")
        self.assertEqual(setup.step(1, _feat(), _face(width=0.05), 1), "running")
        self.assertEqual(setup.instruction, CLOSER)
        self.assertEqual(setup.step(1, _feat(), face, 1), "running")
        self.assertEqual(setup.stage, "ready")
        self.assertEqual(setup.step(4, _feat(), face, 4), "running")
        self.assertEqual(setup.stage, "targets")

    def test_the_head_must_stay_steady_for_a_second(self):
        setup = Setup((1920, 1080), rng=random.Random(0), ready_seconds=0)
        t = 0.0
        # Width swings the way a person does while sitting down (about 40 cm, then 32 cm).
        while t < 2.0:
            width = 0.24 if int(t * 4) % 2 == 0 else 0.36
            self.assertEqual(setup.step(t, _feat(), _face(width=width), t), "running")
            t += 0.05
        self.assertEqual(setup.stage, "ready")
        still = _face()
        t0 = t
        setup.step(t, _feat(), still, t)  # lock the reference pose
        t += 0.05
        while t < t0 + STEADY_SECONDS:
            self.assertEqual(setup.step(t, _feat(), still, t), "running")
            self.assertEqual(setup.stage, "ready")
            t += 0.05
        self.assertEqual(setup.step(t0 + STEADY_SECONDS, _feat(), still, t0 + STEADY_SECONDS), "running")
        self.assertEqual(setup.stage, "targets")
        self.assertEqual(setup.banners[0], EYES_ONLY)
        self.assertEqual(intro_lines()[1], EYES_ONLY)
        self.assertIn(EYES_ONLY, intro_lines("move a little closer"))

    def test_a_small_pose_change_does_not_reset_the_wait(self):
        setup = Setup((1920, 1080), rng=random.Random(0), ready_seconds=0)
        self.assertEqual(setup.step(0, _feat(), _face(yaw=0), 0), "running")
        self.assertEqual(setup.step(STEADY_SECONDS, _feat(), _face(yaw=HEAD_MOVE_DEG), STEADY_SECONDS), "running")
        self.assertEqual(setup.stage, "targets")


class RetryTests(unittest.TestCase):
    def test_a_weak_dot_is_shown_once_more(self):
        weak = 4

        def feat_fn(setup: Setup):
            cur = setup.current
            if cur is not None and cur.phase == "calibration" and cur.id == weak:
                shown = sum(1 for event in setup.events if event["phase"] == "calibration" and event["id"] == weak)
                if shown <= 1:
                    return _feat(0.0)  # a blink: the frame does not count
            return _feat()

        setup = Setup((1920, 1080), rng=random.Random(1), ready_seconds=0, poses=1)
        _drive(setup, feat_fn, _face(), {"fit"})
        ids = [event["id"] for event in setup.events if event["phase"] == "calibration"]
        self.assertEqual(ids.count(weak), 2)
        for dot_id in range(9):
            if dot_id != weak:
                self.assertEqual(ids.count(dot_id), 1)
        self.assertGreaterEqual(setup.targets[weak].good, 10)
        self.assertTrue(all(dot.good >= 10 for dot in setup.targets))
        for event in setup.events:
            self.assertLess(event["shown"], event["start"])
            self.assertLess(event["start"], event["end"])
            settle = CAL_SETTLE if event["phase"] == "calibration" else 0.5
            self.assertAlmostEqual(event["start"] - event["shown"], settle)
            self.assertAlmostEqual(event["end"] - event["start"],
                                   CAL_COLLECT if event["phase"] == "calibration" else VAL_COLLECT)

        setup.accept_fit({"version": 1})
        _drive(setup, lambda _setup: _feat(), _face(), {"score"})
        fit_xy = {xy for _feat_row, xy in setup.usable()}
        val_xy = {xy for _feat_row, xy in setup.validation_samples()}
        self.assertTrue(fit_xy.isdisjoint(val_xy))
        self.assertEqual(len(val_xy), 5)
        val_events = [event for event in setup.events if event["phase"] == "validation"]
        self.assertEqual(len(val_events), 5)
        for event in val_events:
            self.assertIn("shown", event)
            self.assertIn("start", event)
            self.assertIn("end", event)

    def test_drift_dots_are_not_used_for_the_fit(self):
        setup = Setup((1920, 1080), rng=random.Random(2), ready_seconds=0)
        _drive(setup, lambda _setup: _feat(), _face(), {"fit"})
        before = list(setup.usable())
        setup.start_drift()
        self.assertEqual(setup.stage, "drift")
        cells = [cell_index(dot.nx, dot.ny) for dot in setup.drift_targets]
        self.assertEqual(len(cells), 5)
        self.assertEqual(len(set(cells)), 5)
        for dot in setup.drift_targets:
            for cx, cy in calibration_points():
                self.assertGreaterEqual(math.hypot(dot.nx - cx, dot.ny - cy), 0.03)
        _drive(setup, lambda _setup: _feat(), _face(), {"done"})
        self.assertEqual(setup.usable(), before)
        drift = [event for event in setup.events if event["phase"] == "drift"]
        self.assertEqual(len(drift), 5)
        for event in drift:
            self.assertLess(event["shown"], event["start"])
            self.assertLess(event["start"], event["end"])
            self.assertAlmostEqual(event["end"] - event["start"], VAL_COLLECT)

    def test_collection_is_one_and_a_half_seconds(self):
        self.assertEqual(CAL_COLLECT, 1.5)
        self.assertEqual(VAL_COLLECT, 1.5)
        setup = Setup((1920, 1080), rng=random.Random(3), ready_seconds=0)
        _drive(setup, lambda _setup: _feat(), _face(), {"fit"})
        windows = [event["end"] - event["start"] for event in setup.events if event["phase"] == "calibration"]
        self.assertTrue(windows)
        self.assertTrue(all(abs(window - 1.5) < 1e-9 for window in windows))

    def test_a_turned_head_shows_that_dot_once_more(self):
        turned = 4
        wobble = 7

        def feat_fn(setup: Setup):
            cur = setup.current
            if cur is not None and cur.phase == "calibration" and cur.id == turned and not cur.retried:
                return ([1.0] + [0.0] * 8, 0.4)  # marker for the frames taken while the head was turned
            return _feat()

        def face_fn(setup: Setup):
            cur = setup.current
            if cur is None or cur.phase != "calibration":
                return _face()
            if cur.id == turned and not cur.retried:
                return _face(yaw=8)  # away from the first dot
            if cur.id == wobble and not cur.retried:
                # More than 3 degrees of turn while this dot is collected.
                yaw = 0.0 if len(cur.poses) % 2 == 0 else 4.0
                return _face(yaw=yaw)
            if cur.id == 2:
                return _face(yaw=2)  # under the limit: not shown again
            return _face()

        setup = Setup((1920, 1080), rng=random.Random(4), ready_seconds=0, poses=1)
        t = 0.0
        state = "running"
        for _ in range(20000):
            state = setup.step(t, feat_fn(setup), face_fn(setup), 1_000_000.0 + t)
            t += 0.05
            if state == "fit":
                break
        else:
            raise AssertionError(setup.stage)
        ids = [event["id"] for event in setup.events if event["phase"] == "calibration"]
        self.assertEqual(ids.count(turned), 2)
        self.assertEqual(ids.count(wobble), 2)
        self.assertEqual(ids.count(2), 1)
        self.assertEqual(HEAD_RETRY_LINE, "Keep your head still, look with your eyes only.")
        self.assertEqual(setup.banners.count(HEAD_RETRY_LINE), 2)
        self.assertGreaterEqual(setup.targets[turned].good, 10)
        self.assertTrue(all(row[0][0] != 1.0 for row in setup.usable()))
        self.assertEqual(ids.count(turned), 2)  # not a third time


class PoseRoundTests(unittest.TestCase):
    def test_one_pose_is_the_nine_grid_dots(self):
        self.assertEqual(pose_count(1), 1)
        self.assertEqual(pose_count(3), 3)
        self.assertEqual(pose_count("nope"), 3)
        self.assertEqual(setup_duration(1), ABOUT_30)
        self.assertEqual(setup_duration(3), ABOUT_45)
        self.assertEqual(ABOUT_30, "About 30 seconds.")
        self.assertEqual(ABOUT_45, "About 45 seconds.")
        self.assertIn(ABOUT_45, intro_lines())
        self.assertIn(ABOUT_30, intro_lines("", 1))
        setup = Setup((1920, 1080), rng=random.Random(5), ready_seconds=0, poses=1)
        _drive(setup, lambda _s: _feat(), _face(), {"fit"})
        calib = [event for event in setup.events if event["phase"] == "calibration"]
        self.assertEqual(len(calib), 9)
        self.assertTrue(all(event["pose"] == "normal" for event in calib))
        self.assertEqual(setup.announcements, [])
        self.assertEqual(classify_command("calibrate"), ("calibrate", None))
        self.assertEqual(classify_command("calibrate 1"), ("calibrate", 1))
        self.assertEqual(classify_command("calibrate 3"), ("calibrate", 3))
        from ally.config import merge, validate
        bad, _warnings = validate(merge({"gaze": {"calibration_poses": 2}}))
        self.assertTrue(any("calibration_poses" in err for err in bad))
        good, _warnings = validate(merge({"gaze": {"calibration_poses": 1}}))
        self.assertFalse(any("calibration_poses" in err for err in good))

    def test_extra_rounds_are_tagged_and_each_has_its_own_head_anchor(self):
        def face_fn(setup: Setup):
            if setup.stage == "settle":
                yaw = 10.0 if setup._settle_pose == "back" else -8.0
                return _face(yaw=yaw)
            cur = setup.current
            if cur is None or cur.phase != "calibration":
                return _face()
            if cur.pose == "normal":
                if cur.id == 3 and not cur.retried:
                    return _face(yaw=8)  # away from this round's first dot
                return _face(yaw=0)
            if cur.pose == "back":
                # The first dot of this round is the new anchor, not the very first dot.
                if cur.id == 2 and not cur.retried:
                    return _face(yaw=0)
                return _face(yaw=10)
            if cur.id == 1 and not cur.retried:
                return _face(yaw=10)  # still at the sit-back pose, not this round
            return _face(yaw=-8)

        setup = Setup((1920, 1080), rng=random.Random(6), ready_seconds=0, poses=3)
        t = 0.0
        state = "running"
        for _ in range(20000):
            state = setup.step(t, _feat(), face_fn(setup), 1_000_000.0 + t)
            t += 0.05
            if state == "fit":
                break
        else:
            raise AssertionError((setup.stage, len(setup.events)))
        self.assertEqual(setup.announcements, [SIT_BACK, LEAN_IN])
        self.assertEqual(SIT_BACK, "Sit back a little")
        self.assertEqual(LEAN_IN, "Lean in a little")

        def ids(pose):
            return [event["id"] for event in setup.events
                    if event.get("phase") == "calibration" and event.get("pose") == pose]

        normal, back, close = ids("normal"), ids("back"), ids("close")
        self.assertEqual(len(set(normal)), 9)
        self.assertEqual(len(normal), 10)  # 9, plus one retry
        self.assertEqual(normal.count(3), 2)
        self.assertEqual(normal.count(0), 1)
        self.assertEqual(len(back), 6)  # 5, plus the dot that returned to the first pose
        self.assertEqual(back.count(0), 1)
        self.assertEqual(back.count(1), 1)
        self.assertEqual(back.count(2), 2)
        self.assertEqual(len(close), 6)
        self.assertEqual(close.count(0), 1)
        self.assertEqual(close.count(1), 2)
        self.assertEqual(len(set(back)), 5)
        self.assertEqual(len(set(close)), 5)
        poses = {event["pose"] for event in setup.events if event["phase"] == "calibration"}
        self.assertEqual(poses, {"normal", "back", "close"})
        # The check dots are not training targets and carry no pose.
        setup.accept_fit({"version": 1})
        _drive(setup, lambda _s: _feat(), _face(), {"score"})
        checks = [event for event in setup.events if event["phase"] == "validation"]
        self.assertEqual(len(checks), 5)
        self.assertTrue(all("pose" not in event for event in checks))
        trained = {event["pose"] for event in setup.events if event["phase"] == "calibration"}
        self.assertIn("back", trained)
        self.assertGreaterEqual(len(setup.usable()), 9 * 10)

    def test_screen_mm_is_stored_on_the_model(self):
        stamped = attach_screen_mm({"dots": 13}, (345, 194))
        self.assertEqual(stamped["screen_mm"], [345, 194])
        self.assertEqual(stamped["dots"], 13)
        self.assertNotIn("screen_mm", attach_screen_mm({"dots": 13}, ()))
        self.assertNotIn("screen_mm", {"dots": 13})


class ScoreTests(unittest.TestCase):
    def test_empty_check_is_poor(self):
        score = validation_score({}, [], (1920, 1080))
        self.assertEqual(score["error_px"], None)
        self.assertEqual((score["region_hits"], score["region_total"]), (0, 0))
        self.assertEqual(result_grade(score), "poor")

    def test_words_follow_the_bar(self):
        self.assertEqual(result_grade({"error_px": 100, "region_hits": 5, "region_total": 5}), "good")
        self.assertEqual(result_grade({"error_px": 100, "region_hits": 4, "region_total": 5}), "okay")
        self.assertEqual(result_grade({"error_px": 400, "region_hits": 4, "region_total": 5}), "okay")
        self.assertEqual(result_grade({"error_px": 80, "region_hits": 3, "region_total": 5}), "poor")
        self.assertIn("light", likely_cause({"light": 10, "width": 0.3}).lower())
        self.assertIn("closer", likely_cause({"light": 120, "width": 0.05}).lower())
        self.assertIn("glare", likely_cause({"light": 120, "width": 0.3, "glare": True}).lower())

    def test_saved_numbers_and_a_different_camera(self):
        model = {"error_px": 90, "dots": 13}
        checked = with_check(model, {"error_px": 110, "region_hits": 4, "region_total": 5}, 0)
        self.assertEqual(checked["validation"], {"error_px": 110, "region_hits": 4, "region_total": 5})
        self.assertEqual(checked["camera"], 0)
        self.assertNotIn("validation", model)
        self.assertIsNone(kept_model(checked, 1))
        self.assertEqual(kept_model({"error_px": 90}, 1)["error_px"], 90)


@unittest.skipIf(np is None, "numpy is not installed")
class PredictScoreTests(unittest.TestCase):
    def test_median_distance_and_region_hits(self):
        rnd = np.random.default_rng(1)
        screen = (1920, 1080)
        dots = [(fx * screen[0], fy * screen[1]) for fy in (0.06, 0.5, 0.94) for fx in (0.08, 0.5, 0.92)]
        samples = []
        for tx, ty in dots:
            for _ in range(12):
                u, v = tx / screen[0] - 0.5, ty / screen[1] - 0.5
                head = rnd.normal(0, 0.01, 5)
                feat = [0.3 * u, 0.2 * v, 0.8 * u, 0.6 * v, *head]
                feat = (np.array(feat, dtype=float) + rnd.normal(0, 0.002, 9)).tolist()
                samples.append((feat, (tx, ty)))
        model = gaze_model.fit(samples, screen)
        targets = [(180.0, 160.0), (960.0, 160.0), (1700.0, 900.0), (180.0, 900.0), (960.0, 540.0)]
        val = []
        chosen = []
        for index, target in enumerate(targets):
            feat = samples[index * 12][0]
            chosen.append(feat)
            for _ in range(4):
                val.append((feat, target))
        score = validation_score(model, val, screen)
        from ally.gaze_helper import region_of
        dists = []
        hits = 0
        for feat, target in zip(chosen, targets):
            px, py = gaze_model.predict(model, feat)
            dists.append(math.hypot(px - target[0], py - target[1]))
            hits += int(region_of(px, py, screen) == region_of(target[0], target[1], screen))
        self.assertEqual(score["region_total"], 5)
        self.assertEqual(score["region_hits"], hits)
        self.assertEqual(score["error_px"], int(round(statistics.median(dists))))
        self.assertEqual(len(score["per_target"]), 5)


class FormatTests(unittest.TestCase):
    def test_lines_parse_and_carry_the_contract(self):
        home = Path(tempfile.mkdtemp(prefix="ally-gaze-"))
        path = gaze_run_path(home, "run1", "ada", "yes", "dim")
        self.assertEqual(path.name, "ada-yes-dim.jsonl")
        self.assertEqual(path.parent.name, "run1")
        log = RunLog(path)
        landmarks = [(0.123456789, 0.2, -0.001234567)] * 478
        matrix = [[0.1, 0, 0, 0], [0, 0.2, 0, 0], [0, 0, 0.3, 0], [0, 0, 0, 1]]
        frame = frame_record(1730000000.125, 640, 480, landmarks, {"eyeLookUpLeft": 0.12}, matrix)
        self.assertIsNotNone(frame)
        self.assertTrue(log.write(meta_record("ada", "yes", "dim", (1920, 1080), 0, "abc123", 1730000000.0,
                                             screen_mm=(345, 194))))
        self.assertTrue(log.write(frame))
        self.assertTrue(log.write(target_record({
            "phase": "calibration", "id": 2, "x": 400, "y": 300, "pose": "back",
            "shown": 1730000001.0, "start": 1730000001.5, "end": 1730000002.5,
        })))
        self.assertTrue(log.write(click_record(1730000100.5, 812, 403)))
        self.assertFalse(log.write({"rec": "frame", "png": "not-a-picture", "lm": landmarks}))
        self.assertFalse(log.write({"rec": "frame", "image": [[1, 2, 3]], "jpeg": "x"}))
        self.assertFalse(log.write({"rec": "frame", "blob": b"\xff\xd8\xff"}))
        self.assertIsNone(frame_record(1, 640, 480, [(0, 0, 0)] * 10, {}, None))
        log.close()

        text = path.read_text(encoding="utf-8")
        self.assertNotIn("png", text.lower())
        self.assertNotIn("jpeg", text.lower())
        self.assertNotIn("base64", text.lower())
        lines = []
        for line in text.splitlines():
            self.assertTrue(line.strip())
            lines.append(json.loads(line))
        self.assertEqual(len(lines), 4)
        self.assertEqual(lines[0]["rec"], "meta")
        self.assertEqual(lines[0]["screen"], [1920, 1080])
        self.assertEqual(lines[0]["screen_mm"], [345, 194])
        frame_line = lines[1]
        self.assertEqual(frame_line["rec"], "frame")
        self.assertEqual(len(frame_line["lm"]), 478)
        self.assertEqual(len(frame_line["lm"][0]), 3)
        self.assertEqual(frame_line["lm"][0][0], round(0.123456789, 5))
        self.assertEqual(set(frame_line["bs"]), set(BLENDSHAPES))
        self.assertEqual(len(frame_line["bs"]), 52)
        self.assertEqual(frame_line["bs"]["eyeLookUpLeft"], 0.12)
        self.assertEqual(frame_line["bs"]["eyeBlinkLeft"], 0.0)
        self.assertEqual(len(frame_line["mat"]), 16)
        target = lines[2]
        self.assertEqual(target["phase"], "calibration")
        self.assertEqual(target["pose"], "back")
        for key in ("shown", "start", "end"):
            self.assertIn(key, target)
        self.assertLess(target["shown"], target["start"])
        self.assertLess(target["start"], target["end"])
        self.assertEqual(lines[3]["rec"], "click")
        keys = set()

        def walk(value):
            if isinstance(value, dict):
                keys.update(name.lower() for name in value)
                for item in value.values():
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        for line in lines:
            walk(line)
        for banned in ("image", "jpeg", "jpg", "png", "video", "pixels", "bmp", "frame"):
            self.assertNotIn(banned, keys)

        again = gaze_run_path(home, "run1", "ada", "yes", "dim")
        self.assertEqual(again.name, "ada-yes-dim-2.jsonl")

    def test_refuses_while_ally_is_running(self):
        home = Path(tempfile.mkdtemp(prefix="ally-gaze-stop-"))
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = record("ada", "no", "bright", paths=Paths(home), ally_running=lambda: True)
        self.assertEqual(code, 2)
        self.assertIn("python run.py stop", buf.getvalue())
        self.assertFalse((home / "state" / "gaze_runs").exists())
        with redirect_stdout(buf):
            self.assertEqual(record("a person", "no", "bright", paths=Paths(home), ally_running=lambda: False), 2)
            self.assertEqual(record("ada", "sometimes", "bright", paths=Paths(home), ally_running=lambda: False), 2)
        self.assertEqual(READING_SECONDS, 120.0)


class CnnRecordTests(unittest.TestCase):
    def _frame(self):
        landmarks = [(0.2, 0.4, 0.0)] * 478
        return frame_record(10.0, 640, 480, landmarks, {"eyeLookUpLeft": 0.1}, None)

    def test_cnn_fields_are_written_only_when_asked(self):
        row = self._frame()
        self.assertNotIn("cnn", row)
        self.assertNotIn("cnn_ms", row)
        self.assertIs(attach_cnn(row, {}), row)
        self.assertIs(attach_cnn(row, {"mobileone_s0": (float("nan"), 1.0, 1.0)}), row)
        blob = "data:image/png;base64," + ("A" * 400)
        self.assertIs(attach_cnn(row, {"mobileone_s0": (blob, 1.0, 1.0)}), row)

        stamped = attach_cnn(row, {
            "mobileone_s0": (1.23456, -2.5, 12.345),
            "resnet18": (None, None, 40.0),
        })
        self.assertNotIn("cnn", row)
        self.assertEqual(stamped["cnn"], {"mobileone_s0": [1.2346, -2.5]})
        self.assertEqual(stamped["cnn_ms"], {"mobileone_s0": 12.35})
        self.assertFalse(image_like(stamped))

        home = Path(tempfile.mkdtemp(prefix="ally-gaze-cnn-"))
        path = gaze_run_path(home, "run1", "ada", "no", "bright")
        log = RunLog(path)
        self.assertTrue(log.write(stamped))
        self.assertFalse(log.write({"rec": "frame", "cnn": {"mobileone_s0": ["A" * 400]}}))
        log.close()
        text = path.read_text(encoding="utf-8")
        self.assertIn('"cnn"', text)
        self.assertNotIn("base64", text.lower())
        self.assertNotIn("jpeg", text.lower())
        self.assertNotIn("png", text.lower())
        saved = json.loads(text)
        keys = set()

        def walk(value):
            if isinstance(value, dict):
                keys.update(name.lower() for name in value)
                for item in value.values():
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(saved)
        for banned in ("image", "jpeg", "jpg", "png", "video", "pixels", "bmp", "frame"):
            self.assertNotIn(banned, keys)

    def test_a_missing_model_does_not_open_the_camera(self):
        home = Path(tempfile.mkdtemp(prefix="ally-gaze-cnn-miss-"))
        models = home / "state" / "models"
        models.mkdir(parents=True)
        (models / "face_landmarker.task").write_bytes(b"face")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = record("ada", "no", "bright", paths=Paths(home), ally_running=lambda: False,
                          cnn="mobileone_s0,resnet18")
        self.assertEqual(code, 2)
        text = buf.getvalue()
        self.assertIn("python scripts/get_gaze_models.py", text)
        self.assertIn("mobileone_s0_gaze.onnx", text)
        self.assertFalse((home / "state" / "gaze_runs").exists())

    def test_readings_are_numbers_from_the_runner(self):
        class Runner:
            def __init__(self, name, angles):
                self.name = name
                self._angles = angles
                self.frames = []

            def angles(self, frame, landmarks):
                self.frames.append(frame)
                if isinstance(self._angles, Exception):
                    raise self._angles
                return self._angles

        good = Runner("mobileone_s0", (4.0, -1.25))
        bad = Runner("resnet18", RuntimeError("skip"))
        _cnn_warned.clear()
        buf = io.StringIO()
        with redirect_stdout(buf):
            readings = _cnn_readings([bad, good], [[1, 2, 3]], [[0.2, 0.3, 0.0]])
        self.assertEqual(set(readings), {"mobileone_s0"})
        yaw, pitch, ms = readings["mobileone_s0"]
        self.assertEqual((yaw, pitch), (4.0, -1.25))
        self.assertGreaterEqual(ms, 0.0)
        self.assertIn("resnet18", buf.getvalue())
        row = attach_cnn(self._frame(), readings)
        self.assertEqual(row["cnn"]["mobileone_s0"][0], 4.0)
        self.assertNotIn("resnet18", row["cnn"])
        self.assertEqual(good.frames, [[[1, 2, 3]]])


if __name__ == "__main__":
    unittest.main()
