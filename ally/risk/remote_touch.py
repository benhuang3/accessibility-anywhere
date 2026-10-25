"""Bounded touchscreen gestures using Windows mouse input; inert on other platforms."""

import ctypes
import math
import sys

MOVE, LEFTDOWN, LEFTUP = 0x0001, 0x0002, 0x0004
WHEEL, HWHEEL, ABSOLUTE = 0x0800, 0x1000, 0x8000


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_int32), ("dy", ctypes.c_int32), ("mouseData", ctypes.c_uint32),
                ("dwFlags", ctypes.c_uint32), ("time", ctypes.c_uint32), ("dwExtraInfo", ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_uint16), ("wScan", ctypes.c_uint16), ("dwFlags", ctypes.c_uint32),
                ("time", ctypes.c_uint32), ("dwExtraInfo", ctypes.c_size_t)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_uint32), ("wParamL", ctypes.c_uint16), ("wParamH", ctypes.c_uint16)]


class _InputData(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", ctypes.c_uint32), ("data", _InputData)]


def _mouse(flags, x=0, y=0, data=0):
    return INPUT(type=0, mi=MOUSEINPUT(x, y, data & 0xffffffff, flags, 0, 0))


def _send_input(rows) -> bool:
    if sys.platform != "win32":
        return False
    user32 = ctypes.windll.user32
    send = user32.SendInput
    send.argtypes = (ctypes.c_uint32, ctypes.POINTER(INPUT), ctypes.c_int)
    send.restype = ctypes.c_uint32
    count = send(len(rows), (INPUT * len(rows))(*rows), ctypes.sizeof(INPUT))
    if count != len(rows) and any(row.mi.dwFlags & LEFTDOWN for row in rows):
        # A partially injected click must never leave a button down.
        release = _mouse(LEFTUP)
        send(1, ctypes.byref(release), ctypes.sizeof(INPUT))
    return count == len(rows)


def _primary_geometry():
    if sys.platform != "win32":
        return 1, 1, 1.0
    user32 = ctypes.windll.user32
    try:
        scale = max(1.0, user32.GetDpiForSystem() / 96.0)
    except Exception:
        scale = 1.0
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1), scale


class RemoteTouch:
    def __init__(self, send_input=None, geometry=None):
        self.send_input = send_input or _send_input
        self.geometry = geometry or _primary_geometry
        self.reset()

    def reset(self) -> None:
        self.start = self.last = None
        self.scrolling = False
        self.remainder = [0.0, 0.0]

    def handle(self, phase: str, x: float, y: float) -> bool:
        """Return True only after a completed click (for its visual ripple)."""
        try:
            if (phase not in ("start", "move", "end") or type(x) not in (int, float)
                    or type(y) not in (int, float) or not math.isfinite(x) or not math.isfinite(y)
                    or not 0 <= x <= 1 or not 0 <= y <= 1):
                return False
            if phase == "start":
                self.reset()
                self.width, self.height, self.scale = self.geometry()
                if min(self.width, self.height, self.scale) <= 0:
                    return False
                self.start = self.last = (x, y)
                return False
            if self.start is None:
                return False
            dx = (x - self.start[0]) * self.width / self.scale
            dy = (y - self.start[1]) * self.height / self.scale
            if not self.scrolling and math.hypot(dx, dy) >= 12:
                self.scrolling = True
                # Wheel input targets the window under the cursor.
                self.send_input([_mouse(MOVE | ABSOLUTE, round(self.start[0] * 65535),
                                        round(self.start[1] * 65535))])
            if self.scrolling:
                # Positive vertical wheel goes up; positive horizontal wheel goes right.
                deltas = (-(x - self.last[0]) * self.width / self.scale,
                          (y - self.last[1]) * self.height / self.scale)
                for index, (delta, flags) in enumerate(zip(deltas, (HWHEEL, WHEEL))):
                    self.remainder[index] += delta / 48
                    steps = math.trunc(round(self.remainder[index], 8))
                    if steps:
                        self.remainder[index] -= steps
                        self.send_input([_mouse(flags, data=steps * 120)])
            self.last = (x, y)
            if phase == "end" and not self.scrolling:
                return bool(self.send_input([_mouse(MOVE | ABSOLUTE, round(x * 65535), round(y * 65535)),
                                             _mouse(LEFTDOWN), _mouse(LEFTUP)]))
        except Exception:
            self.reset()
        finally:
            if phase == "end":
                self.reset()
        return False
