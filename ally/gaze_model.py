"""Webcam gaze math: face landmarks -> eye features -> screen point. Pure numpy, no camera.

Features combine iris position, eye-direction scores, and head pose. Calibration fits ridge
regression from those features to screen pixels and checks accuracy by holding out each dot.
"""
from __future__ import annotations
import json
import math
import time
from collections import deque
from pathlib import Path
import numpy as np

# MediaPipe Face Landmarker (478 points): corners, lids and iris centers of each eye.
RIGHT_EYE = {'outer': 33, 'inner': 133, 'top': 159, 'bottom': 145, 'iris': 468}
LEFT_EYE = {'outer': 263, 'inner': 362, 'top': 386, 'bottom': 374, 'iris': 473}
FEATURES = ['h', 'v', 'look_h', 'look_v', 'yaw', 'pitch', 'tx', 'ty', 'tz']
FEATURES_V2 = ['right_h', 'right_v', 'left_h', 'left_v', 'left_lid', 'right_lid', 'up_left', 'down_left', 'up_right', 'down_right', 'yaw', 'pitch', 'roll', 'tx', 'ty', 'tz']
FEATURES_V3 = ['gaze_x', 'gaze_y', 'eye_h', 'eye_v', 'yaw', 'pitch', 'roll', 'tx', 'ty', 'tz']
MODEL_VERSION = 1
DEFAULT_FEATURE_SET = 'v4'  # head geometry + learned eyes: survives head movement (see _fit_v4)

def ray_screen_point(origin, direction, screen_mm=(344.0, 193.0), screen_px=(1920, 1080),
                     plane_z=0.0):
    """Intersect a camera-coordinate gaze ray with the screen plane at the camera."""
    origin, direction = np.asarray(origin, float), np.asarray(direction, float)
    if abs(direction[2]) < 1e-9:
        raise ValueError('gaze ray is parallel to screen plane')
    scale = (plane_z - origin[2]) / direction[2]
    hit = origin + scale * direction
    width, height = (float(v) / 10 for v in screen_mm)
    px_w, px_h = screen_px
    return np.array([(hit[0] / width + .5) * px_w, hit[1] / height * px_h])

def _v3(pts, matrix, rh, rv, lh, lv, width, height, screen_mm):
    """Project eye-centred rays through the head pose onto the physical screen plane."""
    if matrix is None:
        return [0.5 + (lh - rh) / 2, 0.5 + (rv + lv) / 2,
                (rh + lh) / 2, (rv + lv) / 2, 0, 0, 0, 0, 0, 0]
    m = np.asarray(matrix, dtype=float).reshape(4, 4)
    rot, trans = m[:3, :3], m[:3, 3]
    rays = []
    for side, h, v in ((-1, rh, rv), (1, lh, lv)):
        eye = trans + rot @ np.array([side * 3.2, 0.0, 0.0])
        local = np.array([h * 2.5, v * 2.5, 1.0])
        rays.append(ray_screen_point(eye, rot @ local, screen_mm, (1, 1)))
    gaze = np.mean(rays, axis=0).tolist()
    fwd = rot @ np.array([0.0, 0.0, 1.0])
    yaw, pitch = math.atan2(fwd[0], fwd[2]), math.atan2(fwd[1], fwd[2])
    roll = math.atan2(rot[1, 0], rot[0, 0])
    return gaze + [(rh + lh) / 2, (rv + lv) / 2, yaw, pitch, roll, *trans.tolist()]

def _eye(pts: np.ndarray, idx: dict) -> tuple[float, float, float]:
    """Return iris offsets across/up the eye and its openness, all in eye widths."""
    inner, outer = (pts[idx['inner'], :2], pts[idx['outer'], :2])
    axis = outer - inner
    width = float(np.hypot(*axis)) or 1e-06
    ux = axis / width
    uy = np.array([-ux[1], ux[0]])
    if uy[1] < 0:
        uy = -uy  # Both eyes use image-down as the positive vertical direction.
    rel = pts[idx['iris'], :2] - (inner + outer) / 2
    return (float(rel @ ux) / width, float(rel @ uy) / width, float(np.hypot(*pts[idx['top'], :2] - pts[idx['bottom'], :2])) / width)

def _frame_parts(landmarks, blendshapes, matrix, width, height, screen_mm=(344.0, 193.0)):
    """Extract both feature versions, openness, and the six head-pose values."""
    pts = np.asarray(landmarks, dtype=float)
    if pts.shape[0] < 478:
        return None
    pts = pts * np.array([width, height, width])
    rh, rv, ro = _eye(pts, RIGHT_EYE)
    lh, lv, lo = _eye(pts, LEFT_EYE)
    b = blendshapes or {}
    if matrix is not None:
        m = np.asarray(matrix, dtype=float).reshape(4, 4)
        rot = m[:3, :3]
        fwd = rot @ np.array([0.0, 0.0, 1.0])
        yaw = math.atan2(fwd[0], fwd[2])
        pitch = math.atan2(fwd[1], fwd[2])
        roll = math.atan2(rot[1, 0], rot[0, 0])
        tx, ty, tz = map(float, m[:3, 3])
    else:
        yaw = float(pts[1, 0] / width - 0.5)
        pitch = float(pts[1, 1] / height - 0.5)
        roll = 0.0
        tx, ty, tz = (yaw, pitch, float(np.hypot(*pts[263, :2] - pts[33, :2])) / width)
    blink = max(b.get('eyeBlinkLeft', 0), b.get('eyeBlinkRight', 0))
    openness = min(lo, ro) * (1 - blink)
    v1 = [(lh - rh) / 2, (lv + rv) / 2, (b.get('eyeLookInLeft', 0) + b.get('eyeLookOutRight', 0) - b.get('eyeLookOutLeft', 0) - b.get('eyeLookInRight', 0)) / 2, (b.get('eyeLookUpLeft', 0) + b.get('eyeLookUpRight', 0) - b.get('eyeLookDownLeft', 0) - b.get('eyeLookDownRight', 0)) / 2, yaw, pitch, tx, ty, tz]
    v2 = [rh, rv, lh, lv, lo, ro, b.get('eyeLookUpLeft', 0), b.get('eyeLookDownLeft', 0), b.get('eyeLookUpRight', 0), b.get('eyeLookDownRight', 0), yaw, pitch, roll, tx, ty, tz]
    v3 = _v3(pts, matrix, rh, rv, lh, lv, width, height, screen_mm)
    return (v1, v2, v3, openness, (yaw, pitch, roll, tx, ty, tz))

def features(landmarks, blendshapes: dict | None, matrix, width: int, height: int) -> tuple[list[float], float] | None:
    """Return the v1 feature vector and eye openness for one normalized landmark frame."""
    p = _frame_parts(landmarks, blendshapes, matrix, width, height)
    return None if p is None else (p[0], p[3])

def features_for_model(model, landmarks, blendshapes, matrix, width, height):
    """Return the selected model's feature vector and eye openness, or None for bad landmarks."""
    p = _frame_parts(landmarks, blendshapes, matrix, width, height, model.get('screen_mm', (344.0, 193.0)))
    if p is None:
        return None
    fs = model.get('features', 'v1')
    return (p[2] if fs == 'v3' else p[1] if fs == 'v2' else p[0], p[3])

def expand(f, feature_set='v1'):
    """Expand raw features with the polynomial and interaction terms used by regression."""
    f = np.atleast_2d(np.asarray(f, dtype=float))
    if feature_set == 'v3':
        # The geometric intersection already includes eye direction and head pose;
        # only fit its two screen-plane coordinates to the person's calibration.
        return f[:, :2]
    if feature_set == 'v1':
        h, v, lh, lv = f[:, :4].T
        return np.column_stack([f, h * h, v * v, h * v, lh * lh, lv * lv, lh * lv])
    # V2 includes iris-by-head interactions to model pose-dependent gaze shifts.
    h1, v1, h2, v2 = f[:, :4].T
    head = f[:, 10:16]
    return np.column_stack([f, h1 * head[:, 0], v1 * head[:, 1], h2 * head[:, 0], v2 * head[:, 1], h1 * v1, h2 * v2])

def _ridge(x: np.ndarray, y: np.ndarray, lam: float, weights=None) -> np.ndarray:
    """Solve ridge regression without shrinking the intercept column."""
    penalty = np.diag([0.0] + [1.0] * (x.shape[1] - 1))
    if weights is not None:
        weighted = np.asarray(weights, dtype=float)[:, None]
        return np.linalg.solve(x.T @ (weighted * x) + lam * penalty, x.T @ (weighted * y))
    return np.linalg.solve(x.T @ x + lam * penalty, x.T @ y)

def fit(samples, screen, feature_set=DEFAULT_FEATURE_SET, openness_cutoff=None,
        sample_weights=None, screen_mm=None):
    """Fit a model from (feature vector, target point) samples across at least six dots.

    screen_mm: the screen's physical size in millimetres (v4 uses it; default this laptop's).
    """
    if feature_set not in ('v1', 'v2', 'v3', 'v4'):
        raise ValueError('feature_set must be v1, v2, v3 or v4')
    targets = sorted({tuple(t) for _f, t in samples})
    if len(targets) < 6:
        raise ValueError(f'saw your eyes at only {len(targets)} of the dots')
    if feature_set == 'v4':
        return _fit_v4(samples, screen, screen_mm, openness_cutoff, sample_weights)
    raw = expand([f for f, _ in samples], feature_set)
    y = np.array([t for _, t in samples], float)
    mean, std = (raw.mean(0), raw.std(0))
    std[std < 1e-09] = 1
    x = np.column_stack([np.ones(len(raw)), (raw - mean) / std])
    groups = np.array([targets.index(tuple(t)) for _, t in samples])
    weights = np.ones(len(samples)) if sample_weights is None else np.asarray(sample_weights, dtype=float)
    if weights.shape != (len(samples),) or np.any(weights <= 0):
        raise ValueError('sample_weights must contain one positive weight per sample')

    def err(lam):
        es = []
        for g in range(len(targets)):
            tr, te = (groups != g, groups == g)
            pred = np.median(x[te] @ _ridge(x[tr], y[tr], lam, weights[tr]), axis=0)
            es.append(float(np.hypot(*pred - y[te][0])))
        return float(np.median(es))
    lam, e = min(((l, err(l)) for l in (0.1, 1.0, 10.0, 100.0)), key=lambda z: z[1])
    coef = _ridge(x, y, lam, weights)
    pose_start = 4 if feature_set == 'v3' else 10 if feature_set == 'v2' else 4
    pose_size = 6 if feature_set in ('v2', 'v3') else 5
    poses = np.asarray([f[pose_start:pose_start + pose_size] for f, _ in samples], dtype=float)
    calibration_pose = np.median(poses, axis=0).tolist()
    return {'version': MODEL_VERSION, 'features': feature_set, 'screen': list(screen), 'screen_mm': [344.0, 193.0], 'mean': mean.tolist(), 'std': std.tolist(), 'coef': coef.tolist(), 'offset': [0.0, 0.0], 'lambda': lam, 'error_px': round(e), 'dots': len(targets), 'training_samples': [[list(f), list(t)] for f, t in samples], 'calibration_pose': calibration_pose, 'openness_cutoff': openness_cutoff, 'created': time.time()}

def retune(model, samples):
    """Return a copy translated by the median target-minus-prediction residual."""
    residuals = [np.asarray(target, float) - np.asarray(predict(model, f, clamp=False), float) for f, target in samples]
    if len(residuals) < 3:
        raise ValueError('retuning needs at least three targets')
    tuned = dict(model)
    # predict() already includes the current offset, so the residual is what is still left over.
    tuned['offset'] = (_offset(model) + np.median(np.asarray(residuals), axis=0)).tolist()
    return tuned


def _offset(model) -> np.ndarray:
    return np.asarray(model.get('offset', [0.0, 0.0]), dtype=float)


# ---------------------------------------------------------------- v4: head geometry, learned eyes
# v4 uses the v1 feature vector (h, v, look_h, look_v, yaw, pitch, tx, ty, tz). The head is taken as
# geometry, not learned: the gaze line starts at the head (tx, ty, tz in cm from the camera, which
# sits at the top centre of the screen) and its angle is the head's angle plus the eye's angle. Only
# the eye angle is fitted, so moving the head after calibration doesn't break the estimate (on four
# real recordings: 770-1000 px after the user moved with v1, 155-260 px with v4, no re-tune).
# Axis directions (MediaPipe's camera frame vs screen pixels), chosen once from the recordings:
_V4_SIGNS = (1.0, 1.0, -1.0, -1.0, -1.0, -1.0)  # screen x, screen y, head yaw, head pitch, tx, ty
_V4_MIN_DISTANCE_CM = 10.0  # closer than this the pose is the rough no-matrix guess: use the fallback
DEFAULT_SCREEN_MM = (344.0, 193.0)


def _v4_eye_terms(f) -> np.ndarray:
    h, v, lh, lv = (float(x) for x in f[:4])
    return np.array([h, v, lh, lv, h * h, v * v, h * v, 1.0])


def _px_per_cm(screen, screen_mm) -> tuple[float, float]:
    return screen[0] / (screen_mm[0] / 10.0), screen[1] / (screen_mm[1] / 10.0)


def _v4_needed(f, target, screen, screen_mm):
    """The eye angles (yaw, pitch) that would put this head's gaze on target, or None."""
    sx, sy, shy, shp, stx, sty = _V4_SIGNS
    yaw, pitch, tx, ty, tz = (float(x) for x in f[4:9])
    if abs(tz) < _V4_MIN_DISTANCE_CM:
        return None
    kx, ky = _px_per_cm(screen, screen_mm)
    xt = sx * (target[0] - screen[0] / 2) / kx
    yt = sy * target[1] / ky
    return (math.atan2(xt - stx * tx, abs(tz)) - shy * yaw, math.atan2(yt - sty * ty, abs(tz)) - shp * pitch)


def _v4_point(model, f):
    """Raw screen point for one feature vector, or None when the head pose isn't usable."""
    sx, sy, shy, shp, stx, sty = _V4_SIGNS
    yaw, pitch, tx, ty, tz = (float(x) for x in f[4:9])
    if abs(tz) < _V4_MIN_DISTANCE_CM:
        return None
    eye_yaw, eye_pitch = _v4_eye_terms(f) @ np.asarray(model['coef'], dtype=float)
    a, b = shy * yaw + eye_yaw, shp * pitch + eye_pitch
    if abs(a) >= 1.4 or abs(b) >= 1.4:  # nearly parallel to the screen: no sensible point
        return None
    screen, screen_mm = model['screen'], model.get('screen_mm') or DEFAULT_SCREEN_MM
    kx, ky = _px_per_cm(screen, screen_mm)
    d = abs(tz)
    return (screen[0] / 2 + sx * (stx * tx + d * math.tan(a)) * kx, sy * (sty * ty + d * math.tan(b)) * ky)


def _v4_coef(rows, screen, screen_mm, lam=1e-3):
    xs, ys = [], []
    for f, t in rows:
        need = _v4_needed(f, t, screen, screen_mm)
        if need is not None:
            xs.append(_v4_eye_terms(f))
            ys.append(need)
    if len(xs) < 8:
        return None
    x, y = np.asarray(xs), np.asarray(ys)
    return np.linalg.solve(x.T @ x + lam * np.eye(x.shape[1]), x.T @ y)


def _fit_v4(samples, screen, screen_mm, openness_cutoff, sample_weights):
    screen_mm = tuple(float(v) for v in (screen_mm or DEFAULT_SCREEN_MM))
    fallback = fit(samples, screen, 'v1', openness_cutoff, sample_weights)  # for frames without a pose
    coef = _v4_coef(samples, screen, screen_mm)
    if coef is None:  # no usable head pose (no transformation matrix): plain v1 still works
        return fallback
    targets = sorted({tuple(t) for _f, t in samples})
    errs = []
    for g in targets:  # leave one target out, like the other feature sets
        part = _v4_coef([s for s in samples if tuple(s[1]) != g], screen, screen_mm)
        if part is None:
            continue
        probe = {'coef': part.tolist(), 'screen': list(screen), 'screen_mm': list(screen_mm)}
        pts = [p for p in (_v4_point(probe, f) for f, t in samples if tuple(t) == g) if p is not None]
        if pts:
            errs.append(float(np.hypot(*(np.median(np.asarray(pts), axis=0) - np.asarray(g, float)))))
    model = dict(fallback)
    model.update({'features': 'v4', 'coef': coef.tolist(), 'screen_mm': list(screen_mm),
                  'error_px': round(float(np.median(errs))) if errs else fallback['error_px'],
                  'fallback': fallback, 'mean': None, 'std': None})
    return model

def predict(model: dict, f, clamp: bool = True) -> tuple[float, float]:
    """Predict a screen point, clamped to a 10% margin beyond its edges.

    clamp=False gives the raw estimate: retune and Corrector measure drift from it, because after a
    head move the estimate can sit far off screen and clamping would hide most of the shift.
    """
    if model.get('features') == 'v4':
        point = _v4_point(model, f)
        if point is None:  # no usable head pose in this frame: the v1 model fitted alongside
            point = predict(model['fallback'], f, clamp=False)
        p = np.asarray(point, dtype=float)
    else:
        raw = expand(f, model.get('features', 'v1'))
        x = np.column_stack([np.ones(len(raw)), (raw - np.array(model['mean'])) / np.array(model['std'])])
        p = (x @ np.array(model['coef']))[0]
    p = p + np.asarray(model.get('offset', [0.0, 0.0]), dtype=float)
    if not clamp:
        return (float(p[0]), float(p[1]))
    width, height = model['screen']
    p[0] = np.clip(p[0], -0.1 * width, 1.1 * width)
    p[1] = np.clip(p[1], -0.1 * height, 1.1 * height)
    return (float(p[0]), float(p[1]))

def clean_samples(samples, min_openness=0.12, jump_threshold=0.08, preserve_openness=False):
    """Drop blinks and fast gaze jumps; optionally retain openness in returned triples."""
    out = []
    prev = None
    for item in samples:
        f, t = item[:2]
        op = item[2] if len(item) > 2 else 1.0
        a = np.asarray(f, float)
        jump = prev is not None and np.linalg.norm(a[:2] - prev[:2]) > jump_threshold
        if op >= min_openness and (not jump):
            out.append((f, t, op) if preserve_openness and len(item) > 2 else (f, t))
        prev = a
    return out

def openness_cutoff(samples, fraction=0.5):
    """Estimate a blink cutoff from half the median of target-level openness medians."""
    by_target = {}
    for item in samples:
        if len(item) < 3:
            continue
        _features, target, openness = item[:3]
        by_target.setdefault(tuple(target), []).append(float(openness))
    medians = [float(np.median(values)) for values in by_target.values() if values]
    return float(np.median(medians) * fraction) if medians else 0.12

def is_blink(model, openness):
    """Return whether openness is below this person's calibrated blink cutoff."""
    return float(openness) < float(model.get('openness_cutoff') or 0.12)

def target_head_moved(frames, calibration_pose, feature_set=DEFAULT_FEATURE_SET,
                     threshold_degrees=3.0):
    """Flag a target if yaw/pitch shifts from session pose or varies during the target."""
    pose_start = 4 if feature_set == 'v3' else 10 if feature_set == 'v2' else 4
    poses = np.asarray([np.asarray(frame, dtype=float)[pose_start:pose_start + 2]
                        for frame in frames], dtype=float)
    typical = np.asarray(calibration_pose, dtype=float)[:2]
    if poses.size == 0 or typical.shape != (2,):
        return False
    threshold = math.radians(threshold_degrees)
    target_pose = np.median(poses, axis=0)
    if np.any(np.abs(target_pose - typical) > threshold):
        return True
    spread = np.percentile(poses, 90, axis=0) - np.percentile(poses, 10, axis=0)
    return bool(np.any(spread > threshold))

def validate(model, samples):
    """Return median held-out pixel error, 3x3 region hits, and per-target predictions."""
    grouped = {}
    for f, t in samples:
        grouped.setdefault(tuple(t), []).append(predict(model, f))
    errs = []
    hits = 0
    details = []
    for t, ps in grouped.items():
        p = np.median(np.asarray(ps), axis=0)
        e = float(np.hypot(*p - np.asarray(t)))
        ok = region(*p, model['screen']) == region(*t, model['screen'])
        errs.append(e)
        hits += ok
        details.append({'target': list(t), 'prediction': p.tolist(), 'error_px': e, 'region_hit': bool(ok)})
    return {'error_px': float(np.median(errs)) if errs else None, 'region_hits': hits, 'region_total': len(grouped), 'per_target': details}

def region(x, y, screen):
    """Map a screen coordinate to one of the nine named screen regions."""
    w, h = screen
    col = min(2, max(0, int(3 * x / w)))
    row = min(2, max(0, int(3 * y / h)))
    return [['top left', 'top', 'top right'], ['left', 'middle', 'right'], ['bottom left', 'bottom', 'bottom right']][row][col]

def fixation(points, t0, t1, radius_px):
    """Return the longest fixation in a time range as a point, span, count, and dispersion."""
    pts = sorted(((float(t), float(x), float(y)) for t, x, y in points if t0 <= t <= t1))
    best = None
    start = 0
    min_x, min_y, max_x, max_y = (deque(), deque(), deque(), deque())
    for end, point in enumerate(pts):
        for queue, value, sign, axis in ((min_x, point[1], 1, 1), (max_x, point[1], -1, 1), (min_y, point[2], 1, 2), (max_y, point[2], -1, 2)):
            while queue and sign * pts[queue[-1]][axis] >= sign * value:
                queue.pop()
            queue.append(end)
        while start < end:
            dx = pts[max_x[0]][1] - pts[min_x[0]][1]
            dy = pts[max_y[0]][2] - pts[min_y[0]][2]
            if dx + dy <= radius_px:
                break
            if min_x[0] == start:
                min_x.popleft()
            if max_x[0] == start:
                max_x.popleft()
            if min_y[0] == start:
                min_y.popleft()
            if max_y[0] == start:
                max_y.popleft()
            start += 1
        duration = point[0] - pts[start][0]
        if duration >= 0.2 and (best is None or duration > best['duration']):
            best = {'start_index': start, 'end_index': end, 'duration': duration}
    if best is None:
        return None
    window = np.asarray([(p[1], p[2]) for p in pts[best['start_index']:best['end_index'] + 1]])
    center = window.mean(axis=0)
    spread = float(np.max(np.linalg.norm(window - center, axis=1)))
    return {'x': float(np.median(window[:, 0])), 'y': float(np.median(window[:, 1])), 'start': pts[best['start_index']][0], 'end': pts[best['end_index']][0], 'samples': len(window), 'spread_px': spread}

class Corrector:
    """Correct consistent head-movement drift from recent mouse clicks."""

    def __init__(self, model):
        self.base = model
        self.model = model
        self.samples = []
        self.errors = deque(maxlen=6)

    def add(self, features, target, t):
        """Store a click and update the translation after four-of-six consensus."""
        base_prediction = np.asarray(predict(self.base, features, clamp=False))
        error = np.asarray(target, dtype=float) - base_prediction
        sample = (features, tuple(target), float(t), error)
        self.samples.append(sample)
        self.samples = self.samples[-50:]
        self.errors.append(sample)
        if len(self.errors) == 6:
            vectors = np.asarray([item[3] for item in self.errors])
            median = np.median(vectors, axis=0)
            inliers = np.linalg.norm(vectors - median, axis=1) <= 150
            if int(inliers.sum()) >= 4:
                consensus = np.median(vectors[inliers], axis=0)
                before = np.median(np.linalg.norm(vectors[inliers], axis=1))
                after = np.median(np.linalg.norm(vectors[inliers] - consensus, axis=1))
                if after < before:
                    self.model = dict(self.base)
                    # Errors are measured against the base, which may already carry a re-tune offset.
                    self.model['offset'] = (_offset(self.base) + consensus).tolist()
        return True

    def predict(self, features):
        """Return the current corrected screen prediction."""
        return predict(self.model, features)

    def info(self):
        """Return the accepted click count and current estimated error."""
        return {'samples_used': len(self.samples), 'error_px': self.model.get('error_px')}

def radius_px(model, features=None):
    """Return validated error expanded by head-pose movement from calibration."""
    base = float(model.get('error_px') or 200)
    if features is None:
        return base
    f = np.asarray(features, float)
    fs = model.get('features')
    ix = 4 if fs == 'v3' else 10 if fs == 'v2' else 4
    calibration = np.asarray(model.get('calibration_pose', []), dtype=float)
    pose_size = 6 if fs in ('v2', 'v3') else 5
    if len(f) < ix + pose_size or calibration.shape != (pose_size,):
        return base
    # v1's pose is yaw, pitch, tx, ty, tz (no roll); v2's is yaw, pitch, roll, tx, ty, tz.
    angles = 3 if fs in ('v2', 'v3') else 2
    rotation_delta = float(np.linalg.norm(f[ix:ix + angles] - calibration[:angles]))
    rotation_delta = math.degrees(rotation_delta)
    distance = max(abs(float(calibration[-1])), 1e-06)
    translation_delta = float(np.linalg.norm(f[ix + angles:ix + pose_size] - calibration[angles:])) / distance
    movement = max(rotation_delta / 10.0, translation_delta / 0.15)
    if fs == 'v4':  # head geometry is modelled: moving costs much less accuracy (about 1.5x on recordings)
        movement *= 0.25
    return base * (1 + min(2.0, movement))

def save(model, path):
    """Write a model as JSON, replacing the destination atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(model), encoding='utf-8')
    tmp.replace(path)

def load(path, screen=None):
    """Return a compatible saved calibration, or None when it cannot be used."""
    try:
        model = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(model, dict) or model.get('version') != MODEL_VERSION:
        return None
    if screen is not None and list(model.get('screen') or []) != list(screen):
        return None
    return model

class OneEuro:
    """One Euro filter: steady at rest and responsive to rapid gaze changes."""

    def __init__(self, min_cutoff=0.8, beta=0.004, d_cutoff=1.0):
        self.min_cutoff, self.beta, self.d_cutoff = (min_cutoff, beta, d_cutoff)
        self.x = self.dx = self.t = None

    @staticmethod
    def _alpha(cutoff, dt):
        return 1 / (1 + 1 / (2 * math.pi * cutoff) / dt)

    def __call__(self, x, t):
        x = np.asarray(x, float)
        if self.x is None or self.t is None or t - self.t > 1:
            self.x, self.dx, self.t = (x, np.zeros_like(x), t)
            return x
        dt = max(0.001, t - self.t)
        dx = (x - self.x) / dt
        a = self._alpha(self.d_cutoff, dt)
        self.dx = a * dx + (1 - a) * self.dx
        a = self._alpha(self.min_cutoff + self.beta * float(np.hypot(*self.dx[:2])), dt)
        self.x, self.t = (a * x + (1 - a) * self.x, t)
        return self.x
