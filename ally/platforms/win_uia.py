"""Windows UI Automation (UIA): read the text and bounding box of any element, and write text back.

This lets Ally read and edit controls in native Windows apps (Win32, WinForms, WPF, UWP) the same
way a screen reader does, via Microsoft's UIAutomation COM API through comtypes. Classic top-level
window titles use the plain Win32 user32 calls (SetWindowTextW / GetWindowTextW), which work even
when an app exposes no UIA ValuePattern.

Everything that touches comtypes or user32 is imported lazily and guarded by a Windows check, so
`import ally.platforms.win_uia` succeeds on other systems and available() returns (False, reason).
The pure helpers rect_to_bounds() and role_for() carry the logic that can be unit-tested anywhere.

Threads: every tool call runs on a fresh thread, and a COM object must be used from a thread that
initialized COM. So all UIA calls run on one worker thread that joins the multithreaded apartment
(Microsoft's advice for UIA clients) and owns the IUIAutomation object. Each call has a timeout: an
app that stops answering can't freeze a tool, and the next call gets a fresh worker.

Docs: IUIAutomation (ElementFromPoint, GetFocusedElement), IUIAutomationElement
(CurrentName, CurrentControlType, CurrentBoundingRectangle, GetCurrentPattern),
IUIAutomationValuePattern (CurrentValue, CurrentIsReadOnly, SetValue). Windows 7+.
"""

from __future__ import annotations

import logging
import queue
import sys
import threading

log = logging.getLogger("ally.os.win_uia")

CALL_TIMEOUT = 2.0  # seconds; UIA asks the target app, which may be busy or hung

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


def usable_bounds(bounds) -> tuple[int, int, int, int] | None:
    """bounds if it is a real on-screen box, else None (UIA reports (0, 0, 0, 0) or negative sizes
    for elements it can't place; centering a magnifier on those would zoom into a corner)."""
    try:
        x, y, w, h = (int(v) for v in bounds)
    except (TypeError, ValueError):
        return None
    return (x, y, w, h) if w > 0 and h > 0 else None


def role_for(control_type_id: int) -> str:
    """Map a UIA control-type id to a short role string ("button"/"edit"/"text"/"tab"/"other")."""
    return _ROLES.get(int(control_type_id), "other")


# ---- the worker thread that owns COM (logic is OS-independent and unit-tested) --------------

class _Worker(threading.Thread):
    """Runs functions one at a time on a single thread that set itself up with `setup()`.

    call() waits up to `timeout` seconds; on a timeout the worker is marked broken (it may be stuck
    inside a hung app forever) and the owner starts a new one for the next call."""

    def __init__(self, setup=None, teardown=None):
        super().__init__(name="uia", daemon=True)
        self.setup, self.teardown = setup, teardown
        self.jobs: queue.Queue = queue.Queue()
        self.ready = threading.Event()
        self.error = ""
        self.broken = False

    def run(self):
        try:
            if self.setup:
                self.setup()
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            self.broken = True
            self.ready.set()
            return
        self.ready.set()
        try:
            while True:
                job = self.jobs.get()
                if job is None:
                    break
                fn, args, box, done = job
                try:
                    box["result"] = fn(*args)
                except BaseException as e:  # handed back to the caller
                    box["error"] = e
                done.set()
        finally:
            if self.teardown:
                try:
                    self.teardown()
                except Exception:
                    pass

    def call(self, fn, *args, timeout: float = CALL_TIMEOUT):
        if not self.ready.wait(timeout) or self.broken:
            raise RuntimeError(self.error or "UI Automation worker didn't start")
        box: dict = {}
        done = threading.Event()
        self.jobs.put((fn, args, box, done))
        if not done.wait(timeout):
            self.broken = True
            self.error = f"UI Automation didn't answer within {timeout:g} s"
            self.jobs.put(None)  # exits after the stuck call returns, if it ever does
            raise TimeoutError(self.error)
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def stop(self):
        self.jobs.put(None)


def _com_setup():
    import comtypes  # type: ignore

    comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)


def _com_teardown():
    global _UIA_CACHE
    _UIA_CACHE = None  # COM objects die with the apartment
    import comtypes  # type: ignore

    comtypes.CoUninitialize()


_worker: _Worker | None = None
_worker_lock = threading.Lock()


def _call(fn, *args, timeout: float = CALL_TIMEOUT):
    """Run fn(*args) on the UIA worker thread (started, or restarted after a hang, as needed)."""
    global _worker
    with _worker_lock:
        if _worker is None or _worker.broken or not _worker.is_alive():
            _worker = _Worker(_com_setup, _com_teardown)
            _worker.start()
        w = _worker
    return w.call(fn, *args, timeout=timeout)


def shutdown() -> None:
    global _worker
    with _worker_lock:
        if _worker is not None:
            _worker.stop()
            _worker = None


# ---- Windows / comtypes bridge (lazy, guarded) ----------------------------------------------
# Every function below whose name starts with "_" and touches UIA runs on the worker thread.

def _on_windows() -> bool:
    return sys.platform == "win32"


_UIA_CACHE: tuple | None = None  # (iuia, UIA module) once built, owned by the worker thread


def _uia():
    """Build and cache the IUIAutomation root object and the generated UIA module (worker thread only)."""
    global _UIA_CACHE
    if _UIA_CACHE is not None:
        return _UIA_CACHE
    from comtypes.client import CreateObject, GetModule  # type: ignore

    GetModule("UIAutomationCore.dll")
    from comtypes.gen import UIAutomationClient as UIA  # type: ignore

    iuia = CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
    _UIA_CACHE = (iuia, UIA)
    return _UIA_CACHE


_available: tuple[bool, str] | None = None


def available() -> tuple[bool, str]:
    """(True, "...") if UIA can be used here, else (False, reason). Never raises. Cached once it works."""
    global _available
    if _available is not None:
        return _available
    if not _on_windows():
        return False, "UI Automation needs Windows"
    try:
        import comtypes  # type: ignore  # noqa: F401
    except Exception as e:  # pragma: no cover - Windows-only path
        return False, f"comtypes not installed: {e} (pip install comtypes)"
    try:
        _call(_uia, timeout=10)  # the first call generates the type library wrapper: slow
    except Exception as e:  # pragma: no cover - Windows-only path
        return False, f"UI Automation COM API unavailable: {type(e).__name__}: {e}"
    _available = (True, "available")
    return _available


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
    """Turn a UIA element into {"text", "value", "role", "bounds", "settable"}. None on failure.

    bounds is None when UIA can't place the element on screen."""
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
        try:
            role = role_for(element.CurrentControlType)
        except Exception:
            role = "other"
        try:
            r = element.CurrentBoundingRectangle
            bounds = usable_bounds(rect_to_bounds(r.left, r.top, r.right, r.bottom))
        except Exception:
            bounds = None
        return {"text": name or value, "value": value, "role": role, "bounds": bounds, "settable": settable}
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


def _focused():
    iuia, _UIA = _uia()
    return iuia.GetFocusedElement()


def _set(element, text: str) -> bool:
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


def element_from_point(x: int, y: int) -> dict | None:
    """Describe the element under screen pixel (x, y). None if UIA is unavailable or finds nothing."""
    if not available()[0]:
        return None
    try:
        return _call(lambda: _describe(_element_from_point(x, y)))
    except Exception as e:  # pragma: no cover - Windows-only path
        log.warning("ElementFromPoint failed at (%s,%s): %s", x, y, e)
        return None


def focused_element() -> dict | None:
    """Describe the element that currently has keyboard focus. None if unavailable."""
    if not available()[0]:
        return None
    try:
        return _call(lambda: _describe(_focused()))
    except Exception as e:  # pragma: no cover - Windows-only path
        log.warning("GetFocusedElement failed: %s", e)
        return None


def set_value(x: int, y: int, text: str) -> bool:
    """Set the text of the element at (x, y) via its ValuePattern. True on success."""
    if not available()[0]:
        return False
    try:
        return bool(_call(lambda: _set(_element_from_point(x, y), text)))
    except Exception as e:  # pragma: no cover - Windows-only path
        log.warning("SetValue failed at (%s,%s): %s", x, y, e)
        return False


def set_focused_value(text: str) -> bool:
    """Set the text of the element with keyboard focus via its ValuePattern. True on success."""
    if not available()[0]:
        return False
    try:
        return bool(_call(lambda: _set(_focused(), text)))
    except Exception as e:  # pragma: no cover - Windows-only path
        log.warning("SetValue on the focused element failed: %s", e)
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
