"""Separate process: webcam eye tracking. Prints one JSON object per line:

    {"ready": true, "calibrated": false}         camera and face model work
    {"t": 1730000000.1, "x": 812, "y": 403}      where the user looks (screen pixels; when calibrated)
    {"t": ..., "face": true} / {"face": false}   face seen but not calibrated / no face (a few per second)
    {"calibrated": true, "error_px": 140}        calibration done ({"calibrated": false, "why": "..."} if not)
    {"t": ..., "f": {"squint_l": 0.21, ...}}     face numbers for struggle sensing (with "signals": true)
    {"camera_off": true}                         the camera-on window was clicked; the camera then closes
    {"error": "..."}                             a problem; the process then exits

Settings "gaze" (default true) turns gaze points on; "signals" sends face numbers (ally.senses.face) at
"signals_fps". Frames never leave this process and are never written to disk.

Commands on stdin: "calibrate"; "hud on" / "hud off" show a live senses window, and "hud [[text, level], ...]"
sets its rows; "card {json}" shows a readable card (text, optional title, size normal|large|tiny, seconds)
and "card off" closes it. The card also closes itself after seconds. Unknown or broken lines are ignored.
It exits when the agent closes its stdin (like the hotkey helper),
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
        if cmd == "calibrate":
            return "calibrate", None
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
        try:
            if kind == "calibrate" and calib is None:
                calib = Calibration(size)
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
                indicator.close()
                cap.release()
                try:
                    cv2.destroyAllWindows()
                except Exception:
                    pass
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
    if card is not None:
        card.close()
    indicator.close()
    cap.release()
    try:
        cv2.destroyAllWindows()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
