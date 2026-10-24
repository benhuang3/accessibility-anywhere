"""One webcam frame -> a few face numbers (squint, smile, blink, head turn, face size, light). Pure numpy.

Only these numbers ever leave the camera process; frames are dropped after each pass. MediaPipe's
"Left"/"Right" blendshapes are the subject's own left and right, so a mirrored preview swaps them.
"""

from __future__ import annotations

import math

import numpy as np

# Face Landmarker points at the widest part of the face (left and right cheek, by the ears).
CHEEK_R, CHEEK_L = 234, 454
FIELDS = ("squint_l", "squint_r", "smile", "blink", "blink_l", "blink_r", "yaw", "pitch", "width", "light")


def head_angles(matrix) -> tuple[float, float]:
    """Yaw (turn) and pitch (nod) in degrees from MediaPipe's face transformation matrix."""
    m = np.asarray(matrix, dtype=float).reshape(4, 4)
    fwd = m[:3, :3] @ np.array([0.0, 0.0, 1.0])
    return math.degrees(math.atan2(fwd[0], fwd[2])), math.degrees(math.atan2(fwd[1], fwd[2]))


def face_box(pts: np.ndarray, width: int, height: int) -> tuple[int, int, int, int]:
    """Pixel box (x0, y0, x1, y1) around the landmarks, clipped to the image."""
    x0, y0 = pts[:, 0].min() * width, pts[:, 1].min() * height
    x1, y1 = pts[:, 0].max() * width, pts[:, 1].max() * height
    return (max(0, int(x0)), max(0, int(y0)), min(width, int(x1)), min(height, int(y1)))


def signals(landmarks, blendshapes: dict | None, matrix, width: int, height: int,
            light: float | None = None) -> dict | None:
    """landmarks: 478 (x, y, z) normalized to the image. light: mean face brightness 0-255, if known."""
    pts = np.asarray(landmarks, dtype=float)
    if pts.ndim != 2 or pts.shape[0] < 468:
        return None
    b = blendshapes or {}
    if matrix is not None:
        yaw, pitch = head_angles(matrix)
    else:  # no matrix: guess the turn from where the nose sits between the cheeks
        span = (pts[CHEEK_L, 0] - pts[CHEEK_R, 0]) or 1e-6
        yaw, pitch = 90.0 * ((pts[1, 0] - pts[CHEEK_R, 0]) / span - 0.5), 0.0
    # Face width as a share of the image width. It grows as 1/distance, so leaning in raises it.
    face_w = float(math.hypot((pts[CHEEK_L, 0] - pts[CHEEK_R, 0]) * width,
                              (pts[CHEEK_L, 1] - pts[CHEEK_R, 1]) * height)) / max(1, width)
    out = {
        "squint_l": float(b.get("eyeSquintLeft", 0.0)), "squint_r": float(b.get("eyeSquintRight", 0.0)),
        "smile": (float(b.get("mouthSmileLeft", 0.0)) + float(b.get("mouthSmileRight", 0.0))) / 2,
        "blink": (float(b.get("eyeBlinkLeft", 0.0)) + float(b.get("eyeBlinkRight", 0.0))) / 2,
        # Each eye on its own, recorded for tuning. On a real face these barely separate a wink from a
        # squint (one-eye close: 0.29 vs 0.38), so no gate uses them yet.
        "blink_l": float(b.get("eyeBlinkLeft", 0.0)), "blink_r": float(b.get("eyeBlinkRight", 0.0)),
        "yaw": float(yaw), "pitch": float(pitch), "width": face_w,
        "light": float(light) if light is not None else -1.0,
    }
    return {k: round(v, 4) for k, v in out.items()}


def brightness(gray: np.ndarray, box: tuple[int, int, int, int]) -> float:
    """Mean brightness (0-255) inside the face box of a grayscale frame."""
    x0, y0, x1, y1 = box
    if x1 - x0 < 4 or y1 - y0 < 4:
        return -1.0
    return float(gray[y0:y1:2, x0:x1:2].mean())
