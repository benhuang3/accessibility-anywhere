"""Run a MobileGaze ONNX model on one face, in memory.

The picture is cropped, scored, and dropped. Nothing is written and nothing is sent.
Yaw and pitch come back in degrees (the model's own bin decode, before it turns them
into radians). Head pose is already inside those angles.

    python -m ally.gaze_record --cnn mobileone_s0,resnet18
    python -m ally.gaze_cnn evaluate state/gaze_runs

Model files live in the app state folder: state/models/<name>_gaze.onnx.
Fetch them with python scripts/get_gaze_models.py.

evaluate fits each recorded model on the calibration dots and scores it on the
validation and drift dots, next to our v4 tracker.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from pathlib import Path

import numpy as np

from ally import gaze_model as gm
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


# ---------------------------------------------------------------- scoring
# Eyes shut are not gaze. Same cutoff the live tracker uses when it keeps a frame.
OPEN_MIN = 0.12
APPROACHES = ("cnn", "hybrid", "v4")
PHASES = ("validation", "drift")
CSV_FIELDS = (
    "file", "model", "approach", "phase", "error_px", "region_hits", "region_total",
    "frame_median_px", "frame_p90_px", "jitter_median_px", "jitter_p90_px", "median_ms",
)


def project(features, gaze_yaw: float, gaze_pitch: float, screen, screen_mm):
    """Screen point for a combined gaze angle, using v4's head-to-screen geometry.

    gaze_yaw and gaze_pitch are radians in v4's signed convention (head angle plus
    eye angle). tx, ty, tz are centimetres; the camera sits at the top centre.
    """
    return gm.project_gaze(features, gaze_yaw, gaze_pitch, screen, screen_mm)


def needed_combined(features, target, screen, screen_mm):
    """Gaze angles (radians) that would land this head position on the target.

    v4's needed eye angle plus the head's own yaw and pitch, so the result is the
    full direction the camera-frame model should report.
    """
    eye = gm._v4_needed(features, target, screen, screen_mm)
    if eye is None:
        return None
    _sx, _sy, shy, shp, _stx, _sty = gm._V4_SIGNS
    yaw, pitch = float(features[4]), float(features[5])
    return (eye[0] + shy * yaw, eye[1] + shp * pitch)


def clamp_point(point, screen) -> tuple[float, float]:
    """Same 10% margin gaze_model.predict uses, so the scores match validate."""
    width, height = screen
    return (
        float(np.clip(point[0], -0.1 * width, 1.1 * width)),
        float(np.clip(point[1], -0.1 * height, 1.1 * height)),
    )


def fit_cnn(samples, screen, screen_mm) -> dict | None:
    """Least squares gain and offset per axis, from model degrees to v4's gaze angle.

    samples are (features, target_xy, yaw_deg, pitch_deg) from the calibration dots.
    """
    angles, needed = [], []
    for feat, target, yaw, pitch in samples:
        full = needed_combined(feat, target, screen, screen_mm)
        if full is None:
            continue
        angles.append((math.radians(float(yaw)), math.radians(float(pitch))))
        needed.append(full)
    if len(angles) < 2:
        return None
    design_x = np.asarray(angles, dtype=float)
    design_y = np.asarray(needed, dtype=float)
    gain, offset = np.zeros(2), np.zeros(2)
    for axis in (0, 1):
        columns = np.column_stack([design_x[:, axis], np.ones(len(design_x))])
        coef, *_ = np.linalg.lstsq(columns, design_y[:, axis], rcond=None)
        gain[axis], offset[axis] = (float(coef[0]), float(coef[1]))
    return {
        "gain": gain.tolist(), "offset": offset.tolist(),
        "screen": [int(screen[0]), int(screen[1])],
        "screen_mm": [float(screen_mm[0]), float(screen_mm[1])],
    }


def predict_cnn(model, features, yaw_deg, pitch_deg):
    """Screen point from calibrated model angles and this frame's head position."""
    gain, offset = model["gain"], model["offset"]
    yaw = gain[0] * math.radians(float(yaw_deg)) + offset[0]
    pitch = gain[1] * math.radians(float(pitch_deg)) + offset[1]
    point = project(features, yaw, pitch, model["screen"], model["screen_mm"])
    if point is None:
        return None
    return clamp_point(point, model["screen"])


def hybrid_terms(features, yaw_deg, pitch_deg) -> np.ndarray:
    """v4's eye terms, then the model's yaw and pitch in radians, then the constant."""
    return gm.hybrid_terms(features, yaw_deg, pitch_deg)


def fit_hybrid(samples, screen, screen_mm, lam: float = 1e-3) -> dict | None:
    """One regression from eye terms plus model angles onto the needed eye angle.

    Same ridge as v4. samples are (features, target_xy, yaw_deg, pitch_deg).
    The live tracker fits this as feature set v5; this is the same function.
    """
    return gm.fit_hybrid(samples, screen, screen_mm, lam)


def predict_hybrid(model, features, yaw_deg, pitch_deg):
    """Screen point: predicted eye angle added to the head, then v4's projection."""
    point = gm.hybrid_point(model, features, yaw_deg, pitch_deg)
    if point is None:
        return None
    return clamp_point(point, model["screen"])


def _percentile(values, q: float):
    if not values:
        return None
    return float(np.percentile(np.asarray(values, dtype=float), q))


def score_frames(items, screen) -> dict:
    """Per-target median error and 3x3 hits, plus per-frame error and jitter.

    Jitter is the distance between consecutive frames of one dot, so the jump
    from one dot to the next is not counted.
    """
    grouped: dict[tuple, list] = {}
    events: dict[tuple, list] = {}
    frame_errors = []
    for item in items:
        key = (float(item["target"][0]), float(item["target"][1]))
        grouped.setdefault(key, []).append(item["point"])
        events.setdefault(tuple(item["event"]), []).append(item)
        frame_errors.append(float(np.hypot(item["point"][0] - key[0], item["point"][1] - key[1])))
    per_target = []
    hits = 0
    for key, points in grouped.items():
        med = np.median(np.asarray(points, dtype=float), axis=0)
        per_target.append(float(np.hypot(med[0] - key[0], med[1] - key[1])))
        hits += int(gm.region(float(med[0]), float(med[1]), screen) == gm.region(key[0], key[1], screen))
    jitters = []
    for event_items in events.values():
        ordered = sorted(event_items, key=lambda it: it["t"])
        for left, right in zip(ordered, ordered[1:]):
            jitters.append(float(np.hypot(
                left["point"][0] - right["point"][0], left["point"][1] - right["point"][1])))
    return {
        "error_px": float(np.median(per_target)) if per_target else None,
        "region_hits": hits,
        "region_total": len(grouped),
        "frame_median_px": float(np.median(frame_errors)) if frame_errors else None,
        "frame_p90_px": _percentile(frame_errors, 90),
        "jitter_median_px": float(np.median(jitters)) if jitters else None,
        "jitter_p90_px": _percentile(jitters, 90),
    }


def _screen_of(meta) -> tuple[int, int]:
    raw = meta.get("screen") or (1920, 1080)
    try:
        return int(raw[0]), int(raw[1])
    except (TypeError, ValueError, IndexError):
        return 1920, 1080


def _mm_of(meta) -> tuple[float, float]:
    raw = meta.get("screen_mm")
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        try:
            return float(raw[0]), float(raw[1])
        except (TypeError, ValueError):
            pass
    return tuple(float(v) for v in gm.DEFAULT_SCREEN_MM)


def read_rows(path: Path) -> list[dict]:
    rows = []
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def has_cnn(rows) -> bool:
    return any(row.get("rec") == "frame" and isinstance(row.get("cnn"), dict) and row["cnn"] for row in rows)


def _angles_of(frame, model: str):
    raw = (frame.get("cnn") or {}).get(model)
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return None
    try:
        return float(raw[0]), float(raw[1])
    except (TypeError, ValueError):
        return None


def _features_of(frame, screen_mm):
    got = gm.features_for_model(
        {"features": "v4", "screen_mm": screen_mm},
        frame.get("lm"), frame.get("bs"), frame.get("mat"), frame.get("w") or 0, frame.get("h") or 0,
    )
    if not got:
        return None
    feat, openness = got
    try:
        open_enough = float(openness) >= OPEN_MIN
    except (TypeError, ValueError):
        return None
    if not open_enough or len(feat) < 9:
        return None
    return [float(v) for v in feat]


def _targets(rows, phase: str) -> list[dict]:
    return [row for row in rows if row.get("rec") == "target" and row.get("phase") == phase]


def _inside(frame_t, target) -> bool:
    try:
        return float(target["start"]) <= float(frame_t) <= float(target["end"])
    except (KeyError, TypeError, ValueError):
        return False


def collect(frames, targets, screen_mm, model: str) -> list[dict]:
    """Frames inside each dot that have this model's angles and an open eye."""
    used = set()
    rows = []
    for index, target in enumerate(targets):
        try:
            xy = (float(target["x"]), float(target["y"]))
        except (KeyError, TypeError, ValueError):
            continue
        event = (target.get("phase"), target.get("id"), target.get("start"), index)
        for number, frame in enumerate(frames):
            if number in used or not _inside(frame.get("t"), target):
                continue
            angles = _angles_of(frame, model)
            feat = _features_of(frame, screen_mm)
            if angles is None or feat is None:
                continue
            used.add(number)
            rows.append({
                "feat": feat, "target": xy, "yaw": angles[0], "pitch": angles[1],
                "t": float(frame.get("t") or 0.0), "event": event,
            })
    return rows


def _predict_row(approach: str, model, row):
    if approach == "cnn":
        return predict_cnn(model, row["feat"], row["yaw"], row["pitch"])
    if approach == "hybrid":
        return predict_hybrid(model, row["feat"], row["yaw"], row["pitch"])
    return gm.predict(model, row["feat"])


def _fit_approach(approach: str, samples, screen, screen_mm):
    plain = [(row["feat"], row["target"]) for row in samples]
    angled = [(row["feat"], row["target"], row["yaw"], row["pitch"]) for row in samples]
    if approach == "cnn":
        return fit_cnn(angled, screen, screen_mm)
    if approach == "hybrid":
        return fit_hybrid(angled, screen, screen_mm)
    return gm.fit(plain, screen, "v4", screen_mm=screen_mm)


def median_ms(frames, model: str):
    values = []
    for frame in frames:
        raw = (frame.get("cnn_ms") or {}).get(model)
        try:
            values.append(float(raw))
        except (TypeError, ValueError):
            continue
    if not values:
        return None
    return float(np.median(values))


def score_recording(path, rows) -> list[dict]:
    """Fit on calibration and score validation and drift, for cnn, hybrid, and v4."""
    meta = next((row for row in rows if row.get("rec") == "meta"), {})
    screen, screen_mm = _screen_of(meta), _mm_of(meta)
    frames = [row for row in rows if row.get("rec") == "frame"]
    names: list[str] = []
    for frame in frames:
        for name in frame.get("cnn") or {}:
            if name not in names:
                names.append(str(name))
    results = []
    label = Path(path).name
    for name in names:
        calibration = collect(frames, _targets(rows, "calibration"), screen_mm, name)
        targets = {tuple(row["target"]) for row in calibration}
        if len(targets) < 6:
            print(f"{label}: {name} has {len(targets)} calibration dots, needs 6")
            continue
        cost = median_ms(frames, name)
        for approach in APPROACHES:
            try:
                model = _fit_approach(approach, calibration, screen, screen_mm)
            except (ValueError, np.linalg.LinAlgError) as exc:
                print(f"{label}: {name} {approach} could not be fit ({exc})")
                continue
            if model is None:
                print(f"{label}: {name} {approach} could not be fit")
                continue
            for phase in PHASES:
                held = collect(frames, _targets(rows, phase), screen_mm, name)
                items = []
                for row in held:
                    point = _predict_row(approach, model, row)
                    if point is None:
                        continue
                    items.append({"target": row["target"], "point": point, "t": row["t"], "event": row["event"]})
                stats = score_frames(items, screen)
                results.append({
                    "file": str(path), "model": name, "approach": approach, "phase": phase,
                    "median_ms": None if cost is None else round(cost, 2), **stats,
                })
    return results


def recording_paths(paths) -> list[Path]:
    found, seen = [], set()
    for raw in paths:
        path = Path(raw)
        candidates = sorted(path.rglob("*.jsonl")) if path.is_dir() else [path]
        for item in candidates:
            key = str(item.resolve()) if item.exists() else str(item)
            if key in seen:
                continue
            seen.add(key)
            if not item.is_file():
                print(f"No such recording: {item}")
                continue
            found.append(item)
    return found


def csv_destination(out) -> Path:
    if not out:
        return Path.cwd() / "gaze_cnn.csv"
    path = Path(out)
    if path.suffix.lower() == ".csv":
        return path
    return path / "gaze_cnn.csv"


def _write_csv(dest: Path, rows: list[dict]) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: "" if row.get(key) is None else row.get(key) for key in CSV_FIELDS})


def _print_table(rows: list[dict]) -> None:
    headers = (
        "file", "model", "approach", "phase", "target px", "areas",
        "frame p50", "frame p90", "jitter p50", "jitter p90", "ms",
    )
    print("| " + " | ".join(headers) + " |")
    print("|" + "|".join(["---"] * len(headers)) + "|")
    for row in rows:
        areas = f"{row['region_hits']}/{row['region_total']}" if row.get("region_total") else ""

        def cell(value):
            return "" if value is None else f"{value:.1f}"

        cells = [
            Path(row["file"]).name, row["model"], row["approach"], row["phase"],
            cell(row.get("error_px")), areas,
            cell(row.get("frame_median_px")), cell(row.get("frame_p90_px")),
            cell(row.get("jitter_median_px")), cell(row.get("jitter_p90_px")),
            cell(row.get("median_ms")),
        ]
        print("| " + " | ".join(str(item) for item in cells) + " |")


def evaluate(paths, out=None) -> list[dict]:
    """Score every recording under paths that stored cnn angles. Prints a table and writes a CSV."""
    scored: list[dict] = []
    saw = False
    for path in recording_paths(paths):
        rows = read_rows(path)
        if not has_cnn(rows):
            continue
        saw = True
        scored.extend(score_recording(path, rows))
    if not saw:
        print("No recordings with gaze-model angles.")
    _print_table(scored)
    dest = csv_destination(out)
    _write_csv(dest, scored)
    print(f"\nCSV: {dest}")
    return scored


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ally.gaze_cnn")
    sub = parser.add_subparsers(dest="cmd", required=True)
    cmd = sub.add_parser("evaluate", help="score recordings that include cnn angles")
    cmd.add_argument("paths", nargs="+")
    cmd.add_argument("--out", default=None, help="CSV file or folder (default: ./gaze_cnn.csv)")
    args = parser.parse_args(argv)
    evaluate(args.paths, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

