"""Run a MobileGaze ONNX model on one face, in memory.

The picture is cropped, scored, and dropped. Nothing is written and nothing is sent.
Yaw and pitch come back in degrees (the model's own bin decode, before it turns them
into radians). Head pose is already inside those angles.

    python -m ally.gaze_record --cnn mobileone_s0,resnet18

Model files live in the app state folder: state/models/<name>_gaze.onnx.
Fetch them with python scripts/get_gaze_models.py.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np

from ally.senses.face import face_box

# MobileGaze onnx_inference.py: ImageNet normalization, 90 bins of 4 degrees.
INPUT_SIZE = 448
BINS = 90
BIN_WIDTH = 4.0
ANGLE_OFFSET = 180.0
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)
_NAME = re.compile(r"[A-Za-z0-9_-]{1,40}")


class MissingDependency(RuntimeError):
    """opencv or onnxruntime is not installed."""


class MissingModelFile(FileNotFoundError):
    """The named ONNX file is not in state/models yet."""

    def __init__(self, names: list[str]):
        self.names = list(names)
        listed = ", ".join(f"state/models/{name}_gaze.onnx" for name in self.names)
        super().__init__(
            f"The gaze model isn't here yet ({listed}).\n"
            "Run python scripts/get_gaze_models.py"
        )


def _cv2():
    try:
        import cv2
    except ImportError as e:
        raise MissingDependency("The gaze model needs opencv-python.") from e
    return cv2


def _onnxruntime():
    try:
        import onnxruntime
    except ImportError as e:
        raise MissingDependency("The gaze model needs onnxruntime.") from e
    return onnxruntime


def model_names(spec: str) -> list[str]:
    """Split a comma-separated --cnn list. Accepts `name` or `name_gaze.onnx`."""
    names: list[str] = []
    for part in str(spec or "").split(","):
        name = part.strip()
        if name.endswith(".onnx"):
            name = name[:-5]
        if name.endswith("_gaze"):
            name = name[:-5]
        if not name:
            continue
        if not _NAME.fullmatch(name) or "/" in part or "\\" in part:
            raise ValueError(f"Gaze model names are letters, digits, _ or -: {part.strip()}")
        if name not in names:
            names.append(name)
    if not names:
        raise ValueError("Name a gaze model, for example mobileone_s0 or resnet18.")
    return names


def model_file(state_dir: Path, name: str) -> Path:
    """Where one model is stored, next to the face landmarker."""
    return Path(state_dir) / "models" / f"{name}_gaze.onnx"


def model_paths(spec: str, state_dir: Path) -> list[Path]:
    """Resolve --cnn names to files. Raises MissingModelFile if any are absent."""
    names = model_names(spec)
    missing = [name for name in names if not model_file(state_dir, name).is_file()]
    if missing:
        raise MissingModelFile(missing)
    return [model_file(state_dir, name) for name in names]


def _stem(path: Path) -> str:
    name = Path(path).name
    if name.endswith(".onnx"):
        name = name[:-5]
    if name.endswith("_gaze"):
        name = name[:-5]
    return name or Path(path).stem


def softmax(logits) -> np.ndarray:
    """Softmax over the 90 gaze bins."""
    x = np.asarray(logits, dtype=np.float64).reshape(-1)
    if x.size != BINS:
        raise ValueError(f"expected {BINS} gaze bins, got {x.size}")
    x = x - np.max(x)
    exp = np.exp(x)
    return exp / np.sum(exp)


def decode_gaze(yaw_logits, pitch_logits) -> tuple[float, float]:
    """Bin logits to yaw and pitch in degrees.

    Softmax, expected bin index, times 4, minus 180. That is the camera-frame
    gaze direction. The upstream demo converts those degrees to radians; we keep
    degrees so a recording can store them as plain numbers.
    """
    idx = np.arange(BINS, dtype=np.float64)
    yaw = float(np.sum(softmax(yaw_logits) * idx) * BIN_WIDTH - ANGLE_OFFSET)
    pitch = float(np.sum(softmax(pitch_logits) * idx) * BIN_WIDTH - ANGLE_OFFSET)
    return yaw, pitch


def face_box_from_landmarks(landmarks, width: int, height: int):
    """Pixel box (x0, y0, x1, y1) around the face, or None when it is too small.

    Same extent the camera helper uses for the face. MobileGaze crops a RetinaFace
    box; this is the landmark box in that role.
    """
    try:
        pts = np.asarray(landmarks, dtype=float)
    except (TypeError, ValueError):
        return None
    if pts.ndim != 2 or pts.shape[0] < 2 or pts.shape[1] < 2:
        return None
    if not np.isfinite(pts[:, :2]).all():
        return None
    width, height = int(width), int(height)
    if width < 2 or height < 2:
        return None
    x0, y0, x1, y1 = face_box(pts, width, height)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return int(x0), int(y0), int(x1), int(y1)


def normalize_rgb(rgb: np.ndarray) -> np.ndarray:
    """HWC RGB image to one NCHW float32 tensor: /255, then ImageNet mean and std."""
    image = np.asarray(rgb, dtype=np.float32) / 255.0
    mean = np.asarray(IMAGENET_MEAN, dtype=np.float32)
    std = np.asarray(IMAGENET_STD, dtype=np.float32)
    image = (image - mean) / std
    chw = np.transpose(image, (2, 0, 1))
    return np.ascontiguousarray(chw[None, ...], dtype=np.float32)


def preprocess(frame_bgr, box) -> np.ndarray | None:
    """Crop a BGR frame to the face box and return a 1x3x448x448 float32 tensor.

    Order matches MobileGaze: BGR to RGB, resize to 448, divide by 255, ImageNet
    normalize, channels first. The crop is not kept.
    """
    if frame_bgr is None or box is None:
        return None
    frame = np.asarray(frame_bgr)
    if frame.ndim != 3 or frame.shape[2] < 3:
        return None
    x0, y0, x1, y1 = (int(v) for v in box)
    height, width = frame.shape[:2]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(width, x1), min(height, y1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    crop = frame[y0:y1, x0:x1, :3]
    cv2 = _cv2()
    rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
    rgb = cv2.resize(rgb, (INPUT_SIZE, INPUT_SIZE))
    return normalize_rgb(rgb)


class GazeCNN:
    """One ONNX gaze model. Load it, then ask for yaw and pitch on a BGR frame."""

    def __init__(self, path: Path | str, session=None):
        self.path = Path(path)
        self.name = _stem(self.path)
        self._session = None
        self._input_name = "input"
        self._output_names = ["yaw", "pitch"]
        if session is not None:
            self._adopt(session)

    def _adopt(self, session) -> None:
        self._session = session
        inputs = session.get_inputs() if hasattr(session, "get_inputs") else []
        outputs = session.get_outputs() if hasattr(session, "get_outputs") else []
        if inputs:
            self._input_name = inputs[0].name
        if outputs:
            self._output_names = [item.name for item in outputs]

    def load(self) -> None:
        """Open the ONNX file on CPU. Also checks that opencv is importable."""
        _cv2()
        if self._session is not None:
            return
        ort = _onnxruntime()
        self._adopt(ort.InferenceSession(str(self.path), providers=["CPUExecutionProvider"]))

    def angles(self, frame_bgr, landmarks) -> tuple[float, float] | None:
        """Yaw and pitch in degrees for this frame, or None when the face box is empty.

        `frame_bgr` is only used to build the input tensor. It is not stored.
        """
        if frame_bgr is None or landmarks is None:
            return None
        frame = np.asarray(frame_bgr)
        if frame.ndim < 2:
            return None
        height, width = int(frame.shape[0]), int(frame.shape[1])
        box = face_box_from_landmarks(landmarks, width, height)
        tensor = preprocess(frame, box)
        if tensor is None:
            return None
        if tensor.shape != (1, 3, INPUT_SIZE, INPUT_SIZE):
            raise RuntimeError(f"gaze input has shape {tensor.shape}, expected 1x3x448x448")
        return self._run(tensor)

    def _run(self, tensor: np.ndarray) -> tuple[float, float]:
        if self._session is None:
            self.load()
        outputs = self._session.run(self._output_names, {self._input_name: tensor})
        by_name = {name: value for name, value in zip(self._output_names, outputs)}
        yaw = by_name.get("yaw", outputs[0])
        pitch = by_name.get("pitch", outputs[1] if len(outputs) > 1 else outputs[0])
        return decode_gaze(yaw, pitch)


def load_models(spec: str, state_dir: Path) -> list[GazeCNN]:
    """Load each named model from state/models. Missing files raise MissingModelFile."""
    runners = []
    for path in model_paths(spec, state_dir):
        runner = GazeCNN(path)
        runner.load()
        runners.append(runner)
    return runners
