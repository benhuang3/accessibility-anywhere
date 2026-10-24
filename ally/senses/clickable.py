"""Windows-only, privacy preserving clickable-point hint. No names or text are read."""
import ctypes


def is_clickable(x: int, y: int) -> bool | None:
    if not hasattr(ctypes, "windll"):
        return None
    try:
        user = ctypes.windll.user32
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        class CURSORINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_ulong), ("flags", ctypes.c_ulong),
                        ("hCursor", ctypes.c_void_p), ("ptScreenPos", POINT)]
        info = CURSORINFO()
        info.cbSize = ctypes.sizeof(info)
        hand = user.LoadCursorW(None, ctypes.c_void_p(32649))
        if user.GetCursorInfo(ctypes.byref(info)) and hand and info.hCursor == hand:
            return True
        return False
    except Exception:
        return None
