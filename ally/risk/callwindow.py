"""Keep Ally Call Chrome popups visible without activating or moving them."""

import ctypes
import sys
from ctypes import wintypes

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
HWND_TOPMOST = -1


def is_call_window(visible, class_name, title) -> bool:
    return bool(visible and class_name == "Chrome_WidgetWin_1" and "Ally Call" in title)


def keep_on_top(user32=None) -> tuple[int, int, int, int] | None:
    """Pin matching visible windows; return their first rect in physical pixels."""
    callback_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    if user32 is None:
        if sys.platform != "win32":
            return None
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        signatures = {
            "EnumWindows": ([callback_type, wintypes.LPARAM], wintypes.BOOL),
            "IsWindowVisible": ([wintypes.HWND], wintypes.BOOL),
            "GetClassNameW": ([wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            "GetWindowTextW": ([wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            "GetWindowRect": ([wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            "SetWindowPos": ([wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT], wintypes.BOOL),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(user32, name)
            fn.argtypes, fn.restype = args, result
    rects = []

    @callback_type
    def visit(hwnd, _param):
        if not user32.IsWindowVisible(hwnd):
            return True
        class_name, title = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(2048)
        user32.GetClassNameW(hwnd, class_name, len(class_name))
        user32.GetWindowTextW(hwnd, title, len(title))
        if is_call_window(True, class_name.value, title.value):
            flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
            if user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags):
                rect = wintypes.RECT()
                if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                    rects.append((rect.left, rect.top, rect.right, rect.bottom))
        return True

    # GetWindowRect is DPI-virtualized unless this thread requests physical coordinates.
    dpi = getattr(user32, "SetThreadDpiAwarenessContext", None)
    previous = None
    if dpi is not None:
        dpi.argtypes, dpi.restype = [ctypes.c_void_p], ctypes.c_void_p
        previous = dpi(ctypes.c_void_p(-4))
    try:
        user32.EnumWindows(visit, 0)
    finally:
        if previous:
            dpi(previous)
    return rects[0] if rects else None
