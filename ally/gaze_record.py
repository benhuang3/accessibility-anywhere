"""Record a gaze session while Ally is stopped. Numbers only; frames are never saved.

    python -m ally.gaze_record --person ALIAS --glasses yes|no --light bright|dim

The same setup as "Ally, set up my eyes" (readiness, 13 dots, then 5 sitting back and 5
leaning in when calibration_poses is 3, then 5 check dots), then about
2 minutes of reading, then 5 check dots again (drift). Writes
state/gaze_runs/<run>/<alias>-<glasses>-<light>.jsonl.

Stop Ally first (`python run.py stop`): only one program can use the camera.
Ctrl+C keeps whatever has already been written.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

READING_SECONDS = 120.0
TOKEN = re.compile(r"[A-Za-z0-9_-]{1,40}")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_IMAGE_KEYS = {"frame", "image", "jpeg", "jpg", "png", "video", "pixels", "data", "bmp", "raw", "png_b64", "b64"}
_MAX_STR = 200

# MediaPipe Face Landmarker scores, all 52. Missing ones are written as 0.
BLENDSHAPES = (
    "_neutral",
    "browDownLeft", "browDownRight", "browInnerUp", "browOuterUpLeft", "browOuterUpRight",
    "cheekPuff", "cheekSquintLeft", "cheekSquintRight",
    "eyeBlinkLeft", "eyeBlinkRight",
    "eyeLookDownLeft", "eyeLookDownRight",
    "eyeLookInLeft", "eyeLookInRight",
    "eyeLookOutLeft", "eyeLookOutRight",
    "eyeLookUpLeft", "eyeLookUpRight",
    "eyeSquintLeft", "eyeSquintRight",
    "eyeWideLeft", "eyeWideRight",
    "jawForward", "jawLeft", "jawOpen", "jawRight",
    "mouthClose",
    "mouthDimpleLeft", "mouthDimpleRight",
    "mouthFrownLeft", "mouthFrownRight",
    "mouthFunnel",
    "mouthLeft",
    "mouthLowerDownLeft", "mouthLowerDownRight",
    "mouthPressLeft", "mouthPressRight",
    "mouthPucker",
    "mouthRight",
    "mouthRollLower", "mouthRollUpper",
    "mouthShrugLower", "mouthShrugUpper",
    "mouthSmileLeft", "mouthSmileRight",
    "mouthStretchLeft", "mouthStretchRight",
    "mouthUpperUpLeft", "mouthUpperUpRight",
    "noseSneerLeft", "noseSneerRight",
)

READING_TEXT = (
    "The window faces a quiet street. A bus stops, the doors open, and two people step off "
    "with a paper bag between them. They wait for the light, then cross. A dog sits by the "
    "bakery door and watches them go.\n\n"
    "Inside, the kettle has just boiled. The mug is the blue one, with a chip on the handle. "
    "There is bread on the board, a knife beside it, and a note that says back by four. The "
    "clock on the wall is a minute fast. The chair by the table is pulled out, as if someone "
    "stood up in a hurry and meant to come back.\n\n"
    "On the shelf, a row of books leans to the left. The top one is a map of the coast, folded "
    "so many times that the creases are pale. Next to it is a glass jar of pencils, most of "
    "them short. A postcard is tucked under the jar. The picture is a harbor at dusk, and the "
    "writing on the back is too small to read from the chair.\n\n"
    "Rain starts, light at first. The street gets darker and the bakery light looks warmer. "
    "The dog stands, shakes, and moves under the awning. A cyclist rides past with one hand "
    "up, not waving at anyone in particular. The bus is gone. The bag is gone. The note still "
    "says back by four.\n\n"
    "Read this page at your usual pace. When you finish, start again at the top. Sit as you "
    "usually sit. You can click if you want to; a click is written down as two numbers, the "
    "place and the time. The camera picture is not saved."
)


def ally_is_running(paths=None) -> bool:
    """True when an Ally agent is accepting connections. Uses the supervisor's own check."""
    from .paths import Paths
    from .supervisor import current_port, listening

    paths = paths or Paths()
    try:
        return bool(listening(current_port(paths)))
    except (OSError, KeyError, TypeError, ValueError):
        return False


def git_commit() -> str:
    from .paths import CODE_ROOT

    try:
        cp = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(CODE_ROOT), capture_output=True,
                            text=True, timeout=5, creationflags=_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return cp.stdout.strip() if cp.returncode == 0 else ""


def image_like(value, key=None) -> bool:
    """True when a value could carry a picture. Landmark lists of numbers are not pictures."""
    if isinstance(key, str) and key.lower() in _IMAGE_KEYS:
        return True
    if isinstance(value, (bytes, bytearray, memoryview)):
        return True
    if isinstance(value, str):
        low = value.lower()
        return len(value) > _MAX_STR or "base64" in low or low.startswith("data:image")
    if isinstance(value, dict):
        return any(image_like(item, name) for name, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(image_like(item, None) for item in value)
    return False


def flat_matrix(matrix) -> list[float] | None:
    if matrix is None:
        return None
    if hasattr(matrix, "tolist"):
        matrix = matrix.tolist()
    try:
        if isinstance(matrix, (list, tuple)) and matrix and isinstance(matrix[0], (list, tuple)):
            flat = [float(x) for row in matrix for x in row]
        else:
            flat = [float(x) for x in matrix]
    except (TypeError, ValueError):
        return None
    if len(flat) != 16:
        return None
    return [round(x, 5) for x in flat]


def _point(p) -> tuple[float, float, float] | None:
    try:
        if hasattr(p, "x"):
            return float(p.x), float(p.y), float(getattr(p, "z", 0.0))
        z = float(p[2]) if len(p) > 2 else 0.0
        return float(p[0]), float(p[1]), z
    except (TypeError, ValueError, IndexError):
        return None


def frame_record(t, w, h, landmarks, blendshapes, matrix) -> dict | None:
    """One face, as numbers. None when there are not 478 landmarks."""
    if landmarks is None:
        return None
    try:
        pts = list(landmarks)
    except TypeError:
        return None
    if len(pts) < 478:
        return None
    lm = []
    for p in pts[:478]:
        got = _point(p)
        if got is None:
            return None
        lm.append([round(got[0], 5), round(got[1], 5), round(got[2], 5)])
    raw = blendshapes or {}
    try:
        bs = {name: round(float(raw.get(name, 0.0) or 0.0), 4) for name in BLENDSHAPES}
    except (TypeError, ValueError):
        return None
    return {"rec": "frame", "t": float(t), "w": int(w), "h": int(h), "lm": lm, "bs": bs,
            "mat": flat_matrix(matrix)}


def target_record(event: dict) -> dict | None:
    phase = event.get("phase")
    if phase not in ("calibration", "validation", "drift"):
        return None
    try:
        row = {"rec": "target", "phase": phase, "id": int(event["id"]), "x": int(event["x"]),
               "y": int(event["y"]), "shown": float(event["shown"]), "start": float(event["start"]),
               "end": float(event["end"])}
    except (KeyError, TypeError, ValueError):
        return None
    pose = event.get("pose")
    if pose in ("normal", "back", "close"):
        row["pose"] = pose
    return row


def meta_record(alias: str, glasses: str, light: str, screen, camera: int, commit: str, created: float,
                screen_mm=None) -> dict:
    row = {"rec": "meta", "alias": alias, "glasses": glasses, "light": light,
           "screen": [int(screen[0]), int(screen[1])], "camera": int(camera),
           "commit": str(commit or ""), "created": float(created)}
    if screen_mm:
        try:
            row["screen_mm"] = [int(screen_mm[0]), int(screen_mm[1])]
        except (TypeError, ValueError, IndexError):
            pass
    return row


def click_record(t, x, y) -> dict:
    return {"rec": "click", "t": float(t), "x": int(x), "y": int(y)}


class RunLog:
    """Append-only JSONL. Lines that could carry a picture are dropped."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._fh = None

    def open(self) -> None:
        if self._fh is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")

    def write(self, obj) -> bool:
        if not isinstance(obj, dict) or image_like(obj):
            return False
        self.open()
        self._fh.write(json.dumps(obj, ensure_ascii=True) + "\n")
        self._fh.flush()
        return True

    def close(self) -> None:
        if self._fh is None:
            return
        try:
            self._fh.close()
        except OSError:
            pass
        self._fh = None


def gaze_run_path(state_dir: Path, run: str, alias: str, glasses: str, light: str) -> Path:
    folder = Path(state_dir) / "gaze_runs" / run
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{alias}-{glasses}-{light}"
    path = folder / f"{stem}.jsonl"
    n = 2
    while path.exists() and path.stat().st_size > 0:
        path = folder / f"{stem}-{n}.jsonl"
        n += 1
    return path


def run_name(run: str) -> str:
    if run and TOKEN.fullmatch(run):
        return run
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def calibration_poses(paths) -> int:
    """How many head positions a recording collects. Same setting as Ally's setup."""
    try:
        from .config import ConfigManager
        from .gaze_helper import pose_count

        cm = ConfigManager(paths.config)
        cm.reload()
        return pose_count((cm.data.get("gaze") or {}).get("calibration_poses", 3))
    except (OSError, KeyError, TypeError, ValueError):
        return 3


def camera_index(paths, camera: int | None) -> int:
    if camera is not None:
        return int(camera)
    try:
        from .config import ConfigManager

        cm = ConfigManager(paths.config)
        cm.reload()
        return int((cm.data.get("gaze") or {}).get("camera", 0))
    except (OSError, KeyError, TypeError, ValueError):
        return 0


class ClickWatch:
    """A mouse click, as a screen point. No keys."""

    def __init__(self):
        self._down = False
        self.pending: list[tuple[int, int]] = []

    def add(self, x: int, y: int) -> None:
        self.pending.append((int(x), int(y)))

    def poll(self) -> tuple[int, int] | None:
        if sys.platform != "win32" and self.pending:
            return self.pending.pop(0)
        if sys.platform != "win32":
            return None
        try:
            import ctypes

            down = bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)
        except Exception:
            return None
        if down and not self._down:
            self._down = True
            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

            pt = POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            return int(pt.x), int(pt.y)
        if not down:
            self._down = False
        return None


class Reading:
    """A full-screen page. Esc stops the session. The picture of the page is not saved."""

    NAME = "Ally reading"

    def __init__(self, cv2, screen: tuple[int, int]):
        import numpy as np

        self.cv2 = cv2
        self.np = np
        self.w, self.h = max(int(screen[0]), 1), max(int(screen[1]), 1)
        self.cancelled = False
        self.alive = True
        cv2.namedWindow(self.NAME, cv2.WINDOW_NORMAL)
        cv2.setWindowProperty(self.NAME, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
        try:
            cv2.setWindowProperty(self.NAME, cv2.WND_PROP_TOPMOST, 1)
        except Exception:
            pass
        self._page = self._render()
        self.step()

    def _render(self):
        cv2, np = self.cv2, self.np
        img = np.full((self.h, self.w, 3), 255, dtype=np.uint8)
        scale, thick = 0.9, 2
        y = 80
        for paragraph in READING_TEXT.split("\n"):
            if not paragraph.strip():
                y += 24
                continue
            for line in _wrap(cv2, paragraph, scale, thick, self.w - 120):
                if y > self.h - 40:
                    return img
                cv2.putText(img, line, (60, y), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick, cv2.LINE_AA)
                y += 42
        return img

    def step(self) -> bool:
        if not self.alive:
            return False
        self.cv2.imshow(self.NAME, self._page)
        if (self.cv2.waitKey(1) & 0xFF) == 27:
            self.cancelled = True
            return False
        return True

    def close(self) -> None:
        if not self.alive:
            return
        self.alive = False
        try:
            self.cv2.destroyWindow(self.NAME)
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


def _on_target(log: RunLog, event: dict) -> None:
    row = target_record(event)
    if row is not None:
        log.write(row)


def _live(log: RunLog, *, model_path: Path, camera: int, screen: tuple[int, int],
          alias: str, glasses: str, light: str, commit: str, created: float,
          poses: int = 3, screen_mm=None) -> int:
    cap = indicator = calib = reading = None
    cv2 = None
    try:
        import cv2  # type: ignore
        import mediapipe as mp  # type: ignore
        from mediapipe.tasks.python import BaseOptions, vision  # type: ignore

        from ally import gaze_model
        from ally.gaze_helper import (NUM_FACES, Calibration, CameraIndicator, likely_cause, look,
                                      open_camera, pose_count, result_grade, result_lines, screen_mm as read_screen_mm,
                                      validation_score)
    except Exception as e:
        print(f"Eye tracking needs mediapipe and opencv-python ({type(e).__name__}: {e})")
        return 3

    cap = open_camera(int(camera))
    if cap is None:
        print("Can't open the camera. Close anything else using it, then try again.")
        return 4
    try:
        landmarker = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(model_path)), running_mode=vision.RunningMode.VIDEO,
            num_faces=NUM_FACES, output_face_blendshapes=True, output_facial_transformation_matrixes=True))
    except Exception as e:
        print(f"Couldn't load the face model: {e}")
        cap.release()
        return 3

    log.open()
    if screen_mm is None:
        screen_mm = read_screen_mm()
    poses = pose_count(poses)
    log.write(meta_record(alias, glasses, light, screen, camera, commit, created, screen_mm=screen_mm))
    print(f"Recording to {log.path}")
    if poses <= 1:
        print("Look at each dot. Esc cancels. Ctrl+C keeps what was recorded.")
    else:
        print("Look at each dot. Then sit back a little, and lean in a little. Esc cancels.")
    indicator = CameraIndicator()
    indicator.open(cv2, screen)
    calib = Calibration(screen, on_target=lambda event: _on_target(log, event), poses=poses)
    clicks = ClickWatch()
    phase = "setup"
    read_until = 0.0
    t_start = time.monotonic()
    period = 1.0 / 20.0
    misses = 0
    code = 0
    try:
        while phase != "end":
            if indicator.pump():
                print("Camera turned off. Kept what was recorded.")
                return 0
            tick = time.monotonic()
            ok, frame = cap.read()
            if not ok:
                misses += 1
                if misses > 100:
                    print("The camera stopped sending pictures. Kept what was recorded.")
                    return 1
                time.sleep(0.05)
                continue
            misses = 0
            wall = time.time()
            seen = look(landmarker, mp, cv2, frame, int((tick - t_start) * 1000))
            row = frame_record(round(wall, 3), seen["w"], seen["h"], seen["lms"], seen["bs"], seen["mat"])
            if row is not None:
                log.write(row)
            mono = time.monotonic()
            if phase == "setup":
                state = calib.step(seen["feat"], mono, seen["face"], wall)
                if state == "fit":
                    try:
                        calib.accept_fit(gaze_model.fit(calib.usable(), screen))
                    except Exception as e:
                        why = f"I {e}. Try again with more light on your face."
                        calib.finish({"calibrated": False, "why": why}, ["Calibration didn't work.", why])
                elif state == "score":
                    try:
                        score = validation_score(calib.model, calib.validation_samples(), screen)
                    except Exception:
                        score = {"error_px": None, "region_hits": 0, "region_total": 0}
                    lines = result_lines(result_grade(score), score, likely_cause(calib.cause_stats()))
                    print(" ".join(lines))
                    calib.finish({"calibrated": True, "error_px": (calib.model or {}).get("error_px"),
                                  "dots": (calib.model or {}).get("dots")}, lines)
                elif state == "cancel":
                    print("Cancelled. Kept what was recorded.")
                    return 0
                elif state == "done":
                    calib.close()
                    phase = "read"
                    read_until = time.monotonic() + READING_SECONDS
                    reading = Reading(cv2, screen)
                    print("Read the page. About 2 minutes.")
            elif phase == "read":
                click = clicks.poll()
                if click is not None:
                    log.write(click_record(round(wall, 3), click[0], click[1]))
                if reading is None or not reading.step() or time.monotonic() >= read_until:
                    if reading is not None and reading.cancelled:
                        print("Stopped. Kept what was recorded.")
                        return 0
                    if reading is not None:
                        reading.close()
                        reading = None
                    print("Look at the dots again.")
                    phase = "drift"
                    calib.start_drift()
            elif phase == "drift":
                state = calib.step(seen["feat"], mono, seen["face"], wall)
                if state == "cancel":
                    print("Stopped. Kept what was recorded.")
                    return 0
                if state == "done":
                    phase = "end"
            spare = period - (time.monotonic() - tick)
            if spare > 0:
                time.sleep(spare)
    finally:
        if reading is not None:
            reading.close()
        if calib is not None:
            calib.close()
        if indicator is not None:
            indicator.close()
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
        if cv2 is not None:
            try:
                cv2.destroyAllWindows()
            except Exception:
                pass
    if code == 0:
        print(f"Done. {log.path}")
    return code


def record(person: str, glasses: str, light: str, camera: int | None = None, run: str = "", *,
           paths=None, ally_running=None, root: Path | None = None, model: Path | None = None,
           screen: tuple[int, int] | None = None, commit: str | None = None, created: float | None = None) -> int:
    """Run one recording. Returns a process exit code. Does not open the camera until checks pass."""
    from .paths import Paths

    paths = paths or Paths()
    if not TOKEN.fullmatch(person or ""):
        print("Use a short alias: letters, digits, _ or -.")
        return 2
    if glasses not in ("yes", "no") or light not in ("bright", "dim"):
        print("Glasses must be yes or no, and light must be bright or dim.")
        return 2
    running = ally_is_running(paths) if ally_running is None else bool(ally_running())
    if running:
        print("Ally is using the camera. Run python run.py stop first, then try again.")
        return 2
    model_path = Path(model) if model else (paths.state_dir / "models" / "face_landmarker.task")
    if not model_path.is_file():
        print("The face model isn't here yet (state/models/face_landmarker.task).")
        print("Start Ally once so it can download, run python run.py stop, then try again.")
        return 2
    if screen is None:
        from .gaze_helper import screen_size
        screen = screen_size()
    cam = camera_index(paths, camera)
    state_dir = Path(root) if root is not None else paths.state_dir
    path = gaze_run_path(state_dir, run_name(run), person, glasses, light)
    log = RunLog(path)
    try:
        return _live(log, model_path=model_path, camera=cam, screen=screen, alias=person, glasses=glasses,
                     light=light, commit=git_commit() if commit is None else commit,
                     created=time.time() if created is None else float(created),
                     poses=calibration_poses(paths))
    except KeyboardInterrupt:
        print("\nStopped. Kept what was recorded.")
        return 0
    finally:
        log.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ally.gaze_record")
    parser.add_argument("--person", required=True)
    parser.add_argument("--glasses", required=True, choices=("yes", "no"))
    parser.add_argument("--light", required=True, choices=("bright", "dim"))
    parser.add_argument("--camera", type=int, default=None)
    parser.add_argument("--run", default="", help="folder name under state/gaze_runs (default: a timestamp)")
    args = parser.parse_args(argv)
    return record(args.person, args.glasses, args.light, args.camera, args.run)


if __name__ == "__main__":
    sys.exit(main())
