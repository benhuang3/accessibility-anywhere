"""Eye tracking in the agent. Runs ally.gaze_helper (the webcam) as a child process, restarts it if it
dies, and keeps the last ~45 s of gaze points so a tool can ask where the user was looking while
they spoke ("Ally, magnify what I'm looking at").

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
import threading
import time
import urllib.request
from pathlib import Path

from .claude import ssl_context
from .hotkeys import python_for_children
from .paths import CODE_ROOT
from .platforms.base import NO_WINDOW
from .registry import ToolError

log = logging.getLogger("ally.gaze")

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
        self._crashes: list[float] = []
        self._signature = None
        self._gave_up = False

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
        return self.override if self.override is not None else bool(self.cfg.get("enabled"))

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    # ---------------------------------------------------------------- process
    def apply(self) -> None:
        """Start or stop the camera helper to match config (or the voice on/off switch)."""
        if not self.wanted() or not has_gaze_packages():
            with self._lock:
                self._kill()
            self._signature = None
            if self.wanted():
                self.error = "eye tracking needs: pip install mediapipe opencv-python"
            return
        signature = (int(self.cfg.get("camera", 0)), int(self.cfg.get("fps", 20)))
        if signature == self._signature and (self.running or self._gave_up):
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
                    "camera": int(self.cfg.get("camera", 0)), "fps": int(self.cfg.get("fps", 20))}
            try:
                self.proc = subprocess.Popen(
                    [python_for_children(), "-m", "ally.gaze_helper", json.dumps(args)], cwd=str(CODE_ROOT),
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
            except OSError as e:
                self.error = f"couldn't start the eye tracker: {e}"
                return
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
                self.points.append((float(msg["t"]), int(msg["x"]), int(msg["y"])))
                self.face_at = time.time()
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
                log.info("eye tracker on (%s)", "calibrated" if self.calibrated else "needs calibration")
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
        if not self.wanted() or self._signature is None or self._gave_up:
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

    def set_enabled(self, on: bool) -> None:
        self.override = on
        if on:
            self._signature, self._gave_up = None, False
        self.apply()

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
            return ToolError("The eye tracker needs a 20-second setup first. Say: calibrate my eyes.")
        return ToolError("I can't see your eyes. Face the screen and the camera, then try again.")

    def latest(self, max_age: float = 0.3) -> tuple[int, int] | None:
        """The newest gaze point, if it's fresh (used by the magnifier to pan with the eyes)."""
        if not self.points:
            return None
        t, x, y = self.points[-1]
        return (x, y) if time.time() - t <= max_age else None

    def point(self, t0: float, t1: float) -> tuple[int, int]:
        """Median gaze point between two wall-clock times (steady even if the eyes flicked away)."""
        pts = [(x, y) for t, x, y in list(self.points) if t0 <= t <= t1]
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

    def calibrate(self, timeout: float = 90) -> dict:
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
        try:
            self.proc.stdin.write("calibrate\n")
            self.proc.stdin.flush()
        except (OSError, ValueError, AttributeError):
            raise ToolError("The eye tracker stopped. Try again in a few seconds.")
        if not self._calib_done.wait(timeout):
            raise ToolError("Calibration took too long.")
        return self._calib_result or {"calibrated": False, "why": "the eye tracker stopped"}
