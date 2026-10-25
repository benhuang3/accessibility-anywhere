"""Scale-aware numeric face features for offline sensing."""
from __future__ import annotations

import math
import statistics

# Face Mesh contour points, ordered by the subject's right and left eyes.
EYES = (
    (33, 133, (160, 159, 158), (144, 145, 153), (469, 470, 471, 472), 468,
     (46, 53, 52, 65, 55)),
    (263, 362, (387, 386, 385), (373, 374, 380), (474, 475, 476, 477), 473,
     (276, 283, 282, 295, 285)),
)


def _point(landmarks, index, width, height):
    """Return a landmark in pixel coordinates."""
    point = landmarks[index]
    z = float(point[2]) if len(point) > 2 else 0.0
    return float(point[0]) * width, float(point[1]) * height, z


def _distance(first, second):
    """Return two-dimensional pixel distance."""
    return math.hypot(first[0] - second[0], first[1] - second[1])


def _blendshapes(blendshapes):
    if isinstance(blendshapes, list):
        return {
            str(item.get("category_name", "")): float(item.get("score", 0.0))
            for item in blendshapes
        }
    return blendshapes or {}


def _eye_features(landmarks, eye, width, height):
    outer, inner, uppers, lowers, iris_ring, iris_center, brows = eye
    first_corner = _point(landmarks, outer, width, height)
    second_corner = _point(landmarks, inner, width, height)
    canthal_width = max(_distance(first_corner, second_corner), 1e-8)
    top = statistics.mean(_point(landmarks, index, width, height)[1] for index in uppers)
    bottom = statistics.mean(_point(landmarks, index, width, height)[1] for index in lowers)
    center = (first_corner[1] + second_corner[1]) / 2
    iris = _point(landmarks, iris_center, width, height)
    iris_diameter = statistics.mean(
        _distance(iris, _point(landmarks, index, width, height)) * 2
        for index in iris_ring
    )
    iris_diameter = max(iris_diameter, 1e-8)
    brow_y = statistics.mean(_point(landmarks, index, width, height)[1] for index in brows)
    lid_aperture = abs(bottom - top)
    return {
        "aperture": lid_aperture / iris_diameter,
        "lower_raise": (bottom - center) / iris_diameter,
        "upper_drop": (center - top) / iris_diameter,
        "iris_vertical": (iris[1] - top) / max(lid_aperture, 1e-8),
        "brow_distance": abs(brow_y - top) / iris_diameter,
        "canthal_width": canthal_width,
        "iris_diameter": iris_diameter,
    }


def _head_angles(matrix):
    """Match ally.senses.face.head_angles for a row-major 4x4 matrix."""
    values = [float(value) for value in matrix]
    forward_x, forward_y, forward_z = values[2], values[6], values[10]
    yaw = math.degrees(math.atan2(forward_x, forward_z))
    pitch = math.degrees(math.atan2(forward_y, forward_z))
    roll = math.degrees(math.atan2(values[4], values[5]))
    return yaw, pitch, roll


def extract(landmarks, blendshapes=None, matrix=None, w=1, h=1):
    """Return pixel-based per-eye geometry, blendshapes and head values."""
    width = float(w)
    height = float(h)
    bs = _blendshapes(blendshapes)
    eyes = [_eye_features(landmarks, eye, width, height) for eye in EYES]
    output = {}
    for key in eyes[0]:
        output[f"{key}_r"] = eyes[0][key]
        output[f"{key}_l"] = eyes[1][key]
        output[key] = statistics.mean((eyes[0][key], eyes[1][key]))

    blendshape_names = (
        "eyeSquintLeft", "eyeSquintRight", "eyeBlinkLeft", "eyeBlinkRight",
        "eyeWideLeft", "eyeWideRight", "cheekSquintLeft", "cheekSquintRight",
        "browDownLeft", "browDownRight", "eyeLookDownLeft", "eyeLookDownRight",
        "mouthSmileLeft", "mouthSmileRight", "jawOpen",
    )
    for name in blendshape_names:
        output[name] = float(bs.get(name, 0.0))

    averaged_shapes = {
        "eyeSquint": ("eyeSquintLeft", "eyeSquintRight"),
        "eyeBlink": ("eyeBlinkLeft", "eyeBlinkRight"),
        "eyeWide": ("eyeWideLeft", "eyeWideRight"),
        "cheekSquint": ("cheekSquintLeft", "cheekSquintRight"),
        "browDown": ("browDownLeft", "browDownRight"),
        "eyeLookDown": ("eyeLookDownLeft", "eyeLookDownRight"),
        "mouthSmile": ("mouthSmileLeft", "mouthSmileRight"),
    }
    for name, sides in averaged_shapes.items():
        output[name] = statistics.mean(output[side] for side in sides)

    cheek_right = _point(landmarks, 234, width, height)
    cheek_left = _point(landmarks, 454, width, height)
    output["face_width"] = _distance(cheek_right, cheek_left)
    output["iris_diameter"] = statistics.mean(
        (output["iris_diameter_r"], output["iris_diameter_l"])
    )
    if matrix is not None and len(matrix) == 16:
        output["head_distance"] = math.sqrt(
            sum(float(matrix[index]) ** 2 for index in (3, 7, 11))
        )
        output["head_yaw"], output["head_pitch"], output["head_roll"] = _head_angles(matrix)
    else:
        output.update(head_distance=0.0, head_yaw=0.0, head_pitch=0.0, head_roll=0.0)
    return output


def robust_baseline(samples):
    """Return each feature's median, MAD and remaining room to one."""
    if not samples:
        return {}
    keys = set.intersection(*(set(sample) for sample in samples))
    result = {}
    for key in keys:
        values = [float(sample[key]) for sample in samples
                  if isinstance(sample[key], (int, float))]
        if not values:
            continue
        median = statistics.median(values)
        mad = statistics.median(abs(value - median) for value in values)
        result[key] = {"median": median, "mad": mad, "room": max(0.0, 1.0 - median)}
    return result
