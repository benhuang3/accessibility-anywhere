"""Eye tracking in the agent. Runs ally.gaze_helper (the webcam) as a child process, restarts it if it
dies, and keeps the last ~45 s of gaze points so a tool can ask where the user was looking while
they spoke ("Ally, magnify what I'm looking at").

The same helper also feeds face numbers to struggle sensing (ally.senses) when [senses] vision is on,
so the camera runs when either feature wants it and is only ever opened by that one process.

The face model (MediaPipe Face Landmarker, ~3.7 MB) downloads once into state/models/.
The calibration is saved in state/gaze_calibration.json.
"""

from __future__ import annotations

import collections
import importlib.util
import json
import logging
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from . import gaze_model
from .gaze_helper import pose_count
from .claude import ssl_context
from .hotkeys import python_for_children
from .paths import CODE_ROOT
from .platforms.base import NO_WINDOW
from .registry import ToolError

log = logging.getLogger("ally.gaze")

# A fixation is a look that stays inside a box this size (width + height, pixels) for 0.2 s.
FIXATION_RADIUS = 150
# High uncertainty, with the face still in view, for this long: offer a re-tune, then stay quiet.
UNSURE_OFFER_AFTER = 10.0
RETUNE_OFFER_EVERY = 300.0
RETUNE_OFFER = "Your position changed. Say check my eye tracker to re-tune it."
# During a setup or check, and this long after it, Ally keeps its other voices quiet: the person is
# moving on purpose (sit back, lean in), and an offer or a re-tune line would talk over the setup.
SETUP_QUIET_AFTER = 5.0

FACE_MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/"
                  "float16/latest/face_landmarker.task")


def has_gaze_packages() -> bool:
    try:
        return all(importlib.util.find_spec(m) is not None for m in ("mediapipe", "cv2", "numpy"))
    except (ImportError, ValueError):
        return False


def download_face_model(path: Path, timeout: float = 60) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    log.info("downloading the face model (3.7 MB) to %s", path)
    with urllib.request.urlopen(FACE_MODEL_URL, timeout=timeout, context=ssl_context()) as resp:
        data = resp.read()
    if len(data) < 1_000_000:
        raise OSError(f"the face model download looks wrong ({len(data)} bytes)")
    tmp = path.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.replace(path)


def choice_center(screen, n: int, index: int) -> tuple[float, float]:
    """Centre of the card area `choice_index` uses, in screen pixels."""
    w = max(int(screen[0]), 1)
    h = max(int(screen[1]), 1)
    n = int(n)
    index = int(index)
    if n <= 2:
        col = 0 if index <= 0 else 1
        return (col + 0.5) * w / 2.0, h / 2.0
    if n == 3:
        col = min(2, max(0, index))
        return (col + 0.5) * w / 3.0, h / 2.0
    col = index % 2
    row = 0 if index < 2 else 1
    return (col + 0.5) * w / 2.0, (row + 0.5) * h / 2.0


def choice_index(x: float, y: float, screen, n: int) -> int:
    """Which large area a point is in. 2: left/right. 3: columns. 4: quadrants, top-left first."""
    w = max(int(screen[0]), 1)
    h = max(int(screen[1]), 1)
    x = min(max(float(x), 0.0), w - 1.0)
    y = min(max(float(y), 0.0), h - 1.0)
    n = int(n)
    if n <= 2:
        return 0 if x < w / 2.0 else 1
    if n == 3:
        return min(2, int(3 * x / w))
    col = 0 if x < w / 2.0 else 1
    row = 0 if y < h / 2.0 else 1
    return row * 2 + col


def dwell_choice(points, screen, n: int, now: float, hold: float = 0.8, gap: float = 0.4):
    """(area index, start, end) when the latest points have stayed in one card for `hold` seconds.

    Region-sized areas, so this still works when the tracker is only okay. None when they have not.
    """
    now = float(now)
    rows = [p for p in points if now - (float(hold) + 0.6) <= p[0] <= now]
    if len(rows) < 2 or now - rows[-1][0] > gap:
        return None
    idx = choice_index(rows[-1][1], rows[-1][2], screen, n)
    # The unbroken run of looks in this area that ends at the newest point. Frames arrive about
    # every 50-100 ms, so a run that started `hold` seconds ago spans up to one frame less than
    # `hold`; allow that much (cutting the window at `hold` first made a dwell almost never count).
    start = rows[-1][0]
    for prev, cur in zip(reversed(rows[:-1]), reversed(rows[1:])):
        if cur[0] - prev[0] > gap or choice_index(prev[1], prev[2], screen, n) != idx:
            break
        start = prev[0]
    if rows[-1][0] - start + 0.1 < float(hold):
        return None
    return idx, float(start), float(rows[-1][0])


def ongoing_choice(points, screen, n: int, now: float, hold: float = 0.8, gap: float = 0.4):
    """Area index the latest points have stayed in for `hold` seconds, or None."""
    found = dwell_choice(points, screen, n, now, hold, gap)
    return None if found is None else found[0]


def cursor_pos() -> tuple[int, int] | None:
    """The mouse pointer, on Windows. None when it can't be read."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        pt = POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            return int(pt.x), int(pt.y)
    except Exception:
        return None
    return None


class GazeManager:
    def __init__(self, agent):
        self.agent = agent
        self.proc: subprocess.Popen | None = None
        self.points: collections.deque = collections.deque(maxlen=900)  # (time, x, y), ~45 s at 20 fps
        self.ready = False
        self.calibrated = False
        self.error_px: int | None = None
        self.error = ""
        self.face_at = 0.0
        self.override: bool | None = None  # "turn off eye tracking" lasts until Ally restarts
        self.last_point: dict | None = None  # the last point a tool asked for, and how steady it was
        self._calib_done = threading.Event()
        self._calib_result: dict | None = None
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._crashes: list[float] = []
        self._signature = None
        self._gave_up = False
        self.screen = (1920, 1080)
        self._unsure_since: float | None = None
        self._retune_offer_at = 0.0
        self.setting_up = False  # a calibration or check is on screen
        self._setup_ended = -1e9
        self._view_sent = None  # last "view LEVEL LEFT TOP" sent to the camera helper

    @property
    def cfg(self) -> dict:
        return self.agent.config.data["gaze"]

    @property
    def model_path(self) -> Path:
        return self.agent.paths.state_dir / "models" / "face_landmarker.task"

    @property
    def calibration_path(self) -> Path:
        return self.agent.paths.state_dir / "gaze_calibration.json"

    def wanted(self) -> bool:
        """Eye tracking itself (gaze points) is wanted."""
        return self.override if self.override is not None else bool(self.cfg.get("enabled"))

    def senses_wanted(self) -> bool:
        senses = getattr(self.agent, "senses", None)
        return bool(senses is not None and senses.vision_wanted())

    def camera_wanted(self) -> bool:
        return self.wanted() or self.senses_wanted()

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    # ---------------------------------------------------------------- process
    def apply(self) -> None:
        """Start or stop the camera helper to match config (or the voice on/off switch)."""
        if not self.camera_wanted() or not has_gaze_packages():
            with self._lock:
                self._kill()
            self._signature = None
            if self.camera_wanted():
                self.error = "eye tracking and face sensing need: pip install mediapipe opencv-python"
            return
        signature = (int(self.cfg.get("camera", 0)), int(self.cfg.get("fps", 20)), self.wanted(),
                     self.senses_wanted(), self._senses_fps(), str(self.cfg.get("features", "v1")),
                     bool(self.cfg.get("click_learning", True)))
        if signature == self._signature and (self.running or self._gave_up):
            self._push_dot()
            return
        self._signature, self._gave_up, self._crashes = signature, False, []
        threading.Thread(target=self._restart, name="gaze-start", daemon=True).start()

    def _restart(self) -> None:
        with self._lock:
            self._kill()
            self.error = ""
            if not self.model_path.exists():
                try:
                    download_face_model(self.model_path)
                except Exception as e:
                    self.error = f"couldn't download the face model: {e}"[:300]
                    self._gave_up = True
                    log.warning("gaze: %s", self.error)
                    return
            args = {"model": str(self.model_path), "calibration": str(self.calibration_path),
                    "state": str(self.agent.paths.state_dir),
                    "camera": int(self.cfg.get("camera", 0)), "fps": int(self.cfg.get("fps", 20)),
                    "gaze": self.wanted(), "signals": self.senses_wanted(), "signals_fps": self._senses_fps(),
                    "features": self.cfg.get("features", "v1"),
                    "click_learning": bool(self.cfg.get("click_learning", True)),
                    "show_dot": bool(self.cfg.get("show_dot", False))}
            self._view_sent = None
            try:
                self.proc = subprocess.Popen(
                    [python_for_children(), "-m", "ally.gaze_helper", json.dumps(args)], cwd=str(CODE_ROOT),
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
            except OSError as e:
                self.error = f"couldn't start the eye tracker: {e}"
                return
            self._dot_sent = bool(self.cfg.get("show_dot", False))
            proc = self.proc
        threading.Thread(target=self._read_stdout, args=(proc,), daemon=True, name="gaze-out").start()
        threading.Thread(target=self._read_stderr, args=(proc,), daemon=True, name="gaze-err").start()

    def _read_stdout(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "x" in msg:
                row = (float(msg["t"]), int(msg["x"]), int(msg["y"]))
                if msg.get("r") is not None:
                    try:
                        row = row + (float(msg["r"]),)
                    except (TypeError, ValueError):
                        pass
                self.points.append(row)
                self.face_at = time.time()
                self._sync_view()
            elif "f" in msg:
                self.face_at = time.time()
                senses = getattr(self.agent, "senses", None)
                if senses is not None:
                    try:
                        senses.on_face(float(msg["t"]), msg["f"])
                    except Exception:
                        log.exception("face signal failed")
            elif "face" in msg:
                if msg["face"]:
                    self.face_at = time.time()
            elif "calibrated" in msg and "ready" not in msg:
                if msg["calibrated"]:
                    self.calibrated, self.error_px = True, msg.get("error_px")
                    self.points.clear()
                self._calib_result = msg
                self._calib_done.set()
            elif msg.get("ready"):
                self.ready, self.error = True, ""
                self.calibrated, self.error_px = bool(msg.get("calibrated")), msg.get("error_px")
                scr = msg.get("screen")
                if isinstance(scr, (list, tuple)) and len(scr) >= 2:
                    try:
                        self.screen = (int(scr[0]), int(scr[1]))
                    except (TypeError, ValueError):
                        pass
                log.info("eye tracker on (%s)", "calibrated" if self.calibrated else "needs calibration")
            elif msg.get("camera_off"):
                # The reader thread must not wait on the helper: closing stdin from here can deadlock.
                threading.Thread(target=self._on_camera_off, daemon=True, name="gaze-off").start()
            elif msg.get("say"):
                self._speak(str(msg["say"]))
            elif "error" in msg:
                self.error = str(msg["error"])[:300]
                log.warning("eye tracker: %s", self.error)
        self.ready = False
        self._calib_done.set()  # nobody waits forever on a dead helper
        try:
            proc.stdout.close()
        except OSError:
            pass

    def _read_stderr(self, proc: subprocess.Popen) -> None:
        for line in proc.stderr:  # MediaPipe logs a lot at start; keep it out of ally.log unless debugging
            if line.strip():
                log.debug("gaze helper: %s", line.strip()[:300])
        try:
            proc.stderr.close()
        except OSError:
            pass

    def ensure_running(self) -> None:
        """Called every few seconds: restart a crashed helper (with limits)."""
        if not self.camera_wanted() or self._signature is None or self._gave_up:
            return
        proc = self.proc
        if proc is None or proc.poll() is None:
            return
        if proc.returncode in (2, 3):  # missing packages or face model: restarting won't help
            self._gave_up = True
            return
        now = time.time()
        self._crashes = [t for t in self._crashes if now - t < 120] + [now]
        if len(self._crashes) > 4:
            self._gave_up = True
            self.error = self.error or "the eye tracker keeps stopping (see logs/ally.log)"
            return
        log.warning("eye tracker exited (%s); restarting", proc.returncode)
        threading.Thread(target=self._restart, name="gaze-start", daemon=True).start()

    def _kill(self) -> None:
        proc, self.proc = self.proc, None
        self.ready = False
        if proc is None:
            return
        try:
            proc.stdin.close()  # the helper exits when its stdin closes
            proc.wait(timeout=3)
        except Exception:
            proc.kill()

    def stop(self) -> None:
        with self._lock:
            self._kill()
        self._signature = None

    def _senses_fps(self) -> int:
        senses = getattr(self.agent, "senses", None)
        return int(senses.cfg.get("fps", 10)) if senses is not None else 10

    def _current_view(self):
        """(level, left, top) of the magnified view. The magnifier thread's own numbers when it
        is running, so a poll does not queue a command and stall the pan. Otherwise platform.view()."""
        platform = getattr(self.agent, "platform", None)
        if platform is None:
            return None
        mag = getattr(platform, "_mag", None)
        if mag is not None and getattr(mag, "ok", False):
            off = getattr(mag, "offset", (0, 0)) or (0, 0)
            try:
                return float(getattr(mag, "level", 1.0) or 1.0), int(off[0]), int(off[1])
            except (TypeError, ValueError, IndexError):
                return None
        view = getattr(platform, "view", None)
        if view is None:
            return None
        try:
            level, left, top = view()
            return float(level), int(left), int(top)
        except Exception:
            return None

    def _sync_view(self) -> None:
        """Tell the camera helper when the magnified view moves, so the gaze dot stays on the glass."""
        if not bool(self.cfg.get("show_dot", False)) and not getattr(self, "_dot_sent", False):
            return
        got = self._current_view()
        if got is None:
            return
        level, left, top = got
        key = (round(level, 3), left, top)
        if key == self._view_sent:
            return
        if self.send(f"view {level:g} {left} {top}"):
            self._view_sent = key

    def _push_dot(self) -> None:
        """Send show/hide when [gaze] show_dot changed and the helper is already running."""
        if not self.running:
            return
        on = bool(self.cfg.get("show_dot", False))
        if on == getattr(self, "_dot_sent", None):
            return
        self.set_show_dot(on)

    def set_show_dot(self, on: bool) -> None:
        """Show or hide the gaze circle in the camera helper. The helper draws it, so there is no lag."""
        on = bool(on)
        self._dot_sent = on
        self.send("dot on" if on else "dot off")
        if on:
            self._view_sent = None
            self._sync_view()

    def on_click(self, t: float, x: int, y: int) -> bool:
        """Tell the camera helper about a mouse click so it can learn. The click stays on this computer."""
        if not bool(self.cfg.get("click_learning", True)):
            return False
        return self.send(f"click {float(t):.3f} {int(x)} {int(y)}")

    def send(self, line: str) -> bool:
        """One command line to the camera helper ("calibrate", "hud on", "hud [...]", "hud off")."""
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return False
        try:
            with self._send_lock:  # the HUD and calibration write from different threads
                proc.stdin.write(line.replace("\n", " ") + "\n")
                proc.stdin.flush()
            return True
        except (OSError, ValueError, AttributeError):
            return False

    def set_enabled(self, on: bool) -> None:
        self.override = on
        if on:
            self._signature, self._gave_up = None, False
        self.apply()

    def _on_camera_off(self) -> None:
        """The on-screen indicator was clicked: eye tracking off, face sensing off, camera closed."""
        # Set the gaze switch first. set_vision() calls apply(), and if gaze were still wanted that
        # would restart the camera for eye tracking alone.
        self.override = False
        senses = getattr(self.agent, "senses", None)
        if senses is not None and hasattr(senses, "set_vision"):
            try:
                senses.set_vision(False)
            except Exception:
                log.exception("couldn't turn off face sensing")
        self.set_enabled(False)
        try:
            self.agent.events.add("gaze", "Camera turned off.")
        except Exception:
            log.exception("couldn't record the camera-off event")

    # ---------------------------------------------------------------- queries
    def info(self) -> dict:
        return {"status": self.status(), "error": self.error, "calibrated": self.calibrated,
                "error_px": self.error_px}

    def status(self) -> str:
        if not self.wanted():
            return "off"
        if self.error and not self.ready:
            return "error"
        if not self.ready:
            return "starting"
        if not self.calibrated:
            return "needs calibration"
        return "tracking" if time.time() - self.face_at < 2.0 else "can't see you"

    def problem(self) -> ToolError:
        s = self.status()
        if s == "off":
            return ToolError("Eye tracking is off. Say: turn on eye tracking.")
        if s == "error":
            return ToolError(f"Eye tracking isn't working: {self.error}")
        if s == "starting":
            return ToolError("The eye tracker is still starting. Try again in a few seconds.")
        if s == "needs calibration":
            return ToolError("The eye tracker needs a setup first. Say: calibrate my eyes.")
        return ToolError("I can't see your eyes. Face the screen and the camera, then try again.")

    def latest(self, max_age: float = 0.3) -> tuple[int, int] | None:
        """The newest gaze point, if it's fresh (used by the magnifier to pan with the eyes)."""
        if not self.points:
            return None
        t, x, y = self.points[-1][0], self.points[-1][1], self.points[-1][2]
        return (x, y) if time.time() - t <= max_age else None

    def point(self, t0: float, t1: float) -> tuple[int, int]:
        """Median gaze point between two wall-clock times (steady even if the eyes flicked away)."""
        pts = [(item[1], item[2]) for item in list(self.points) if t0 <= item[0] <= t1]
        if len(pts) < 3:
            raise self.problem() if self.status() != "tracking" else ToolError(
                "I couldn't follow your eyes just then. Look at the spot and try again.")
        mx, my = int(statistics.median(p[0] for p in pts)), int(statistics.median(p[1] for p in pts))
        # How steady the eyes were (median distance from that point), shown in the event log.
        spread = int(statistics.median(((x - mx) ** 2 + (y - my) ** 2) ** 0.5 for x, y in pts))
        self.last_point = {"x": mx, "y": my, "samples": len(pts), "spread_px": spread}
        return mx, my

    def point_for_request(self) -> tuple[int, int]:
        """Where the user looked while saying the current request, or in the last second."""
        if self.status() != "tracking":
            raise self.problem()
        now = time.time()
        said = getattr(self.agent, "last_utterance", None)
        if said and now - said[1] < 20:
            try:
                return self.point(said[0] - 0.3, said[1] + 0.2)
            except ToolError:
                pass
        return self.point(now - 1.0, now)

    def target(self, t0: float | None = None, t1: float | None = None) -> dict:
        """Where the user is looking in a time window, or over the last 1.5 s.

        Returns x, y, radius_px, confidence, region, source ("eyes" or "mouse") and why.
        The mouse pointer is used when the eyes aren't calibrated, aren't visible, didn't hold
        still, or the uncertainty is above [gaze] max_radius_px.
        """
        why = self._eyes_blocked()
        if why:
            self._note_uncertainty(False)
            return self._from_mouse(why)
        now = time.time()
        explicit = t0 is not None and t1 is not None
        if not explicit:
            said = getattr(self.agent, "last_utterance", None)
            if said and now - float(said[1]) < 20:
                t0, t1 = float(said[0]) - 0.3, float(said[1]) + 0.2
            else:
                t0, t1 = now - 1.5, now
        found = self._fixation(float(t0), float(t1))
        if found is None and not explicit:
            said = getattr(self.agent, "last_utterance", None)
            if said and now - float(said[1]) < 20:
                found = self._fixation(now - 1.5, now)
        if found is None:
            self._note_uncertainty(False)
            return self._from_mouse("Your eyes didn't stay on one spot, so this is the mouse pointer.")
        point, radius = found
        limit = self._max_radius()
        if radius > limit:
            self._note_uncertainty(True)
            return self._from_mouse("The eye tracker isn't sure enough, so this is the mouse pointer.")
        self._note_uncertainty(False)
        x, y = int(round(point["x"])), int(round(point["y"]))
        region = gaze_model.region(x, y, self.screen)
        span = max(limit * 2.0, 1.0)
        confidence = round(max(0.0, min(1.0, 1.0 - float(radius) / span)), 2)
        self.last_point = {"x": x, "y": y, "samples": point.get("samples"),
                           "spread_px": int(round(point.get("spread_px") or 0)), "source": "eyes",
                           "radius_px": radius, "region": region}
        return {"x": x, "y": y, "radius_px": round(float(radius), 1), "confidence": confidence,
                "region": region, "source": "eyes", "why": ""}

    def _note_uncertainty(self, unsure: bool) -> None:
        """Offer a re-tune when the eyes stay too unsure, with the face still visible, for ~10 s."""
        now = time.time()
        if not unsure:
            self._unsure_since = None
            return
        if self._unsure_since is None:
            self._unsure_since = now
            return
        if now - self._unsure_since < UNSURE_OFFER_AFTER:
            return
        if self.busy():
            return
        if now - self._retune_offer_at < RETUNE_OFFER_EVERY:
            return
        self._retune_offer_at = now
        self._offer_retune()

    def _offer_retune(self) -> None:
        events = getattr(self.agent, "events", None)
        if events is not None:
            try:
                events.add("gaze", RETUNE_OFFER)
            except Exception:
                log.exception("couldn't record the eye-tracker re-tune offer")
        speaker = getattr(self.agent, "speaker", None)
        if speaker is not None:
            try:
                speaker.say(RETUNE_OFFER)
            except Exception:
                log.exception("couldn't say the eye-tracker re-tune offer")

    def _eyes_blocked(self) -> str | None:
        if not self.wanted():
            return "Eye tracking is off, so this is the mouse pointer."
        if not self.ready or self.status() == "error":
            return "The eye tracker isn't ready, so this is the mouse pointer."
        if not self.calibrated:
            return "The eye tracker isn't set up, so this is the mouse pointer."
        if time.time() - self.face_at >= 2.0:
            return "I can't see your eyes, so this is the mouse pointer."
        return None

    def _fixation(self, t0: float, t1: float):
        rows = [item for item in list(self.points) if t0 <= item[0] <= t1]
        if len(rows) < 2:
            return None
        found = gaze_model.fixation([(item[0], item[1], item[2]) for item in rows], t0, t1, FIXATION_RADIUS)
        if not found:
            return None
        radii = [float(item[3]) for item in rows if len(item) > 3 and found["start"] <= item[0] <= found["end"]]
        if radii:
            radius = float(statistics.median(radii))
        elif self.error_px:
            radius = float(self.error_px)
        else:
            radius = 200.0
        return found, radius

    def _from_mouse(self, why: str) -> dict:
        x, y = self._cursor()
        region = gaze_model.region(x, y, self.screen)
        self.last_point = {"x": x, "y": y, "samples": 0, "spread_px": None, "source": "mouse", "region": region}
        return {"x": x, "y": y, "radius_px": None, "confidence": 0.0, "region": region,
                "source": "mouse", "why": why}

    def _cursor(self) -> tuple[int, int]:
        senses = getattr(self.agent, "senses", None)
        pointer = getattr(senses, "pointer", None) if senses is not None else None
        if isinstance(pointer, (tuple, list)) and len(pointer) >= 2 and None not in (pointer[0], pointer[1]):
            return int(pointer[0]), int(pointer[1])
        found = cursor_pos()
        return found if found is not None else (0, 0)

    # ------------------------------------------------------------ answering offers by looking
    def attach_bus(self, bus) -> None:
        """Let the user answer the proactive layer's yes/no offers (ally.contracts.Offer) by looking."""
        bus.subscribe("offer", self._on_offer)

    def _on_offer(self, offer) -> None:
        # Runs on the coordinator's thread, which waits for the answer: never block it here.
        if not self.cfg.get("answer_offers", True) or self._eyes_blocked():
            return
        if self.error_px and float(self.error_px) > self._max_radius():
            return  # the saved check says the eyes aren't reliable enough to answer with
        threading.Thread(target=self._answer_offer, args=(offer,), name="gaze-offer", daemon=True).start()

    def _answer_offer(self, offer) -> None:
        options = [str(o).capitalize() for o in getattr(offer, "options", ("yes", "no"))]
        try:
            picked = self.choose(options, timeout=float(getattr(offer, "timeout", 8.0)),
                                 stop=lambda: bool(getattr(offer, "done", False)))
        except Exception:
            log.debug("couldn't show the offer for a look-to-answer", exc_info=True)
            return
        if picked and offer.resolve(picked.lower(), by="eyes"):  # first answer wins
            self.agent.events.add("gaze", f"answered {picked} by looking")

    def _max_radius(self) -> float:
        try:
            return float(self.cfg.get("max_radius_px", 250))
        except (TypeError, ValueError):
            return 250.0

    def busy(self) -> bool:
        """True while a setup or check is on screen, and for a few seconds after it."""
        return self.setting_up or time.monotonic() - self._setup_ended < SETUP_QUIET_AFTER

    def _speak(self, text: str) -> None:
        """Say a setup prompt. It waits for the sentence already playing instead of cutting it off."""
        speaker = getattr(self.agent, "speaker", None)
        if speaker is None or not text:
            return
        try:
            try:
                speaker.say(text, interrupt=False)
            except TypeError:  # a speaker without the interrupt option
                speaker.say(text)
        except Exception:
            log.debug("couldn't say the eye-tracker prompt", exc_info=True)

    def _setup_on(self) -> None:
        self.setting_up = True

    def _setup_off(self) -> None:
        self.setting_up = False
        self._setup_ended = time.monotonic()

    def calibrate(self, timeout: float = 180) -> dict:
        """Show the dots and wait for the result: {"calibrated": bool, "error_px" or "why"}."""
        if not self.wanted():
            self.set_enabled(True)
        end = time.monotonic() + 20
        while not self.ready:
            if time.monotonic() > end or (self.error and not self.running):
                raise self.problem()
            time.sleep(0.2)
        self._calib_done.clear()
        self._calib_result = None
        count = pose_count(self.cfg.get("calibration_poses", 3))
        self._setup_on()
        try:
            if not self.send(f"calibrate {count}"):
                raise ToolError("The eye tracker stopped. Try again in a few seconds.")
            if not self._calib_done.wait(timeout):
                raise ToolError("Calibration took too long.")
        finally:
            self._setup_off()
        return self._calib_result or {"calibrated": False, "why": "the eye tracker stopped"}

    def check(self, timeout: float = 40) -> dict:
        """Run only the 5 check dots and wait for the words and the saved validation numbers."""
        if not self.wanted():
            self.set_enabled(True)
        end = time.monotonic() + 20
        while not self.ready:
            if time.monotonic() > end or (self.error and not self.running):
                raise self.problem()
            time.sleep(0.2)
        self._calib_done.clear()
        self._calib_result = None
        self._setup_on()
        try:
            if not self.send("check"):
                raise ToolError("The eye tracker stopped. Try again in a few seconds.")
            if not self._calib_done.wait(timeout):
                raise ToolError("The eye tracker check took too long.")
        finally:
            self._setup_off()
        return self._calib_result or {"calibrated": False, "why": "the eye tracker stopped"}

    def choose(self, options, timeout: float = 8.0, hold: float = 0.8, clock=None, sleep=None, stop=None):
        """Show the options and return the one the gaze stays on for `hold` seconds, or None.

        stop: optional callable; when it returns True (the question was answered another way), the
        cards close and None is returned.
        """
        options = [" ".join(str(item).split()) for item in options]
        if not 2 <= len(options) <= 4 or not all(options):
            raise ToolError("Choose two, three, or four options.")
        clock = clock or time.time
        sleep = sleep or time.sleep
        self.send("choose " + json.dumps({"options": options, "seconds": float(timeout)}))
        deadline = clock() + float(timeout)
        try:
            while clock() < deadline and not (stop is not None and stop()):
                found = dwell_choice(list(self.points), self.screen, len(options), clock(), hold)
                if found is not None:
                    idx, t0, t1 = found
                    cx, cy = choice_center(self.screen, len(options), idx)
                    # The helper still has the feature frames from this dwell. One line teaches it.
                    self.send(f"choice {t0:.3f} {t1:.3f} {cx:.1f} {cy:.1f}")
                    return options[idx]
                sleep(0.05)
            return None
        finally:
            self.send("choose off")
