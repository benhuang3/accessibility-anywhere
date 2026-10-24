"""Windows-only, privacy preserving clickable-point hint. No names or text are read."""
from __future__ import annotations

import ctypes
import threading

# MSAA ROLE_SYSTEM_* values. Only roles that reliably accept a click are positive.
CLICKABLE_ROLES = {0x2B, 0x1E, 0x0B, 0x3C, 0x3D, 0x25, 0x22, 0x23, 0x2E, 0x3E, 0x3F, 0x40}
NONCLICKABLE_ROLES = {0x0A, 0x0F, 0x29, 0x10, 0x09, 0x16, 0x12, 0x2A, 0x2C, 0x2D, 0x2F}


def _role_result(role: int) -> bool | None:
    if role in CLICKABLE_ROLES:
        return True
    if role in NONCLICKABLE_ROLES:
        return False
    return None


def _msaa_role(x: int, y: int) -> bool | None:
    """Read only the role at a point through AccessibleObjectFromPoint."""
    if not hasattr(ctypes, "windll"):
        return None
    oleacc = ctypes.windll.oleacc

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class VARIANT_UNION(ctypes.Union):
        _fields_ = [("lVal", ctypes.c_long), ("ptr", ctypes.c_void_p), ("words", ctypes.c_ushort * 8)]

    class VARIANT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort),
                    ("r3", ctypes.c_ushort), ("u", VARIANT_UNION)]

    point, child, obj = POINT(x, y), VARIANT(), ctypes.c_void_p()
    try:
        fn = oleacc.AccessibleObjectFromPoint
        fn.argtypes = [POINT, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(VARIANT)]
        fn.restype = ctypes.c_long
        if fn(point, ctypes.byref(obj), ctypes.byref(child)) != 0 or not obj.value:
            return None
        # IAccessible.get_accRole is IDispatch vtable slot 19. VARIANT child is passed
        # by value; role is returned as a VARIANT. No text-bearing method is invoked.
        vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        get_role_type = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, VARIANT, ctypes.POINTER(VARIANT))
        get_role = get_role_type(vtable[19])
        role = VARIANT()
        if get_role(obj, child, ctypes.byref(role)) != 0 or role.vt != 3:  # VT_I4
            return None
        return _role_result(int(role.lVal))
    finally:
        if obj.value:
            vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            release_type = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
            release_type(vtable[2])(obj)


def _lookup(x: int, y: int) -> bool | None:
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
        return _msaa_role(x, y)
    except Exception:
        return None


def is_clickable(x: int, y: int, timeout: float = 0.25) -> bool | None:
    """Bound potentially slow accessibility calls; timeout or failure means unknown."""
    result: list[bool | None] = []
    done = threading.Event()

    def run() -> None:
        try:
            result.append(_lookup(x, y))
        finally:
            done.set()

    threading.Thread(target=run, daemon=True, name="clickable-query").start()
    return result[0] if done.wait(timeout) and result else None
