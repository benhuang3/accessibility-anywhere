"""Windows: full-screen magnifier and color filters via the Magnification API (ctypes).

All Mag* calls run on one dedicated thread. While zoomed in, that thread keeps the view centered
on the mouse. On exit the screen is reset (level 1.0, identity colors) before MagUninitialize.
Docs: MagSetFullscreenTransform (level 1.0-4096.0, offsets in unmagnified pixels) and
MagSetFullscreenColorEffect (5x5 color matrix), Windows 8+, 64-bit process on 64-bit Windows.
The Windows Magnifier app owns the API while it is open, so Ally closes it (Win+Esc) and takes
over. If the API is unavailable (e.g. 32-bit Python) Ally falls back to the Magnifier app's
shortcuts: Win+Plus, Win+Minus, Win+Esc.

Speech: one PowerShell process keeps a System.Speech voice loaded (starting PowerShell for every
sentence costs 2-4 s before the first word). Sounds play with winsound. The clipboard (for
"read the selection") uses the Win32 clipboard API directly.
"""

from __future__ import annotations

import base64
import ctypes
import functools
import itertools
import logging
import os
import queue
import subprocess
import threading
import time
import wave

from ..registry import ToolError
from .base import NO_WINDOW, Platform, ReadingPan, is_64bit_python, popen, run, which

log = logging.getLogger("ally.os.windows")

IDENTITY = [1, 0, 0, 0, 0,
            0, 1, 0, 0, 0,
            0, 0, 1, 0, 0,
            0, 0, 0, 1, 0,
            0, 0, 0, 0, 1]
# From Microsoft's MagSetFullscreenColorEffect example.
GRAYSCALE = [0.3, 0.3, 0.3, 0, 0,
             0.6, 0.6, 0.6, 0, 0,
             0.1, 0.1, 0.1, 0, 0,
             0, 0, 0, 1, 0,
             0, 0, 0, 0, 1]
# Row vector [r g b a 1] x M: r' = 1 - r, etc.
INVERT = [-1, 0, 0, 0, 0,
          0, -1, 0, 0, 0,
          0, 0, -1, 0, 0,
          0, 0, 0, 1, 0,
          1, 1, 1, 0, 1]
MATRICES = {"none": IDENTITY, "grayscale": GRAYSCALE, "invert": INVERT}

VK = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10, "cmd": 0x5B, "esc": 0x1B, "enter": 0x0D, "space": 0x20,
      "tab": 0x09, "plus": 0xBB, "minus": 0xBD, "=": 0xBB, "-": 0xBD, "up": 0x26, "down": 0x28,
      "left": 0x25, "right": 0x27}
KEYEVENTF_KEYUP = 0x0002


def follow_offset(cursor: int, screen: int, level: float) -> int:
    """Top-left offset (unmagnified px) that centers the cursor, clamped to the screen."""
    view = screen / level
    return int(max(0, min(screen - view, cursor - view / 2)))


def tap(*vks: int) -> None:
    user32 = ctypes.windll.user32
    for vk in vks:
        user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.03)
    for vk in reversed(vks):
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


MODIFIER_VKS = (0x11, 0x12, 0x10, 0x5B, 0x5C)  # Ctrl, Alt, Shift, left Win, right Win


def wait_for_keys_up(vks: tuple[int, ...] = MODIFIER_VKS, timeout: float = 1.5) -> bool:
    """Wait until these keys are released (gives up after timeout).

    Hotkeys fire while their keys are still held, and keys Ally sends mix with them:
    Ctrl+Alt+C isn't "copy", and Ctrl+Alt+Win+Esc doesn't close Windows Magnifier.
    """
    user32 = ctypes.windll.user32
    end = time.monotonic() + timeout
    while any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in vks):
        if time.monotonic() > end:
            return False
        time.sleep(0.02)
    return True


def win_shortcut(vk: int) -> None:
    """Press Win+key once the user has let go of the hotkey's modifiers."""
    wait_for_keys_up()
    tap(VK["cmd"], vk)


def magnifier_app_open() -> bool:
    """True while the Windows Magnifier app runs (it owns the Magnification API until it closes)."""
    try:
        return bool(ctypes.windll.user32.FindWindowW("MagUIClass", None))
    except Exception:
        return False


def close_magnifier_app(timeout: float = 3.0) -> bool:
    win_shortcut(VK["esc"])  # Win+Esc closes Windows Magnifier (WM_CLOSE is blocked: it runs with UIAccess)
    end = time.monotonic() + timeout
    while magnifier_app_open():
        if time.monotonic() > end:
            return False
        time.sleep(0.05)
    return True


def sapi_rate(rate: float) -> int:
    """Ally's speed (1.0 = normal) as a Windows voice rate (-10 to 10)."""
    return max(-10, min(10, round((rate - 1.0) * 10)))


# ---- clipboard (Win32 API; a PowerShell call per read or write took over a second each)
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


@functools.cache
def _clip_api():
    import ctypes.wintypes as wt

    u = ctypes.WinDLL("user32", use_last_error=True)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    u.OpenClipboard.argtypes, u.OpenClipboard.restype = [wt.HWND], wt.BOOL
    u.CloseClipboard.restype = wt.BOOL
    u.EmptyClipboard.restype = wt.BOOL
    u.GetClipboardData.argtypes, u.GetClipboardData.restype = [wt.UINT], wt.HANDLE
    u.SetClipboardData.argtypes, u.SetClipboardData.restype = [wt.UINT, wt.HANDLE], wt.HANDLE
    u.GetClipboardSequenceNumber.restype = wt.DWORD
    k.GlobalAlloc.argtypes, k.GlobalAlloc.restype = [wt.UINT, ctypes.c_size_t], wt.HGLOBAL
    k.GlobalLock.argtypes, k.GlobalLock.restype = [wt.HGLOBAL], ctypes.c_void_p
    k.GlobalUnlock.argtypes, k.GlobalUnlock.restype = [wt.HGLOBAL], wt.BOOL
    k.GlobalFree.argtypes, k.GlobalFree.restype = [wt.HGLOBAL], wt.HGLOBAL
    return u, k


def _open_clipboard(u) -> bool:
    for _ in range(25):  # another app may be holding it for a moment
        if u.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def clipboard_sequence() -> int:
    return _clip_api()[0].GetClipboardSequenceNumber()


def clipboard_text() -> str | None:
    """The clipboard's text, or None if it holds no text (or can't be opened)."""
    u, k = _clip_api()
    if not _open_clipboard(u):
        return None
    try:
        h = u.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = k.GlobalLock(h)
        if not p:
            return None
        try:
            return ctypes.wstring_at(p)
        finally:
            k.GlobalUnlock(h)
    finally:
        u.CloseClipboard()


def set_clipboard_text(text: str) -> bool:
    u, k = _clip_api()
    buf = ctypes.create_unicode_buffer(text)
    h = k.GlobalAlloc(GMEM_MOVEABLE, ctypes.sizeof(buf))
    if not h:
        return False
    p = k.GlobalLock(h)
    if not p:
        k.GlobalFree(h)
        return False
    ctypes.memmove(p, buf, ctypes.sizeof(buf))
    k.GlobalUnlock(h)
    if not _open_clipboard(u):
        k.GlobalFree(h)
        return False
    try:
        u.EmptyClipboard()
        if not u.SetClipboardData(CF_UNICODETEXT, h):
            k.GlobalFree(h)
            return False
        return True  # the clipboard owns the memory now
    finally:
        u.CloseClipboard()


# ---- speech
# Reads one command per line: "SAY <id> <rate> <voice b64 or -> <text b64>" or "STOP".
# Prints "DONE <id>" when that speech finishes or is cut off. Exits when stdin closes.
SPEECH_WORKER = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$default = $s.Voice.Name
$utf8 = [System.Text.Encoding]::UTF8
function Dec($b) { if ($b -eq '-') { '' } else { $utf8.GetString([Convert]::FromBase64String($b)) } }
function Done($i) { [Console]::Out.WriteLine("DONE $i"); [Console]::Out.Flush() }
$in = New-Object System.IO.StreamReader([Console]::OpenStandardInput())
$cur = $null; $id = ''
$read = $in.ReadLineAsync()
[Console]::Out.WriteLine('READY'); [Console]::Out.Flush()
while ($true) {
  if ($read.Wait(30)) {
    $line = $read.Result
    if ($null -eq $line) { break }
    $f = $line.Split(' ')
    $s.SpeakAsyncCancelAll()
    if ($null -ne $cur) { Done $id; $cur = $null }
    if ($f[0] -eq 'SAY' -and $f.Length -ge 5) {
      $id = $f[1]
      try {
        $s.Rate = [int]$f[2]
        $v = Dec $f[3]; if ($v -eq '') { $v = $default }
        if ($v -ne $s.Voice.Name) { try { $s.SelectVoice($v) } catch { [Console]::Error.WriteLine("no voice named $v") } }
        $cur = $s.SpeakAsync((Dec $f[4]))
      } catch { [Console]::Error.WriteLine($_); Done $id }
    }
    $read = $in.ReadLineAsync()
  }
  if ($null -ne $cur -and $cur.IsCompleted) { Done $id; $cur = $null }
}
$s.Dispose()
"""


def _b64(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("ascii") if s else "-"


class _Utterance:
    """One sentence on the speech worker, with the parts of Popen that Speaker uses."""

    def __init__(self, worker: "_SpeechWorker", uid: str):
        self.worker, self.uid = worker, uid
        self.done = threading.Event()

    def poll(self):
        return 0 if self.done.is_set() else None

    def wait(self, timeout: float | None = None) -> int:
        if not self.done.wait(timeout):
            raise subprocess.TimeoutExpired("speech", timeout)
        return 0

    def terminate(self) -> None:
        self.worker.send("STOP")

    def kill(self) -> None:
        self.worker.kill()  # a stuck voice: the next sentence starts a fresh worker


class _SpeechWorker:
    def __init__(self):
        script = base64.b64encode(SPEECH_WORKER.encode("utf-16-le")).decode("ascii")
        try:
            self.proc = subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", script],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        except OSError as e:
            raise ToolError(f"Couldn't start Windows speech (PowerShell): {e}")
        self.waiting: dict[str, _Utterance] = {}
        self.lock = threading.Lock()
        self.ids = itertools.count(1)
        self.closed = False
        threading.Thread(target=self._read_out, daemon=True, name="speech-out").start()
        threading.Thread(target=self._read_err, daemon=True, name="speech-err").start()

    def alive(self) -> bool:
        return not self.closed and self.proc.poll() is None

    def send(self, line: str) -> bool:
        try:
            self.proc.stdin.write(line + "\n")
            self.proc.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def say(self, text: str, rate: float, voice: str) -> _Utterance:
        u = _Utterance(self, str(next(self.ids)))
        with self.lock:
            self.waiting[u.uid] = u
        if not self.send(f"SAY {u.uid} {sapi_rate(rate)} {_b64(voice)} {_b64(text)}"):
            self.kill()
            raise ToolError("Windows speech stopped working.")
        return u

    def _read_out(self) -> None:
        for line in self.proc.stdout:
            parts = line.split()
            if len(parts) == 2 and parts[0] == "DONE":
                with self.lock:
                    u = self.waiting.pop(parts[1], None)
                if u:
                    u.done.set()
        self._release()
        self.proc.stdout.close()

    def _read_err(self) -> None:
        for line in self.proc.stderr:
            line = line.strip()
            if line and not line.startswith(("#< CLIXML", "<Objs")):  # PowerShell progress records
                log.warning("windows speech: %s", line[:300])
        self.proc.stderr.close()

    def _release(self) -> None:
        self.closed = True
        with self.lock:
            waiting, self.waiting = self.waiting, {}
        for u in waiting.values():
            u.done.set()

    def kill(self) -> None:
        try:
            self.proc.kill()
        except OSError:
            pass
        self._release()

    def close(self) -> None:
        try:
            self.proc.stdin.close()  # the worker exits when its stdin closes
            self.proc.wait(timeout=3)
        except Exception:
            self.kill()


class _Sound:
    """A WAV file playing through winsound, with the parts of Popen that Speaker uses."""

    def __init__(self, path: str):
        import winsound

        with wave.open(path, "rb") as w:
            seconds = w.getnframes() / float(w.getframerate())
        self.end = time.monotonic() + seconds
        self.stopped = threading.Event()
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)

    def poll(self):
        return 0 if self.stopped.is_set() or time.monotonic() >= self.end else None

    def wait(self, timeout: float | None = None) -> int:
        left = max(0.0, self.end - time.monotonic())
        if timeout is not None and left > timeout:
            if not self.stopped.wait(timeout):
                raise subprocess.TimeoutExpired("sound", timeout)
        else:
            self.stopped.wait(left)
        return 0

    def terminate(self) -> None:
        import winsound

        winsound.PlaySound(None, 0)
        self.stopped.set()

    kill = terminate


class _MagThread(threading.Thread):
    """Owns the Magnification API. The view follows the mouse, stays put ("fixed"), or follows the
    eyes ("eyes": reading along a line pans with the reader, looking near an edge pans that way, and
    moving the mouse a lot hands control back to it)."""

    def __init__(self):
        super().__init__(name="magnifier", daemon=True)
        self.cmds: queue.Queue = queue.Queue()
        self.ready = threading.Event()
        self.ok = False
        self.error = ""
        self.level = 1.0
        self.offset = (0, 0)  # top-left of the magnified view, in unmagnified screen pixels
        self.follow = "mouse"
        self.gaze = None  # callable -> (x, y) of the eyes on the screen, or None
        self.pan_speed = 1.0

    def call(self, op: str, value, timeout: float = 3.0):
        done = threading.Event()
        box = {}
        self.cmds.put((op, value, done, box))
        if not done.wait(timeout):
            raise ToolError("The magnifier didn't respond.")
        if "error" in box:
            raise ToolError(box["error"])
        return box.get("result")

    def run(self):
        import ctypes.wintypes as wt

        class MAGCOLOREFFECT(ctypes.Structure):
            _fields_ = [("transform", ctypes.c_float * 25)]

        user32 = ctypes.windll.user32
        try:
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                user32.SetProcessDPIAware()
            mag = ctypes.WinDLL("Magnification.dll")
            mag.MagInitialize.restype = wt.BOOL
            mag.MagUninitialize.restype = wt.BOOL
            mag.MagSetFullscreenTransform.argtypes = [ctypes.c_float, ctypes.c_int, ctypes.c_int]
            mag.MagSetFullscreenTransform.restype = wt.BOOL
            mag.MagGetFullscreenTransform.argtypes = [ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_int),
                                                      ctypes.POINTER(ctypes.c_int)]
            mag.MagGetFullscreenTransform.restype = wt.BOOL
            mag.MagSetFullscreenColorEffect.argtypes = [ctypes.POINTER(MAGCOLOREFFECT)]
            mag.MagSetFullscreenColorEffect.restype = wt.BOOL
            if not mag.MagInitialize():
                raise OSError("MagInitialize failed")
            self.ok = True
        except Exception as e:
            self.error = str(e)
            self.ready.set()
            return
        self.ready.set()

        def set_transform(level, x, y) -> bool:
            if not mag.MagSetFullscreenTransform(ctypes.c_float(level), x, y):
                return False
            got, gx, gy = ctypes.c_float(), ctypes.c_int(), ctypes.c_int()
            if mag.MagGetFullscreenTransform(ctypes.byref(got), ctypes.byref(gx), ctypes.byref(gy)):
                return abs(got.value - level) < 0.01
            return True

        def set_effect(name) -> bool:
            eff = MAGCOLOREFFECT()
            for i, v in enumerate(MATRICES[name]):
                eff.transform[i] = float(v)
            return bool(mag.MagSetFullscreenColorEffect(ctypes.byref(eff)))

        pt = wt.POINT()
        pos = [0.0, 0.0]  # exact view offset while panning with the eyes (sub-pixel steps add up)
        mouse_at = (0, 0)
        tick = time.monotonic()
        failed = ("Windows wouldn't change the magnification. If the Windows Magnifier app "
                  "is open, close it (Win+Esc) and try again.")

        def apply_view(lvl: float, x: int, y: int) -> bool:
            if not set_transform(lvl, x, y):
                return False
            self.level, self.offset = lvl, (x, y)
            pos[:] = [float(x), float(y)]
            self._eye_pan = None  # a new view starts a new line
            return True

        try:
            while True:
                try:
                    # 60 steps a second while following the eyes, so a slow reading pan glides.
                    wait = (1 / 60 if self.follow == "eyes" else 1 / 30) if self.level > 1.0 else 0.5
                    op, value, done, box = self.cmds.get(timeout=wait)
                except queue.Empty:
                    op = None
                if op == "stop":
                    done.set()
                    break
                w, h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
                user32.GetCursorPos(ctypes.byref(pt))
                if op == "level":  # zoom; keep following what was followed, around the same spot
                    lvl = max(1.0, float(value))
                    if lvl <= 1.0:
                        x, y = 0, 0
                        self.follow = "mouse"
                    elif self.follow == "mouse" or self.level <= 1.0:
                        x, y = follow_offset(pt.x, w, lvl), follow_offset(pt.y, h, lvl)
                    else:
                        cx = self.offset[0] + w / self.level / 2
                        cy = self.offset[1] + h / self.level / 2
                        x, y = follow_offset(int(cx), w, lvl), follow_offset(int(cy), h, lvl)
                    if apply_view(lvl, x, y):
                        box["result"] = lvl
                    else:
                        box["error"] = failed
                    mouse_at = (pt.x, pt.y)
                    done.set()
                elif op == "view":  # zoom centered on a point, then follow "eyes", "mouse" or nothing
                    lvl, cx, cy, follow = value
                    lvl = max(1.0, float(lvl))
                    x, y = follow_offset(int(cx), w, lvl), follow_offset(int(cy), h, lvl)
                    if apply_view(lvl, x, y):
                        self.follow = follow if lvl > 1.0 else "mouse"
                        box["result"] = lvl
                    else:
                        box["error"] = failed
                    mouse_at = (pt.x, pt.y)
                    done.set()
                elif op == "follow":
                    self.follow = value
                    mouse_at = (pt.x, pt.y)
                    done.set()
                elif op == "get":  # the real zoom, even when the Windows Magnifier app set it
                    got, gx, gy = ctypes.c_float(), ctypes.c_int(), ctypes.c_int()
                    if mag.MagGetFullscreenTransform(ctypes.byref(got), ctypes.byref(gx), ctypes.byref(gy)):
                        box["result"] = (float(got.value), gx.value, gy.value)
                    else:
                        box["result"] = (self.level, *self.offset)
                    done.set()
                elif op == "effect":
                    if not set_effect(value):
                        box["error"] = "Windows wouldn't apply the color filter."
                    done.set()
                elif op is None and self.level > 1.0:
                    now = time.monotonic()
                    dt, tick = min(0.1, now - tick), now
                    if self.follow != "mouse" and abs(pt.x - mouse_at[0]) + abs(pt.y - mouse_at[1]) > 80:
                        self.follow = "mouse"  # the user reached for the mouse: let the view follow it again
                        log.info("magnifier: mouse moved, following the mouse")
                    if self.follow == "mouse":
                        xy = (follow_offset(pt.x, w, self.level), follow_offset(pt.y, h, self.level))
                        if xy != self.offset:
                            apply_view(self.level, *xy)
                    elif self.follow == "eyes" and self.gaze is not None:
                        g = self.gaze()
                        # A gap means the view was following the mouse or was just moved. Start
                        # fresh, and don't carry an old velocity or line into the new look.
                        pan = getattr(self, "_eye_pan", None)
                        if pan is None or now - pan["tick"] > 0.25:
                            pan = {"read": ReadingPan(), "tick": now}
                        pan["tick"] = now
                        self._eye_pan = pan
                        gaze = None if g is None else (g[0] / max(w, 1), g[1] / max(h, 1))
                        vw, vh = w / self.level, h / self.level
                        pos[:] = pan["read"].step(gaze, pos, (vw, vh), (w - vw, h - vh), dt, self.pan_speed)
                        xy = (int(pos[0]), int(pos[1]))
                        if xy != self.offset and mag.MagSetFullscreenTransform(ctypes.c_float(self.level), *xy):
                            self.offset = xy
                else:
                    tick = time.monotonic()
        finally:
            try:
                mag.MagSetFullscreenTransform(ctypes.c_float(1.0), 0, 0)
                set_effect("none")
                mag.MagUninitialize()
            except Exception:
                pass


class WindowsPlatform(Platform):
    name = "windows"
    label = "Windows"

    def __init__(self):
        super().__init__()
        self._mag: _MagThread | None = None
        self._speech: _SpeechWorker | None = None
        self._gaze = None  # (latest gaze point callable, pan speed) from the eye tracker
        self._lock = threading.Lock()

    def _magnifier(self) -> _MagThread | None:
        with self._lock:
            if self._mag is None:
                if not is_64bit_python():
                    log.warning("32-bit Python: the Magnification API needs 64-bit Python")
                    return None
                t = _MagThread()
                if self._gaze is not None:
                    t.gaze, t.pan_speed = self._gaze
                t.start()
                t.ready.wait(5)
                self._mag = t
                if not t.ok:
                    log.warning("Magnification API unavailable: %s", t.error)
            return self._mag if self._mag.ok else None

    def _zoom(self, level: float, op: str, value) -> str | None:
        """Run a zoom op on the API thread. Returns the message, or None if the API can't be used."""
        m = self._magnifier()
        if m is None:
            return None
        took_over = level > 1.0 and magnifier_app_open() and close_magnifier_app()
        for attempt in range(6 if took_over else 1):  # Magnifier lets go of the API a moment after closing
            try:
                m.call(op, value)
                break
            except ToolError as e:
                if attempt == (5 if took_over else 0):
                    log.warning("Magnification API failed (%s); using Magnifier shortcuts", e)
                    return None
                time.sleep(0.25)
        self.magnify_level = level
        if level > 1.0:
            self.last_magnify = level
        follows = {"eyes": "follows your eyes as you read; look near an edge to move", "mouse": "follows your mouse"}
        msg = ("Screen magnifier off." if level <= 1.0 else
               f"Screen magnified to {level:g}x" + (f" ({follows[m.follow]})." if m.follow in follows else "."))
        return ("Closed the Windows Magnifier app so I could take over. " + msg) if took_over else msg

    def magnify_at(self, level: float, x: float, y: float, follow: str = "eyes") -> str:
        level = max(1.0, min(16.0, float(level)))
        msg = self._zoom(level, "view", (level, x, y, follow))
        return msg if msg is not None else super().magnify_at(level, x, y, follow)

    def set_follow(self, mode: str) -> str:
        m = self._magnifier()
        if m is None:
            raise ToolError("Following your eyes needs the Windows Magnification API (64-bit Python).")
        m.call("follow", mode)
        if self.magnify_level <= 1.0:
            return f"The magnifier will follow your {mode} when it's on."
        return f"The magnifier follows your {mode} now." + (" It moves along as you read, or look near an edge to move." if mode == "eyes" else "")

    def view(self) -> tuple[float, int, int]:
        m = self._magnifier()
        if m is None:
            return super().view()
        try:
            return m.call("get", None)
        except ToolError:
            return m.level, m.offset[0], m.offset[1]

    def other_magnifier_open(self) -> bool:
        return magnifier_app_open()

    def set_gaze_source(self, latest, pan_speed: float = 1.0) -> None:
        self._gaze = (latest, pan_speed)  # handed to the magnifier thread when it starts
        if self._mag is not None:
            self._mag.gaze, self._mag.pan_speed = latest, pan_speed

    def magnify(self, level: float) -> str:
        level = max(1.0, min(16.0, float(level)))
        msg = self._zoom(level, "level", level)
        if msg is not None:
            return msg
        # Fallback: the Windows Magnifier app's shortcuts. Its zoom level can't be read here, so
        # start it (it opens at its last zoom) or move one step.
        if level <= 1.0:
            if magnifier_app_open():
                win_shortcut(VK["esc"])
            self.magnify_level = 1.0
            return "Closed Windows Magnifier."
        if not magnifier_app_open():
            win_shortcut(VK["plus"])
            self.magnify_level = level
            return "Opened Windows Magnifier. Win+Plus and Win+Minus zoom, Win+Esc closes it."
        up = level > self.magnify_level
        win_shortcut(VK["plus"] if up else VK["minus"])
        self.magnify_level = level
        return f"Zoomed Windows Magnifier {'in' if up else 'out'} one step. Win+Esc closes it."

    def color_filter(self, kind: str) -> str:
        if kind == "high_contrast":
            raise ToolError("Turn on Windows contrast themes with Left Alt + Left Shift + Print Screen, "
                            "or Settings > Accessibility > Contrast themes.")
        m = self._magnifier()
        if m is None:
            raise ToolError("Color filters need the Windows Magnification API (64-bit Python on Windows 8+). "
                            "You can also press Win+Ctrl+C after turning on color filters in Settings > Accessibility.")
        m.call("effect", kind)
        self.color = kind
        return "Colors back to normal." if kind == "none" else f"Screen color filter: {kind}."

    def _screenshot_fallback(self) -> bytes:
        from .base import tempfile_path

        path = tempfile_path(".png")
        ps = ("Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
              "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
              "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
              "$g=[System.Drawing.Graphics]::FromImage($bmp);"
              "$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size);"
              f"$bmp.Save('{path}',[System.Drawing.Imaging.ImageFormat]::Png)")
        try:
            run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps], timeout=15, check=True)
            with open(path, "rb") as f:
                return f.read()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def _speech_worker(self) -> _SpeechWorker:
        with self._lock:
            if self._speech is None or not self._speech.alive():
                self._speech = _SpeechWorker()
            return self._speech

    def warm_up(self) -> None:
        try:
            self._speech_worker()  # loading the voice takes a few seconds; do it before the first sentence
        except ToolError as e:
            log.warning("%s", e)

    def tts_start(self, text, rate, voice):
        for _ in range(2):  # a worker that died since the last sentence is replaced once
            worker = self._speech_worker()
            try:
                return worker.say(text, rate, voice)
            except ToolError:
                continue
        raise ToolError("Windows speech isn't working (PowerShell System.Speech).")

    def play_wav_start(self, path):
        try:
            return _Sound(path)
        except (ImportError, OSError, RuntimeError, wave.Error, ZeroDivisionError) as e:
            log.debug("winsound can't play %s (%s); using PowerShell", path, e)
            return popen(self.play_wav_argv(path))

    def tts_argv(self, text, rate, voice):
        b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
        r = sapi_rate(rate)
        select = ""
        if voice:
            v = voice.replace("'", "''")
            select = f"try {{ $s.SelectVoice('{v}') }} catch {{}};"
        ps = ("Add-Type -AssemblyName System.Speech;"
              "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
              f"$s.Rate={r};{select}"
              f"$t=[System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{b64}'));"
              "$s.Speak($t)")
        return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps], None

    def play_wav_argv(self, path: str) -> list[str]:
        p = path.replace("'", "''")
        return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                f"(New-Object System.Media.SoundPlayer '{p}').PlaySync()"]

    def get_selected_text(self) -> str:
        """Copy the selection with Ctrl+C, read it, and put the user's clipboard text back.

        Nothing selected means nothing gets copied, so the clipboard is left alone.
        """
        wait_for_keys_up((0x12, 0x10, 0x5B, 0x5C))  # Ctrl may stay down: it is part of Ctrl+C
        old = clipboard_text()
        seq = clipboard_sequence()
        tap(VK["ctrl"], 0x43)  # Ctrl+C
        end = time.monotonic() + 1.0
        while clipboard_sequence() == seq:
            if time.monotonic() > end:
                return ""
            time.sleep(0.02)
        time.sleep(0.05)  # some apps add more formats right after the first one
        text = clipboard_text() or ""
        if old is not None and old != text:
            set_clipboard_text(old)
        return text.strip()

    def press_keys(self, combo: str) -> None:
        try:
            return super().press_keys(combo)
        except ToolError:
            pass
        from .. import keys

        vks = []
        for p in keys.split_combo(keys.expand_mod(combo)):
            name = p[1:-1] if (p.startswith("<") and len(p) > 2) else p
            if name in VK:
                vks.append(VK[name])
            elif len(name) == 1 and name.isalnum():
                vks.append(ord(name.upper()))
            else:
                raise ToolError("Install pynput to send this key combination: pip install pynput")
        tap(*vks)

    def open(self, target: str) -> None:
        try:
            os.startfile(target[4:] if target.startswith("app:") else target)  # type: ignore[attr-defined]
        except OSError as e:
            raise ToolError(f"Couldn't open {target}: {e}")

    def frontmost_app(self) -> str | None:
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            return buf.value[:120] or None
        except Exception:
            return None

    def read_focused_box(self) -> tuple | None:
        """Screen rectangle of the element with keyboard focus, via UI Automation, or None."""
        from . import win_uia

        el = win_uia.focused_element()
        return el.get("bounds") if el else None

    def checks(self):
        out = []
        out.append(("ok", "64-bit Python", "yes") if is_64bit_python() else
                   ("warn", "64-bit Python", "no: the magnifier and color filters need 64-bit Python"))
        from . import win_uia

        out.extend(win_uia.checks())
        try:
            ctypes.WinDLL("Magnification.dll")
            out.append(("ok", "Magnification API", "available"))
        except OSError:
            out.append(("warn", "Magnification API", "Magnification.dll not found"))
        if magnifier_app_open():
            out.append(("warn", "Windows Magnifier", "open: Ally closes it when you use Ally's magnifier"))
        out.append(("ok", "Speech", "Windows PowerShell (System.Speech)") if which("powershell.exe", "powershell")
                   else ("warn", "Speech", "powershell.exe not found"))
        return out

    def shutdown(self) -> None:
        if self._speech is not None:
            self._speech.close()
        if self._mag is not None and self._mag.ok and self._mag.is_alive():
            try:
                self._mag.call("stop", None, timeout=3)
            except ToolError:
                pass
