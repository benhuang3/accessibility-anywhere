"""`python run.py doctor`: checks everything Ally needs and says how to fix problems."""

from __future__ import annotations

import importlib.util
import os
import platform
import sys

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


def voice_checks(cfg: dict, online: bool = True) -> list[tuple[str, str, str]]:
    """Cartesia voice, the fallback voice, and how spoken yes/no answers will be heard."""
    out = []
    sp, vo, cf = cfg["speech"], cfg["voice"], cfg.get("confirm", {})
    key = bool(os.environ.get("CARTESIA_API_KEY", "").strip())
    voice_id = sp.get("cartesia_voice_id", "")
    wants = sp["engine"] in ("auto", "cartesia")
    if sp["engine"] == "off":
        out.append(("warn", "Voice", "speech.engine is off: Ally won't speak offers or warnings"))
    elif wants and not key:
        out.append(("warn" if sp["engine"] == "cartesia" else "ok", "Voice",
                    "built-in voice (add CARTESIA_API_KEY to .env for the Cartesia voice)"))
    elif wants and not voice_id:
        out.append(("warn", "Voice", "Cartesia key set, but no voice: set [speech] cartesia_voice_id "
                    "(python run.py voices lists them). Using the built-in voice."))
    elif wants and online:
        from .speech import CartesiaError, cartesia_tts, resolve_voice, wav_seconds

        try:
            vid = resolve_voice(voice_id)
            err, used = "", ""
            for m in (sp.get("cartesia_model", "sonic-3.6"), sp.get("cartesia_fallback_model", "sonic-3")):
                try:
                    audio = cartesia_tts("Ally is ready.", vid, m, speed=float(cf.get("speed", 0.9)),
                                         emotion=sp.get("emotion", ""), timeout=10)
                    used = f"{m}, {wav_seconds(audio):.1f} s of audio"
                    break
                except CartesiaError as e:
                    err = str(e)
                    if not e.bad_request:
                        break
            out.append(("ok", "Cartesia voice", f"works ({used})") if used else
                       ("warn", "Cartesia voice", f"{err[:300]}; Ally will use the built-in voice"))
        except CartesiaError as e:
            out.append(("warn", "Cartesia voice", f"{str(e)[:300]}; Ally will use the built-in voice"))
    elif wants:
        out.append(("ok", "Cartesia voice", "key and voice set (not tested offline)"))
    else:
        out.append(("ok", "Voice", "built-in voice (speech.engine = system)"))

    from .voice import has_whisper

    stt = []
    if vo.get("engine") != "off":
        if key and vo.get("engine") in ("auto", "cartesia"):
            stt.append("Cartesia Ink")
        if has_whisper() and vo.get("engine") in ("auto", "whisper", "cartesia"):
            stt.append("offline Whisper")
    mic = importable("sounddevice")[0] and has("numpy")
    if not cf.get("enabled", True):
        out.append(("warn", "Spoken questions", "[confirm] enabled = false: offers use the buttons only"))
    elif not stt or not mic:
        missing = ("speech-to-text (CARTESIA_API_KEY or pip install faster-whisper)" if not stt else
                   "a microphone recorder (pip install sounddevice numpy)")
        out.append(("warn", "Spoken questions", f"no {missing}: Ally asks out loud, answers come from the buttons"))
    else:
        out.append(("ok", "Spoken questions", "answers heard with " + ", then ".join(stt)))
    return out


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

    out.extend(voice_checks(cm.data, online))

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
    if not cm.data["gaze"].get("enabled"):
        out.append(("ok", "Eye tracking", "off (gaze.enabled = false)"))
    elif not has_gaze_packages():
        out.append(("warn", "Eye tracking", "pip install mediapipe opencv-python"))
    else:
        from . import gaze_model

        calibrated = gaze_model.load(paths.state_dir / "gaze_calibration.json") is not None
        model = (paths.state_dir / "models" / "face_landmarker.task").exists()
        out.append(("ok" if calibrated else "warn", "Eye tracking",
                    ("calibrated" if calibrated else "not calibrated yet: say \"Ally, calibrate my eyes\"")
                    + ("" if model else "; the face model (3.7 MB) downloads on first start")))
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
