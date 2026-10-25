"""Scale-free numeric face features for offline sensing; never accepts or stores images."""
from __future__ import annotations

import statistics
import math

# MediaPipe Face Mesh eye contours and iris rings (478 point model).
EYES = ((33, 133, (160, 158, 144, 153), (161, 159, 145, 154), 468),
        (362, 263, (385, 387, 373, 380), (386, 388, 374, 381), 473))


def _point(lm, i):
    p = lm[i]
    return float(p[0]), float(p[1]), float(p[2]) if len(p) > 2 else 0.0


def _dist(a, b):
    return ((a[0]-b[0])**2 + (a[1]-b[1])**2) ** .5


def extract(landmarks, blendshapes=None, matrix=None, w=1, h=1):
    """Return per-eye and averaged geometry plus blendshape/head values from one frame."""
    bs = blendshapes or {}
    if isinstance(bs, list):
        bs = {str(x.get("category_name", "")): float(x.get("score", 0)) for x in bs}
    eyes = []
    for ci, cj, uppers, lowers, iris in EYES:
        a, b = _point(landmarks, ci), _point(landmarks, cj)
        width = max(_dist(a, b), 1e-8)
        top = statistics.mean(_point(landmarks, i)[1] for i in uppers)
        bottom = statistics.mean(_point(landmarks, i)[1] for i in lowers)
        center = (a[1] + b[1]) / 2
        ip = _point(landmarks, iris)
        eyes.append({"aperture": abs(bottom-top)/width, "lower_height": (bottom-center)/width,
                     "upper_height": (center-top)/width, "iris_vertical": (ip[1]-top)/max(bottom-top,1e-8),
                     "brow_distance": abs(_point(landmarks, ci-10)[1]-top)/width})
    out = {}
    for key in eyes[0]:
        out[key+"_l"], out[key+"_r"] = eyes[0][key], eyes[1][key]
        out[key] = statistics.mean((eyes[0][key], eyes[1][key]))
    for name in ("eyeSquintLeft", "eyeSquintRight", "eyeBlinkLeft", "eyeBlinkRight", "eyeWideLeft", "eyeWideRight",
                 "cheekSquintLeft", "cheekSquintRight", "browDownLeft", "browDownRight", "mouthSmileLeft",
                 "mouthSmileRight", "jawOpen"):
        out[name] = float(bs.get(name, 0.0))
    out.update(eyeSquint=statistics.mean((out["eyeSquintLeft"],out["eyeSquintRight"])),
               eyeBlink=statistics.mean((out["eyeBlinkLeft"],out["eyeBlinkRight"])),
               eyeWide=statistics.mean((out["eyeWideLeft"],out["eyeWideRight"])),
               cheekSquint=statistics.mean((out["cheekSquintLeft"],out["cheekSquintRight"])),
               browDown=statistics.mean((out["browDownLeft"],out["browDownRight"])),
               mouthSmile=statistics.mean((out["mouthSmileLeft"],out["mouthSmileRight"])))
    out["face_width"] = _dist(_point(landmarks, 234), _point(landmarks, 454))*float(w)
    # Iris ring diameter, average of opposing ring landmarks.
    out["iris_diameter"] = statistics.mean(_dist(_point(landmarks, 468),_point(landmarks, i))*float(w)
                                            for i in (469,471,474,476))
    if matrix is not None and len(matrix) == 16:
        out["head_distance"] = sum(float(matrix[i])**2 for i in (3,7,11))**.5
        m=[float(x) for x in matrix]
        out["head_yaw"] = math.degrees(math.atan2(m[2],m[10]))
        out["head_pitch"] = math.degrees(math.asin(max(-1.0,min(1.0,-m[6]))))
        out["head_roll"] = math.degrees(math.atan2(m[4],m[5]))
    else:
        out.update(head_distance=0.0, head_yaw=0.0, head_pitch=0.0, head_roll=0.0)
    return out


def robust_baseline(samples):
    """Feature-wise median, MAD and available range to the upper 1.0 bound."""
    if not samples: return {}
    keys=set.intersection(*(set(s) for s in samples))
    result={}
    for k in keys:
        vals=[float(s[k]) for s in samples if isinstance(s[k],(int,float))]
        if vals:
            med=statistics.median(vals); result[k]={"median":med,"mad":statistics.median(abs(v-med) for v in vals),
                                                       "room":max(0.0,1.0-med)}
    return result
