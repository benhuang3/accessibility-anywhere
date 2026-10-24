"""Webcam gaze math: face landmarks -> eye features -> screen point. Pure numpy, no camera.

Features per video frame: where each iris sits between its eye corners, MediaPipe's
eye-direction scores (eyeLookIn/Out/Up/Down), and head pose (turn, tilt, position).
Calibration fits a small ridge regression from those features to screen pixels, using the
frames recorded while the user looked at known dots. The fit is checked by leaving out one
dot at a time, which gives an honest accuracy estimate in pixels.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np

# MediaPipe Face Landmarker (478 points): corners, lids and iris centers of each eye.
RIGHT_EYE = {"outer": 33, "inner": 133, "top": 159, "bottom": 145, "iris": 468}
LEFT_EYE = {"outer": 263, "inner": 362, "top": 386, "bottom": 374, "iris": 473}
FEATURES = ["h", "v", "look_h", "look_v", "yaw", "pitch", "tx", "ty", "tz"]
MODEL_VERSION = 1


def _eye(pts: np.ndarray, idx: dict) -> tuple[float, float, float]:
    """Iris position across (h) and up/down (v) the eye, in eye widths, and how open the eye is."""
    inner, outer = pts[idx["inner"], :2], pts[idx["outer"], :2]
    axis = outer - inner
    width = float(np.hypot(*axis)) or 1e-6
    ux = axis / width
    uy = np.array([-ux[1], ux[0]])
    if uy[1] < 0:  # "down" in the image for both eyes (their corner axes point opposite ways)
        uy = -uy
    rel = pts[idx["iris"], :2] - (inner + outer) / 2
    openness = float(np.hypot(*(pts[idx["top"], :2] - pts[idx["bottom"], :2]))) / width
    return float(rel @ ux) / width, float(rel @ uy) / width, openness


def features(landmarks, blendshapes: dict | None, matrix, width: int, height: int) -> tuple[list[float], float] | None:
    """One frame -> (feature vector, eye openness). landmarks: 478 (x, y, z) normalized to the image."""
    pts = np.asarray(landmarks, dtype=float)
    if pts.shape[0] < 478:
        return None
    pts = pts * np.array([width, height, width])  # pixels, so x and y use the same units
    rh, rv, ro = _eye(pts, RIGHT_EYE)
    lh, lv, lo = _eye(pts, LEFT_EYE)
    # The two eyes' corner axes point opposite ways (outward); flip one so "h" means the same direction.
    h, v = (lh - rh) / 2, (lv + rv) / 2
    b = blendshapes or {}
    look_h = (b.get("eyeLookInLeft", 0) + b.get("eyeLookOutRight", 0) - b.get("eyeLookOutLeft", 0)
              - b.get("eyeLookInRight", 0)) / 2
    look_v = (b.get("eyeLookUpLeft", 0) + b.get("eyeLookUpRight", 0) - b.get("eyeLookDownLeft", 0)
              - b.get("eyeLookDownRight", 0)) / 2
    if matrix is not None:
        m = np.asarray(matrix, dtype=float).reshape(4, 4)
        fwd = m[:3, :3] @ np.array([0.0, 0.0, 1.0])
        yaw, pitch = math.atan2(fwd[0], fwd[2]), math.atan2(fwd[1], fwd[2])
        tx, ty, tz = (float(x) for x in m[:3, 3])
    else:  # rough head pose from the nose tip and the face's place in the image
        yaw, pitch = float(pts[1, 0] / width - 0.5), float(pts[1, 1] / height - 0.5)
        tx, ty, tz = yaw, pitch, float(np.hypot(*(pts[263, :2] - pts[33, :2]))) / width
    blink = max(b.get("eyeBlinkLeft", 0), b.get("eyeBlinkRight", 0))
    openness = min(lo, ro) * (1.0 - blink)
    return [h, v, look_h, look_v, yaw, pitch, tx, ty, tz], openness


def expand(f) -> np.ndarray:
    """Feature vector -> regression inputs (adds the curved terms gaze needs near the screen edges)."""
    f = np.atleast_2d(np.asarray(f, dtype=float))
    h, v, lh, lv = f[:, 0], f[:, 1], f[:, 2], f[:, 3]
    return np.column_stack([f, h * h, v * v, h * v, lh * lh, lv * lv, lh * lv])


def _ridge(x: np.ndarray, y: np.ndarray, lam: float) -> np.ndarray:
    a = x.T @ x + lam * np.diag([0.0] + [1.0] * (x.shape[1] - 1))  # don't shrink the intercept
    return np.linalg.solve(a, x.T @ y)


def fit(samples: list[tuple[list[float], tuple[float, float]]], screen: tuple[int, int]) -> dict:
    """samples: (feature vector, target screen point). Needs frames from at least 6 different dots."""
    targets = sorted({tuple(t) for _f, t in samples})
    if len(targets) < 6:
        raise ValueError(f"saw your eyes at only {len(targets)} of the dots")
    raw = expand([f for f, _t in samples])
    y = np.array([t for _f, t in samples], dtype=float)
    mean, std = raw.mean(axis=0), raw.std(axis=0)
    std[std < 1e-9] = 1.0
    x = np.column_stack([np.ones(len(raw)), (raw - mean) / std])
    groups = np.array([targets.index(tuple(t)) for _f, t in samples])

    def held_out_error(lam: float) -> float:
        errs = []
        for g in range(len(targets)):
            train, test = groups != g, groups == g
            coef = _ridge(x[train], y[train], lam)
            pred = np.median(x[test] @ coef, axis=0)  # the median frame per dot, like a short fixation
            errs.append(float(np.hypot(*(pred - y[test][0]))))
        return float(np.median(errs))

    lam, err = min(((lam, held_out_error(lam)) for lam in (0.1, 1.0, 10.0, 100.0)), key=lambda p: p[1])
    coef = _ridge(x, y, lam)
    return {"version": MODEL_VERSION, "screen": list(screen), "mean": mean.tolist(), "std": std.tolist(),
            "coef": coef.tolist(), "lambda": lam, "error_px": round(err), "dots": len(targets),
            "created": time.time()}


def predict(model: dict, f) -> tuple[float, float]:
    raw = expand(f)
    x = np.column_stack([np.ones(len(raw)), (raw - np.array(model["mean"])) / np.array(model["std"])])
    p = (x @ np.array(model["coef"]))[0]
    return float(p[0]), float(p[1])


def save(model: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(model), encoding="utf-8")
    tmp.replace(path)


def load(path: Path, screen: tuple[int, int] | None = None) -> dict | None:
    """The saved calibration, or None if missing, broken, old, or made for a different screen size."""
    try:
        model = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(model, dict) or model.get("version") != MODEL_VERSION:
        return None
    if screen is not None and list(model.get("screen") or []) != list(screen):
        return None
    return model


class OneEuro:
    """One Euro filter: steady when the eyes rest on something, quick when they jump."""

    def __init__(self, min_cutoff: float = 0.8, beta: float = 0.004, d_cutoff: float = 1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.x = self.dx = None
        self.t = None

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x: np.ndarray, t: float) -> np.ndarray:
        x = np.asarray(x, dtype=float)
        if self.x is None or self.t is None or t - self.t > 1.0:  # first frame, or after a gap
            self.x, self.dx, self.t = x, np.zeros_like(x), t
            return x
        dt = max(1e-3, t - self.t)
        dx = (x - self.x) / dt
        a_d = self._alpha(self.d_cutoff, dt)
        self.dx = a_d * dx + (1 - a_d) * self.dx
        cutoff = self.min_cutoff + self.beta * float(np.hypot(*self.dx[:2]))
        a = self._alpha(cutoff, dt)
        self.x, self.t = a * x + (1 - a) * self.x, t
        return self.x
