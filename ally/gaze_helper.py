"""Separate process: webcam eye tracking. Prints one JSON object per line:

    {"ready": true, "calibrated": false}         camera and face model work
    {"t": 1730000000.1, "x": 812, "y": 403, "r": 140}  where the user looks, and how unsure that frame is (pixels)
    {"t": ..., "face": true} / {"face": false}   face seen but not calibrated / no face (a few per second)
    {"calibrated": true, "error_px": 140, "dots": 13, "validation": {"error_px": 120, "region_hits": 5, "region_total": 5}}
                                           calibration done ({"calibrated": false, "why": "..."} if not)
    {"t": ..., "f": {"squint_l": 0.21, ...}}     face numbers for struggle sensing (with "signals": true)
    {"camera_off": true}                         the camera-on window was clicked; the camera then closes
    {"error": "..."}                             a problem; the process then exits

Settings "gaze" (default true) turns gaze points on; "signals" sends face numbers (ally.senses.face) at
"signals_fps". Frames never leave this process and are never written to disk.

Commands on stdin: "calibrate" or "calibrate 1" / "calibrate 3" (head positions); "check" runs only the 5 check dots; "stop" cancels a calibration in
progress; "click <t> <x> <y>" notes a mouse click so the estimate can learn (the click is not saved
and is not sent anywhere); "choose {json}" shows 2 to 4 large cards and "choose off" closes them;
"hud on" / "hud off" show a live senses window, and "hud [[text, level], ...]" sets its rows;
"card {json}" shows a readable card (text, optional title, size normal|large|tiny, seconds) and
"card off" closes it. The card also closes itself after seconds. "dot on" / "dot off" show or hide
the click-through gaze circle. "view LEVEL LEFT TOP" is the magnified view, so the circle can sit
on the glass point while zoomed in. Unknown or broken lines are ignored.
It exits when the agent closes its stdin (like the hotkey helper),
so a camera or MediaPipe crash can never take Ally down. The agent never opens the camera.
ally.gaze_record opens it only while Ally is stopped, and it writes numbers, not pictures.
Usage: python -m ally.gaze_helper '{"model": "...", "calibration": "...", "camera": 0, "fps": 20}'
"""

from __future__ import annotations

import json
import math
import os
import queue
import random
import statistics
import sys
import threading
import time
from pathlib import Path

WINDOW = "Ally eye tracker setup"
HUD = "Ally senses (only on this screen; nothing is saved)"
HUD_COLORS = {"ok": (255, 255, 255), "warn": (0, 215, 255), "hot": (40, 40, 255)}  # BGR: white, yellow, red
HUD_SCALE = 0.9
HUD_THICK = 2
HUD_STEP = 36
HUD_FOOTER = "Nothing is saved."
# 3x3 grid, inset from the edges, then the four midpoints between the center and the corners.
GRID_X = (0.08, 0.50, 0.92)
GRID_Y = (0.10, 0.50, 0.90)
READY_SECONDS = 3.0
STEADY_SECONDS = 1.0  # face width and head pose must hold still this long before the first dot
CAL_SETTLE, CAL_COLLECT = 0.8, 1.5
VAL_SETTLE, VAL_COLLECT = 0.5, 1.5  # 1.5 s kept per dot, so a 10 fps camera still beats the 10-frame minimum
VAL_COUNT = 5
MIN_GOOD = 10  # fewer good frames than this, and that dot is shown once more
HEAD_MOVE_DEG = 3.0  # yaw or pitch change that means the head turned instead of the eyes
WIDTH_STEADY = 0.05  # relative face-width change; 40 cm to 32 cm is much larger than this
EYES_ONLY = "Move only your eyes, keep your head still."
HEAD_RETRY_LINE = "Keep your head still, look with your eyes only."
SIT_BACK = "Sit back a little"
LEAN_IN = "Lean in a little"
POSE_NORMAL, POSE_BACK, POSE_CLOSE = "normal", "back", "close"
MINUTE = "About a minute."
MINUTE_HALF = "About a minute and a half."
RESULT_SECONDS = 4.0
MIN_OPEN = 0.12  # eye openness below this is a blink: skip the frame
JUMP = 0.08  # change in iris position (eye widths) that means the eye jumped: skip that frame
# Distance from cheek-to-cheek width (share of the frame). Light matches the senses light floor.
FACE_MIN, FACE_MAX = 0.18, 0.46
LIGHT_MIN = 40.0
HEAD_MAX = 20.0  # degrees of turn or nod; the same cutoff senses uses before it stops trusting the face
CENTER_X = (0.25, 0.75)
CENTER_Y = (0.20, 0.80)
GLARE_LIGHT = 235.0  # eye-region brightness this high, held, is treated as glasses glare
ERROR_BAR = 150  # px; the held-out bar on a 1920x1080 screen
CLOSER = "move a little closer"
FARTHER = "move a little farther back"
NEED_LIGHT = "turn on a light in front of you"
NO_FACE = "I can't see your face. Look at the screen."
TWO_FACES = "I see two faces. Only one person, please."
CENTER_HEAD = "Center your head and look at the screen."
REGION_NAMES = (
    ("top left", "top", "top right"),
    ("left", "middle", "right"),
    ("bottom left", "bottom", "bottom right"),
)
NUM_FACES = 2
SECOND_FACE = 0.70  # a second face this wide, compared with the largest, means two people
CHEEK_R, CHEEK_L = 234, 454  # same cheek points ally.senses.face uses for width


def _out():
    return sys.stdout if sys.stdout is not None else os.fdopen(1, "w", buffering=1)


OUT = _out()


def emit(obj: dict) -> None:
    try:
        OUT.write(json.dumps(obj) + "\n")
        OUT.flush()
    except (OSError, ValueError):
        os._exit(0)  # the agent is gone


def screen_size() -> tuple[int, int]:
    """Primary screen in physical pixels (the Windows magnifier uses the same units)."""
    if sys.platform == "win32":
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
        u = ctypes.windll.user32
        return int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))
    import tkinter

    root = tkinter.Tk()
    root.withdraw()
    size = int(root.winfo_screenwidth()), int(root.winfo_screenheight())
    root.destroy()
    return size


def open_camera(index: int):
    import cv2  # type: ignore

    backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF] if sys.platform == "win32" else [cv2.CAP_ANY]
    for backend in backends:
        cap = cv2.VideoCapture(index, backend)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            for _ in range(10):
                ok, _frame = cap.read()
                if ok:
                    return cap
                time.sleep(0.05)
        cap.release()
    return None


def _mid(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    return (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0


def _third(pos: float) -> int:
    if pos < 1.0 / 3.0:
        return 0
    if pos < 2.0 / 3.0:
        return 1
    return 2


def grid_points() -> list[tuple[float, float]]:
    """The 3x3 dots, one in each area of the screen."""
    return [(x, y) for y in GRID_Y for x in GRID_X]


def calibration_points() -> list[tuple[float, float]]:
    """13 dots: the 3x3 grid, center first, then the four dots between the center and the corners."""
    grid = grid_points()
    center = (GRID_X[1], GRID_Y[1])
    corners = (
        (GRID_X[0], GRID_Y[0]), (GRID_X[2], GRID_Y[0]),
        (GRID_X[0], GRID_Y[2]), (GRID_X[2], GRID_Y[2]),
    )
    return [center] + [p for p in grid if p != center] + [_mid(c, center) for c in corners]


def cell_index(nx: float, ny: float) -> int:
    """Which of the 9 equal areas a normalized point sits in (row-major)."""
    return _third(ny) * 3 + _third(nx)


def region_of(x: float, y: float, screen: tuple[int, int]) -> str:
    """3x3 area name for a screen pixel. Same words the model will use later."""
    w = max(int(screen[0]), 1)
    h = max(int(screen[1]), 1)
    return REGION_NAMES[_third(float(y) / h)][_third(float(x) / w)]


def _point_in_cell(rng: random.Random, row: int, col: int, avoid: list[tuple[float, float]]) -> tuple[float, float] | None:
    for _ in range(40):
        nx = (col + rng.uniform(0.18, 0.82)) / 3.0
        ny = (row + rng.uniform(0.18, 0.82)) / 3.0
        if all(math.hypot(nx - ax, ny - ay) >= 0.03 for ax, ay in avoid):
            return (nx, ny)
    return None


def validation_points(rng: random.Random | None = None, n: int = VAL_COUNT,
                      avoid: list[tuple[float, float]] | None = None) -> list[tuple[float, float]]:
    """`n` dots in different grid cells, not on a calibration dot. Not used for fitting."""
    rng = rng or random.Random()
    blocked = list(calibration_points() if avoid is None else avoid)
    cells = list(range(9))
    rng.shuffle(cells)
    picked: list[tuple[float, float]] = []
    for cell in cells:
        if len(picked) >= n:
            break
        row, col = divmod(cell, 3)
        point = _point_in_cell(rng, row, col, blocked + picked)
        if point is not None:
            picked.append(point)
    return picked


def _pose_summary(poses: list[tuple[float, float]]) -> tuple[float, tuple[float, float]] | None:
    """Range of yaw/pitch during a dot, and the median pose. None when the head was never seen."""
    if not poses:
        return None
    yaws = [p[0] for p in poses]
    pitches = [p[1] for p in poses]
    span = max(max(yaws) - min(yaws), max(pitches) - min(pitches))
    return span, (statistics.median(yaws), statistics.median(pitches))


def pose_count(value) -> int:
    """3 runs the extra sit-back and lean-in rounds. 1 is the original 13 dots only."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 3
    return 1 if n <= 1 else 3


def setup_duration(poses: int = 3) -> str:
    """The time shown and spoken before the dots. The extra rounds add about half a minute."""
    return MINUTE if pose_count(poses) <= 1 else MINUTE_HALF


def intro_lines(instruction: str = "", poses: int = 3) -> list[str]:
    """The words on the setup screen before the first dot."""
    third = instruction if instruction else "Next, look at each dot until it moves."
    return ["Eye tracker setup", EYES_ONLY, third, setup_duration(poses), "Esc cancels."]


def screen_mm() -> tuple[int, int] | None:
    """The primary screen's physical size in millimetres, or None when Windows can't say."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        hdc = user32.GetDC(0)
        try:
            width = int(gdi32.GetDeviceCaps(hdc, 4))   # HORZSIZE
            height = int(gdi32.GetDeviceCaps(hdc, 6))  # VERTSIZE
        finally:
            user32.ReleaseDC(0, hdc)
    except Exception:
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


def attach_screen_mm(model: dict, mm=None) -> dict:
    """Copy of a fitted model with screen_mm set, when the size is known."""
    out = dict(model)
    if mm is None:
        mm = screen_mm()
    if not mm:
        return out
    try:
        out["screen_mm"] = [int(mm[0]), int(mm[1])]
    except (TypeError, ValueError, IndexError):
        return out
    return out


def readiness_issue(face: dict | None) -> str | None:
    """None when setup can start. Otherwise the one instruction to show until it is fixed.

    Order: face, then distance, then light, then the head. One sentence at a time.
    """
    if not face or not face.get("present"):
        if face and face.get("two"):
            return TWO_FACES
        return NO_FACE
    width = float(face.get("width") or 0)
    if width < FACE_MIN:
        return CLOSER
    if width > FACE_MAX:
        return FARTHER
    try:
        light = float(face.get("light"))
    except (TypeError, ValueError):
        light = -1.0
    if light >= 0 and light < LIGHT_MIN:
        return NEED_LIGHT
    yaw = abs(float(face.get("yaw") or 0))
    pitch = abs(float(face.get("pitch") or 0))
    cx = 0.5 if face.get("cx") is None else float(face["cx"])
    cy = 0.5 if face.get("cy") is None else float(face["cy"])
    off = yaw > HEAD_MAX or pitch > HEAD_MAX
    off = off or not (CENTER_X[0] <= cx <= CENTER_X[1]) or not (CENTER_Y[0] <= cy <= CENTER_Y[1])
    if off:
        return CENTER_HEAD
    return None


def eye_jumped(prev, vector, limit: float = JUMP) -> bool:
    """True when the iris moved too fast to be a look at the dot."""
    if prev is None or vector is None:
        return False
    try:
        return math.hypot(float(vector[0]) - float(prev[0]), float(vector[1]) - float(prev[1])) > limit
    except (TypeError, ValueError, IndexError):
        return False


CLICK_LAG = 0.1  # people look at a spot, then click; the look is a little earlier than the click
CLICK_SLACK = 0.25  # ignore a click when no frame is this close to that moment
FEATURE_MEMORY = 1.0  # seconds of recent feature vectors kept for click correction


class RecentFeatures:
    """About one second of feature vectors, so a click can use the look just before it."""

    def __init__(self, seconds: float = FEATURE_MEMORY):
        self.seconds = float(seconds)
        self.frames: list = []

    def add(self, t, features) -> None:
        t = float(t)
        self.frames.append((t, features))
        cutoff = t - self.seconds
        self.frames = [frame for frame in self.frames if frame[0] >= cutoff]


def nearest_click_frame(frames, click_time, lag: float = CLICK_LAG, slack: float = CLICK_SLACK):
    """The feature frame closest to `lag` seconds before the click, or None when none is close."""
    goal = float(click_time) - float(lag)
    best = None
    best_dt = None
    for frame in frames:
        dt = abs(float(frame[0]) - goal)
        if best_dt is None or dt < best_dt:
            best, best_dt = frame, dt
    if best is None or best_dt > slack:
        return None
    return best


def apply_click(corrector, frames, t, x, y) -> bool:
    """Teach the corrector from the frame nearest 0.1 s before this click. False if none qualifies."""
    frame = nearest_click_frame(frames, t)
    if frame is None or corrector is None:
        return False
    return bool(corrector.add(frame[1], (float(x), float(y)), float(t)))


def feature_set_name(value) -> str:
    """The feature set a calibration should fit. Anything else stays on v1."""
    return value if value in ("v1", "v2") else "v1"


def validation_score(model: dict, samples: list, screen: tuple[int, int]) -> dict:
    """Held-out error from gaze_model.validate.

    `samples` are (features, (x, y)) from dots that were not used in fit. error_px is rounded to
    a whole pixel, which is what the saved file and the on-screen words use.
    """
    from ally import gaze_model

    use = dict(model or {})
    if not use.get("screen"):
        use["screen"] = [int(screen[0]), int(screen[1])]
    score = gaze_model.validate(use, samples)
    err = score.get("error_px")
    if err is None:
        return score
    out = dict(score)
    out["error_px"] = int(round(float(err)))
    return out


def result_grade(score: dict) -> str:
    """good meets the bar, okay is areas of the screen only, poor is neither."""
    err = score.get("error_px")
    hits = int(score.get("region_hits") or 0)
    total = int(score.get("region_total") or 0)
    if err is None or total <= 0:
        return "poor"
    try:
        err_f = float(err)
    except (TypeError, ValueError):
        return "poor"
    if err_f <= ERROR_BAR and hits * 9 >= 8 * total:
        return "good"
    if hits * 9 >= 7 * total:
        return "okay"
    return "poor"


def likely_cause(stats: dict | None) -> str:
    """Plain reason when the check comes out poor."""
    stats = stats or {}
    light, width = stats.get("light"), stats.get("width")
    if isinstance(light, (int, float)) and not isinstance(light, bool) and 0 <= light < LIGHT_MIN:
        return "The light on your face was too dim. Turn on a light in front of you."
    if isinstance(width, (int, float)) and not isinstance(width, bool) and width < FACE_MIN:
        return "You were a bit far away. Move a little closer and try again."
    if isinstance(width, (int, float)) and not isinstance(width, bool) and width > FACE_MAX:
        return "You were very close. Move a little farther back and try again."
    if stats.get("glare"):
        return "Glasses glare may be covering your eyes. Move the light off to the side."
    return "Sit still, with light on your face, and try again."


def result_lines(grade: str, score: dict, cause: str) -> list[str]:
    hits = int(score.get("region_hits") or 0)
    total = int(score.get("region_total") or 0)
    err = score.get("error_px")
    if grade == "good":
        return ["Good.", f"About {err} pixels off, and {hits} of {total} areas right."]
    if grade == "okay":
        return ["Okay for areas of the screen, not exact spots.",
                f"{hits} of {total} areas right, about {err} pixels off."]
    return ["Poor.", cause]


def validation_numbers(score: dict | None) -> dict:
    """The three held-out numbers saved with the model and printed on the result line."""
    score = score or {}
    err = score.get("error_px")
    try:
        err_out = None if err is None else int(err)
    except (TypeError, ValueError):
        err_out = None
    return {"error_px": err_out, "region_hits": int(score.get("region_hits") or 0),
            "region_total": int(score.get("region_total") or 0)}


def with_check(model: dict, score: dict | None, camera: int) -> dict:
    """Copy of the fit, plus the held-out numbers and the camera it was made for."""
    out = dict(model)
    out["validation"] = validation_numbers(score)
    out["camera"] = int(camera)
    return out


def _per_target(samples: list) -> list:
    """One sample per dot: the median feature vector, so each dot counts once."""
    grouped: dict[tuple[float, float], list] = {}
    for item in samples or []:
        try:
            feat, target = item[0], item[1]
            key = (float(target[0]), float(target[1]))
            grouped.setdefault(key, []).append([float(v) for v in feat])
        except (TypeError, ValueError, IndexError):
            continue
    out = []
    for key, feats in grouped.items():
        width = min(len(row) for row in feats)
        if width <= 0:
            continue
        med = [float(statistics.median(row[i] for row in feats)) for i in range(width)]
        out.append((med, key))
    return out


def public_samples(samples: list) -> list:
    """Check frames as plain lists, so they can be sent to the agent as JSON."""
    out = []
    for item in samples or []:
        try:
            feat, target = item[0], item[1]
            out.append([[float(v) for v in feat], [float(target[0]), float(target[1])]])
        except (TypeError, ValueError, IndexError):
            continue
    return out


def retune_sentence(before, after) -> str | None:
    """The before-and-after line for a check, or None when there is no number."""
    if before is None or after is None:
        return None
    try:
        return f"It was about {int(before)} pixels off. After re-tuning, about {int(after)}."
    except (TypeError, ValueError):
        return None


def retune_check(model: dict, samples: list) -> dict:
    """Re-tune a saved model from the 5 check dots. Nothing is written here.

    `error_before` is the check as the model stands. `error_after` is the same dots
    after gaze_model.retune shifts it. Fewer than 3 dots leaves the model unmoved.
    """
    from ally import gaze_model

    raw = model.get("screen") or (1920, 1080)
    screen = (int(raw[0]), int(raw[1]))
    before = validation_score(model, samples, screen)
    tuned = dict(model)
    per = _per_target(samples)
    if len(per) >= 3:
        try:
            tuned = gaze_model.retune(model, per)
        except (ValueError, TypeError):
            tuned = dict(model)
    after = validation_score(tuned, samples, screen)
    try:
        camera = int(model.get("camera", 0))
    except (TypeError, ValueError):
        camera = 0
    tuned = with_check(tuned, after, camera)
    return {"model": tuned, "error_before": before.get("error_px"), "error_after": after.get("error_px"),
            "validation": tuned.get("validation"), "grade": result_grade(after)}


def adopt_model(path, screen, camera: int, click_learning: bool):
    """The model a check just saved, and a fresh click corrector. (None, None) if it cannot be used."""
    from ally import gaze_model

    loaded = kept_model(gaze_model.load(path, screen), camera)
    if not loaded:
        return None, None
    corrector = gaze_model.Corrector(loaded) if click_learning else None
    return loaded, corrector


def kept_model(model: dict | None, camera: int) -> dict | None:
    """None when this file was fit for a different camera. An older file with no camera still loads.

    A different screen size is already dropped by gaze_model.load.
    """
    if not model:
        return None
    saved = model.get("camera")
    if saved is None:
        return model
    try:
        if int(saved) != int(camera):
            return None
    except (TypeError, ValueError):
        return None
    return model


def _wrap_plain(text: str, limit: int = 42) -> list[str]:
    words = str(text).split()
    if not words:
        return [""]
    lines, cur = [], ""
    for word in words:
        trial = (cur + " " + word).strip()
        if cur and len(trial) > limit:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


class _Dot:
    def __init__(self, phase: str, tid: int, nx: float, ny: float, settle: float, collect: float,
                 pose: str = ""):
        self.phase, self.id = phase, tid
        self.nx, self.ny = nx, ny
        self.settle, self.collect = settle, collect
        self.pose = pose
        self.good = 0
        self.samples: list[tuple[list[float], tuple[float, float]]] = []
        self.poses: list[tuple[float, float]] = []  # yaw, pitch in degrees, from the face matrix
        self.head_moved = False
        self.retried = False  # shown again once already
        self.warn_head = False  # this showing is the retry after the head turned
        self.shown = self.start = self.end = None


class Setup:
    """Readiness, 13 calibration dots, one retry, then 5 check dots. No camera and no window."""

    def __init__(self, screen: tuple[int, int], rng: random.Random | None = None,
                 ready_seconds: float = READY_SECONDS, min_good: int = MIN_GOOD, on_target=None,
                 poses: int = 3):
        self.w, self.h = int(screen[0]), int(screen[1])
        self.rng = rng or random.Random()
        self.ready_seconds = float(ready_seconds)
        self.min_good = int(min_good)
        self.on_target = on_target
        self.poses = pose_count(poses)
        self.stage = "ready"
        self.instruction = ""
        self.good_since: float | None = None
        self.targets: list[_Dot] = []
        self.val_targets: list[_Dot] = []
        self.drift_targets: list[_Dot] = []
        self.order: list[_Dot] = []
        self.pos = 0
        self.t0: float | None = None
        self.events: list[dict] = []
        self._retried_stages: set[str] = set()
        self._fit_sent = False
        self._score_sent = False
        self.model = None
        self.result_at = 0.0
        self.prev = None
        self.line = ""
        self.banners: list[str] = []
        self._eyes_announced = False
        self._steady_ref: tuple[float, float, float] | None = None
        self._steady_since: float | None = None
        self.anchor_pose: tuple[float, float] | None = None
        self._round: list[_Dot] = []  # the dots of the round in progress; retries stay inside it
        self.pose_plan: list[tuple[str, str, list]] = []
        self.announcements: list[str] = []
        self._lights: list[float] = []
        self._widths: list[float] = []
        self._glare_frames = 0
        self.glare = False

    @property
    def current(self) -> _Dot | None:
        if self.stage not in ("targets", "validate", "drift") or self.t0 is None:
            return None
        if self.pos >= len(self.order):
            return None
        return self.order[self.pos]

    def step(self, now: float, feat, face: dict | None, wall: float | None = None) -> str:
        """Advance one frame. `now` is monotonic, `wall` is the timestamp written on target lines."""
        wall = now if wall is None else wall
        if self.stage == "ready":
            return self._ready(now, face, wall)
        if self.stage == "settle":
            return self._settle(now, face, wall)
        if self.stage in ("targets", "validate", "drift"):
            if self.t0 is None:
                self._begin(now, wall)
                return "running"
            return self._dots(now, feat, face, wall)
        if self.stage == "await_fit":
            if not self._fit_sent:
                self._fit_sent = True
                return "fit"
            return "running"
        if self.stage == "await_score":
            if not self._score_sent:
                self._score_sent = True
                return "score"
            return "running"
        if self.stage == "result":
            if now - self.result_at >= RESULT_SECONDS:
                self.stage = "done"
                return "done"
            return "running"
        if self.stage == "done":
            return "done"
        return "running"

    def _ready(self, now: float, face, wall: float) -> str:
        issue = readiness_issue(face)
        if issue:
            self.instruction = issue
            self.good_since = None
            self._steady_ref = None
            self._steady_since = None
            return "running"
        # In range is not enough: width and pose have to stay put, or the first dots
        # are taken while the person is still sitting down.
        if not self._update_steady(now, face):
            self.instruction = ""
            self.good_since = None
            return "running"
        if self.good_since is None:
            self.good_since = self._steady_since
        self.instruction = ""
        need = max(self.ready_seconds, STEADY_SECONDS)
        if self.good_since is None or now - self.good_since < need:
            return "running"
        self._start_targets(now, wall)
        return "running"

    def _update_steady(self, now: float, face) -> bool:
        """True once width, yaw, and pitch have stayed within tolerance for STEADY_SECONDS."""
        try:
            width = float(face.get("width"))
            yaw = float(face.get("yaw"))
            pitch = float(face.get("pitch"))
        except (TypeError, ValueError, AttributeError):
            self._steady_ref = None
            self._steady_since = None
            return False
        if self._steady_ref is None or self._steady_since is None:
            self._steady_ref = (width, yaw, pitch)
            self._steady_since = now
            return False
        base_w, base_yaw, base_pitch = self._steady_ref
        width_jump = abs(width - base_w) > WIDTH_STEADY * max(abs(base_w), 1e-6)
        pose_jump = abs(yaw - base_yaw) > HEAD_MOVE_DEG or abs(pitch - base_pitch) > HEAD_MOVE_DEG
        if width_jump or pose_jump:
            self._steady_ref = (width, yaw, pitch)
            self._steady_since = now
            return False
        return now - self._steady_since >= STEADY_SECONDS

    def _start_targets(self, now: float, wall: float) -> None:
        self.targets = [_Dot("calibration", i, x, y, CAL_SETTLE, CAL_COLLECT, pose=POSE_NORMAL)
                        for i, (x, y) in enumerate(calibration_points())]
        checks = validation_points(self.rng)
        self.val_targets = [_Dot("validation", i, x, y, VAL_SETTLE, VAL_COLLECT)
                            for i, (x, y) in enumerate(checks)]
        self.pose_plan = []
        if self.poses >= 3:
            avoid = [(dot.nx, dot.ny) for dot in self.targets + self.val_targets]
            back = validation_points(self.rng, n=5, avoid=avoid)
            close = validation_points(self.rng, n=5, avoid=avoid + back)
            self.pose_plan = [(POSE_BACK, SIT_BACK, back), (POSE_CLOSE, LEAN_IN, close)]
        self._begin_round(self.targets, now, wall)

    def _begin_round(self, dots: list[_Dot], now: float, wall: float) -> None:
        """Start a round. Head movement is judged against this round's first dot, not an earlier one."""
        self.anchor_pose = None
        self._round = list(dots)
        self.order = list(dots)
        self.pos = 0
        self.t0 = None
        self.stage = "targets"
        self._retried_stages.discard("targets")
        if self.order:
            self._begin(now, wall)

    def _settle(self, now: float, face, wall: float) -> str:
        """Wait until the new head position holds still, then collect that round."""
        if not self._update_steady(now, face):
            return "running"
        name = getattr(self, "_settle_pose", "")
        prompt = getattr(self, "_settle_prompt", "")
        points = getattr(self, "_settle_points", [])
        dots = [_Dot("calibration", i, x, y, CAL_SETTLE, CAL_COLLECT, pose=name)
                for i, (x, y) in enumerate(points)]
        self.targets.extend(dots)
        self._begin_round(dots, now, wall)
        if prompt:
            self.line = prompt
        return "running"

    def _start_settle(self, pose: str, prompt: str, points: list) -> None:
        self.stage = "settle"
        self._settle_pose = pose
        self._settle_prompt = prompt
        self._settle_points = list(points)
        self.t0 = None
        self._steady_ref = None
        self._steady_since = None
        self.line = prompt
        self.instruction = prompt
        self.banners.append(prompt)
        self.announcements.append(prompt)

    def _begin(self, now: float, wall: float) -> None:
        target = self.order[self.pos]
        target.shown = float(wall)
        target.start = float(wall) + target.settle
        target.end = target.start + target.collect
        self.t0 = now
        x, y = self._px(target)
        event = {"phase": target.phase, "id": target.id, "x": x, "y": y,
                 "shown": target.shown, "start": target.start, "end": target.end}
        if target.pose:
            event["pose"] = target.pose
        self.events.append(event)
        if target.warn_head:
            self.line = HEAD_RETRY_LINE
        elif not self._eyes_announced:
            self.line = EYES_ONLY
            self._eyes_announced = True
        else:
            self.line = ""
        if self.line:
            self.banners.append(self.line)
        if self.on_target is not None:
            self.on_target(dict(event))

    def _dots(self, now: float, feat, face, wall: float) -> str:
        target = self.order[self.pos]
        elapsed = 0.0 if self.t0 is None else now - self.t0
        if target.settle <= elapsed < target.settle + target.collect:
            self._watch_head(target, face)
            self._consider(target, feat, face)
        if elapsed >= target.settle + target.collect:
            return self._advance(now, wall)
        return "running"

    def _watch_head(self, target: _Dot, face) -> None:
        """Yaw and pitch, in degrees, from the face snapshot (the face matrix when the camera has one)."""
        if not face or not face.get("present"):
            return
        try:
            target.poses.append((float(face["yaw"]), float(face["pitch"])))
        except (KeyError, TypeError, ValueError):
            return

    def _mark_head(self, target: _Dot) -> None:
        """Flag a dot whose head turned during the collection, or away from this round's first dot."""
        if target.retried:
            return
        summary = _pose_summary(target.poses)
        if summary is None:
            return
        span, median = summary
        if self.anchor_pose is None:
            self.anchor_pose = median
            target.head_moved = span > HEAD_MOVE_DEG
            return
        turned = abs(median[0] - self.anchor_pose[0]) > HEAD_MOVE_DEG
        turned = turned or abs(median[1] - self.anchor_pose[1]) > HEAD_MOVE_DEG
        target.head_moved = span > HEAD_MOVE_DEG or turned

    def _consider(self, target: _Dot, feat, face) -> None:
        if face and face.get("present"):
            light, width = face.get("light"), face.get("width")
            if isinstance(light, (int, float)) and not isinstance(light, bool) and light >= 0:
                self._lights.append(float(light))
            if isinstance(width, (int, float)) and not isinstance(width, bool) and width > 0:
                self._widths.append(float(width))
            eye = face.get("eye_light")
            if isinstance(eye, (int, float)) and not isinstance(eye, bool) and eye >= GLARE_LIGHT:
                self._glare_frames += 1
                if self._glare_frames >= 10:
                    self.glare = True
        if feat is None:
            return
        try:
            vector, openness = feat[0], float(feat[1])
        except (TypeError, ValueError, IndexError):
            return
        jumped = eye_jumped(self.prev, vector)
        if openness >= MIN_OPEN:
            try:
                self.prev = [float(v) for v in vector]
            except (TypeError, ValueError):
                return
        if openness < MIN_OPEN or jumped:
            return
        x, y = self._px(target)
        target.samples.append((list(self.prev), (float(x), float(y))))
        target.good += 1

    def _advance(self, now: float, wall: float) -> str:
        self._mark_head(self.order[self.pos])
        self.pos += 1
        self.t0 = None
        if self.pos < len(self.order):
            self._begin(now, wall)
            return "running"
        if self.stage not in self._retried_stages:
            self._retried_stages.add(self.stage)
            again = self._again()
            if again:
                self.order.extend(again)
                self._begin(now, wall)
                return "running"
        if self.stage == "targets" and self.pose_plan:
            pose, prompt, points = self.pose_plan.pop(0)
            self._start_settle(pose, prompt, points)
            return "running"
        if self.stage == "targets":
            self.stage = "await_fit"
            self._fit_sent = False
            return "fit"
        if self.stage == "validate":
            self.stage = "await_score"
            self._score_sent = False
            return "score"
        self.stage = "done"
        return "done"

    def _again(self) -> list[_Dot]:
        """Dots to show once more: too few frames (calibration only), or the head turned."""
        dots = {"targets": self._round, "validate": self.val_targets, "drift": self.drift_targets}
        again = []
        for target in dots.get(self.stage, []):
            if target.retried:
                continue
            weak = self.stage == "targets" and target.good < self.min_good
            if not weak and not target.head_moved:
                continue
            target.retried = True
            if target.head_moved:
                # The turned frames would teach the model the wrong pose. Keep the still attempt.
                target.warn_head = True
                target.samples.clear()
                target.good = 0
                target.poses.clear()
                target.head_moved = False
            again.append(target)
        return again

    def _px(self, target: _Dot) -> tuple[int, int]:
        return int(target.nx * self.w), int(target.ny * self.h)

    def usable(self) -> list[tuple[list[float], tuple[float, float]]]:
        """Calibration frames from dots that collected enough good frames. Check dots are left out."""
        out = []
        for target in self.targets:
            if target.good >= self.min_good:
                out.extend(target.samples)
        return out

    def validation_samples(self) -> list[tuple[list[float], tuple[float, float]]]:
        out = []
        for target in self.val_targets:
            out.extend(target.samples)
        return out

    def cause_stats(self) -> dict:
        return {
            "light": statistics.median(self._lights) if self._lights else None,
            "width": statistics.median(self._widths) if self._widths else None,
            "glare": self.glare,
        }

    def accept_fit(self, model: dict) -> None:
        self.model = model
        self.stage = "validate"
        self.order = list(self.val_targets)
        self.pos = 0
        self.t0 = None
        self._score_sent = False
        self.anchor_pose = None  # the check is its own round
        self._retried_stages.discard("validate")

    def start_check(self, now: float, wall: float) -> None:
        """Five check dots only. The model is already fit; these frames are not used to fit it."""
        points = validation_points(self.rng)
        self.val_targets = [_Dot("validation", i, x, y, VAL_SETTLE, VAL_COLLECT) for i, (x, y) in enumerate(points)]
        self.order = list(self.val_targets)
        self.pos = 0
        self.t0 = None
        self.stage = "validate" if self.order else "done"
        self._score_sent = False
        self._fit_sent = True
        self.anchor_pose = None
        self._retried_stages.discard("validate")
        if self.order:
            self._begin(now, wall)

    def start_drift(self, rng: random.Random | None = None) -> None:
        """Five new check dots, phase "drift". Not used for fitting."""
        rng = rng or self.rng
        avoid = list(calibration_points())
        avoid += [(t.nx, t.ny) for t in self.targets]
        avoid += [(t.nx, t.ny) for t in self.val_targets]
        points = validation_points(rng, avoid=avoid)
        self.drift_targets = [_Dot("drift", i, x, y, VAL_SETTLE, VAL_COLLECT) for i, (x, y) in enumerate(points)]
        self.order = list(self.drift_targets)
        self.pos = 0
        self.t0 = None
        self.stage = "drift" if self.drift_targets else "done"
        self.anchor_pose = None
        self._retried_stages.discard("drift")

    def finish(self, now: float) -> None:
        self.stage = "result"
        self.result_at = now


class Calibration:
    """Full-screen setup: readiness, 13 dots, optional sit-back and lean-in rounds, then 5 check dots."""

    def __init__(self, size: tuple[int, int], on_target=None, rng: random.Random | None = None,
                 ready_seconds: float = READY_SECONDS, poses: int = 3):
        import cv2  # type: ignore
        import numpy as np  # type: ignore

        self.cv2, self.np = cv2, np
        self.w, self.h = size
        self.speak = False  # the camera helper turns this on so Ally can say the pose prompts
        self._said = 0
        self.setup = Setup(size, rng=rng, ready_seconds=ready_seconds, on_target=on_target, poses=poses)
        self.last_face = time.monotonic()
        self.check_only = False
        self.result: dict | None = None
        self.lines: list[str] = []
        self.base = np.full((self.h, self.w, 3), 32, dtype=np.uint8)
        self._open_window()

    def _open_window(self) -> None:
        cv2 = self.cv2
        cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
        cv2.setWindowProperty(WINDOW, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
        try:
            cv2.setWindowProperty(WINDOW, cv2.WND_PROP_TOPMOST, 1)
        except Exception:
            pass

    def reopen(self) -> None:
        self._open_window()

    def start_drift(self, rng: random.Random | None = None) -> None:
        self.lines = []
        self.result = None
        self.check_only = False
        self.last_face = time.monotonic()
        self.setup.start_drift(rng)
        self.reopen()

    def begin_check(self) -> None:
        """Open the screen on the 5 check dots. Does not fit a new model."""
        self.lines = []
        self.result = None
        self.check_only = True
        self.last_face = time.monotonic()
        self.setup.start_check(time.monotonic(), time.time())
        self.reopen()

    def _text(self, img, text: str, y: float, scale: float = 1.0) -> None:
        cv2 = self.cv2
        s = max(0.8, self.h / 700) * scale
        th = max(2, int(s * 2))
        (tw, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, s, th)
        cv2.putText(img, text, ((self.w - tw) // 2, int(self.h * y)), cv2.FONT_HERSHEY_SIMPLEX, s,
                    (235, 235, 235), th, cv2.LINE_AA)

    def step(self, feat, now: float, face: dict | None = None, wall: float | None = None) -> str:
        """Draw one frame and record feat. Returns running, fit, score, done, or cancel."""
        if feat is not None:
            self.last_face = now
        state = self.setup.step(now, feat, face, wall)
        if self.speak:
            while self._said < len(self.setup.announcements):
                emit({"say": self.setup.announcements[self._said]})
                self._said += 1
        self._draw(now)
        if (self.cv2.waitKey(1) & 0xFF) == 27:
            if self.setup.stage in ("result", "done"):
                return "done"
            return "cancel"
        return state

    def _draw(self, now: float) -> None:
        cv2 = self.cv2
        img = self.base.copy()
        stage = self.setup.stage
        target = self.setup.current
        if stage == "ready":
            title, eyes, third, duration, cancel = intro_lines(self.setup.instruction, self.setup.poses)
            self._text(img, title, 0.28, 1.2)
            self._text(img, eyes, 0.40, 0.75)
            self._text(img, third, 0.52, 0.8)
            self._text(img, duration, 0.64, 0.75)
            self._text(img, cancel, 0.76, 0.7)
        elif stage == "settle":
            self._text(img, self.setup.line or SIT_BACK, 0.46, 1.15)
            self._text(img, "Hold still there.", 0.58, 0.8)
        elif target is not None and self.setup.t0 is not None:
            x, y = self.setup._px(target)
            elapsed = now - self.setup.t0
            if elapsed < target.settle and target.settle > 0:  # a ring that shrinks onto the dot
                frac = min(1.0, max(0.0, elapsed / target.settle))
                cv2.circle(img, (x, y), int(14 + 40 * (1 - frac)), (230, 230, 230), 3, cv2.LINE_AA)
            cv2.circle(img, (x, y), 12, (0, 140, 255), -1, cv2.LINE_AA)
            cv2.circle(img, (x, y), 3, (255, 255, 255), -1, cv2.LINE_AA)
            if self.setup.line:
                self._text(img, self.setup.line, 0.90, 0.65)
            elif stage == "validate":
                self._text(img, "Checking. Keep looking at the dot.", 0.90, 0.7)
            elif stage == "drift":
                self._text(img, "Checking again. Keep looking at the dot.", 0.90, 0.7)
            if now - self.last_face > 1.0:
                self._text(img, "I can't see your eyes. Face the screen, with light on your face.", 0.96, 0.65)
        elif stage in ("await_fit", "await_score"):
            self._text(img, "One moment.", 0.48, 1.2)
        elif self.lines or self.result is not None:
            lines = self.lines
            if not lines and self.result is not None:
                if self.result.get("calibrated"):
                    lines = ["Eye tracker ready", f"Accuracy: about {self.result.get('error_px')} pixels"]
                else:
                    lines = ["Calibration didn't work.", str(self.result.get("why", ""))]
            y = 0.38
            first = True
            for line in lines:
                for part in _wrap_plain(line, 42):
                    self._text(img, part, y, 1.15 if first else 0.75)
                    first = False
                    y += 0.08
        cv2.imshow(WINDOW, img)

    def usable(self) -> list[tuple[list[float], tuple[float, float]]]:
        return self.setup.usable()

    def validation_samples(self) -> list[tuple[list[float], tuple[float, float]]]:
        return self.setup.validation_samples()

    def cause_stats(self) -> dict:
        return self.setup.cause_stats()

    def accept_fit(self, model: dict) -> None:
        self.setup.accept_fit(model)

    @property
    def model(self):
        return self.setup.model

    def finish(self, result: dict, lines: list[str] | None = None) -> None:
        self.result = result
        self.lines = list(lines or [])
        self.setup.finish(time.monotonic())

    def close(self) -> None:
        try:
            self.cv2.destroyWindow(WINDOW)
            self.cv2.waitKey(1)
        except Exception:
            pass


def draw_hud(cv2, frame, rows: list) -> None:
    """The camera picture (mirrored, like a mirror) with large, high-contrast signal rows.

    `rows` is still [[text, level], ...] from the `hud` command. Level picks the color.
    """
    img = cv2.flip(frame, 1)
    rows = list(rows or [])
    band = min(int(img.shape[0]), 12 + HUD_STEP * (len(rows) + 1))
    img[:band] = 0
    y = HUD_STEP
    for row in rows:
        if y > band - 4:
            break
        text, level = (list(row) + ["ok"])[:2] if isinstance(row, (list, tuple)) else (str(row), "ok")
        color = HUD_COLORS.get(str(level), HUD_COLORS["ok"])
        cv2.putText(img, str(text)[:80], (12, y), cv2.FONT_HERSHEY_SIMPLEX, HUD_SCALE,
                    color, HUD_THICK, cv2.LINE_AA)
        y += HUD_STEP
    if y <= band - 4:
        cv2.putText(img, HUD_FOOTER, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    HUD_COLORS["ok"], HUD_THICK, cv2.LINE_AA)
    cv2.imshow(HUD, img)
    cv2.waitKey(1)


# Always-on-top, not in the taskbar, and a click does not focus it (WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW).
INDICATOR_TEXT = "Camera on - click to turn off"
_EX_NOACTIVATE = 0x08000000
_EX_TOOLWINDOW = 0x00000080
_EX_TOPMOST = 0x00000008
INDICATOR_EX_STYLE = _EX_NOACTIVATE | _EX_TOOLWINDOW | _EX_TOPMOST
_noted = False


def note_once(msg: str) -> None:
    """One line on stderr. Used when the camera-on window cannot be shown."""
    global _noted
    if _noted:
        return
    _noted = True
    try:
        sys.stderr.write(msg + "\n")
        sys.stderr.flush()
    except (OSError, ValueError):
        pass


class CameraIndicator:
    """Small corner window while the camera is open. Clicking it asks the helper to turn the camera off."""

    def __init__(self):
        self.clicked = False
        self._hwnd = None
        self._proc = None
        self._cv2 = None
        self._name = INDICATOR_TEXT

    def open(self, cv2, screen: tuple[int, int]) -> bool:
        if sys.platform == "win32":
            try:
                if self._open_win32(screen):
                    return True
            except Exception:
                self._hwnd = None
        try:
            if cv2 is not None and self._open_highgui(cv2, screen):
                return True
        except Exception:
            self._cv2 = None
        note_once("Camera-on indicator skipped: no Win32 window and no HighGUI. The camera is still on.")
        return False

    def pump(self) -> bool:
        if self.clicked:
            return True
        if self._hwnd:
            try:
                self._pump_win32()
            except Exception:
                pass
        elif self._cv2 is not None:
            try:
                self._cv2.waitKey(1)
            except Exception:
                pass
        return self.clicked

    def close(self) -> None:
        hwnd, self._hwnd = self._hwnd, None
        if hwnd:
            try:
                import ctypes
                ctypes.windll.user32.DestroyWindow(hwnd)
            except Exception:
                pass
        cv2, self._cv2 = self._cv2, None
        if cv2 is not None:
            try:
                cv2.destroyWindow(self._name)
                cv2.waitKey(1)
            except Exception:
                pass

    def _open_win32(self, screen: tuple[int, int]) -> bool:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = LRESULT

        clicked = self

        def proc(hwnd, msg, wparam, lparam):
            if msg in (0x0202, 0x0010):  # WM_LBUTTONUP, WM_CLOSE
                clicked.clicked = True
                return 0
            if msg == 0x000F:  # WM_PAINT
                ps = _PAINT(user32, wintypes)
                hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
                rect = wintypes.RECT(4, 4, 276, 32)
                user32.DrawTextW(hdc, INDICATOR_TEXT, -1, ctypes.byref(rect), 0x0020 | 0x0004)  # single line, vcenter
                user32.EndPaint(hwnd, ctypes.byref(ps))
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        cb = WNDPROC(proc)
        self._proc = cb  # the callback must stay alive for as long as the window exists

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.lpfnWndProc = cb
        wc.hInstance = hinst
        wc.hbrBackground = gdi32.GetStockObject(0)  # WHITE_BRUSH
        wc.lpszClassName = "AllyCameraOn"
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        user32.RegisterClassW.restype = wintypes.ATOM
        atom = user32.RegisterClassW(ctypes.byref(wc))
        if not atom and ctypes.get_last_error() not in (0, 1410):  # 1410: class already registered
            return False
        width, height = 280, 36
        x = max(0, int(screen[0]) - width - 16)
        y = 16
        user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                           wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        user32.CreateWindowExW.restype = wintypes.HWND
        hwnd = user32.CreateWindowExW(INDICATOR_EX_STYLE, "AllyCameraOn", INDICATOR_TEXT,
                                      0x80000000 | 0x10000000, x, y, width, height, None, None, hinst, None)
        if not hwnd:
            return False
        user32.ShowWindow(hwnd, 5)  # SW_SHOWNA: show without taking focus
        user32.SetWindowPos.argtypes = [wintypes.HWND, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.SetWindowPos(hwnd, ctypes.c_void_p(-1), x, y, width, height, 0x0010 | 0x0040)  # NOACTIVATE, SHOWWINDOW
        user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
                                        wintypes.UINT]
        user32.PeekMessageW.restype = wintypes.BOOL
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.restype = LRESULT
        self._hwnd = hwnd
        self._user32 = user32
        return True

    def _pump_win32(self) -> None:
        import ctypes
        user32 = self._user32
        msg = ctypes.wintypes.MSG()
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _open_highgui(self, cv2, screen: tuple[int, int]) -> bool:
        import numpy as np

        cv2.namedWindow(self._name, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(self._name, 320, 48)
        try:
            cv2.setWindowProperty(self._name, cv2.WND_PROP_TOPMOST, 1)
        except Exception:
            pass
        try:
            cv2.moveWindow(self._name, max(0, int(screen[0]) - 340), 16)
        except Exception:
            pass
        img = np.full((48, 320, 3), 32, dtype=np.uint8)
        cv2.putText(img, INDICATOR_TEXT, (8, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (240, 240, 240), 1, cv2.LINE_AA)
        cv2.imshow(self._name, img)

        def on_mouse(event, _x, _y, _flags, _param):
            if event == cv2.EVENT_LBUTTONUP:
                self.clicked = True

        cv2.setMouseCallback(self._name, on_mouse)
        cv2.waitKey(1)
        self._cv2 = cv2
        return True


def _PAINT(user32, wintypes):
    """A PAINTSTRUCT, with BeginPaint/EndPaint/DrawText signatures set once."""
    import ctypes

    class PAINTSTRUCT(ctypes.Structure):
        _fields_ = [("hdc", wintypes.HDC), ("fErase", wintypes.BOOL), ("rcPaint", wintypes.RECT),
                    ("fRestore", wintypes.BOOL), ("fIncUpdate", wintypes.BOOL), ("rgbReserved", wintypes.BYTE * 32)]

    user32.BeginPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
    user32.BeginPaint.restype = wintypes.HDC
    user32.EndPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
    user32.EndPaint.restype = wintypes.BOOL
    user32.DrawTextW.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(wintypes.RECT),
                                 wintypes.UINT]
    user32.DrawTextW.restype = ctypes.c_int
    return PAINTSTRUCT()


CARD = "Ally card"
CARD_SCALES = {"tiny": 0.4, "normal": 1.15, "large": 1.9}


def parse_choose(arg: str) -> dict | None:
    """The JSON after `choose`: 2 to 4 option strings. None when the line is unusable."""
    try:
        spec = json.loads(arg)
    except ValueError:
        return None
    if not isinstance(spec, dict):
        return None
    options = spec.get("options")
    if not isinstance(options, list) or not 2 <= len(options) <= 4:
        return None
    texts = []
    for item in options:
        text = " ".join(str(item).split())
        if not text:
            return None
        texts.append(text[:80])
    try:
        seconds = float(spec.get("seconds") or 8)
    except (TypeError, ValueError):
        seconds = 8.0
    return {"options": texts, "seconds": max(0.5, min(60.0, seconds))}


def parse_card(arg: str) -> dict | None:
    """The JSON after `card`. None when the line is missing text or is not JSON."""
    try:
        spec = json.loads(arg)
    except ValueError:
        return None
    if not isinstance(spec, dict):
        return None
    text = spec.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    size = spec.get("size", "normal")
    if size not in CARD_SCALES:
        size = "normal"
    try:
        seconds = float(spec.get("seconds") or 0)
    except (TypeError, ValueError):
        seconds = 0.0
    title = spec.get("title") if isinstance(spec.get("title"), str) else ""
    return {"text": text.strip(), "title": title.strip(), "size": size, "seconds": max(0.0, seconds)}


def classify_command(cmd: str) -> tuple[str, object]:
    """What a stdin line is asking for. Never raises; unknown lines are 'ignore'."""
    if not isinstance(cmd, str) or not cmd:
        return "ignore", None
    try:
        if cmd == "quit":
            return "quit", None
        if cmd == "stop":
            return "stop", None
        if cmd == "calibrate":
            return "calibrate", None
        if cmd.startswith("calibrate "):
            parts = cmd.split()
            if len(parts) != 2:
                return "ignore", None
            try:
                return "calibrate", pose_count(parts[1])
            except ValueError:
                return "ignore", None
        if cmd == "check":
            return "check", None
        if cmd == "reload":
            return "reload", None
        if cmd == "dot on":
            return "dot", True
        if cmd == "dot off":
            return "dot", False
        if cmd.startswith("view "):
            parts = cmd.split()
            if len(parts) != 4:
                return "ignore", None
            try:
                return "view", (float(parts[1]), int(float(parts[2])), int(float(parts[3])))
            except ValueError:
                return "ignore", None
        if cmd == "choose off":
            return "choose_off", None
        if cmd.startswith("choose"):
            spec = parse_choose(cmd[6:].strip())
            return ("choose", spec) if spec else ("ignore", None)
        if cmd.startswith("click "):
            parts = cmd.split()
            if len(parts) != 4:
                return "ignore", None
            try:
                return "click", (float(parts[1]), float(parts[2]), float(parts[3]))
            except ValueError:
                return "ignore", None
        if cmd == "card off":
            return "card_off", None
        if cmd.startswith("card"):
            spec = parse_card(cmd[4:].strip())
            return ("card", spec) if spec else ("ignore", None)
        if cmd.startswith("hud"):
            arg = cmd[3:].strip()
            if arg == "off":
                return "hud_off", None
            if arg.startswith("["):
                try:
                    rows = json.loads(arg)
                except ValueError:
                    return "hud_on", None
                return ("hud_rows", rows) if isinstance(rows, list) else ("hud_on", None)
            return "hud_on", None
    except Exception:
        return "ignore", None
    return "ignore", None


class CardWindow:
    """A readable card. size tiny uses small text, for the 'read small text' recording task."""

    def __init__(self, cv2, spec: dict, screen: tuple[int, int]):
        self.cv2 = cv2
        self.spec = spec
        self.screen = screen
        self.start = time.monotonic()
        self.alive = True
        cv2.namedWindow(CARD, cv2.WINDOW_NORMAL)
        width, height = self._window_size()
        cv2.resizeWindow(CARD, width, height)
        try:
            cv2.setWindowProperty(CARD, cv2.WND_PROP_TOPMOST, 1)
        except Exception:
            pass
        try:
            cv2.moveWindow(CARD, max(0, (int(screen[0]) - width) // 2), max(0, (int(screen[1]) - height) // 2))
        except Exception:
            pass
        self.step()

    def _window_size(self) -> tuple[int, int]:
        sw, sh = int(self.screen[0]), int(self.screen[1])
        if self.spec["size"] == "tiny":
            return min(max(sw, 1), 900), min(max(sh, 1), 420)
        if self.spec["size"] == "large":
            return min(max(sw - 40, 1), 1400), min(max(sh - 40, 1), 800)
        return min(max(sw - 80, 1), 1100), min(max(sh - 80, 1), 640)

    def step(self) -> bool:
        if not self.alive:
            return False
        seconds = float(self.spec.get("seconds") or 0)
        if seconds and time.monotonic() - self.start >= seconds:
            self.close()
            return False
        self._draw()
        return True

    def _draw(self) -> None:
        import numpy as np

        cv2 = self.cv2
        width, height = self._window_size()
        img = np.full((height, width, 3), 255, dtype=np.uint8)
        scale = CARD_SCALES[self.spec["size"]]
        thick = max(1, int(round(scale * 2)))
        y = 36
        if self.spec["title"]:
            title_scale = scale if self.spec["size"] == "tiny" else min(scale + 0.35, 2.4)
            y = self._line(img, self.spec["title"], y, title_scale, max(thick, 2), width)
            y += 12
        self._line(img, self.spec["text"], y, scale, thick, width)
        cv2.imshow(CARD, img)
        cv2.waitKey(1)

    def _line(self, img, text: str, y: int, scale: float, thick: int, width: int) -> int:
        cv2 = self.cv2
        for line in _wrap(cv2, text, scale, thick, width - 48):
            cv2.putText(img, line, (24, y), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick, cv2.LINE_AA)
            y += int(28 * max(scale, 0.6))
        return y

    def close(self) -> None:
        if not self.alive:
            return
        self.alive = False
        try:
            self.cv2.destroyWindow(CARD)
            self.cv2.waitKey(1)
        except Exception:
            pass


def _wrap(cv2, text: str, scale: float, thick: int, width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines, cur = [], ""
    for word in words:
        trial = (cur + " " + word).strip()
        (tw, _), _ = cv2.getTextSize(trial, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
        if cur and tw > width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def _xy(point) -> tuple[float, float]:
    if hasattr(point, "x"):
        return float(point.x), float(point.y)
    return float(point[0]), float(point[1])


def face_width(landmarks) -> float:
    """Cheek-to-cheek span. Larger means a closer or bigger face."""
    try:
        rx, ry = _xy(landmarks[CHEEK_R])
        lx, ly = _xy(landmarks[CHEEK_L])
    except (IndexError, TypeError, ValueError):
        return 0.0
    return math.hypot(lx - rx, ly - ry)


def choose_face(widths: list[float], second: float = SECOND_FACE) -> int | None:
    """Index of the largest face. None when another face is at least `second` as wide.

    Two people in frame must not be mixed into one set of face numbers, so the frame is
    treated like no face at all.
    """
    if not widths:
        return None
    order = sorted(range(len(widths)), key=lambda i: widths[i], reverse=True)
    best = order[0]
    if len(order) > 1 and widths[best] > 0 and widths[order[1]] >= second * widths[best]:
        return None
    return best


def _pose(landmarks, matrix) -> tuple[float, float]:
    if matrix is not None:
        try:
            from ally.senses.face import head_angles

            yaw, pitch = head_angles(matrix)
            return float(yaw), float(pitch)
        except Exception:
            pass
    try:
        nx, ny = _xy(landmarks[1])
    except (IndexError, TypeError, ValueError):
        return 0.0, 0.0
    return (float(nx) - 0.5) * 90.0, (float(ny) - 0.5) * 90.0


def _eye_light(gray, landmarks, width: int, height: int) -> float:
    """Mean brightness in a small box on each iris. High values are glare, not a saved image."""
    vals = []
    for idx in (468, 473):
        try:
            x = int(float(landmarks[idx][0]) * width)
            y = int(float(landmarks[idx][1]) * height)
        except (IndexError, TypeError, ValueError):
            continue
        x0, x1 = max(0, x - 4), min(width, x + 5)
        y0, y1 = max(0, y - 4), min(height, y + 5)
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        vals.append(float(gray[y0:y1, x0:x1].mean()))
    if not vals:
        return -1.0
    return sum(vals) / len(vals)


def face_snapshot(landmarks, matrix, light, eye_light=None, two: bool = False) -> dict:
    """Numbers the readiness check needs. No picture."""
    if two:
        return {"present": False, "two": True}
    if not landmarks:
        return {"present": False}
    try:
        rx, ry = _xy(landmarks[CHEEK_R])
        lx, ly = _xy(landmarks[CHEEK_L])
        cx, cy = (rx + lx) / 2.0, (ry + ly) / 2.0
    except (IndexError, TypeError, ValueError):
        cx, cy = 0.5, 0.5
    yaw, pitch = _pose(landmarks, matrix)
    try:
        light_f = -1.0 if light is None else float(light)
    except (TypeError, ValueError):
        light_f = -1.0
    try:
        eye_f = -1.0 if eye_light is None else float(eye_light)
    except (TypeError, ValueError):
        eye_f = -1.0
    return {"present": True, "width": face_width(landmarks), "light": light_f, "cx": cx, "cy": cy,
            "yaw": yaw, "pitch": pitch, "eye_light": eye_f}


def frame_features(model, landmarks, blendshapes, matrix, width, height):
    """Feature vector for this model (v1 or v2) plus eye openness. None when the landmarks are unusable."""
    from ally import gaze_model

    probe = model if isinstance(model, dict) else {"features": "v1"}
    if probe.get("features") not in ("v1", "v2"):
        probe = {"features": "v1"}
    return gaze_model.features_for_model(probe, landmarks, blendshapes, matrix, width, height)


def look(landmarker, mp, cv2, frame, t_ms: int, model=None) -> dict:
    """One camera frame -> landmarks, features, and readiness numbers. The frame is not returned.

    `model` only picks the feature set (a fitted model, or {"features": "v1"} / "v2").
    """
    import numpy as np
    from ally.senses.face import brightness, face_box

    h, w = frame.shape[:2]
    image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    res = landmarker.detect_for_video(image, int(t_ms))
    empty = {"w": w, "h": h, "feat": None, "lms": None, "bs": None, "mat": None,
             "face": {"present": False}, "light": -1.0}
    widths = [face_width(face) for face in res.face_landmarks] if res.face_landmarks else []
    chosen = choose_face(widths)
    if chosen is None:
        if len(widths) >= 2:
            empty["face"] = {"present": False, "two": True}
        return empty
    lms = [(p.x, p.y, getattr(p, "z", 0.0)) for p in res.face_landmarks[chosen]]
    bs = None
    if res.face_blendshapes and chosen < len(res.face_blendshapes):
        bs = {c.category_name: c.score for c in res.face_blendshapes[chosen]}
    mat = None
    if res.facial_transformation_matrixes and chosen < len(res.facial_transformation_matrixes):
        mat = res.facial_transformation_matrixes[chosen]
    feat = frame_features(model, lms, bs, mat, w, h)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    light = brightness(gray, face_box(np.asarray(lms), w, h))
    face = face_snapshot(lms, mat, light, _eye_light(gray, lms, w, h))
    return {"w": w, "h": h, "feat": feat, "lms": lms, "bs": bs, "mat": mat, "face": face, "light": light}


class ChoiceBoard:
    """Large cards, one per option, each in its own part of the screen. 2 options: left and right."""

    def __init__(self, cv2, spec: dict, screen: tuple[int, int]):
        self.cv2 = cv2
        self.spec = spec
        self.screen = screen
        self.names: list[str] = []
        self.alive = True
        self.start = time.monotonic()
        self._open()

    def _boxes(self) -> list[tuple[int, int, int, int]]:
        w, h = int(self.screen[0]), int(self.screen[1])
        n = len(self.spec["options"])
        margin = 24
        if n == 2:
            raw = [(0, 0, w // 2, h), (w // 2, 0, w - w // 2, h)]
        elif n == 3:
            col = w // 3
            raw = [(i * col, 0, col if i < 2 else w - 2 * col, h) for i in range(3)]
        else:
            hw, hh = w // 2, h // 2
            raw = [(0, 0, hw, hh), (hw, 0, w - hw, hh), (0, hh, hw, h - hh), (hw, hh, w - hw, h - hh)]
        boxes = []
        for x, y, bw, bh in raw:
            boxes.append((x + margin, y + margin, max(80, bw - 2 * margin), max(80, bh - 2 * margin)))
        return boxes

    def _open(self) -> None:
        cv2 = self.cv2
        for i, (x, y, bw, bh) in enumerate(self._boxes()):
            name = f"Ally choice {i + 1}"
            self.names.append(name)
            cv2.namedWindow(name, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(name, bw, bh)
            try:
                cv2.moveWindow(name, x, y)
            except Exception:
                pass
            try:
                cv2.setWindowProperty(name, cv2.WND_PROP_TOPMOST, 1)
            except Exception:
                pass
        self._draw()

    def step(self) -> bool:
        if not self.alive:
            return False
        seconds = float(self.spec.get("seconds") or 0)
        if seconds and time.monotonic() - self.start >= seconds:
            self.close()
            return False
        self._draw()
        return True

    def _draw(self) -> None:
        import numpy as np

        cv2 = self.cv2
        scale_0 = 2.2 if len(self.spec["options"]) <= 2 else 1.6
        for name, text, (_x, _y, bw, bh) in zip(self.names, self.spec["options"], self._boxes()):
            img = np.full((bh, bw, 3), 255, dtype=np.uint8)
            scale, thick = scale_0, 3
            (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
            while tw > bw - 40 and scale > 0.6:
                scale = round(scale - 0.2, 2)
                thick = max(1, int(round(scale * 2)))
                (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
            cv2.putText(img, text, (max(12, (bw - tw) // 2), (bh + th) // 2), cv2.FONT_HERSHEY_SIMPLEX,
                        scale, (0, 0, 0), thick, cv2.LINE_AA)
            cv2.imshow(name, img)
        cv2.waitKey(1)

    def close(self) -> None:
        if not self.alive:
            return
        self.alive = False
        for name in self.names:
            try:
                self.cv2.destroyWindow(name)
            except Exception:
                pass
        try:
            self.cv2.waitKey(1)
        except Exception:
            pass


def main(argv: list[str]) -> int:
    try:
        cfg = json.loads(argv[1]) if len(argv) > 1 else {}
    except ValueError as e:
        emit({"error": f"bad settings: {e}"})
        return 2
    try:
        import cv2  # type: ignore
        import mediapipe as mp  # type: ignore
        import numpy as np  # type: ignore
        from mediapipe.tasks.python import BaseOptions, vision  # type: ignore

        from ally import gaze_model
    except Exception as e:
        emit({"error": f"eye tracking needs mediapipe and opencv-python ({type(e).__name__}: {e})"[:300]})
        return 3

    gaze_on, signals_on = bool(cfg.get("gaze", True)), bool(cfg.get("signals", False))
    feature_set = feature_set_name(cfg.get("features", "v1"))
    click_learning = bool(cfg.get("click_learning", True))
    if signals_on:
        from ally.senses import face as face_signals
    size = screen_size()
    calib_path = Path(cfg.get("calibration", "gaze_calibration.json"))
    camera = int(cfg.get("camera", 0))
    model = kept_model(gaze_model.load(calib_path, size), camera)
    corrector = gaze_model.Corrector(model) if model is not None and click_learning else None
    recent = RecentFeatures()
    try:
        landmarker = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(cfg["model"])), running_mode=vision.RunningMode.VIDEO,
            num_faces=NUM_FACES, output_face_blendshapes=True, output_facial_transformation_matrixes=True))
    except Exception as e:
        emit({"error": f"couldn't load the face model: {e}"[:300]})
        return 3
    cap = open_camera(camera)
    if cap is None:
        emit({"error": "can't open the camera. Check Settings > Privacy & security > Camera (let desktop apps "
                       "use it) and that no other app is using it."})
        return 4

    indicator = CameraIndicator()
    indicator.open(cv2, size)  # up before the first frame, so it is visible within a second of the camera opening
    cmds: queue.Queue = queue.Queue()

    def read_stdin() -> None:
        try:
            for line in sys.stdin:
                cmds.put(line.strip())
        except (OSError, ValueError):
            pass
        cmds.put("quit")

    threading.Thread(target=read_stdin, daemon=True).start()
    emit({"ready": True, "calibrated": model is not None, "screen": list(size),
          "error_px": (model or {}).get("error_px")})

    sig_period = 1.0 / max(2, min(30, int(cfg.get("signals_fps", 10))))
    period = 1.0 / max(5, min(30, int(cfg.get("fps", 20)))) if gaze_on else sig_period
    last_sig, hud, hud_rows = 0.0, False, []
    smooth = gaze_model.OneEuro()
    calib: Calibration | None = None
    card: CardWindow | None = None
    choices: ChoiceBoard | None = None
    from ally.gaze_dot import GazeDot
    dot = GazeDot()
    dot.set_enabled(bool(cfg.get("show_dot", False)))
    view = [1.0, 0, 0]
    t_start = time.monotonic()
    last_face_msg, misses = 0.0, 0
    while True:
        try:
            cmd = cmds.get_nowait()
        except queue.Empty:
            cmd = None
        try:
            kind, payload = classify_command(cmd or "")
        except Exception:
            kind, payload = "ignore", None
        if kind == "quit":
            break
        if kind == "stop" and calib is not None:
            emit({"calibrated": False, "why": "cancelled"})
            calib.close()
            calib = None
            smooth = gaze_model.OneEuro()
        try:
            if kind == "calibrate" and calib is None:
                poses = 3 if payload is None else pose_count(payload)
                calib = Calibration(size, poses=poses)
                calib.speak = True
            elif kind == "check" and calib is None:
                if model is None:
                    emit({"calibrated": False, "why": "The eye tracker needs a setup first."})
                else:
                    calib = Calibration(size)
                    calib.begin_check()
            elif kind == "dot":
                dot.set_enabled(bool(payload))
            elif kind == "view" and payload is not None:
                view[0], view[1], view[2] = payload
                dot.set_view(*view)
            elif kind == "reload":
                loaded, new_corrector = adopt_model(calib_path, size, camera, click_learning)
                if loaded is not None:
                    model = loaded
                    corrector = new_corrector
                    recent = RecentFeatures()
            elif kind == "click" and corrector is not None and calib is None:
                apply_click(corrector, recent.frames, payload[0], payload[1], payload[2])
            elif kind == "hud_off":
                if hud:
                    try:
                        cv2.destroyWindow(HUD)
                        cv2.waitKey(1)
                    except Exception:
                        pass
                hud = False
            elif kind == "hud_on":
                hud = True
            elif kind == "hud_rows":
                hud = True
                hud_rows = payload
            elif kind == "card_off":
                if card is not None:
                    card.close()
                    card = None
            elif kind == "choose_off":
                if choices is not None:
                    choices.close()
                    choices = None
            elif kind == "choose" and calib is None:
                if choices is not None:
                    choices.close()
                choices = ChoiceBoard(cv2, payload, size)
            elif kind == "card":
                if card is not None:
                    card.close()
                    card = None
                card = CardWindow(cv2, payload, size)
        except Exception:
            pass
        if indicator.pump():
            emit({"camera_off": True})
            break
        tick = time.monotonic()
        ok, frame = cap.read()
        if not ok:
            misses += 1
            if misses > 100:
                emit({"error": "the camera stopped sending pictures"})
                if card is not None:
                    card.close()
                if choices is not None:
                    choices.close()
                indicator.close()
                dot.close()
                cap.release()
                try:
                    cv2.destroyAllWindows()
                except Exception:
                    pass
                return 1
            time.sleep(0.05)
            continue
        misses = 0
        # A new calibration is fit with [gaze] features. A check, and ordinary tracking, use the
        # saved model's own set, so a v1 file still predicts when features is already v2.
        checking = calib is not None and getattr(calib, "check_only", False)
        if calib is not None and not checking:
            probe = {"features": feature_set}
        else:
            probe = model or {"features": feature_set}
        seen = look(landmarker, mp, cv2, frame, int((tick - t_start) * 1000), probe)
        feat, lms, bs, mat = seen["feat"], seen["lms"], seen["bs"], seen["mat"]
        face, w, h = seen["face"], seen["w"], seen["h"]
        now = time.time()
        if signals_on and lms is not None and now - last_sig >= sig_period:
            last_sig = now
            sig = face_signals.signals(lms, bs, mat, w, h, seen.get("light"))
            if sig is not None:
                emit({"t": round(now, 3), "f": sig})
        if hud:
            draw_hud(cv2, frame, hud_rows)
        if card is not None:
            try:
                if not card.step():
                    card = None
            except Exception:
                try:
                    card.close()
                except Exception:
                    pass
                card = None
        if choices is not None:
            try:
                if not choices.step():
                    choices = None
            except Exception:
                try:
                    choices.close()
                except Exception:
                    pass
                choices = None

        if calib is not None:
            dot.update(face=False, calibrated=False)  # the dots on screen are the thing to look at
            state = calib.step(feat, time.monotonic(), face, now)
            if state == "fit":
                try:
                    fitted = attach_screen_mm(gaze_model.fit(calib.usable(), size, feature_set))
                    calib.accept_fit(fitted)
                except (ValueError, np.linalg.LinAlgError) as e:
                    result = {"calibrated": False, "why": f"I {e}. Try again with more light on your face."}
                    emit(result)
                    calib.finish(result, ["Calibration didn't work.", result["why"]])
            elif state == "score":
                check_only = getattr(calib, "check_only", False)
                scoring = model if check_only else calib.model
                samples = calib.validation_samples()
                if scoring is None:
                    result = {"calibrated": False, "why": "The eye tracker needs a setup first."}
                    lines = ["The eye tracker needs a setup first."]
                elif check_only:
                    # The 5 dots re-tune the saved model. check_eye_tracker does the
                    # retune and the save, then sends "reload" so this process uses it.
                    try:
                        preview = retune_check(scoring, samples)
                        score = dict(preview["validation"] or {})
                        score["error_px"] = preview["error_after"]
                        grade = preview["grade"]
                    except Exception:
                        preview = None
                        score = {"error_px": None, "region_hits": 0, "region_total": 0}
                        grade = result_grade(score)
                    cause = likely_cause(calib.cause_stats())
                    lines = result_lines(grade, score, cause)
                    sentence = None if preview is None else retune_sentence(
                        preview.get("error_before"), preview.get("error_after"))
                    if sentence:
                        lines.append(sentence)
                    result = {"calibrated": True, "error_px": scoring.get("error_px"),
                              "dots": scoring.get("dots"), "validation": score, "grade": grade,
                              "lines": lines, "cause": cause, "check": True,
                              "error_before": None if preview is None else preview.get("error_before"),
                              "error_after": None if preview is None else preview.get("error_after"),
                              "samples": public_samples(samples)}
                else:
                    try:
                        score = validation_score(scoring, samples, size)
                    except Exception:
                        score = {"error_px": None, "region_hits": 0, "region_total": 0}
                    cause = likely_cause(calib.cause_stats())
                    grade = result_grade(score)
                    lines = result_lines(grade, score, cause)
                    checked = with_check(scoring, score, camera)
                    result = {"calibrated": True, "error_px": checked.get("error_px"), "dots": checked.get("dots"),
                              "validation": checked["validation"], "grade": grade, "lines": lines, "cause": cause}
                    try:
                        gaze_model.save(checked, calib_path)
                        model = checked
                        corrector = gaze_model.Corrector(model) if click_learning else None
                        recent = RecentFeatures()
                    except OSError as e:
                        result = {"calibrated": False, "why": f"couldn't save the calibration ({e})"}
                        lines = ["Calibration didn't work.", result["why"]]
                        result.pop("grade", None)
                emit(result)
                calib.finish(result, lines)
            elif state in ("done", "cancel"):
                if state == "cancel":
                    emit({"calibrated": False, "why": "cancelled"})
                calib.close()
                calib = None
                smooth = gaze_model.OneEuro()
            continue

        if feat is None or feat[1] < MIN_OPEN:
            if feat is None:
                dot.update(face=False, calibrated=model is not None)
            if now - last_face_msg > 1.0 and feat is None:
                emit({"t": now, "face": False})
                last_face_msg = now
        elif model is None or not gaze_on:
            dot.update(face=True, calibrated=False)
            if now - last_face_msg > 0.5:
                emit({"t": now, "face": True})
                last_face_msg = now
        else:
            vector = feat[0]
            if corrector is not None:
                recent.add(now, vector)
                x, y = corrector.predict(vector)
                radius_model = corrector.model
            else:
                x, y = gaze_model.predict(model, vector)
                radius_model = model
            x = min(max(x, -0.05 * size[0]), 1.05 * size[0])
            y = min(max(y, -0.05 * size[1]), 1.05 * size[1])
            sx, sy = smooth(np.array([x, y]), now)
            px = int(min(max(sx, 0), size[0] - 1))
            py = int(min(max(sy, 0), size[1] - 1))
            radius = round(float(gaze_model.radius_px(radius_model, vector)), 1)
            emit({"t": round(now, 3), "x": px, "y": py, "r": radius})
            dot.set_view(*view)
            dot.update(px, py, radius, face=True, calibrated=True)
        spare = period - (time.monotonic() - tick)
        if spare > 0:
            time.sleep(spare)
    if card is not None:
        card.close()
    if choices is not None:
        choices.close()
    indicator.close()
    dot.close()
    cap.release()
    try:
        cv2.destroyAllWindows()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
