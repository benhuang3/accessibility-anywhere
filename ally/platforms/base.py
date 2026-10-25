"""Shared OS helpers. Each OS file overrides what it supports; the rest raise a clear ToolError."""

from __future__ import annotations

import io
import logging
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ..registry import ToolError

log = logging.getLogger("ally.os")

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # hide console windows on Windows
MAG_STEPS = [1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0]


def which(*names: str) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def run(argv: list[str], timeout: float = 10, input_bytes: bytes | None = None, check: bool = False) -> subprocess.CompletedProcess:
    """Run a helper command quietly. Raises ToolError if it can't start or times out."""
    try:
        cp = subprocess.run(argv, input=input_bytes, capture_output=True, timeout=timeout,
                            creationflags=NO_WINDOW)
    except FileNotFoundError:
        raise ToolError(f"'{argv[0]}' is not installed on this computer.")
    except subprocess.TimeoutExpired:
        raise ToolError(f"'{Path(argv[0]).name}' took too long.")
    if check and cp.returncode != 0:
        err = (cp.stderr or b"").decode("utf-8", "replace").strip()[:300]
        raise ToolError(f"'{Path(argv[0]).name}' failed: {err or 'exit code ' + str(cp.returncode)}")
    return cp


def popen(argv: list[str], stdin_bytes: bytes | None = None) -> subprocess.Popen:
    p = subprocess.Popen(argv, stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    if stdin_bytes is not None:
        try:
            p.stdin.write(stdin_bytes)
            p.stdin.close()
        except OSError:
            pass
    return p


def edge_speed(n: float, band: float = 0.18) -> float:
    """Pan speed for eyes at fraction n (0..1) across the view: 0 in the middle, up to -1/+1 at an edge."""
    if n < band:
        return -min(1.0, (band - n) / band)
    if n > 1 - band:
        return min(1.0, (n - (1 - band)) / band)
    return 0.0


def next_level(current: float, up: bool, steps: list[float] = MAG_STEPS) -> float:
    if up:
        return next((s for s in steps if s > current + 1e-3), steps[-1])
    return next((s for s in reversed(steps) if s < current - 1e-3), steps[0])


def png_to_jpeg_if_possible(png: bytes, max_side: int = 1568) -> tuple[bytes, str]:
    """Shrink a screenshot for Claude. Uses Pillow if installed, else returns the PNG."""
    try:
        from PIL import Image  # type: ignore
    except Exception:
        return png, "image/png"
    try:
        img = Image.open(io.BytesIO(png))
        img = img.convert("RGB")
        w, h = img.size
        scale = min(1.0, max_side / max(w, h))
        if scale < 1.0:
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, "JPEG", quality=80, optimize=True)
        return out.getvalue(), "image/jpeg"
    except Exception as e:
        log.warning("could not shrink screenshot: %s", e)
        return png, "image/png"


class Platform:
    name = "unknown"
    label = "this computer"

    def __init__(self):
        self.magnify_level = 1.0
        self.last_magnify = 2.0
        self.color = "none"
        self.text_scale_factor = 1.0

    # ---- screen
    def magnify(self, level: float) -> str:
        raise ToolError(f"Screen magnification isn't supported on {self.label} yet.")

    def magnify_at(self, level: float, x: float, y: float, follow: str = "eyes") -> str:
        """Magnify around a screen point. Default: put the pointer there, since macOS Zoom and the
        GNOME magnifier follow the pointer. (Windows centers the view exactly and can follow the eyes.)"""
        self.move_pointer(int(x), int(y))
        return self.magnify(level)

    def set_follow(self, mode: str) -> str:
        raise ToolError(f"The magnifier can only follow the mouse on {self.label}.")

    def view(self) -> tuple[float, int, int]:
        """(zoom level, left, top) of the magnified view in unmagnified screen pixels."""
        return self.magnify_level, 0, 0

    def other_magnifier_open(self) -> bool:
        """True if the system's own magnifier app is zooming the screen (not Ally)."""
        return False

    def read_focused_box(self) -> tuple | None:
        """Screen rectangle (x, y, w, h) of the focused text element in the front app, or None.

        Lets a magnifier center on exactly what the user is editing or reading. The default
        can't see other apps' focus, so it returns None and callers magnify the whole screen."""
        return None

    def show_text_overlay(self, box, text: str) -> str:
        """Show `text` big on a reading card (optionally near screen rectangle `box`), for apps
        whose own text can't be edited. The default has no overlay and says so."""
        raise ToolError(f"Showing replacement text isn't supported on {self.label} yet.")

    def clear_text_overlays(self) -> None:
        """Remove any reading cards shown by show_text_overlay. Default: nothing to clear."""

    def set_gaze_source(self, latest, pan_speed: float = 1.0) -> None:
        """latest() -> where the eyes are on the screen (or None); a magnifier can pan with it."""

    def move_pointer(self, x: int, y: int) -> None:
        try:
            from pynput.mouse import Controller  # type: ignore
        except Exception:
            raise ToolError("Moving the pointer needs pynput: pip install pynput")
        Controller().position = (x, y)

    def color_filter(self, kind: str) -> str:
        raise ToolError(f"Screen color filters aren't supported on {self.label} yet.")

    def text_scale(self, factor: float) -> str:
        raise ToolError(f"System-wide text size can't be changed by Ally on {self.label}. "
                        "Use page zoom in Chrome or the screen magnifier instead.")

    def screenshot_png(self) -> bytes:
        try:
            import mss  # type: ignore
            import mss.tools  # type: ignore

            with mss.mss() as sct:
                mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                shot = sct.grab(mon)
                return mss.tools.to_png(shot.rgb, shot.size)
        except ImportError:
            pass
        except Exception as e:
            log.warning("mss screenshot failed: %s", e)
        return self._screenshot_fallback()

    def _screenshot_fallback(self) -> bytes:
        raise ToolError("Can't take a screenshot here. Install mss: pip install mss")

    # ---- speech
    def tts_argv(self, text: str, rate: float, voice: str) -> tuple[list[str], bytes | None]:
        raise ToolError("No text-to-speech program found.")

    def tts_cancel(self) -> None:
        pass

    def play_wav_argv(self, path: str) -> list[str]:
        raise ToolError("No audio player found.")

    def tts_start(self, text: str, rate: float, voice: str):
        """Start speaking. Returns a Popen, or anything with its wait/poll/terminate/kill."""
        argv, stdin = self.tts_argv(text, rate, voice)
        return popen(argv, stdin)

    def play_wav_start(self, path: str):
        """Start playing a WAV file. Returns a Popen, or anything with its wait/poll/terminate/kill."""
        return popen(self.play_wav_argv(path))

    # ---- input / apps
    def get_selected_text(self) -> str:
        raise ToolError(f"Reading selected text isn't supported on {self.label} yet.")

    def press_keys(self, combo: str) -> None:
        """Press a key combo like '<ctrl>+c' using pynput (if installed)."""
        from .. import keys

        try:
            from pynput.keyboard import Controller, Key, KeyCode  # type: ignore
        except Exception:
            raise ToolError("Sending keys needs pynput: pip install pynput")
        parts = keys.split_combo(keys.expand_mod(combo))
        resolved = []
        for p in parts:
            if len(p) > 2 and p[0] == "<" and p[-1] == ">":
                name = p[1:-1].lower()
                resolved.append(KeyCode.from_vk(int(name)) if name.isdigit() else Key[name])
            else:
                resolved.append(p)
        kb = Controller()
        for k in resolved:
            kb.press(k)
        time.sleep(0.03)
        for k in reversed(resolved):
            kb.release(k)

    def open(self, target: str) -> None:
        raise ToolError(f"Opening things isn't supported on {self.label} yet.")

    def notify(self, title: str, text: str) -> None:
        log.info("notify: %s: %s", title, text)

    def frontmost_app(self) -> str | None:
        return None

    def shell(self, command: str, timeout: float = 30) -> str:
        try:
            cp = subprocess.run(command, shell=True, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired:
            raise ToolError("The command took too long.")
        out = (cp.stdout or b"").decode("utf-8", "replace").strip()
        if cp.returncode != 0:
            err = (cp.stderr or b"").decode("utf-8", "replace").strip()
            raise ToolError(f"Command failed ({cp.returncode}): {err[:300] or out[:300]}")
        return out[:2000]

    # ---- lifecycle
    def warm_up(self) -> None:
        """Get slow-starting helpers ready. Called once when the agent starts."""

    # ---- health
    def checks(self) -> list[tuple[str, str, str]]:
        """(status, name, detail) where status is ok | warn | fail."""
        return []

    def shutdown(self) -> None:
        """Undo screen-wide changes when Ally stops."""


def tempfile_path(suffix: str) -> str:
    f = tempfile.NamedTemporaryFile(prefix="ally-", suffix=suffix, delete=False)
    f.close()
    return f.name


def is_64bit_python() -> bool:
    return sys.maxsize > 2**32
