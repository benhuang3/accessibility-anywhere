"""The control under a screen point, on Windows. Read only when a tool asks.

The role and accessible name go to Claude only as part of the user's own request.
Nothing here is saved, and this module does not use the network.
"""

from __future__ import annotations

import sys
import threading

# IAccessible vtable, after IUnknown (3) and IDispatch (4): accName is 10, accRole is 13.
_GET_NAME = 10
_GET_ROLE = 13
_VT_I4 = 3
_VT_BSTR = 8

# CoInitializeEx: apartment-threaded, so AccessibleObjectFromPoint is legal on this thread.
_COINIT_APARTMENTTHREADED = 0x2

ROLE_NAMES = {
    0x09: "window",
    0x0A: "client",
    0x0C: "menu item",
    0x0F: "document",
    0x10: "pane",
    0x12: "dialog",
    0x1D: "cell",
    0x1E: "link",
    0x22: "list item",
    0x24: "outline item",
    0x25: "tab",
    0x29: "text",
    0x2A: "text",
    0x2B: "button",
    0x2C: "checkbox",
    0x2D: "radio button",
    0x2E: "combo box",
    0x38: "button",
    0x39: "button",
    0x3E: "button",
}


def _com_init() -> bool:
    """Initialize COM on this thread. True when this call must be paired with CoUninitialize."""
    import ctypes

    if not hasattr(ctypes, "windll"):
        return False
    try:
        fn = ctypes.windll.ole32.CoInitializeEx
        fn.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        fn.restype = ctypes.c_long
        hr = int(fn(None, _COINIT_APARTMENTTHREADED))
    except Exception:
        return False
    return hr in (0, 1)  # S_OK, or S_FALSE when this thread already initialized COM


def _com_uninit() -> None:
    import ctypes

    try:
        ctypes.windll.ole32.CoUninitialize()
    except Exception:
        pass


def describe(x: int, y: int, timeout: float = 0.4) -> dict | None:
    """{"role", "name"} at this point, or None off Windows and when the lookup fails."""
    if sys.platform != "win32":
        return None
    result: list[dict | None] = []
    done = threading.Event()

    def run() -> None:
        com = False
        try:
            com = _com_init()
            result.append(_describe(int(x), int(y)))
        except Exception:
            result.append(None)
        finally:
            if com:
                _com_uninit()
            done.set()

    threading.Thread(target=run, daemon=True, name="gaze-element").start()
    if not done.wait(timeout) or not result:
        return None
    return result[0]


def _describe(x: int, y: int) -> dict | None:
    import ctypes

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
    name_var = None
    try:
        fn = oleacc.AccessibleObjectFromPoint
        fn.argtypes = [POINT, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(VARIANT)]
        fn.restype = ctypes.c_long
        if fn(point, ctypes.byref(obj), ctypes.byref(child)) != 0 or not obj.value:
            return None
        vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        call = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, VARIANT, ctypes.POINTER(VARIANT))
        role = VARIANT()
        if call(vtable[_GET_ROLE])(obj, child, ctypes.byref(role)) != 0 or role.vt != _VT_I4:
            return None
        role_id = int(role.lVal)
        label = ROLE_NAMES.get(role_id, "control")
        name_var = VARIANT()
        name = ""
        if call(vtable[_GET_NAME])(obj, child, ctypes.byref(name_var)) == 0 and name_var.vt == _VT_BSTR and name_var.ptr:
            raw = ctypes.wstring_at(name_var.ptr)
            name = "".join(ch for ch in raw if ch.isprintable()).strip()[:200]
        return {"role": label, "name": name}
    finally:
        if name_var is not None and getattr(name_var, "vt", 0) == _VT_BSTR and name_var.ptr:
            try:
                ctypes.windll.oleaut32.SysFreeString(name_var.ptr)
            except Exception:
                pass
        if obj.value:
            vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
            release(vtable[2])(obj)
