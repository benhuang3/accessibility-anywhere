"""`python run.py doctor`: checks everything Ally needs and says how to fix problems."""

from __future__ import annotations

import importlib.util
import os
import platform
import sys
import time

from . import __version__, autostart
from .claude import Claude, ClaudeError
from .config import ConfigManager
from .gaze import has_gaze_packages
from .paths import Paths, load_env_file
from .platforms import get_platform
from .registry import PluginLoader, Registry
from .supervisor import health
from .tools import BUILTIN_MODULES
from .voice import has_whisper

MARK = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}


def has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def importable(module: str) -> tuple[bool, str]:
    """Really import it: some packages install fine but fail to load (e.g. missing PortAudio or X11)."""
    if not has(module):
        return False, "not installed"
    try:
        importlib.import_module(module)
        return True, "installed"
    except Exception as e:
        return False, f"installed but won't load: {type(e).__name__}: {e}"[:200]


def _ago(created: float, now: float) -> str:
    age = max(0, int(now - created))
    if age < 90:
        return "just now"
    if age < 3600:
        minutes = max(1, age // 60)
        return "1 minute ago" if minutes == 1 else f"{minutes} minutes ago"
    if age < 48 * 3600:
        hours = max(1, age // 3600)
        return "1 hour ago" if hours == 1 else f"{hours} hours ago"
    days = max(1, age // 86400)
    return "1 day ago" if days == 1 else f"{days} days ago"


def describe_calibration(model, camera, features="v1", now=None) -> tuple[str, str]:
    """(status, detail) for a saved calibration: age, check, feature set, and camera.

    The camera check is the same one the camera helper uses. This does not open the camera.
    """
    from .gaze_helper import kept_model, result_grade

    features = features if features in ("v1", "v2") else "v1"
    if not model:
        return "warn", f'not calibrated yet: say "Ally, calibrate my eyes" (features {features})'
    if kept_model(model, camera) is None:
        return "warn", (f"saved for camera {model.get('camera')}, but gaze.camera is {camera}. "
                        'Say "Ally, calibrate my eyes".')
    now = time.time() if now is None else float(now)
    created = model.get("created")
    if isinstance(created, (int, float)) and not isinstance(created, bool):
        lead = f"calibrated {_ago(float(created), now)}"
    else:
        lead = "calibrated"
    val = model.get("validation") if isinstance(model.get("validation"), dict) else {}
    err, hits, total = val.get("error_px"), val.get("region_hits"), val.get("region_total")
    if err is not None and total:
        check = f"check about {err} px, {int(hits or 0)} of {int(total)} areas"
        grade = result_grade({"error_px": err, "region_hits": int(hits or 0), "region_total": int(total)})
        status = "ok" if grade in ("good", "okay") else "warn"
    else:
        check = "no held-out check saved"
        status = "ok"
    feat = model.get("features") if model.get("features") in ("v1", "v2") else features
    cam = model.get("camera")
    cam_bit = f"camera {cam}" if cam is not None else f"camera {camera}"
    return status, f"{lead}; {check}; features {feat}; {cam_bit}"


def run_checks(home=None, online: bool = True) -> list[tuple[str, str, str]]:
    paths = Paths(home)
    load_env_file(paths.env_file)
    out = []
    v = sys.version_info
    out.append(("ok" if v >= (3, 11) else "fail", "Python", f"{platform.python_version()} "
                + ("" if v >= (3, 11) else "(need 3.11 or newer)")))
    out.append(("ok", "Ally", f"version {__version__} in {paths.home}"))

    reg = Registry()
    for m in BUILTIN_MODULES:
        reg.load_module(m)
    cm = ConfigManager(paths.config)
    if not paths.config.exists():
        out.append(("warn", "config.toml", "missing (created on first start)"))
    else:
        cm.reload(reg.names())
        loader = PluginLoader(reg, paths.plugins_dir(cm.data["plugins"]["folder"]))
        loader.scan()
        _cfg, errors, warnings = cm.check_text(cm.read_text(), {t.name for t in reg.all() if t.source != "config"})
        if errors:
            out.append(("fail", "config.toml", "; ".join(errors)[:600]))
        else:
            out.append(("warn" if warnings else "ok", "config.toml", "; ".join(warnings)[:400] or "valid"))
        for src, err in loader.errors.items():
            out.append(("fail", f"plugin {os.path.basename(src)}", err[:300]))
        plugin_tools = [t.name for t in reg.all() if t.source not in ("builtin", "config")]
        out.append(("ok", "Plug-in tools", ", ".join(plugin_tools) or "none"))

    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        out.append(("warn", "Claude API key", "ANTHROPIC_API_KEY not set: Ally runs in offline mode. "
                    f"Put ANTHROPIC_API_KEY=... in {paths.env_file}"))
    elif online:
        try:
            model = Claude(api_version=cm.data["brain"]["api_version"]).ping()
            out.append(("ok", "Claude API", f"reachable, key works (e.g. {model})"))
        except ClaudeError as e:
            hint = ""
            if "CERTIFICATE_VERIFY_FAILED" in str(e):
                hint = " Fix: pip install certifi (or run 'Install Certificates.command' for python.org Python on Mac)."
            out.append(("fail" if e.status in (401, 403) else "warn", "Claude API", f"{e}{hint}"[:400]))

    if cm.data["speech"]["engine"] == "cartesia" or cm.data["voice"]["engine"] in ("auto", "cartesia"):
        ck = bool(os.environ.get("CARTESIA_API_KEY"))
        if cm.data["speech"]["engine"] == "cartesia" and not ck:
            out.append(("warn", "Cartesia", "speech.engine is cartesia but CARTESIA_API_KEY isn't set"))
        elif cm.data["speech"]["engine"] == "cartesia" and not cm.data["speech"]["cartesia_voice_id"]:
            out.append(("warn", "Cartesia", "set speech.cartesia_voice_id (from https://play.cartesia.ai)"))
        elif ck:
            out.append(("ok", "Cartesia", "key set"))

    port = int(cm.data["agent"]["port"])
    h = health(port)
    if h:
        out.append(("ok", "Agent", f"running (pid {h.get('pid')}, up {h.get('uptime')}s, brain {h.get('brain')})"))
        out.append(("ok" if h.get("extension") else "warn", "Chrome extension",
                    "connected" if h.get("extension") else
                    "not connected: chrome://extensions > Developer mode > Load unpacked > the extension folder"))
    else:
        out.append(("warn", "Agent", f"not running on port {port}. Start it with: python run.py"))

    ok, why = importable("pynput")
    out.append(("ok" if ok else "warn", "Global hotkeys", "pynput " + why + ("" if ok else " (pip install pynput)")))
    out.append(("ok" if has("mss") else "warn", "Screenshots",
                "mss installed" if has("mss") else "pip install mss (built-in tools are used otherwise)"))
    out.append(("ok" if has("PIL") else "warn", "Image shrinking",
                "Pillow installed" if has("PIL") else "pip install pillow (smaller, faster screenshots)"))
    ok, why = importable("sounddevice")
    out.append(("ok" if ok and has("numpy") else "warn", "Microphone", "sounddevice " + why +
                ("" if ok else " (pip install sounddevice numpy; Linux also needs libportaudio2)")))
    v = cm.data["voice"]
    if not v.get("always_listen") or v.get("engine") == "off":
        out.append(("ok", "Always listening", "off (voice.always_listen = false)"))
    elif has_whisper():
        out.append(("ok", "Always listening", f"on: say \"{str((v.get('wake_words') or ['ally'])[0]).title()}, ...\" "
                    f"(offline speech-to-text {v.get('whisper_model', 'base.en')}; the model downloads once)"))
    else:
        out.append(("warn", "Always listening", "needs offline speech-to-text: pip install faster-whisper"))
    gaze_cfg = cm.data["gaze"]
    if not gaze_cfg.get("enabled"):
        out.append(("ok", "Eye tracking", "off (gaze.enabled = false)"))
    elif not has_gaze_packages():
        out.append(("warn", "Eye tracking", "pip install mediapipe opencv-python"))
    else:
        from . import gaze_model

        loaded = gaze_model.load(paths.state_dir / "gaze_calibration.json")
        status, detail = describe_calibration(loaded, int(gaze_cfg.get("camera", 0)),
                                              str(gaze_cfg.get("features", "v1")))
        if loaded is None and not (paths.state_dir / "models" / "face_landmarker.task").exists():
            detail += "; the face model (3.7 MB) downloads on first start"
        out.append((status, "Eye tracking", detail))
    s = cm.data["senses"]
    if not s.get("vision"):
        out.append(("ok", "Face sensing", "off (senses.vision = false)"))
    elif not has_gaze_packages():
        out.append(("warn", "Face sensing", "pip install mediapipe opencv-python"))
    else:
        has_base = (paths.state_dir / "senses_baseline.json").exists()
        out.append(("ok", "Face sensing", "on, baseline saved" if has_base else
                    "on; learns your resting face in the first 20 s (or say \"Ally, set up face sensing\")"))
    if s.get("input"):
        ok, why = importable("pynput")
        out.append(("ok" if ok else "warn", "Mouse and keyboard sensing",
                    "on (counts clicks and backspaces, never which keys)" if ok else "pip install pynput"))
    else:
        out.append(("ok", "Mouse and keyboard sensing", "off (senses.input = false)"))
    out.append(("ok" if autostart.installed() else "warn", "Start at login",
                "on" if autostart.installed() else "off: python run.py install"))
    try:
        out.extend(get_platform().checks())
    except Exception as e:
        out.append(("warn", "Platform checks", str(e)))
    return out


def main(home=None) -> int:
    results = run_checks(home)
    width = max(len(n) for _s, n, _d in results)
    for status, name, detail in results:
        print(f"[{MARK[status]}] {name.ljust(width)}  {detail}")
    fails = sum(1 for s, _n, _d in results if s == "fail")
    print("\nAll good." if not fails else f"\n{fails} problem(s) need fixing.")
    return 1 if fails else 0
