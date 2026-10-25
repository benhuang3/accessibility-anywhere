"""Record a scripted senses session, or run the live face self-check.

    python -m ally.senses.matrix record --person ALIAS --glasses yes|no --light bright|dim [--free MINUTES]
    python -m ally.senses.matrix selfcheck

The camera helper and the mouse/keyboard helper run as child processes. This writes numbers only
(face scores, clicks, labels, the settings in effect) to state/senses_runs/<run>/<alias>-<glasses>-<light>.jsonl.
Frames are never stored. Stop Ally first (`python run.py stop`): only one program can use the camera.

Ctrl+C keeps whatever has already been written.
"""

from __future__ import annotations

import argparse
import json
import math
import queue
import re
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

from ..config import ConfigManager
from ..hotkeys import python_for_children
from ..paths import CODE_ROOT, Paths
from ..platforms.base import NO_WINDOW
from ..supervisor import current_port, listening
from .detectors import Tuning

CALIBRATION_SECONDS = 15.0
GAP_SECONDS = 3.0
ACTION_SECONDS = 8.0
STRUGGLE_SECONDS = 10.0
TOKEN = re.compile(r"[A-Za-z0-9_-]{1,40}")
# Anything that could carry a picture is dropped before a line is written.
_IMAGE_KEYS = {"frame", "image", "jpeg", "jpg", "png", "video", "pixels", "data", "bmp", "raw", "png_b64"}
_MAX_STR = 400
_DROP = object()

# Ground truth for each acted part. "natural" is scored on its own. Gaps and calibration are not scored.
_TASKS = (
    ("read_normal", "Normal text", "Read this sentence normally. Do not squint or lean closer.",
     ACTION_SECONDS, "none", "normal"),
    ("read_tiny", "Small text", "Read this small line naturally. Do not act it out.",
     ACTION_SECONDS, "natural", "tiny"),
    ("act_squint", "Squint", "Squint as if this text were too small to read, and hold it.",
     ACTION_SECONDS, "squint", "normal"),
    ("smile", "Smile", "Smile, and keep smiling for a few seconds.",
     ACTION_SECONDS, "none", "normal"),
    ("laugh", "Laugh", "Laugh for a few seconds, then relax your face.",
     ACTION_SECONDS, "none", "normal"),
    ("look_down", "Phone", "Look down at a phone in your lap, then look back at the screen.",
     ACTION_SECONDS, "none", "normal"),
    ("close_left", "Left eye", "Close only your LEFT eye. Keep the right eye open.",
     ACTION_SECONDS, "none", "normal"),
    ("close_right", "Right eye", "Close only your RIGHT eye. Keep the left eye open.",
     ACTION_SECONDS, "none", "normal"),
    ("turn_head", "Turn", "Turn your head left, then right, then face the screen again.",
     ACTION_SECONDS, "none", "normal"),
    ("lean", "Lean in", "Lean in close to the screen. Do not squint.",
     ACTION_SECONDS, "lean", "normal"),
    ("lean_squint", "Lean and squint", "Lean in and squint, as if this were too small to read.",
     STRUGGLE_SECONDS, "struggle", "normal"),
)


def ally_is_running(paths: Paths | None = None) -> bool:
    """True when an Ally agent is accepting connections. Uses the supervisor's own check."""
    paths = paths or Paths()
    try:
        return bool(listening(current_port(paths)))
    except (OSError, KeyError, TypeError, ValueError):
        return False


def protocol(free_minutes: float = 0, rearm_seconds: float = Tuning.rearm_seconds,
             cooldown_seconds: float = 30.0) -> list[dict]:
    """The scripted parts, with a 3 s rest between them. Calibration is first and lasts 15 s.

    The rest before a struggle part outlasts rearm_seconds and cooldown_seconds: a detector that just
    fired (the lean in "Lean in") waits rearm_seconds before firing again, and after any struggle no
    other is reported for cooldown_seconds. Either would hide the struggle.
    """
    parts = [{
        "name": "calibration", "title": "Read normally",
        "text": "Read this sentence at a comfortable size. Keep a relaxed face and sit as you usually do.",
        "seconds": CALIBRATION_SECONDS, "expect": "baseline", "size": "normal",
    }]
    gap = {
        "name": "gap", "title": "Rest", "text": "Relax your face and look at the screen.",
        "seconds": GAP_SECONDS, "expect": "gap", "size": "normal",
    }
    for name, title, text, seconds, expect, size in _TASKS:
        rest = dict(gap)
        if expect == "struggle":
            rest["seconds"] = max(GAP_SECONDS, float(rearm_seconds) + 2.0, float(cooldown_seconds) + 2.0)
        parts.append(rest)
        parts.append({"name": name, "title": title, "text": text, "seconds": float(seconds),
                      "expect": expect, "size": size})
    if free_minutes > 0:
        parts.append(dict(gap))
        parts.append({
            "name": "free", "title": "Normal use",
            "text": "Use the computer as you normally would. Only numbers are being written down.",
            "seconds": float(free_minutes) * 60.0, "expect": "none", "size": "normal",
        })
    return parts


def card_line(part: dict) -> str:
    """Stdin command the camera helper understands: `card` plus a JSON object."""
    payload = {"text": part["text"], "title": part["title"], "size": part["size"], "seconds": part["seconds"]}
    return "card " + json.dumps(payload, ensure_ascii=True)


def sanitize(obj: dict) -> dict | None:
    """Keep short text and numbers. Drop picture keys, long strings, and long lists."""
    if not isinstance(obj, dict):
        return None
    cleaned = _clean(obj)
    if cleaned is _DROP or not isinstance(cleaned, dict):
        return None
    return cleaned


def _clean(value):
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return _DROP
        return value
    if isinstance(value, str):
        lower = value.lower()
        if len(value) > _MAX_STR or "base64" in lower or lower.startswith("data:image"):
            return _DROP
        return value
    if isinstance(value, list):
        if len(value) > 8:
            return _DROP
        out = []
        for item in value:
            cleaned = _clean(item)
            if cleaned is _DROP:
                return _DROP
            out.append(cleaned)
        return out
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if not isinstance(key, str) or key.lower() in _IMAGE_KEYS:
                continue
            cleaned = _clean(item)
            if cleaned is _DROP:
                continue
            out[key] = cleaned
        return out
    return _DROP


def camera_command(model: Path, calibration: Path, camera: int, fps: int, signals_fps: int) -> list[str]:
    args = {"model": str(model), "calibration": str(calibration), "camera": int(camera), "fps": int(fps),
            "gaze": False, "signals": True, "signals_fps": int(signals_fps)}
    return [python_for_children(), "-m", "ally.gaze_helper", json.dumps(args)]


def input_command() -> list[str]:
    return [python_for_children(), "-m", "ally.senses.input_helper"]


def git_commit() -> str:
    try:
        cp = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(CODE_ROOT), capture_output=True,
                            text=True, timeout=5, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return cp.stdout.strip() if cp.returncode == 0 else ""


def load_senses(paths: Paths) -> dict:
    cm = ConfigManager(paths.config)
    cm.reload()
    data = cm.data.get("senses") or {}
    return dict(data) if isinstance(data, dict) else {}


def load_camera_index(paths: Paths) -> int:
    cm = ConfigManager(paths.config)
    cm.reload()
    try:
        return int(cm.data.get("gaze", {}).get("camera", 0))
    except (TypeError, ValueError):
        return 0


class _Log:
    """Append-only JSONL. Lines received before the header stay in memory until the header is written."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._buf: list[str] = []
        self._open = False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    def write(self, obj: dict) -> None:
        line = json.dumps(obj, ensure_ascii=True) + "\n"
        with self._lock:
            if not self._open:
                self._buf.append(line)
                return
            self._append(line)

    def open_with(self, meta: dict) -> None:
        line = json.dumps(meta, ensure_ascii=True) + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line)
                fh.writelines(self._buf)
                fh.flush()
            self._buf.clear()
            self._open = True

    def _append(self, line: str) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()


def _default_spawn(kind: str, command: list[str]):
    return subprocess.Popen(command, cwd=str(CODE_ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                            creationflags=NO_WINDOW)


def _stop(proc) -> None:
    if proc is None:
        return
    try:
        if proc.poll() is not None:
            return
    except Exception:
        return
    try:
        if proc.stdin:
            proc.stdin.close()
    except Exception:
        pass
    try:
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _send(proc, line: str) -> None:
    try:
        if proc is not None and proc.stdin:
            proc.stdin.write(line.rstrip("\n") + "\n")
            proc.stdin.flush()
    except (OSError, ValueError, AttributeError):
        pass


def _drain_stderr(proc) -> None:
    try:
        if proc.stderr:
            for _line in proc.stderr:
                pass
    except Exception:
        pass


def _read_stdout(proc, kind: str, log: _Log, found: queue.Queue, watching: threading.Event) -> None:
    try:
        stream = proc.stdout
        if stream is None:
            return
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            clean = sanitize(obj)
            if not clean:
                continue
            rec = {"rec": "input" if kind == "input" else "helper"}
            rec.update(clean)
            log.write(rec)
            # Only the startup handshake needs the queue; the file keeps every line.
            if watching.is_set() and rec["rec"] == "helper":
                found.put(rec)
    except Exception:
        pass


def _wait_ready(found: queue.Queue, timeout: float) -> dict | None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            msg = found.get(timeout=0.05)
        except queue.Empty:
            continue
        if not isinstance(msg, dict) or msg.get("rec") != "helper":
            continue
        if msg.get("ready"):
            return msg
        if "error" in msg:
            return msg
    return None


def _print_part(part: dict, index: int, total: int) -> None:
    print(f"\n[{index}/{total}] {part['title']} ({part['seconds']:.0f} s)")
    print(part["text"])
    expect = part["expect"]
    if expect == "gap":
        print("Ground truth: rest (not scored).")
    elif expect == "baseline":
        print("Ground truth: calibration (not scored).")
    elif expect == "natural":
        print("Ground truth: natural reading (scored on its own).")
    elif expect == "none":
        print("Ground truth: no struggle.")
    else:
        print(f"Ground truth: {expect}.")


def _run_name(run: str) -> str:
    if run and TOKEN.fullmatch(run):
        return run
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _output_path(state_dir: Path, run: str, person: str, glasses: str, light: str) -> Path:
    folder = state_dir / "senses_runs" / run
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{person}-{glasses}-{light}"
    path = folder / f"{stem}.jsonl"
    n = 2
    while path.exists() and path.stat().st_size > 0:
        path = folder / f"{stem}-{n}.jsonl"
        n += 1
    return path


def record(person: str, glasses: str, light: str, free_minutes: float = 0, camera: int | None = None,
           run: str = "", *, root: Path | None = None, model: Path | None = None, spawn=None,
           sleep=None, ally_running=None, now=None, commit: str | None = None,
           senses: dict | None = None, screen: tuple[int, int] | None = None,
           paths: Paths | None = None) -> int:
    """Walk one person through the protocol and write the JSONL. Returns a process exit code."""
    paths = paths or Paths()
    if not TOKEN.fullmatch(person or ""):
        print("Use a short alias: letters, digits, _ or -.")
        return 2
    if glasses not in ("yes", "no") or light not in ("bright", "dim"):
        print("Glasses must be yes or no, and light must be bright or dim.")
        return 2
    if free_minutes < 0:
        print("--free minutes cannot be negative.")
        return 2
    running = ally_is_running(paths) if ally_running is None else ally_running()
    if running:
        print("Ally is using the camera. Run python run.py stop first, then try again.")
        return 2
    state_dir = root or paths.state_dir
    model_path = model or (paths.state_dir / "models" / "face_landmarker.task")
    if not Path(model_path).exists():
        print("The face model isn't here yet (state/models/face_landmarker.task).")
        print("Start Ally once so it can download, run python run.py stop, then try again.")
        return 2
    senses_cfg = dict(senses) if senses is not None else load_senses(paths)
    if camera is None:
        camera = load_camera_index(paths)
    run_id = _run_name(run)
    path = _output_path(state_dir, run_id, person, glasses, light)
    calib_path = path.parent / "calibration.json"
    fps = 20
    try:
        signals_fps = int(senses_cfg.get("fps", 10))
    except (TypeError, ValueError):
        signals_fps = 10
    spawn = spawn or _default_spawn
    sleep = sleep or time.sleep
    now = now or time.time
    log = _Log(path)
    found: queue.Queue = queue.Queue()
    watching = threading.Event()
    watching.set()
    procs = []
    threads = []
    try:
        camera_proc = spawn("camera", camera_command(Path(model_path), calib_path, int(camera), fps, signals_fps))
        procs.append(camera_proc)
        input_proc = spawn("input", input_command())
        procs.append(input_proc)
    except OSError as e:
        print(f"Couldn't start the helpers: {e}")
        for proc in procs:
            _stop(proc)
        return 2
    for proc, kind in ((camera_proc, "camera"), (input_proc, "input")):
        threading.Thread(target=_drain_stderr, args=(proc,), daemon=True).start()
        th = threading.Thread(target=_read_stdout, args=(proc, kind, log, found, watching), daemon=True)
        th.start()
        threads.append(th)
    print(f"Recording to {path}")
    interrupted = False
    code = 0
    current = None
    try:
        ready = _wait_ready(found, 25)
        watching.clear()
        if not ready or not ready.get("ready"):
            why = (ready or {}).get("error") or "The camera helper didn't start."
            print(why)
            code = 4
            meta_screen = list(screen or (0, 0))
        else:
            got = ready.get("screen")
            meta_screen = list(got) if isinstance(got, list) and len(got) == 2 else list(screen or (0, 0))
        meta = {
            "rec": "meta", "alias": person, "glasses": glasses, "light": light, "camera": int(camera),
            "screen": meta_screen, "senses": senses_cfg, "commit": git_commit() if commit is None else commit,
            "started": now(),
        }
        log.open_with(meta)
        if code != 0:
            return code
        parts = protocol(free_minutes, Tuning.from_config(senses_cfg).rearm_seconds,
                         float(senses_cfg.get("cooldown_seconds", 30)))
        total = len(parts)
        for index, part in enumerate(parts, start=1):
            current = {"name": part["name"], "expect": part["expect"], "start": now(), "partial": False}
            _print_part(part, index, total)
            _send(camera_proc, card_line(part))
            sleep(part["seconds"])
            current["end"] = now()
            log.write({"rec": "label", "name": current["name"], "start": current["start"],
                       "end": current["end"], "expect": current["expect"]})
            current = None
        _send(camera_proc, "card off")
    except KeyboardInterrupt:
        interrupted = True
        if not log._open:
            log.open_with({
                "rec": "meta", "alias": person, "glasses": glasses, "light": light, "camera": int(camera),
                "screen": list(screen or (0, 0)), "senses": senses_cfg,
                "commit": git_commit() if commit is None else commit, "started": now(),
            })
        if current is not None:
            current["end"] = now()
            current["partial"] = True
            log.write({"rec": "label", "name": current["name"], "start": current["start"],
                       "end": current["end"], "expect": current["expect"], "partial": True})
        print("\nStopped. Kept what was recorded.")
    finally:
        for proc in procs:
            _stop(proc)
        for th in threads:
            th.join(timeout=2)
    if interrupted:
        return 0
    if code == 0:
        print(f"Done. {path}")
    return code


# About 60 seconds: 12 s to see the resting face and the light, then six 8 s actions.
_SELF = (
    ("settle", 12.0, "Look at the screen",
     "Sit as you usually sit and look at the screen. This checks the light."),
    ("left", 8.0, "Left eye", "Close only your LEFT eye and hold it."),
    ("right", 8.0, "Right eye", "Close only your RIGHT eye and hold it."),
    ("smile", 8.0, "Smile", "Smile, with the corners of your mouth up."),
    ("blink", 8.0, "Blink", "Blink slowly, a few times."),
    ("turn", 8.0, "Turn your head", "Turn your head left, then right."),
    ("lean", 8.0, "Lean in", "Lean in close to the screen."),
)


def selfcheck_parts() -> list[dict]:
    return [{"name": name, "seconds": seconds, "title": title, "text": text, "size": "normal",
             "expect": "check"} for name, seconds, title, text in _SELF]


def score_selfcheck(collected: dict[str, list[dict]], tune: Tuning) -> list[dict]:
    """PASS/FAIL for one self-check, using the real [senses] thresholds. Numbers only."""
    settle = collected.get("settle") or []
    widths = [float(s["width"]) for s in settle if float(s.get("width") or 0) > 0]
    lights = [float(s["light"]) for s in settle if float(s.get("light") or -1) >= 0]
    rest = statistics.median(widths) if widths else 0.0
    light = statistics.median(lights) if lights else -1.0
    rows = [_light_row(light, tune),
            _eye_row("left", "left eye", collected.get("left") or [], "squint_l", "squint_r",
                     "Close only your left eye, on your left, and hold it. If you did and these numbers "
                     "are backwards, left and right are swapped."),
            _eye_row("right", "right eye", collected.get("right") or [], "squint_r", "squint_l",
                     "Close only your right eye, on your right, and hold it. If you did and these numbers "
                     "are backwards, left and right are swapped."),
            _max_row("smile", "smile", collected.get("smile") or [], "smile", tune.smile_gate, True,
                     "Smile more clearly, with the corners of your mouth up."),
            _max_row("blink", "blink", collected.get("blink") or [], "blink", tune.blink_gate, True,
                     "Close both eyes for a moment during the check."),
            _yaw_row(collected.get("turn") or [], tune),
            _lean_row(collected.get("lean") or [], rest, tune)]
    return rows


def _light_row(light: float, tune: Tuning) -> dict:
    if light < 0:
        return {"name": "light", "label": "light", "ok": False, "measured": "no face",
                "hint": "Face the camera, with light on your face, then try again."}
    ok = light >= tune.light_floor
    measured = f"{light:.0f} (need at least {tune.light_floor:.0f})"
    hint = f"Put more light on your face. Brightness was {light:.0f}; it needs to be at least {tune.light_floor:.0f}."
    return {"name": "light", "label": "light", "ok": ok, "measured": measured, "hint": hint}


def _eye_row(name: str, label: str, samples: list[dict], primary: str, other: str, hint: str) -> dict:
    if not samples:
        return {"name": name, "label": label, "ok": False, "measured": "no face",
                "hint": "Face the camera and try that eye again. " + hint}
    frame = max(samples, key=lambda s: float(s.get(primary) or 0))
    a, b = float(frame.get(primary) or 0), float(frame.get(other) or 0)
    return {"name": name, "label": label, "ok": a > b,
            "measured": f"{primary} {a:.2f}, {other} {b:.2f}", "hint": hint}


def _max_row(name, label, samples, field, gate, at_least, hint) -> dict:
    if not samples:
        return {"name": name, "label": label, "ok": False, "measured": "no face",
                "hint": "Face the camera and try again. " + hint}
    value = max(float(s.get(field) or 0) for s in samples)
    ok = value >= gate if at_least else value > gate
    return {"name": name, "label": label, "ok": ok,
            "measured": f"{value:.2f} (need at least {gate:.2f})", "hint": hint + f" It reached {value:.2f}; it needs {gate:.2f}."}


def _yaw_row(samples: list[dict], tune: Tuning) -> dict:
    if not samples:
        return {"name": "turn", "label": "head turn", "ok": False, "measured": "no face",
                "hint": "Face the camera, then turn your head left and right."}
    yaw = max(abs(float(s.get("yaw") or 0)) for s in samples)
    ok = yaw > tune.pose_gate
    return {"name": "turn", "label": "head turn", "ok": ok,
            "measured": f"yaw {yaw:.0f} (need above {tune.pose_gate:.0f})",
            "hint": f"Turn your head farther, past {tune.pose_gate:.0f} degrees. The largest turn was {yaw:.0f}."}


def _lean_row(samples: list[dict], rest: float, tune: Tuning) -> dict:
    if rest <= 0 or not samples:
        return {"name": "lean", "label": "lean", "ok": False, "measured": "no resting face",
                "hint": "I couldn't see your face at the start. Sit facing the camera and try again."}
    width = max(float(s.get("width") or 0) for s in samples)
    ratio = width / rest
    ok = ratio >= tune.lean_ratio
    return {"name": "lean", "label": "lean", "ok": ok,
            "measured": f"width x{ratio:.2f} (need at least {tune.lean_ratio:.2f})",
            "hint": f"Lean closer to the screen. Your face got {ratio:.2f} times wider; it needs {tune.lean_ratio:.2f}."}


def format_results(rows: list[dict]) -> str:
    lines = []
    for row in rows:
        status = "PASS" if row["ok"] else "FAIL"
        lines.append(f"{status}  {row['label']}: {row['measured']}")
        if not row["ok"]:
            lines.append(f"      {row['hint']}")
    failed = sum(1 for row in rows if not row["ok"])
    if failed == 0:
        lines.append("All checks passed.")
    elif failed == 1:
        lines.append("1 check failed.")
    else:
        lines.append(f"{failed} checks failed.")
    return "\n".join(lines) + "\n"


def _during(faces: list[tuple[float, dict]], start: float, end: float) -> list[dict]:
    return [sig for t, sig in faces if start <= t < end]


def _read_faces(proc, faces: list, found: queue.Queue, watching: threading.Event) -> None:
    try:
        stream = proc.stdout
        if stream is None:
            return
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            clean = sanitize(obj)
            if not clean:
                continue
            if "f" in clean and "t" in clean:
                try:
                    faces.append((float(clean["t"]), clean["f"]))
                except (TypeError, ValueError):
                    pass
            if watching.is_set():
                found.put({"rec": "helper", **clean})
    except Exception:
        pass


def selfcheck(camera: int | None = None, *, model: Path | None = None, spawn=None, sleep=None,
              ally_running=None, now=None, senses: dict | None = None, paths: Paths | None = None) -> int:
    """About 60 seconds of guided checks. Prints PASS or FAIL for each one."""
    paths = paths or Paths()
    running = ally_is_running(paths) if ally_running is None else ally_running()
    if running:
        print("Ally is using the camera. Run python run.py stop first, then try again.")
        return 2
    model_path = model or (paths.state_dir / "models" / "face_landmarker.task")
    if not Path(model_path).exists():
        print("The face model isn't here yet (state/models/face_landmarker.task).")
        print("Start Ally once so it can download, run python run.py stop, then try again.")
        return 2
    senses_cfg = dict(senses) if senses is not None else load_senses(paths)
    if camera is None:
        camera = load_camera_index(paths)
    tune = Tuning.from_config(senses_cfg)
    spawn = spawn or _default_spawn
    sleep = sleep or time.sleep
    now = now or time.time
    try:
        signals_fps = int(senses_cfg.get("fps", 10))
    except (TypeError, ValueError):
        signals_fps = 10
    calib_path = paths.state_dir / "selfcheck_calibration.json"
    faces: list[tuple[float, dict]] = []
    found: queue.Queue = queue.Queue()
    watching = threading.Event()
    watching.set()
    try:
        proc = spawn("camera", camera_command(Path(model_path), calib_path, int(camera), 20, signals_fps))
    except OSError as e:
        print(f"Couldn't start the camera helper: {e}")
        return 2
    threading.Thread(target=_drain_stderr, args=(proc,), daemon=True).start()
    threading.Thread(target=_read_faces, args=(proc, faces, found, watching), daemon=True).start()
    collected: dict[str, list[dict]] = {}
    try:
        ready = _wait_ready(found, 25)
        watching.clear()
        if not ready or not ready.get("ready"):
            print((ready or {}).get("error") or "The camera helper didn't start.")
            return 4
        # The reader thread may still be taking lines that arrived with the ready message.
        time.sleep(0.05)
        parts = selfcheck_parts()
        total = len(parts)
        for index, part in enumerate(parts, start=1):
            print(f"\n[{index}/{total}] {part['title']} ({part['seconds']:.0f} s)")
            print(part["text"])
            _send(proc, card_line(part))
            start = now()
            sleep(part["seconds"])
            collected[part["name"]] = _during(list(faces), start, now())
        _send(proc, "card off")
    except KeyboardInterrupt:
        print("\nStopped.")
        return 1
    finally:
        _stop(proc)
    rows = score_selfcheck(collected, tune)
    print()
    print(format_results(rows), end="")
    return 0 if all(row["ok"] for row in rows) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ally.senses.matrix")
    sub = parser.add_subparsers(dest="cmd", required=True)
    rec = sub.add_parser("record", help="record one person against the scripted ground truth")
    rec.add_argument("--person", required=True)
    rec.add_argument("--glasses", required=True, choices=("yes", "no"))
    rec.add_argument("--light", required=True, choices=("bright", "dim"))
    rec.add_argument("--free", type=float, default=0, metavar="MINUTES",
                     help="extra minutes of normal computer use, labeled as no struggle")
    rec.add_argument("--camera", type=int, default=None)
    rec.add_argument("--run", default="", help="folder name under state/senses_runs (default: a timestamp)")
    chk = sub.add_parser("selfcheck", help="about 60 seconds of guided face checks")
    chk.add_argument("--camera", type=int, default=None)
    args = parser.parse_args(argv)
    if args.cmd == "record":
        return record(args.person, args.glasses, args.light, args.free, args.camera, args.run)
    if args.cmd == "selfcheck":
        return selfcheck(camera=args.camera)
    return 2


if __name__ == "__main__":
    sys.exit(main())
