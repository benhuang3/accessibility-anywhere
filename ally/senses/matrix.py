"""Record a scripted senses session, and (later) run the live self-check.

    python -m ally.senses.matrix record --person ALIAS --glasses yes|no --light bright|dim [--free MINUTES]

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


def protocol(free_minutes: float = 0) -> list[dict]:
    """The scripted parts, with a 3 s rest between them. Calibration is first and lasts 15 s."""
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
        parts.append(dict(gap))
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
        parts = protocol(free_minutes)
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
    args = parser.parse_args(argv)
    if args.cmd == "record":
        return record(args.person, args.glasses, args.light, args.free, args.camera, args.run)
    return 2


if __name__ == "__main__":
    sys.exit(main())
