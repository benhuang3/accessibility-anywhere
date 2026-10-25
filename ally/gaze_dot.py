"""A thin ring around the spot the eye tracker is using, sized to how sure it is.

The camera helper moves it on every gaze point, so it has no extra lag. The window is layered,
always on top, and click-through: it never takes focus, never blocks a click, and has no taskbar
button. If Win32 is unavailable the circle is skipped and that is logged once.

Nothing here opens a socket or writes a picture. The circle is drawn into a memory bitmap.
"""

from __future__ import annotations

import logging
import math
import sys
import threading
import time

log = logging.getLogger("ally.gaze.dot")

# Big enough to see, small enough that a wide uncertainty does not cover the screen.
DOT_MIN_R = 16.0
DOT_MAX_R = 96.0
SHOWN_ALPHA = 0.9  # crisp while the tracker has a face and a calibration (the ring is thin)
# The ring: a dark line on the edge of the estimate with a faint white edge, and a small centre dot.
RING_HALF = 1.25  # half the dark line's width, pixels
HALO = 1.0  # width of the white edge on each side, pixels
HALO_ALPHA = 0.55
LINE_ALPHA = 0.85
CENTRE_R = 2.5
RING_PAD = 4  # room for the line and its halo inside the bitmap
# Smooth motion: the window is redrawn this often, easing toward each new estimate.
FRAME_HZ = 60
EASE_MOVE = 0.09  # seconds; the ring settles on a new spot in about 0.25 s
EASE_SIZE = 0.25  # the ring's size changes more slowly than its place
EASE_FADE = 0.12
CANVAS = int(2 * (DOT_MAX_R + 2)) + 1  # one window size, so nothing is reallocated while animating
FADE_OUT = 0.35  # seconds to disappear after the face or the calibration is gone

# WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
DOT_EX_STYLE = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW

_logged = False


def log_once(msg: str) -> None:
    """One warning. Repeated failures (every frame) stay quiet."""
    global _logged
    if _logged:
        return
    _logged = True
    log.warning("%s", msg)


def dot_radius(r) -> float:
    """Circle radius from the gaze point's uncertainty, clamped to a visible size."""
    try:
        value = float(r)
    except (TypeError, ValueError):
        return DOT_MIN_R
    if value != value or value in (float("inf"), float("-inf")):
        return DOT_MIN_R
    if value < DOT_MIN_R:
        return DOT_MIN_R
    if value > DOT_MAX_R:
        return DOT_MAX_R
    return value


def dot_point(x, y, level, left, top) -> tuple[float, float]:
    """Where to draw a glass gaze point for the magnified view (level, left, top).

    The fullscreen magnifier stretches the rectangle that starts at (left, top). A marker
    drawn at left + x/level is what that stretch puts under the gaze. At 1x the point stays
    where the eyes are.
    """
    try:
        scale = float(level)
    except (TypeError, ValueError):
        scale = 1.0
    try:
        gx, gy = float(x), float(y)
    except (TypeError, ValueError):
        return 0.0, 0.0
    if not scale or scale <= 1.0:
        return gx, gy
    try:
        return float(left) + gx / scale, float(top) + gy / scale
    except (TypeError, ValueError):
        return gx, gy


class DotState:
    """Position, radius, and fade. No window."""

    def __init__(self):
        self.enabled = False
        self.level = 1.0
        self.left = 0.0
        self.top = 0.0
        self.x = 0.0
        self.y = 0.0
        self.radius = DOT_MIN_R
        self.alpha = 0.0

    def set_view(self, level, left, top) -> None:
        try:
            self.level = float(level)
            self.left = float(left)
            self.top = float(top)
        except (TypeError, ValueError):
            return

    def step(self, face: bool, calibrated: bool, x, y, r, dt: float) -> dict | None:
        """The circle to draw, or None when it should be hidden.

        Turned off: hidden at once. No face or no calibration: the last circle fades out.
        """
        if not self.enabled:
            self.alpha = 0.0
            return None
        show = bool(face and calibrated and x is not None and y is not None)
        if show:
            self.x, self.y = dot_point(x, y, self.level, self.left, self.top)
            self.radius = dot_radius(r)
            self.alpha = SHOWN_ALPHA
            return self._spec()
        if self.alpha <= 0.0:
            return None
        # Linear fade from the shown alpha, so it is gone after FADE_OUT seconds.
        self.alpha = max(0.0, self.alpha - SHOWN_ALPHA * max(0.0, float(dt)) / FADE_OUT)
        if self.alpha <= 0.02:
            self.alpha = 0.0
            return None
        return self._spec()

    def _spec(self) -> dict:
        return {"x": self.x, "y": self.y, "radius": self.radius, "alpha": self.alpha}


class GazeDot:
    """Moves the circle. `painter` stands in for the window in tests."""

    def __init__(self, painter=None, clock=None):
        self.state = DotState()
        self.painter = painter
        self.clock = clock or time.monotonic
        self._last = None
        self._window = None
        self._unavailable = False
        self._lock = threading.Lock()
        self._target = None  # the latest estimate the animator eases toward
        self._cut = False  # hide at once (turned off), no fade
        self._animator = None

    def set_enabled(self, on: bool) -> None:
        self.state.enabled = bool(on)
        if not on:
            self.state.alpha = 0.0
            with self._lock:
                self._cut = True
            self._paint(None)

    def set_view(self, level, left, top) -> None:
        self.state.set_view(level, left, top)

    def update(self, x=None, y=None, r=None, face: bool = True, calibrated: bool = True) -> dict | None:
        now = self.clock()
        if self._last is None:
            dt = 0.0
        else:
            dt = min(0.5, max(0.0, now - self._last))
        self._last = now
        spec = self.state.step(bool(face), bool(calibrated), x, y, r, dt)
        self._paint(spec)
        return spec

    def close(self) -> None:
        animator, self._animator = self._animator, None
        if animator is not None:
            animator.stop.set()
            animator.thread.join(timeout=1.0)
        window, self._window = self._window, None
        if window is not None:
            try:
                window.close()
            except Exception:
                log.debug("gaze dot close failed", exc_info=True)

    def _paint(self, spec) -> None:
        if self.painter is not None:
            draw = getattr(self.painter, "draw", self.painter)
            try:
                draw(spec)
            except Exception:
                log.debug("gaze dot painter failed", exc_info=True)
            return
        if not self._unavailable:
            with self._lock:
                self._target = None if spec is None else dict(spec)
            if spec is not None and self._animator is None:
                self._animator = _Animator(self)
            return
        if spec is None:
            if self._window is not None:
                try:
                    self._window.draw(None)
                except Exception:
                    self._give_up("Eye dot hidden after a window error.")
            return
        if self._unavailable:
            return
        if self._window is None:
            self._window = LayeredDot.open()
            if self._window is None:
                self._unavailable = True
                log_once("Eye dot skipped: no Win32 layered window. The tracker still runs.")
                return
        try:
            self._window.draw(spec)
        except Exception:
            self._give_up("Eye dot skipped after a window error. The tracker still runs.")

    def _give_up(self, msg: str) -> None:
        self._unavailable = True
        log_once(msg)
        self.close()


def _ring(radius: float, alpha: float, fx: float = 0.0, fy: float = 0.0, size: int | None = None):
    """Premultiplied BGRA: a thin dark ring on the edge of the estimate, a small centre dot, and a
    faint white edge on both so they show on dark pages too. Inside the ring stays clear, so the
    marker never hides what the person is looking at. Top row first.

    fx, fy shift the centre by a fraction of a pixel, so a slow glide never steps. `size` fixes the
    square's side (the animated window keeps one size); by default it just fits the ring.
    """
    r = max(RING_PAD + 2.0, float(radius))
    d = int(size) if size else int(math.ceil(r)) * 2 + 1
    c = (d - 1) / 2.0
    peak = max(0.0, min(1.0, float(alpha)))
    ring = r - RING_PAD  # the stroke's centre line, so the halo fits inside the bitmap
    try:
        import numpy as np
    except ImportError:
        np = None
    if np is not None:
        grid = _GRIDS.get(d)
        if grid is None:
            grid = _GRIDS[d] = np.mgrid[0:d, 0:d].astype(np.float32)
        yy, xx = grid
        dist = np.hypot(xx - (c + fx), yy - (c + fy))
        off = np.abs(dist - ring)
        line = np.clip(RING_HALF + 0.5 - off, 0.0, 1.0)
        halo = np.clip(RING_HALF + HALO + 0.5 - off, 0.0, 1.0)
        core = np.clip(CENTRE_R + 0.5 - dist, 0.0, 1.0)
        core_halo = np.clip(CENTRE_R + HALO + 0.5 - dist, 0.0, 1.0)
        dark = np.maximum(line, core)
        light = np.maximum(halo, core_halo) * HALO_ALPHA * (1.0 - dark)
        a = (dark * LINE_ALPHA + light) * peak
        out = np.zeros((d, d, 4), dtype=np.uint8)
        white = np.clip(255.0 * light * peak, 0, 255).astype(np.uint8)  # premultiplied: only the halo adds colour
        out[..., 0] = out[..., 1] = out[..., 2] = white
        out[..., 3] = np.clip(np.rint(255.0 * a), 0, 255).astype(np.uint8)
        out[a <= 0.004] = 0
        return d, out.tobytes()
    buf = bytearray(d * d * 4)
    for y in range(d):
        dy = y - (c + fy)
        row = y * d
        for x in range(d):
            dist = math.hypot(x - (c + fx), dy)
            off = abs(dist - ring)
            line = max(0.0, min(1.0, RING_HALF + 0.5 - off))
            halo = max(0.0, min(1.0, RING_HALF + HALO + 0.5 - off))
            core = max(0.0, min(1.0, CENTRE_R + 0.5 - dist))
            core_halo = max(0.0, min(1.0, CENTRE_R + HALO + 0.5 - dist))
            dark = max(line, core)
            light = max(halo, core_halo) * HALO_ALPHA * (1.0 - dark)
            a = (dark * LINE_ALPHA + light) * peak
            if a <= 0.004:
                continue
            i = (row + x) * 4
            buf[i] = buf[i + 1] = buf[i + 2] = min(255, int(255 * light * peak))
            buf[i + 3] = min(255, int(round(255 * a)))
    return d, bytes(buf)


_GRIDS: dict = {}


def ease(shown: dict | None, target: dict | None, dt: float) -> dict | None:
    """One animation step from what is on screen toward the latest estimate.

    Position, size and fade each ease exponentially (no overshoot, no jumps). A ring that was not
    showing appears in place rather than flying in from its old spot. None once fully faded out.
    """
    dt = max(0.0, min(0.1, float(dt)))
    if target is not None and (shown is None or shown["alpha"] <= 0.02):
        shown = dict(target, alpha=0.0)  # appear where the eyes are
    if shown is None:
        return None
    move = 1.0 - math.exp(-dt / EASE_MOVE)
    grow = 1.0 - math.exp(-dt / EASE_SIZE)
    fade = 1.0 - math.exp(-dt / EASE_FADE)
    out = dict(shown)
    goal_alpha = 0.0 if target is None else float(target["alpha"])
    if target is not None:
        out["x"] += (float(target["x"]) - out["x"]) * move
        out["y"] += (float(target["y"]) - out["y"]) * move
        out["radius"] += (float(target["radius"]) - out["radius"]) * grow
    out["alpha"] += (goal_alpha - out["alpha"]) * fade
    if target is None and out["alpha"] <= 0.02:
        return None
    return out

class _Animator:
    """Redraws the ring FRAME_HZ times a second on its own thread, easing toward the latest estimate.

    The window is created and used only on this thread (Win32 windows belong to their thread)."""

    def __init__(self, owner):
        self.owner = owner
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="gaze-dot", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        window = LayeredDot.open()
        if window is None:
            self.owner._unavailable = True
            log_once("Eye dot skipped: no Win32 layered window. The tracker still runs.")
            return
        shown, drawn, hidden = None, None, True
        last = time.monotonic()
        period = 1.0 / FRAME_HZ
        try:
            while not self.stop.is_set():
                now = time.monotonic()
                dt, last = now - last, now
                with self.owner._lock:
                    target = self.owner._target
                    cut = self.owner._cut
                    self.owner._cut = False
                shown = None if cut else ease(shown, target, dt)
                if shown is None:
                    if not hidden:
                        window.draw(None)
                        hidden, drawn = True, None
                elif drawn is None or _moved(drawn, shown):
                    window.draw(shown)
                    hidden, drawn = False, dict(shown)
                self.stop.wait(max(0.0, period - (time.monotonic() - now)))
        except Exception:
            log.debug("gaze dot animation failed", exc_info=True)
            self.owner._unavailable = True
            log_once("Eye dot skipped after a window error. The tracker still runs.")
        finally:
            window.close()


def _moved(a: dict, b: dict) -> bool:
    """Worth redrawing: a visible change in place, size or fade."""
    return (abs(a["x"] - b["x"]) > 0.05 or abs(a["y"] - b["y"]) > 0.05
            or abs(a["radius"] - b["radius"]) > 0.05 or abs(a["alpha"] - b["alpha"]) > 0.004)


class LayeredDot:
    """The real window. open() returns None when Win32 cannot make one."""

    def __init__(self, hwnd, user32, gdi32):
        self.hwnd = hwnd
        self.user32 = user32
        self.gdi32 = gdi32
        self._hdc = None
        self._mem = None
        self._bmp = None
        self._bits = None
        self._size = 0
        self._proc = None

    @classmethod
    def open(cls):
        if sys.platform != "win32":
            log_once("Eye dot skipped: not Windows. The tracker still runs.")
            return None
        try:
            return cls._open()
        except Exception:
            log.debug("gaze dot window failed", exc_info=True)
            log_once("Eye dot skipped: Win32 would not open a layered window. The tracker still runs.")
            return None

    @classmethod
    def _open(cls):
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                user32.SetProcessDPIAware()
            except Exception:
                pass

        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = LRESULT

        def proc(hwnd, msg, wparam, lparam):
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        cb = WNDPROC(proc)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.lpfnWndProc = cb
        wc.hInstance = hinst
        wc.lpszClassName = "AllyGazeDot"
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        user32.RegisterClassW.restype = wintypes.ATOM
        atom = user32.RegisterClassW(ctypes.byref(wc))
        if not atom and ctypes.get_last_error() not in (0, 1410):
            return None
        user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                           wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.DestroyWindow.restype = wintypes.BOOL
        user32.SetWindowPos.argtypes = [wintypes.HWND, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.SetWindowPos.restype = wintypes.BOOL
        hwnd = user32.CreateWindowExW(DOT_EX_STYLE, "AllyGazeDot", "", 0x80000000,  # WS_POPUP
                                      0, 0, 0, 0, None, None, hinst, None)
        if not hwnd:
            return None
        dot = cls(hwnd, user32, gdi32)
        dot._proc = cb  # the callback has to outlive this function
        return dot

    def draw(self, spec) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = self.user32
        if spec is None:
            user32.ShowWindow(self.hwnd, 0)  # SW_HIDE
            return
        ix, iy = math.floor(spec["x"]), math.floor(spec["y"])
        d, pixels = _ring(spec["radius"], spec["alpha"], spec["x"] - ix, spec["y"] - iy, size=CANVAS)
        self._ensure_bitmap(d)
        if not self._bits:
            return
        ctypes.memmove(self._bits, pixels, len(pixels))
        x = int(ix) - (d - 1) // 2
        y = int(iy) - (d - 1) // 2

        class POINT(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

        class SIZE(ctypes.Structure):
            _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]

        class BLENDFUNCTION(ctypes.Structure):
            _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                        ("SourceConstantAlpha", ctypes.c_byte), ("AlphaFormat", ctypes.c_byte)]

        dest, size, src = POINT(x, y), SIZE(d, d), POINT(0, 0)
        blend = BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_OVER, AC_SRC_ALPHA
        user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.POINTER(POINT),
                                               ctypes.POINTER(SIZE), wintypes.HDC, ctypes.POINTER(POINT),
                                               wintypes.DWORD, ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
        user32.UpdateLayeredWindow.restype = wintypes.BOOL
        if not user32.UpdateLayeredWindow(self.hwnd, None, ctypes.byref(dest), ctypes.byref(size),
                                          self._mem, ctypes.byref(src), 0, ctypes.byref(blend), 0x00000002):
            raise OSError("UpdateLayeredWindow failed")
        # Show without activating, and stay above other windows. SWP_NOACTIVATE.
        user32.SetWindowPos(self.hwnd, ctypes.c_void_p(-1), x, y, d, d, 0x0010 | 0x0040)

    def _ensure_bitmap(self, d: int) -> None:
        if self._bmp is not None and self._size == d:
            return
        import ctypes
        from ctypes import wintypes

        self._drop_bitmap()
        user32, gdi32 = self.user32, self.gdi32
        user32.GetDC.argtypes = [wintypes.HWND]
        user32.GetDC.restype = wintypes.HDC
        user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]  # 64-bit handles need the real types
        user32.ReleaseDC.restype = ctypes.c_int
        gdi32.DeleteDC.argtypes = [wintypes.HDC]
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        screen = user32.GetDC(None)
        mem = gdi32.CreateCompatibleDC(screen)
        user32.ReleaseDC(None, screen)

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                        ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

        info = BITMAPINFOHEADER()
        info.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.biWidth = d
        info.biHeight = -d  # top-down, so row 0 is the top
        info.biPlanes = 1
        info.biBitCount = 32
        bits = ctypes.c_void_p()
        gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                           ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
        gdi32.CreateDIBSection.restype = wintypes.HBITMAP
        bmp = gdi32.CreateDIBSection(mem, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        if not bmp or not bits:
            gdi32.DeleteDC(mem)
            raise OSError("CreateDIBSection failed")
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        gdi32.SelectObject.restype = wintypes.HGDIOBJ
        gdi32.SelectObject(mem, bmp)
        self._mem, self._bmp, self._bits, self._size = mem, bmp, bits, d

    def _drop_bitmap(self) -> None:
        gdi32 = self.gdi32
        if self._bmp:
            try:
                gdi32.DeleteObject(self._bmp)
            except Exception:
                pass
        if self._mem:
            try:
                gdi32.DeleteDC(self._mem)
            except Exception:
                pass
        self._bmp = self._mem = self._bits = None
        self._size = 0

    def close(self) -> None:
        self._drop_bitmap()
        hwnd, self.hwnd = self.hwnd, None
        if hwnd:
            try:
                self.user32.DestroyWindow(hwnd)
            except Exception:
                pass
