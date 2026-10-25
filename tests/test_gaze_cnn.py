"""MobileGaze decode, preprocess, and synthetic scoring. No camera and no ONNX file."""

from __future__ import annotations

import ast
import importlib.util
import io
import json
import math
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from helpers import ROOT

try:
    import numpy as np
    from ally import gaze_cnn
    from ally import gaze_model
except ImportError:
    np = None
    gaze_cnn = None
    gaze_model = None

try:
    import cv2
except ImportError:
    cv2 = None


def _script():
    path = ROOT / "scripts" / "get_gaze_models.py"
    spec = importlib.util.spec_from_file_location("get_gaze_models", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _logits(hot):
    values = np.full(gaze_cnn.BINS, -50.0)
    for index in hot:
        values[index] = 50.0
    return values


def _landmarks():
    pts = [[0.5, 0.5, 0.0] for _ in range(478)]
    pts[33] = [0.40, 0.45, 0.0]
    pts[133] = [0.50, 0.45, 0.0]
    pts[159] = [0.45, 0.43, 0.0]
    pts[145] = [0.45, 0.47, 0.0]
    pts[468] = [0.45, 0.45, 0.0]
    pts[263] = [0.70, 0.45, 0.0]
    pts[362] = [0.60, 0.45, 0.0]
    pts[386] = [0.65, 0.43, 0.0]
    pts[374] = [0.65, 0.47, 0.0]
    pts[473] = [0.65, 0.45, 0.0]
    return pts


def _matrix(tx, ty, tz):
    return [1, 0, 0, tx, 0, 1, 0, ty, 0, 0, 1, tz, 0, 0, 0, 1]


@unittest.skipIf(np is None, "numpy is not installed")
class DecodeTests(unittest.TestCase):
    def test_bin_expectation_is_degrees(self):
        yaw, pitch = gaze_cnn.decode_gaze(_logits([45]), _logits([0]))
        self.assertAlmostEqual(yaw, 0.0)
        self.assertAlmostEqual(pitch, -180.0)
        self.assertAlmostEqual(gaze_cnn.decode_gaze(_logits([89]), _logits([89]))[0], 176.0)
        # Bins 45 and 46 share the mass: index 45.5, times 4, minus 180.
        split = gaze_cnn.decode_gaze(_logits([45, 46]), _logits([45, 46]))
        self.assertAlmostEqual(split[0], 2.0)
        self.assertAlmostEqual(split[1], 2.0)
        batched = _logits([45]).reshape(1, gaze_cnn.BINS)
        self.assertAlmostEqual(gaze_cnn.decode_gaze(batched, batched)[0], 0.0)
        with self.assertRaises(ValueError):
            gaze_cnn.decode_gaze(np.zeros(10), np.zeros(10))


@unittest.skipIf(np is None or cv2 is None, "numpy and opencv are not installed")
class PreprocessTests(unittest.TestCase):
    def test_shape_and_imagenet_normalization(self):
        frame = np.zeros((80, 100, 3), np.uint8)
        frame[:] = (10, 20, 30)  # BGR, so RGB is (30, 20, 10)
        box = (10, 5, 90, 70)
        tensor = gaze_cnn.preprocess(frame, box)
        self.assertEqual(tensor.shape, (1, 3, 448, 448))
        self.assertEqual(tensor.dtype, np.float32)
        rgb = np.array([30, 20, 10], dtype=np.float32) / 255.0
        expected = (rgb - np.array(gaze_cnn.IMAGENET_MEAN, dtype=np.float32)) / np.array(
            gaze_cnn.IMAGENET_STD, dtype=np.float32)
        np.testing.assert_allclose(tensor[0, :, 0, 0], expected, rtol=1e-5, atol=1e-5)
        np.testing.assert_allclose(tensor[0, :, 200, 200], expected, rtol=1e-5, atol=1e-5)
        self.assertIsNone(gaze_cnn.preprocess(frame, (0, 0, 1, 1)))
        flat = np.zeros((448, 448, 3), np.uint8)
        flat[:] = (30, 20, 10)
        direct = gaze_cnn.normalize_rgb(flat)
        self.assertEqual(direct.shape, (1, 3, 448, 448))
        np.testing.assert_allclose(direct[0, :, 3, 3], expected, rtol=1e-5, atol=1e-5)


@unittest.skipIf(np is None, "numpy is not installed")
class FaceBoxTests(unittest.TestCase):
    def test_box_matches_the_helper_and_clips(self):
        from ally.senses.face import face_box

        pts = np.tile(np.array([[0.5, 0.5, 0.0]]), (6, 1))
        pts[0] = [0.2, 0.1, 0.0]
        pts[1] = [1.4, -0.3, 0.0]
        self.assertEqual(gaze_cnn.face_box_from_landmarks(pts, 100, 80), face_box(pts, 100, 80))
        x0, y0, x1, y1 = gaze_cnn.face_box_from_landmarks(pts, 100, 80)
        self.assertGreaterEqual(x0, 0)
        self.assertGreaterEqual(y0, 0)
        self.assertLessEqual(x1, 100)
        self.assertLessEqual(y1, 80)
        self.assertIsNone(gaze_cnn.face_box_from_landmarks([[0.5, 0.5, 0.0]], 100, 100))
        self.assertIsNone(gaze_cnn.face_box_from_landmarks([[0.5, 0.5, 0.0], [0.5, 0.5, 0.0]], 100, 100))


@unittest.skipIf(np is None, "numpy is not installed")
class FitTests(unittest.TestCase):
    def test_screen_geometry_matches_v4(self):
        screen, mm = (1920, 1080), (344.0, 193.0)
        features = [0.0, 0.0, 0.0, 0.0, 0.15, -0.08, 2.0, -3.0, 50.0]
        target = (400.0, 700.0)
        needed = gaze_cnn.needed_combined(features, target, screen, mm)
        point = gaze_cnn.project(features, needed[0], needed[1], screen, mm)
        self.assertAlmostEqual(point[0], target[0], places=6)
        self.assertAlmostEqual(point[1], target[1], places=6)
        eye_yaw, eye_pitch = 0.05, -0.04
        coef = np.zeros((8, 2))
        coef[-1] = (eye_yaw, eye_pitch)
        model = {"coef": coef, "screen": list(screen), "screen_mm": list(mm)}
        shy, shp = gaze_model._V4_SIGNS[2], gaze_model._V4_SIGNS[3]
        mine = gaze_cnn.project(features, shy * features[4] + eye_yaw, shp * features[5] + eye_pitch, screen, mm)
        theirs = gaze_model._v4_point(model, features)
        self.assertTrue(np.allclose(mine, theirs))

    def test_cnn_and_hybrid_recover_a_known_mapping(self):
        screen, mm = (1920, 1080), (344.0, 193.0)
        gain = np.array([1.25, 0.8])
        offset = np.array([0.03, -0.04])
        samples = []
        for y in (180, 540, 900):
            for x in (320, 960, 1600):
                for head in ((0, 2, 55), (3, -1, 48), (-2, 4, 62)):
                    features = [0.0, 0.0, 0.0, 0.0, 0.1, -0.05, *head]
                    needed = np.array(gaze_cnn.needed_combined(features, (x, y), screen, mm))
                    degrees = np.degrees((needed - offset) / gain)
                    samples.append((features, (x, y), float(degrees[0]), float(degrees[1])))
        fitted = gaze_cnn.fit_cnn(samples, screen, mm)
        np.testing.assert_allclose(fitted["gain"], gain, atol=1e-8)
        np.testing.assert_allclose(fitted["offset"], offset, atol=1e-8)
        for features, target, yaw, pitch in samples:
            point = gaze_cnn.predict_cnn(fitted, features, yaw, pitch)
            self.assertLess(math.hypot(point[0] - target[0], point[1] - target[1]), 1e-4)

        rng = np.random.default_rng(0)
        coef = np.zeros((10, 2))
        coef[0, 0], coef[7, 0], coef[9, 0] = 0.35, 0.5, 0.02
        coef[1, 1], coef[8, 1], coef[9, 1] = -0.25, 0.4, -0.01
        shy, shp = gaze_model._V4_SIGNS[2], gaze_model._V4_SIGNS[3]
        hybrid = []
        draws = 0
        while len(hybrid) < 40:
            draws += 1
            if draws > 5000:
                self.fail("could not draw on-screen hybrid samples")
            h, v, lh, lv = (float(rng.uniform(-0.25, 0.25)) for _ in range(4))
            yaw_deg, pitch_deg = float(rng.uniform(-12, 12)), float(rng.uniform(-8, 8))
            features = [h, v, lh, lv, 0.05, -0.02, 1.0, -1.0, 55.0]
            eye = gaze_cnn.hybrid_terms(features, yaw_deg, pitch_deg) @ coef
            point = gaze_cnn.project(
                features, float(eye[0] + shy * features[4]), float(eye[1] + shp * features[5]), screen, mm)
            if point is None or not (80 < point[0] < 1840 and 80 < point[1] < 1000):
                continue
            hybrid.append((features, point, yaw_deg, pitch_deg))
        recovered = np.asarray(gaze_cnn.fit_hybrid(hybrid, screen, mm)["coef"])
        np.testing.assert_allclose(recovered[7], coef[7], atol=0.02)
        np.testing.assert_allclose(recovered[8], coef[8], atol=0.02)
        errors = []
        for features, point, yaw_deg, pitch_deg in hybrid:
            pred = gaze_cnn.predict_hybrid(
                {"coef": recovered.tolist(), "screen": list(screen), "screen_mm": list(mm)},
                features, yaw_deg, pitch_deg)
            errors.append(math.hypot(pred[0] - point[0], pred[1] - point[1]))
        self.assertLess(float(np.median(errors)), 1.0)
        self.assertLess(max(errors), 5.0)

    def test_score_counts_targets_frames_and_jitter(self):
        items = [
            {"target": (0, 0), "point": (0, 0), "t": 0.0, "event": ("validation", 0)},
            {"target": (0, 0), "point": (10, 0), "t": 0.1, "event": ("validation", 0)},
            {"target": (500, 500), "point": (500, 500), "t": 1.0, "event": ("validation", 1)},
            {"target": (500, 500), "point": (500, 530), "t": 1.1, "event": ("validation", 1)},
        ]
        stats = gaze_cnn.score_frames(items, (1920, 1080))
        self.assertAlmostEqual(stats["error_px"], 10.0)
        self.assertEqual((stats["region_hits"], stats["region_total"]), (2, 2))
        self.assertAlmostEqual(stats["frame_median_px"], 5.0)
        self.assertAlmostEqual(stats["jitter_median_px"], 20.0)
        self.assertAlmostEqual(stats["frame_p90_px"], float(np.percentile([0, 10, 0, 30], 90)))
        self.assertAlmostEqual(stats["jitter_p90_px"], float(np.percentile([10, 30], 90)))


@unittest.skipIf(np is None, "numpy is not installed")
class EvaluateTests(unittest.TestCase):
    def test_a_synthetic_recording_recovers_the_model(self):
        screen, mm = (1920, 1080), (344.0, 193.0)
        landmarks = _landmarks()

        def phase(rows, name, targets, t0, head):
            for index, (x, y) in enumerate(targets):
                start = t0 + index * 2.0
                rows.append({
                    "rec": "target", "phase": name, "id": index, "x": x, "y": y,
                    "shown": start - 0.5, "start": start, "end": start + 1.0,
                })
                for step in range(3):
                    frame = {
                        "rec": "frame", "t": round(start + 0.2 * step, 3), "w": 640, "h": 480,
                        "lm": landmarks, "bs": {}, "mat": _matrix(*head),
                    }
                    features = gaze_cnn._features_of(frame, mm)
                    yaw, pitch = gaze_cnn.needed_combined(features, (x, y), screen, mm)
                    degrees = [math.degrees(yaw), math.degrees(pitch)]
                    frame["cnn"] = {"mobileone_s0": degrees, "resnet18": degrees}
                    frame["cnn_ms"] = {"mobileone_s0": 12.0, "resnet18": 48.0}
                    rows.append(frame)

        rows = [{"rec": "meta", "alias": "ada", "screen": [1920, 1080], "screen_mm": [344, 193]}]
        calibration = [(x, y) for y in (180, 540, 900) for x in (320, 960, 1600)]
        validation = [(320, 180), (960, 180), (1600, 180), (320, 540), (960, 540)]
        drift = [(1600, 540), (320, 900), (960, 900), (1600, 900), (960, 700)]
        phase(rows, "calibration", calibration, 10, (0, 0, 50))
        phase(rows, "validation", validation, 100, (3, -2, 60))
        phase(rows, "drift", drift, 200, (-2, 1, 45))

        folder = Path(tempfile.mkdtemp(prefix="ally-gaze-eval-"))
        nested = folder / "run" / "ada.jsonl"
        nested.parent.mkdir()
        nested.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        (folder / "plain.jsonl").write_text(json.dumps({"rec": "frame", "t": 1, "lm": landmarks}) + "\n",
                                            encoding="utf-8")
        out = folder / "report.csv"
        buf = io.StringIO()
        with redirect_stdout(buf):
            scored = gaze_cnn.evaluate([str(folder)], out=str(out))
        text = buf.getvalue()
        self.assertIn("mobileone_s0", text)
        self.assertIn("5/5", text)
        files = {Path(row["file"]).name for row in scored}
        self.assertEqual(files, {"ada.jsonl"})
        cnn = [row for row in scored if row["model"] == "mobileone_s0" and row["approach"] == "cnn"]
        self.assertEqual([row["phase"] for row in cnn], ["validation", "drift"])
        for row in cnn:
            self.assertLess(row["error_px"], 1.0)
            self.assertEqual((row["region_hits"], row["region_total"]), (5, 5))
            self.assertAlmostEqual(row["median_ms"], 12.0)
        hybrid = next(row for row in scored if row["approach"] == "hybrid" and row["phase"] == "validation"
                      and row["model"] == "resnet18")
        v4 = next(row for row in scored if row["approach"] == "v4" and row["phase"] == "validation"
                  and row["model"] == "resnet18")
        self.assertLess(hybrid["error_px"], 10.0)
        self.assertGreater(v4["error_px"], 100.0)
        self.assertAlmostEqual(hybrid["median_ms"], 48.0)
        csv_text = out.read_text(encoding="utf-8")
        self.assertIn("error_px", csv_text)
        self.assertNotIn("plain.jsonl", csv_text)
        self.assertNotIn("jpeg", csv_text.lower())

        empty = Path(tempfile.mkdtemp(prefix="ally-gaze-empty-"))
        nowhere = empty / "none.csv"
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(gaze_cnn.evaluate([str(empty)], out=str(nowhere)), [])
        self.assertIn("No recordings with gaze-model angles.", buf.getvalue())


class DownloadTests(unittest.TestCase):
    def test_present_files_are_kept_and_nothing_is_fetched(self):
        module = _script()
        state = Path(tempfile.mkdtemp(prefix="ally-gaze-models-")) / "state"
        models = state / "models"
        models.mkdir(parents=True)
        mobile = models / "mobileone_s0_gaze.onnx"
        resnet = models / "resnet18_gaze.onnx"
        mobile.write_bytes(b"already-mobile")
        resnet.write_bytes(b"already-res")

        def boom(url, timeout=120):
            raise AssertionError(f"network {url}")

        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(module.ensure_models(state, ["mobileone_s0", "resnet18"], opener=boom), 0)
        self.assertIn("Already have mobileone_s0_gaze.onnx", buf.getvalue())
        self.assertEqual(mobile.read_bytes(), b"already-mobile")
        self.assertEqual(resnet.read_bytes(), b"already-res")

        mobile.unlink()
        calls = []

        class Body:
            def read(self):
                return b"Z" * 200_000

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake(url, timeout=120):
            calls.append(url)
            return Body()

        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(module.ensure_models(state, ["mobileone_s0", "resnet18"], opener=fake), 0)
        self.assertIn("Downloading mobileone_s0_gaze.onnx", buf.getvalue())
        self.assertIn("Already have resnet18_gaze.onnx", buf.getvalue())
        self.assertEqual(calls, [module.URL.format(name="mobileone_s0")])
        self.assertEqual(mobile.read_bytes(), b"Z" * 200_000)
        self.assertEqual(resnet.read_bytes(), b"already-res")
        self.assertFalse(list(state.rglob("*.part")))

        mobile.unlink()

        class Tiny:
            def read(self):
                return b"short"

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(module.ensure_models(state, ["mobileone_s0"], opener=lambda url, timeout=120: Tiny()), 1)
        self.assertIn("looks wrong", buf.getvalue())
        self.assertFalse(mobile.exists())
        mobile.write_bytes(b"already-mobile")

        import unittest.mock as mock

        buf = io.StringIO()
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network")):
            with redirect_stdout(buf):
                code = module.main(["--state", str(state)])
        self.assertEqual(code, 0)
        self.assertIn("Already have resnet18_gaze.onnx", buf.getvalue())
        self.assertEqual(mobile.read_bytes(), b"already-mobile")
        self.assertEqual(resnet.read_bytes(), b"already-res")


class PrivacyTests(unittest.TestCase):
    def test_the_runner_does_not_import_the_network(self):
        path = ROOT / "ally" / "gaze_cnn.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        banned = {"socket", "http", "ssl", "urllib", "requests", "httpx", "websocket"}
        names = {"imwrite", "VideoWriter", "imencode"}
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                mods = [node.module]
            for mod in mods:
                self.assertNotIn(mod.split(".")[0], banned, mod)
            if isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, names)


if __name__ == "__main__":
    unittest.main()
