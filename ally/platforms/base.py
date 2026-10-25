"""Shared OS helpers. Each OS file overrides what it supports; the rest raise a clear ToolError."""

from __future__ import annotations

import io
import logging
import shutil
import subprocess
import sys
import tempfile
import time
from collections import deque
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


# The gaze has to sit in the edge band this long before the view starts to move.
EDGE_DWELL = 0.6
# How quickly the pan velocity eases toward its target. A look that only brushes the edge
# does not kick the view, and a change of speed never jumps.
PAN_EASE = 0.2


def edge_speed(n: float, band: float = 0.18) -> float:
    """Pan speed for eyes at fraction n (0..1) across the view: 0 in the middle, up to -1/+1 at an edge.

    The ramp is quadratic in how far into the edge band the gaze is, so a look just inside
    the band barely moves the view.
    """
    if n < band:
        depth = min(1.0, (band - n) / band)
        return -(depth * depth)
    if n > 1 - band:
        depth = min(1.0, (n - (1 - band)) / band)
        return depth * depth
    return 0.0


def ease_pan(nx: float, ny: float, held: tuple[float, float], vel: tuple[float, float], dt: float,
             dwell: float = EDGE_DWELL, smooth: float = PAN_EASE) -> tuple[float, float, tuple[float, float]]:
    """Velocity for one magnifier frame, and how long the gaze has already sat in each edge.

    `nx` and `ny` are where the eyes are, as a fraction of the screen. Panning on an axis
    stays at rest until the gaze has been in that edge for `dwell` seconds. The velocity
    then eases toward the quadratic edge speed, so it never jumps.
    """
    tx, ty = edge_speed(nx), edge_speed(ny)
    dt = max(0.0, float(dt))
    hx = (held[0] + dt) if tx else 0.0
    hy = (held[1] + dt) if ty else 0.0
    if hx < dwell:
        tx = 0.0
    if hy < dwell:
        ty = 0.0
    alpha = 1.0 if smooth <= 0 else min(1.0, dt / smooth)
    vx = vel[0] + (tx - vel[0]) * alpha
    vy = vel[1] + (ty - vel[1]) * alpha
    if abs(vx) < 1e-3:
        vx = 0.0
    if abs(vy) < 1e-3:
        vy = 0.0
    return vx, vy, (hx, hy)


# Reading with the magnifier. While the eyes move forward along a line, the view pans right at the
# pace they read and keeps the words being read near the middle. A jump back to the left side after
# reading a stretch is the start of the next line, so the view goes back to where the line began.
# Looking at an edge still pans that way.
READ_WINDOW = 1.2      # seconds of gaze used to decide whether the user is reading forward
READ_PROGRESS = 0.05   # forward progress along the text in that time, as a fraction of the view
READ_MAX_JUMP = 0.15   # one bigger jump is a look somewhere else, not reading
READ_STEPS = 4         # the window is cut into this many parts; none may be further back ...
READ_BACK = 0.02       # ... than this (fraction of the view) from the one before, and all but one
READ_FORWARD = 0.01    # ... must be at least this much further on: forward step by step
READ_MAX_RISE = 0.12   # reading stays on one line (vertical spread, fraction of the screen)
READ_CONFIRM = 0.3     # reading has to hold this long before the view starts to move
READ_ANCHOR = 0.4      # the view pans to keep the words being read about here, with room to read on
READ_GAIN = 2.0        # pan speed per unit past the anchor, in views per second at the default pan_speed
READ_MAX = 0.8         # views per second; the reader's pace sets the speed, pan_speed only how eagerly
READ_EASE = 0.5        # seconds for the reading pan speed to ease toward its target
RETURN_JUMP = 0.35     # a jump left this far (fraction of the view) ...
RETURN_TO = 0.3        # ... that lands left of here ...
RETURN_DWELL = 0.25    # ... and stays this long goes back to the start of the line,
RETURN_AFTER = 1.5     # if the user was reading this recently
RETURN_TIME = 0.8      # seconds to carry the view back (longer when the top speed would be passed)
RETURN_BOOST = 2.0     # the trip back may peak at this many times the top speed


class ReadingPan:
    """Where the magnified view should be, frame by frame, while it follows the eyes."""

    def __init__(self):
        self.t = 0.0
        self.trail = deque()  # (t, x along the text in screen px, nx, ny)
        self.vel = 0.0  # reading pan speed, in views per second before pan_speed
        self.held, self.edge = (0.0, 0.0), (0.0, 0.0)  # ease_pan state for the edges
        self.line_start = None  # view x where reading this line began
        self.read_at = None  # when the eyes were last seen reading
        self.read_since = None  # when the current stretch of reading began
        self.left_since = None
        self.returning = None  # (from x, from y, to x, to y, started) while carrying the view back
        self.return_time = RETURN_TIME

    def reading(self, vw: float) -> bool:
        """The eyes moved forward along one line in small steps over the last READ_WINDOW."""
        if len(self.trail) < 4 or self.trail[-1][0] - self.trail[0][0] < READ_WINDOW / 2:
            return False
        rows = list(self.trail)
        if max(r[3] for r in rows) - min(r[3] for r in rows) > READ_MAX_RISE:
            return False
        if any(abs(b[1] - a[1]) > READ_MAX_JUMP * vw for a, b in zip(rows, rows[1:])):
            return False
        t0, span = rows[0][0], rows[-1][0] - rows[0][0]
        parts = [sorted(r[1] for r in rows if i <= (r[0] - t0) / span * READ_STEPS < i + 1 or
                        (i == READ_STEPS - 1 and r[0] - t0 >= span)) for i in range(READ_STEPS)]
        if not all(parts):
            return False
        mids = [p[len(p) // 2] for p in parts]
        steps = [b - a for a, b in zip(mids, mids[1:])]
        if any(d < -READ_BACK * vw for d in steps) or sum(d >= READ_FORWARD * vw for d in steps) < len(steps) - 1:
            return False
        return mids[-1] - mids[0] >= READ_PROGRESS * vw

    def step(self, gaze, pos, view, limit, dt: float, speed: float = 1.0,
             max_speed: float | None = None) -> tuple[float, float]:
        """The view's new top-left after one frame.

        `gaze` is (nx, ny), where the eyes are as a fraction of the screen, or None. `pos` is the
        view's top-left and `view` its size, in unmagnified screen pixels; `limit` is the largest
        top-left. `speed` is the user's pan_speed: it scales edge panning, and how eagerly the
        view keeps up while reading. `max_speed` caps any pan, in views per second (on the glass,
        screen widths per second); None means no cap.
        """
        dt = max(0.0, float(dt))
        self.t += dt
        x, y = pos
        vw, vh = view
        if gaze is not None:
            nx, ny = gaze
            self.trail.append((self.t, x + nx * vw, nx, ny))
        while self.trail and self.t - self.trail[0][0] > READ_WINDOW:
            self.trail.popleft()

        if self.returning is not None:
            fx, fy, tx, ty, t0 = self.returning
            k = min(1.0, (self.t - t0) / self.return_time)
            k = k * k * (3 - 2 * k)  # eases in and out
            if k >= 1.0:
                self.returning = None
                self.trail.clear()
            return fx + (tx - fx) * k, fy + (ty - fy) * k

        if gaze is None:
            nx, ny = 0.5, 0.5
        reading = gaze is not None and self.reading(vw)
        if reading:
            if self.read_at is None or self.t - self.read_at > READ_CONFIRM:
                self.read_since = self.t  # a new stretch; a blip of a frame or two doesn't end one
            self.read_at = self.t
            if self.line_start is None:
                self.line_start = x

        # The start of the next line: a big jump left, after the view has moved along this one.
        recent = [r[2] for r in self.trail if self.t - r[0] <= RETURN_DWELL + 0.6]
        if (gaze is not None and self.line_start is not None and x > self.line_start + 0.05 * vw
                and self.read_at is not None and self.t - self.read_at <= RETURN_AFTER and nx < RETURN_TO and recent and max(recent) - nx >= RETURN_JUMP):
            self.left_since = self.left_since if self.left_since is not None else self.t
        elif gaze is None or nx >= RETURN_TO:
            self.left_since = None
        if self.left_since is not None and self.t - self.left_since >= RETURN_DWELL:
            tx = self.line_start
            # Bring the new line toward the middle when it is low in the view.
            ty = min(max(y + (ny - 0.5) * vh, 0.0), limit[1]) if ny > 0.62 else y
            self.returning = (x, y, tx, ty, self.t)
            self.return_time = RETURN_TIME
            if max_speed:  # the eased trip peaks at 1.5 times its average speed
                trip = max(abs(x - tx) / max(vw, 1.0), abs(y - ty) / max(vh, 1.0))
                self.return_time = max(RETURN_TIME, 1.5 * trip / (RETURN_BOOST * max_speed))
            self.vel, self.edge, self.held, self.left_since = 0.0, (0.0, 0.0), (0.0, 0.0), None
            return x, y

        gain = READ_GAIN * (max(0.05, speed) / 0.35) ** 0.5
        confirmed = (gaze is not None and self.read_at is not None and self.t - self.read_at <= READ_CONFIRM
                     and self.t - self.read_since >= READ_CONFIRM)
        target = min(READ_MAX, gain * (nx - READ_ANCHOR)) if confirmed and nx > READ_ANCHOR else 0.0
        self.vel += (target - self.vel) * (1.0 if READ_EASE <= 0 else min(1.0, dt / READ_EASE))
        if abs(self.vel) < 1e-3:
            self.vel = 0.0
        ex, ey, self.held = ease_pan(nx, ny, self.held, self.edge, dt)
        self.edge = (ex, ey)
        if ex < 0:
            self.vel = 0.0
        vx = ex * speed if ex < 0 else max(ex * speed, self.vel)
        vy = ey * speed
        if max_speed:
            vx, vy = max(-max_speed, min(max_speed, vx)), max(-max_speed, min(max_speed, vy))
        x = min(max(x + vx * vw * dt, 0.0), limit[0])
        y = min(max(y + vy * vh * dt, 0.0), limit[1])
        if self.line_start is not None and x < self.line_start:
            self.line_start = x  # panned left past where the line began: that is the new start
        return x, y


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

    def set_gaze_source(self, latest, pan_speed: float = 1.0, max_speed: float | None = None) -> None:
        """latest() -> where the eyes are on the screen (or None); a magnifier can pan with it.

        max_speed caps the pan in screen widths per second; None keeps the current cap."""

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
