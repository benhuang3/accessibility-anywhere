"""LibreFace action-unit alignment and a synthetic model smoke test. No camera."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from helpers import ROOT

try:
    import numpy as np
    from ally.senses import au
except ImportError:
    np = None
    au = None

try:
    import cv2
except ImportError:
    cv2 = None


# The lead's scratchpad copy. The test skips when those files are not on this machine.
_SCRATCH_ONNX = Path(
    r"C:\Users\frank\AppData\Local\Temp\claude\C--Users-frank"
    r"\da933cbb-02ed-43b4-94e6-42eb6f2beb48\scratchpad"
    r"\libreface_nupkg\x\contentFiles\any\any"
)


def _pixels():
    """Eyes on a horizontal line, mouth below, so the quad is easy to check by hand."""
    pts = np.zeros((478, 2), dtype=np.float64)
    pts[list(au.LEFT_EYE)] = (100.0, 50.0)
    pts[list(au.RIGHT_EYE)] = (40.0, 50.0)
    pts[list(au.LIPS)] = (70.0, 90.0)
    pts[au.LIPS[0]] = (40.0, 90.0)
    pts[au.LIPS[-1]] = (100.0, 90.0)
    return pts


def _vertical_pixels():
    """Eyes on a vertical line, so a sign error in the perpendicular shows up."""
    pts = np.zeros((478, 2), dtype=np.float64)
    pts[list(au.LEFT_EYE)] = (0.0, 60.0)
    pts[list(au.RIGHT_EYE)] = (0.0, 0.0)
    pts[list(au.LIPS)] = (40.0, 30.0)
    pts[au.LIPS[0]] = (20.0, 30.0)
    pts[au.LIPS[-1]] = (60.0, 30.0)
    return pts


def _normalized(pixels, width, height):
    pts = np.zeros((478, 3), dtype=np.float64)
    pts[:, 0] = pixels[:, 0] / width
    pts[:, 1] = pixels[:, 1] / height
    return pts


@unittest.skipIf(np is None, "numpy is not installed")
class AlignMathTests(unittest.TestCase):
    def test_horizontal_face_matches_the_ffhq_quad(self):
        quad = au.quad_from_pixels(_pixels())
        expected = np.array([
            [-50.0, -66.0],
            [-50.0, 174.0],
            [190.0, 174.0],
            [190.0, -66.0],
        ])
        np.testing.assert_allclose(quad, expected, atol=1e-6)
        self.assertEqual(quad.shape, (4, 2))

    def test_vertical_face_uses_the_perpendicular(self):
        quad = au.quad_from_pixels(_vertical_pixels())
        expected = np.array([
            [124.0, -90.0],
            [-116.0, -90.0],
            [-116.0, 150.0],
            [124.0, 150.0],
        ])
        np.testing.assert_allclose(quad, expected, atol=1e-6)

    def test_normalized_landmarks_scale_to_the_same_quad(self):
        width, height = 200, 160
        pixels = _pixels()
        got = au.align_quad(_normalized(pixels, width, height), width, height)
        np.testing.assert_allclose(got, au.quad_from_pixels(pixels), atol=1e-6)

    def test_short_or_collapsed_landmarks_have_no_quad(self):
        self.assertIsNone(au.align_quad([[0.5, 0.5, 0.0]] * 10, 640, 480))
        self.assertIsNone(au.quad_from_pixels(np.zeros((478, 2))))
        self.assertIsNone(au.align_quad(None, 640, 480))
        flat = _pixels()
        flat[:] = (10.0, 10.0)
        self.assertIsNone(au.quad_from_pixels(flat))


@unittest.skipIf(np is None or cv2 is None, "numpy and opencv are not installed")
class InputTests(unittest.TestCase):
    def test_centre_crop_and_imagenet_normalization(self):
        image = np.zeros((au.ALIGN_SIZE, au.ALIGN_SIZE, 3), np.uint8)
        image[:] = (30, 20, 10)
        tensor = au.to_input(image)
        self.assertEqual(tensor.shape, (1, 3, au.INPUT_SIZE, au.INPUT_SIZE))
        self.assertEqual(tensor.dtype, np.float32)
        rgb = np.array([30, 20, 10], dtype=np.float32) / 255.0
        expected = (rgb - np.array(au.IMAGENET_MEAN, dtype=np.float32)) / np.array(
            au.IMAGENET_STD, dtype=np.float32)
        np.testing.assert_allclose(tensor[0, :, 0, 0], expected, rtol=1e-5, atol=1e-5)
        np.testing.assert_allclose(tensor[0, :, 100, 100], expected, rtol=1e-5, atol=1e-5)

    def test_warp_stays_in_memory_and_keeps_a_flat_color(self):
        frame = np.zeros((480, 640, 3), np.uint8)
        frame[:] = (10, 20, 30)  # BGR, so RGB is (30, 20, 10)
        aligned = au.align(frame, _normalized(_pixels(), 640, 480))
        self.assertEqual(aligned.shape, (au.ALIGN_SIZE, au.ALIGN_SIZE, 3))
        self.assertTrue(np.all(aligned[256, 256] == np.array([30, 20, 10])))


@unittest.skipIf(np is None, "numpy is not installed")
class MissingModelTests(unittest.TestCase):
    def tearDown(self):
        au._said = False

    def test_missing_files_return_none_and_are_said_once(self):
        au._said = False
        state = Path(tempfile.mkdtemp(prefix="ally-au-"))
        runner = au.ActionUnits(state)
        frame = np.zeros((48, 64, 3), np.uint8)
        marks = _normalized(_pixels(), 64, 48)
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertIsNone(runner.presence(frame, marks))
            self.assertIsNone(runner.presence(frame, marks))
        text = err.getvalue()
        self.assertEqual(len(text.strip().splitlines()), 1)
        self.assertIn(au.ENCODER_FILE, text)
        self.assertIn(au.PRESENCE_FILE, text)
        self.assertIn("python scripts/get_au_models.py", text)
        self.assertFalse(any(state.rglob("*.png")))
        self.assertFalse(any(state.rglob("*.jpg")))

    def test_missing_onnxruntime_is_said_once(self):
        au._said = False
        state = Path(tempfile.mkdtemp(prefix="ally-au-ort-"))
        models = state / "models"
        models.mkdir()
        (models / au.ENCODER_FILE).write_bytes(b"not-a-model")
        (models / au.PRESENCE_FILE).write_bytes(b"not-a-model")
        real = au._onnxruntime

        def boom():
            raise au.MissingDependency("The action-unit model needs onnxruntime. Run pip install onnxruntime.")

        au._onnxruntime = boom
        try:
            runner = au.ActionUnits(state)
            frame = np.zeros((48, 64, 3), np.uint8)
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertIsNone(runner.presence(frame, _normalized(_pixels(), 64, 48)))
                self.assertIsNone(runner.presence(frame, _normalized(_pixels(), 64, 48)))
            text = err.getvalue()
            self.assertEqual(len(text.strip().splitlines()), 1)
            self.assertIn("onnxruntime", text)
        finally:
            au._onnxruntime = real


def _scratch_ready() -> bool:
    if au is None:
        return False
    encoder = _SCRATCH_ONNX / au.ENCODER_FILE
    presence = _SCRATCH_ONNX / au.PRESENCE_FILE
    return encoder.is_file() and presence.is_file()


@unittest.skipIf(np is None or cv2 is None or not _scratch_ready(), "LibreFace ONNX copy is not on this machine")
class SmokeTests(unittest.TestCase):
    def test_synthetic_frame_returns_twelve_probabilities(self):
        runner = au.ActionUnits(
            Path(tempfile.mkdtemp(prefix="ally-au-smoke-")),
            encoder_file=_SCRATCH_ONNX / au.ENCODER_FILE,
            presence_file=_SCRATCH_ONNX / au.PRESENCE_FILE,
        )
        frame = np.zeros((480, 640, 3), np.uint8)
        frame[:, :, 0] = 40
        frame[:, :, 1] = 80
        frame[:, :, 2] = 120
        values = runner.presence(frame, _normalized(_pixels(), 640, 480))
        self.assertIsNotNone(values)
        self.assertEqual(len(values), au.AU_COUNT)
        self.assertEqual(len(au.AU_ORDER), au.AU_COUNT)
        for value in values:
            self.assertIsInstance(value, float)
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)
        again = runner.presence(frame, _normalized(_pixels(), 640, 480))
        self.assertEqual(len(again), au.AU_COUNT)


if __name__ == "__main__":
    unittest.main()
