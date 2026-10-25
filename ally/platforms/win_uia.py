"""Windows UI Automation (UIA): read the text and bounding box of any element, and write text back.

This lets Ally read and edit controls in native Windows apps (Win32, WinForms, WPF, UWP) the same
way a screen reader does, via Microsoft's UIAutomation COM API through comtypes. Classic top-level
window titles use the plain Win32 user32 calls (SetWindowTextW / GetWindowTextW), which work even
when an app exposes no UIA ValuePattern.

Everything that touches comtypes or user32 is imported lazily and guarded by a Windows check, so
`import ally.platforms.win_uia` succeeds on macOS/Linux and available() returns (False, reason).
The pure helpers rect_to_bounds() and role_for() carry the logic that can be unit-tested anywhere.

Docs: IUIAutomation (ElementFromPoint, GetFocusedElement), IUIAutomationElement
(CurrentName, CurrentControlType, CurrentBoundingRectangle, GetCurrentPattern),
IUIAutomationValuePattern (CurrentValue, CurrentIsReadOnly, SetValue). Windows 7+.
"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger("ally.os.win_uia")

# UIA control-type ids (UIA_*ControlTypeId). A few common ones map to a short role; the rest are
# "other". These constants are stable and documented, so we don't need comtypes to know them.
UIA_ButtonControlTypeId = 50000
UIA_EditControlTypeId = 50004
UIA_TextControlTypeId = 50020
UIA_TabControlTypeId = 50018

UIA_ValuePatternId = 10002

_ROLES = {
    UIA_ButtonControlTypeId: "button",
    UIA_EditControlTypeId: "edit",
    UIA_TextControlTypeId: "text",
    UIA_TabControlTypeId: "tab",
}


# ---- pure helpers (unit-tested on any OS) ---------------------------------------------------

def rect_to_bounds(left: int, top: int, right: int, bottom: int) -> tuple[int, int, int, int]:
    """A UIA BoundingRectangle (left, top, right, bottom) as (x, y, width, height)."""
    return int(left), int(top), int(right - left), int(bottom - top)


def role_for(control_type_id: int) -> str:
    """Map a UIA control-type id to a short role string ("button"/"edit"/"text"/"tab"/"other")."""
    return _ROLES.get(int(control_type_id), "other")


# ---- Windows / comtypes bridge (lazy, guarded) ----------------------------------------------

def _on_windows() -> bool:
    return sys.platform == "win32"


_UIA_CACHE: tuple | None = None  # (iuia, UIA module) once built


def _uia():
    """Build and cache the IUIAutomation root object and the generated UIA module.

    Raises OSError/Exception if comtypes or the UIAutomationCore type library is unavailable; all
    callers catch broadly and degrade. Must only be called on Windows.
    """
    global _UIA_CACHE
    if _UIA_CACHE is not None:
        return _UIA_CACHE
    from comtypes.client import CreateObject, GetModule  # type: ignore

    GetModule("UIAutomationCore.dll")
    from comtypes.gen import UIAutomationClient as UIA  # type: ignore

    iuia = CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
    _UIA_CACHE = (iuia, UIA)
    return _UIA_CACHE


def available() -> tuple[bool, str]:
    """(True, "...") if UIA can be used here, else (False, reason). Never raises."""
    if not _on_windows():
        return False, "UI Automation needs Windows"
    try:
        import comtypes  # type: ignore  # noqa: F401
    except Exception as e:  # pragma: no cover - Windows-only path
        return False, f"comtypes not installed: {e} (pip install comtypes)"
    try:
        _uia()
    except Exception as e:  # pragma: no cover - Windows-only path
        return False, f"UI Automation COM API unavailable: {type(e).__name__}: {e}"
    return True, "available"


def _value_pattern(element):
    """The element's IUIAutomationValuePattern, or None if it doesn't support one."""
    _iuia, UIA = _uia()
    try:
        raw = element.GetCurrentPattern(UIA_ValuePatternId)
        if not raw:
            return None
        return raw.QueryInterface(UIA.IUIAutomationValuePattern)
    except Exception:
        return None


def _describe(element) -> dict | None:
    """Turn a UIA element into {"text", "role", "bounds", "settable"}. None on failure."""
    if element is None:
        return None
    try:
        name = ""
        try:
            name = element.CurrentName or ""
        except Exception:
            name = ""
        vp = _value_pattern(element)
        settable = False
        value = ""
        if vp is not None:
            try:
                settable = not bool(vp.CurrentIsReadOnly)
            except Exception:
                settable = False
            try:
                value = vp.CurrentValue or ""
            except Exception:
                value = ""
        text = name or value
        try:
            role = role_for(element.CurrentControlType)
        except Exception:
            role = "other"
        try:
            r = element.CurrentBoundingRectangle
            bounds = rect_to_bounds(r.left, r.top, r.right, r.bottom)
        except Exception:
            bounds = (0, 0, 0, 0)
        return {"text": text, "role": role, "bounds": bounds, "settable": settable}
    except Exception as e:  # pragma: no cover - Windows-only path
        log.warning("UIA describe failed: %s", e)
        return None


def _element_from_point(x: int, y: int):
    """The raw UIA element at screen pixel (x, y), or None."""
    iuia, UIA = _uia()
    # ElementFromPoint takes the UIA typelib's own POINT struct by value. A ctypes.wintypes.POINT
    # has the same layout but comtypes rejects it at the marshalling layer, so resolve the
    # generated struct by name only (comtypes usually emits it as `tagPOINT`, sometimes `POINT`).
    point_type = getattr(UIA, "tagPOINT", None) or getattr(UIA, "POINT", None)
    if point_type is None:
        raise RuntimeError("UIAutomationClient typelib exposes no POINT struct")
    pt = point_type(int(x), int(y))
    return iuia.ElementFromPoint(pt)


def element_from_point(x: int, y: int) -> dict | None:
    """Describe the element under screen pixel (x, y). None if UIA is unavailable or finds nothing."""
    if not available()[0]:
        return None
    try:
        return _describe(_element_from_point(x, y))
    except Exception as e:  # pragma: no cover - Windows-only path
        log.warning("ElementFromPoint failed at (%s,%s): %s", x, y, e)
        return None


def focused_element() -> dict | None:
    """Describe the element that currently has keyboard focus. None if unavailable."""
    if not available()[0]:
        return None
    try:
        iuia, _UIA = _uia()
        return _describe(iuia.GetFocusedElement())
    except Exception as e:  # pragma: no cover - Windows-only path
        log.warning("GetFocusedElement failed: %s", e)
        return None


def set_value(x: int, y: int, text: str) -> bool:
    """Set the text of the element at (x, y) via its ValuePattern. True on success."""
    if not available()[0]:
        return False
    try:
        element = _element_from_point(x, y)
        vp = _value_pattern(element)
        if vp is None:
            return False
        try:
            if bool(vp.CurrentIsReadOnly):
                return False
        except Exception:
            return False
        vp.SetValue(text)
        return True
    except Exception as e:  # pragma: no cover - Windows-only path
        log.warning("SetValue failed at (%s,%s): %s", x, y, e)
        return False


# ---- classic top-level window titles (plain Win32, no UIA needed) ---------------------------

def set_window_title(hwnd: int, text: str) -> bool:
    """Set a top-level window's title bar text via user32.SetWindowTextW. True on success."""
    if not _on_windows():
        return False
    try:
        import ctypes  # type: ignore

        return bool(ctypes.windll.user32.SetWindowTextW(int(hwnd), str(text)))
    except Exception as e:  # pragma: no cover - Windows-only path
        log.warning("SetWindowTextW failed for hwnd %s: %s", hwnd, e)
        return False


def get_window_title(hwnd: int) -> str:
    """Read a top-level window's title bar text via user32.GetWindowTextW. "" on failure."""
    if not _on_windows():
        return ""
    try:
        import ctypes  # type: ignore

        user32 = ctypes.windll.user32
        n = user32.GetWindowTextLengthW(int(hwnd))
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(int(hwnd), buf, n + 1)
        return buf.value or ""
    except Exception as e:  # pragma: no cover - Windows-only path
        log.warning("GetWindowTextW failed for hwnd %s: %s", hwnd, e)
        return ""


# ---- health ---------------------------------------------------------------------------------

def checks() -> list[tuple[str, str, str]]:
    """Doctor-style (status, name, detail) tuples for UIA availability."""
    ok, why = available()
    return [("ok" if ok else "warn", "UI Automation", why)]
