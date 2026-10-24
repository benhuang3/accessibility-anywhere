"""Separate process: webcam eye tracking. Prints one JSON object per line:

    {"ready": true, "calibrated": false}         camera and face model work
    {"t": 1730000000.1, "x": 812, "y": 403}      where the user looks (screen pixels; when calibrated)
    {"t": ..., "face": true} / {"face": false}   face seen but not calibrated / no face (a few per second)
    {"calibrated": true, "error_px": 140}        calibration done ({"calibrated": false, "why": "..."} if not)
    {"t": ..., "f": {"squint_l": 0.21, ...}}     face numbers for struggle sensing (with "signals": true)
    {"error": "..."}                             a problem; the process then exits

Settings "gaze" (default true) turns gaze points on; "signals" sends face numbers (ally.senses.face) at
"signals_fps". Frames never leave this process and are never written to disk.

Commands on stdin: "calibrate"; "hud on" / "hud off" show a live senses window, and "hud [[text, level], ...]"
sets its rows. It exits when the agent closes its stdin (like the hotkey helper),
so a camera or MediaPipe crash can never take Ally down. The camera is only ever used here.
Usage: python -m ally.gaze_helper '{"model": "...", "calibration": "...", "camera": 0, "fps": 20}'
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
from pathlib import Path

WINDOW = "Ally eye tracker setup"
HUD = "Ally senses (only on this screen; nothing is saved)"
HUD_COLORS = {"ok": (225, 225, 225), "warn": (0, 200, 255), "hot": (80, 80, 255)}
DOTS = [(0.5, 0.5), (0.08, 0.1), (0.5, 0.1), (0.92, 0.1), (0.92, 0.5), (0.92, 0.9), (0.5, 0.9),
        (0.08, 0.9), (0.08, 0.5)]
INTRO, SETTLE, COLLECT, RESULT = 3.0, 0.9, 1.2, 2.0  # seconds
MIN_OPEN = 0.12  # eye openness below this is a blink: skip the frame


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


class Calibration:
    """Full-screen dots. The user looks at each one; frames recorded at each dot train the model."""

    def __init__(self, size: tuple[int, int]):
        import cv2  # type: ignore
        import numpy as np  # type: ignore

        self.cv2, self.np = cv2, np
        self.w, self.h = size
        self.start = time.monotonic()
        self.samples: list[tuple[int, list[float], tuple[float, float]]] = []  # (dot, features, dot position)
        self.per_dot = [0] * len(DOTS)
        self.last_face = time.monotonic()
        self.result: dict | None = None
        self.result_at = 0.0
        self.base = np.full((self.h, self.w, 3), 32, dtype=np.uint8)
        cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
        cv2.setWindowProperty(WINDOW, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
        try:
            cv2.setWindowProperty(WINDOW, cv2.WND_PROP_TOPMOST, 1)
        except Exception:
            pass

    def _text(self, img, text: str, y: float, scale: float = 1.0) -> None:
        cv2 = self.cv2
        s = max(0.8, self.h / 700) * scale
        th = max(2, int(s * 2))
        (tw, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, s, th)
        cv2.putText(img, text, ((self.w - tw) // 2, int(self.h * y)), cv2.FONT_HERSHEY_SIMPLEX, s,
                    (235, 235, 235), th, cv2.LINE_AA)

    def step(self, feat, now: float) -> str:
        """Draw one frame and record feat. Returns "running", "fit" (all dots done), "done" or "cancel"."""
        cv2 = self.cv2
        img = self.base.copy()
        t = now - self.start
        state = "running"
        if feat is not None:
            self.last_face = now
        if self.result is not None:
            if self.result.get("calibrated"):
                self._text(img, "Eye tracker ready", 0.45, 1.4)
                self._text(img, f"Accuracy: about {self.result['error_px']} pixels", 0.56)
            else:
                self._text(img, "Calibration didn't work", 0.45, 1.4)
                self._text(img, self.result.get("why", "")[:70], 0.56)
            if now - self.result_at > RESULT:
                state = "done"
        elif t < INTRO:
            self._text(img, "Eye tracker setup", 0.38, 1.6)
            self._text(img, "Look at each dot until it moves.", 0.5)
            self._text(img, "Keep your head still.   Esc cancels.", 0.58, 0.8)
        else:
            i, phase = divmod(t - INTRO, SETTLE + COLLECT)
            i = int(i)
            if i >= len(DOTS):
                state = "fit"
            else:
                x, y = int(DOTS[i][0] * self.w), int(DOTS[i][1] * self.h)
                if phase < SETTLE:  # a ring that shrinks onto the dot draws the eyes in
                    r = int(14 + 40 * (1 - phase / SETTLE))
                    cv2.circle(img, (x, y), r, (230, 230, 230), 3, cv2.LINE_AA)
                elif feat is not None and feat[1] >= MIN_OPEN:
                    self.samples.append((i, feat[0], (float(x), float(y))))
                    self.per_dot[i] += 1
                cv2.circle(img, (x, y), 12, (0, 140, 255), -1, cv2.LINE_AA)
                cv2.circle(img, (x, y), 3, (255, 255, 255), -1, cv2.LINE_AA)
                if now - self.last_face > 1.0:
                    self._text(img, "I can't see your eyes. Face the screen, with light on your face.", 0.95, 0.7)
        cv2.imshow(WINDOW, img)
        if (cv2.waitKey(1) & 0xFF) == 27:
            return "cancel"
        return state

    def usable(self) -> list[tuple[list[float], tuple[float, float]]]:
        """Frames from dots where the eyes were seen long enough (8+ frames, about a third of a second)."""
        return [(f, t) for i, f, t in self.samples if self.per_dot[i] >= 8]

    def finish(self, result: dict) -> None:
        self.result, self.result_at = result, time.monotonic()

    def close(self) -> None:
        try:
            self.cv2.destroyWindow(WINDOW)
            self.cv2.waitKey(1)
        except Exception:
            pass


def draw_hud(cv2, frame, rows: list) -> None:
    """The camera picture (mirrored, like a mirror) with each signal's numbers on top."""
    img = cv2.flip(frame, 1)
    band = 14 + 20 * len(rows)
    img[:band] = (img[:band] * 0.35).astype(img.dtype)
    for i, row in enumerate(rows):
        text, level = (row + ["ok"])[:2] if isinstance(row, list) else (str(row), "ok")
        cv2.putText(img, str(text)[:90], (8, 22 + 20 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                    HUD_COLORS.get(level, HUD_COLORS["ok"]), 1, cv2.LINE_AA)
    cv2.imshow(HUD, img)
    cv2.waitKey(1)


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
    if signals_on:
        from ally.senses import face as face_signals
    size = screen_size()
    calib_path = Path(cfg.get("calibration", "gaze_calibration.json"))
    model = gaze_model.load(calib_path, size)
    try:
        landmarker = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(cfg["model"])), running_mode=vision.RunningMode.VIDEO,
            num_faces=1, output_face_blendshapes=True, output_facial_transformation_matrixes=True))
    except Exception as e:
        emit({"error": f"couldn't load the face model: {e}"[:300]})
        return 3
    cap = open_camera(int(cfg.get("camera", 0)))
    if cap is None:
        emit({"error": "can't open the camera. Check Settings > Privacy & security > Camera (let desktop apps "
                       "use it) and that no other app is using it."})
        return 4

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
    t_start = time.monotonic()
    last_face_msg, misses = 0.0, 0
    while True:
        try:
            cmd = cmds.get_nowait()
        except queue.Empty:
            cmd = None
        if cmd == "quit":
            break
        if cmd == "calibrate" and calib is None:
            calib = Calibration(size)
        elif cmd and cmd.startswith("hud"):
            arg = cmd[3:].strip()
            if arg == "off" and hud:
                try:
                    cv2.destroyWindow(HUD)
                    cv2.waitKey(1)
                except Exception:
                    pass
            hud = arg != "off"
            if arg.startswith("["):
                try:
                    hud_rows = json.loads(arg)
                except ValueError:
                    pass
        tick = time.monotonic()
        ok, frame = cap.read()
        if not ok:
            misses += 1
            if misses > 100:
                emit({"error": "the camera stopped sending pictures"})
                return 1
            time.sleep(0.05)
            continue
        misses = 0
        h, w = frame.shape[:2]
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        res = landmarker.detect_for_video(image, int((tick - t_start) * 1000))
        feat = None
        if res.face_landmarks:
            lms = [(p.x, p.y, p.z) for p in res.face_landmarks[0]]
            bs = {c.category_name: c.score for c in res.face_blendshapes[0]} if res.face_blendshapes else None
            mat = res.facial_transformation_matrixes[0] if res.facial_transformation_matrixes else None
            feat = gaze_model.features(lms, bs, mat, w, h)
        now = time.time()
        if signals_on and res.face_landmarks and now - last_sig >= sig_period:
            last_sig = now
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            light = face_signals.brightness(gray, face_signals.face_box(np.asarray(lms), w, h))
            sig = face_signals.signals(lms, bs, mat, w, h, light)
            if sig is not None:
                emit({"t": round(now, 3), "f": sig})
        if hud:
            draw_hud(cv2, frame, hud_rows)

        if calib is not None:
            state = calib.step(feat, time.monotonic())
            if state == "fit":
                try:
                    model = gaze_model.fit(calib.usable(), size)
                    gaze_model.save(model, calib_path)
                    result = {"calibrated": True, "error_px": model["error_px"], "dots": model["dots"]}
                except (ValueError, np.linalg.LinAlgError) as e:
                    result = {"calibrated": False, "why": f"I {e}. Try again with more light on your face."}
                emit(result)
                calib.finish(result)
            elif state in ("done", "cancel"):
                if state == "cancel":
                    emit({"calibrated": False, "why": "cancelled"})
                calib.close()
                calib = None
                smooth = gaze_model.OneEuro()
            continue

        if feat is None or feat[1] < MIN_OPEN:
            if now - last_face_msg > 1.0 and feat is None:
                emit({"t": now, "face": False})
                last_face_msg = now
        elif model is None or not gaze_on:
            if now - last_face_msg > 0.5:
                emit({"t": now, "face": True})
                last_face_msg = now
        else:
            x, y = gaze_model.predict(model, feat[0])
            x = min(max(x, -0.05 * size[0]), 1.05 * size[0])
            y = min(max(y, -0.05 * size[1]), 1.05 * size[1])
            sx, sy = smooth(np.array([x, y]), now)
            emit({"t": round(now, 3), "x": int(min(max(sx, 0), size[0] - 1)), "y": int(min(max(sy, 0), size[1] - 1))})
        spare = period - (time.monotonic() - tick)
        if spare > 0:
            time.sleep(spare)
    cap.release()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
