"""LibreFace action units on one face, in memory.

The frame is aligned, scored, and dropped. Nothing is written and nothing is sent.
The presence head returns 12 probabilities in BP4D order (AU 1, 2, 4, 6, 7, 10, 12, 14, 15, 17, 23, 24).
AU6 is index 3, AU7 is index 4, AU4 is index 2, AU12 is index 6.

Model files live in the app state folder: state/models/LibreFace_AU_Encoder.onnx and
state/models/LibreFace_AU_Presence.onnx. Fetch them with python scripts/get_au_models.py.
They are not bundled. USC research license: free for education, research and non-profit use;
commercial use needs a license from the USC Stevens Center.

Off unless the camera helper is started with au set to libreface.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

# USC research license. Doctor, the recorder, and the download script repeat this sentence.
LICENSE = (
    "USC research license: free for education, research and non-profit use; "
    "commercial use needs a license from the USC Stevens Center."
)

# BP4D presence head. The intensity head is a different set and is not used.
AU_ORDER = (1, 2, 4, 6, 7, 10, 12, 14, 15, 17, 23, 24)
AU_COUNT = 12
FEATURE_SIZE = 512

ENCODER_FILE = "LibreFace_AU_Encoder.onnx"
PRESENCE_FILE = "LibreFace_AU_Presence.onnx"

# FFHQ-style alignment, then the classifier's 224 centre crop.
ALIGN_SIZE = 512
RESIZE_SIZE = 256
INPUT_SIZE = 224
CROP_OFFSET = 16  # (256 - 224) / 2
HALF_PIXEL = 0.5  # PIL's QUAD transform addresses pixel centers
EYE_SCALE = 2.0  # the eye-to-eye span sets the quad when it is the longer one
MOUTH_SCALE = 1.8  # the eye-to-mouth span sets the quad when the face is taller
MOUTH_SHIFT = 0.1  # the quad center sits a tenth of the way from the eyes toward the mouth

# ImageNet normalization, same numbers LibreFace uses on the cropped face.
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)

# Unique MediaPipe Face Landmarker connection indices, in connection order.
# LibreFace calls the image-right eye (the person's left) eye_right. The mean does not
# depend on order, and the mouth uses only its leftmost and rightmost points.
LEFT_EYE = (263, 249, 390, 373, 374, 380, 381, 382, 362, 466, 388, 387, 386, 385, 384, 398)
RIGHT_EYE = (33, 7, 163, 144, 145, 153, 154, 155, 133, 246, 161, 160, 159, 158, 157, 173)
LIPS = (
    61, 146, 91, 181, 84, 17, 314, 405, 321, 375,
    291, 185, 40, 39, 37, 0, 267, 269, 270, 409,
    78, 95, 88, 178, 87, 14, 317, 402, 318, 324,
    308, 191, 80, 81, 82, 13, 312, 311, 310, 415,
)

# A coordinate this small is a fraction of the frame. Larger values are already pixels.
_NORMALIZED_MAX = 2.0
_MIN_SPAN = 1e-6

_said = False


class MissingDependency(RuntimeError):
    """opencv or onnxruntime is not installed."""


def say_once(reason: str) -> None:
    """One line on stderr. Later frames stay quiet."""
    global _said
    if _said or not reason:
        return
    _said = True
    err = sys.stderr
    if err is None:
        return
    try:
        err.write(reason + "\n")
        err.flush()
    except OSError:
        pass


def _cv2():
    try:
        import cv2
    except ImportError as exc:
        raise MissingDependency("The action-unit model needs opencv-python.") from exc
    return cv2


def _onnxruntime():
    try:
        import onnxruntime
    except ImportError as exc:
        raise MissingDependency("The action-unit model needs onnxruntime. Run pip install onnxruntime.") from exc
    return onnxruntime


def _ort_imports() -> bool:
    try:
        _onnxruntime()
    except MissingDependency:
        return False
    return True


def model_dir(state_dir: Path | str) -> Path:
    return Path(state_dir) / "models"


def encoder_path(state_dir: Path | str) -> Path:
    return model_dir(state_dir) / ENCODER_FILE


def presence_path(state_dir: Path | str) -> Path:
    return model_dir(state_dir) / PRESENCE_FILE


def au_readiness(state_dir: Path | str) -> dict:
    """Whether both ONNX files are on disk, and whether onnxruntime imports.

    This does not open the camera and does not download anything.
    """
    encoder = encoder_path(state_dir)
    presence = presence_path(state_dir)
    return {
        "files": encoder.is_file() and presence.is_file(),
        "encoder": encoder.is_file(),
        "presence": presence.is_file(),
        "onnxruntime": _ort_imports(),
    }


def missing_reason(encoder: Path, presence: Path) -> str | None:
    """Why the model cannot run, or None when both files and onnxruntime are ready."""
    absent = []
    if not encoder.is_file():
        absent.append(encoder.name)
    if not presence.is_file():
        absent.append(presence.name)
    ort_ok = _ort_imports()
    if not absent and ort_ok:
        return None
    parts = []
    if absent:
        listed = ", ".join(absent)
        parts.append(f"The action-unit model isn't here yet ({listed}).")
        parts.append("Run python scripts/get_au_models.py.")
    if not ort_ok:
        parts.append("The action-unit model needs onnxruntime. Run pip install onnxruntime.")
    return " ".join(parts)


def _xy(landmarks, index: int, width: int, height: int) -> np.ndarray | None:
    """One landmark as pixel x, y. MediaPipe fractions are scaled by the frame size."""
    try:
        item = landmarks[index]
    except (IndexError, TypeError, KeyError):
        return None
    try:
        if hasattr(item, "x") and hasattr(item, "y"):
            x = float(item.x)
            y = float(item.y)
        else:
            x = float(item[0])
            y = float(item[1])
    except (TypeError, ValueError, IndexError):
        return None
    if not math.isfinite(x) or not math.isfinite(y):
        return None
    if abs(x) <= _NORMALIZED_MAX and abs(y) <= _NORMALIZED_MAX:
        x = x * width
        y = y * height
    return np.array([x, y], dtype=np.float64)


def quad_from_pixels(points) -> np.ndarray | None:
    """FFHQ quad from pixel landmarks. Corners are top-left, bottom-left, bottom-right, top-right."""
    pts = np.asarray(points, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] < 2 or pts.shape[0] <= max(LEFT_EYE + RIGHT_EYE + LIPS):
        return None
    used = list(LEFT_EYE + RIGHT_EYE + LIPS)
    if not np.isfinite(pts[used, :2]).all():
        return None
    eye_right = pts[list(LEFT_EYE), :2].mean(axis=0)
    eye_left = pts[list(RIGHT_EYE), :2].mean(axis=0)
    mouth = pts[list(LIPS), :2]
    xs = mouth[:, 0]
    mouth_avg = (mouth[int(np.argmin(xs))] + mouth[int(np.argmax(xs))]) * 0.5
    eye_avg = (eye_left + eye_right) * 0.5
    eye_to_eye = eye_right - eye_left
    eye_to_mouth = mouth_avg - eye_avg
    # flipud(v) * [-1, 1] is the perpendicular LibreFace uses.
    spun = np.flipud(eye_to_mouth) * np.array([-1.0, 1.0])
    x = eye_to_eye - spun
    unit = float(np.hypot(x[0], x[1]))
    if unit < _MIN_SPAN:
        return None
    x = x / unit
    eye_span = float(np.hypot(eye_to_eye[0], eye_to_eye[1])) * EYE_SCALE
    mouth_span = float(np.hypot(eye_to_mouth[0], eye_to_mouth[1])) * MOUTH_SCALE
    x = x * max(eye_span, mouth_span)
    y = np.flipud(x) * np.array([-1.0, 1.0])
    center = eye_avg + eye_to_mouth * MOUTH_SHIFT
    quad = np.stack([center - x - y, center - x + y, center + x + y, center + x - y])
    return quad


def align_quad(landmarks, width: int, height: int) -> np.ndarray | None:
    """The same quad, from MediaPipe landmarks on a frame of this size."""
    if landmarks is None:
        return None
    try:
        width = int(width)
        height = int(height)
    except (TypeError, ValueError):
        return None
    if width < 2 or height < 2:
        return None
    count = max(LEFT_EYE + RIGHT_EYE + LIPS) + 1
    pixels = np.zeros((count, 2), dtype=np.float64)
    for index in LEFT_EYE + RIGHT_EYE + LIPS:
        point = _xy(landmarks, index, width, height)
        if point is None:
            return None
        pixels[index] = point
    return quad_from_pixels(pixels)


def align(frame_bgr, landmarks) -> np.ndarray | None:
    """BGR frame to a 512x512 RGB face. The frame is not kept.

    Four quad corners go through cv2.getPerspectiveTransform and warpPerspective (bilinear).
    Pixels outside the frame are filled with the edge color.
    """
    if frame_bgr is None or landmarks is None:
        return None
    frame = np.asarray(frame_bgr)
    if frame.ndim != 3 or frame.shape[2] < 3:
        return None
    height, width = int(frame.shape[0]), int(frame.shape[1])
    quad = align_quad(landmarks, width, height)
    if quad is None:
        return None
    cv2 = _cv2()
    rgb = cv2.cvtColor(frame[:, :, :3], cv2.COLOR_BGR2RGB)
    src = np.asarray(quad, dtype=np.float32) + HALF_PIXEL
    side = float(ALIGN_SIZE - 1)
    dst = np.array([[0.0, 0.0], [0.0, side], [side, side], [side, 0.0]], dtype=np.float32)
    try:
        matrix = cv2.getPerspectiveTransform(src, dst)
    except Exception:
        return None
    warped = cv2.warpPerspective(
        rgb, matrix, (ALIGN_SIZE, ALIGN_SIZE),
        flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return warped


def normalize_rgb(rgb: np.ndarray) -> np.ndarray:
    """HWC RGB image to one NCHW float32 tensor: /255, then ImageNet mean and std."""
    image = np.asarray(rgb, dtype=np.float32) / 255.0
    mean = np.asarray(IMAGENET_MEAN, dtype=np.float32)
    std = np.asarray(IMAGENET_STD, dtype=np.float32)
    image = (image - mean) / std
    chw = np.transpose(image, (2, 0, 1))
    return np.ascontiguousarray(chw[None, ...], dtype=np.float32)


def to_input(aligned_rgb) -> np.ndarray | None:
    """512x512 RGB face to a 1x3x224x224 tensor. Resize to 256, then centre-crop 224."""
    image = np.asarray(aligned_rgb)
    if image.ndim != 3 or image.shape[2] < 3:
        return None
    cv2 = _cv2()
    small = cv2.resize(image[:, :, :3], (RESIZE_SIZE, RESIZE_SIZE), interpolation=cv2.INTER_LINEAR)
    crop = small[CROP_OFFSET:CROP_OFFSET + INPUT_SIZE, CROP_OFFSET:CROP_OFFSET + INPUT_SIZE]
    if crop.shape[0] != INPUT_SIZE or crop.shape[1] != INPUT_SIZE:
        return None
    return normalize_rgb(crop)


class ActionUnits:
    """Encoder plus presence head. Load them once, then ask for 12 probabilities."""

    def __init__(self, state_dir: Path | str, encoder_file: Path | str | None = None,
                 presence_file: Path | str | None = None):
        self.state_dir = Path(state_dir)
        if encoder_file is None:
            self.encoder_file = encoder_path(self.state_dir)
        else:
            self.encoder_file = Path(encoder_file)
        if presence_file is None:
            self.presence_file = presence_path(self.state_dir)
        else:
            self.presence_file = Path(presence_file)
        self._encoder = None
        self._presence = None
        self._enc_input = "image"
        self._pres_input = "feature"
        self._failed = False

    def load(self) -> bool:
        """Open both ONNX files on CPU. False, said once, when they cannot run."""
        if self._encoder is not None and self._presence is not None:
            return True
        if self._failed:
            return False
        reason = missing_reason(self.encoder_file, self.presence_file)
        if reason:
            say_once(reason)
            self._failed = True
            return False
        try:
            _cv2()
            ort = _onnxruntime()
            encoder = ort.InferenceSession(str(self.encoder_file), providers=["CPUExecutionProvider"])
            presence = ort.InferenceSession(str(self.presence_file), providers=["CPUExecutionProvider"])
        except MissingDependency as exc:
            say_once(str(exc))
            self._failed = True
            return False
        except Exception as exc:
            say_once(f"The action-unit model could not be loaded ({exc}).")
            self._failed = True
            return False
        self._encoder = encoder
        self._presence = presence
        inputs = encoder.get_inputs() if hasattr(encoder, "get_inputs") else []
        if inputs:
            self._enc_input = inputs[0].name
        inputs = presence.get_inputs() if hasattr(presence, "get_inputs") else []
        if inputs:
            self._pres_input = inputs[0].name
        return True

    def presence(self, frame_bgr, landmarks) -> list[float] | None:
        """12 presence probabilities, or None. The frame is only used to build the tensor."""
        if not self.load():
            return None
        try:
            aligned = align(frame_bgr, landmarks)
        except MissingDependency as exc:
            say_once(str(exc))
            self._failed = True
            self._encoder = None
            self._presence = None
            return None
        if aligned is None:
            return None
        tensor = to_input(aligned)
        if tensor is None:
            return None
        if tensor.shape != (1, 3, INPUT_SIZE, INPUT_SIZE):
            return None
        return self._run(tensor)

    def _run(self, tensor: np.ndarray) -> list[float] | None:
        feature = self._encoder.run(None, {self._enc_input: tensor})[0]
        feature = np.asarray(feature, dtype=np.float32).reshape(1, -1)
        if feature.shape[1] != FEATURE_SIZE:
            return None
        output = self._presence.run(None, {self._pres_input: feature})[0]
        flat = np.asarray(output, dtype=np.float64).reshape(-1)
        if flat.size != AU_COUNT or not np.isfinite(flat).all():
            return None
        return [round(float(value), 4) for value in flat]
