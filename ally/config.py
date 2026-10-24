"""Load, validate and hot-reload config.toml.

Rules that keep Ally running:
- A broken file never stops the agent: the last good settings stay active and the error is shown.
- Unknown keys are warnings (likely typos), not errors.
- [hotkeys] and [commands] replace the built-in tables; every other table is merged key by key.
"""

from __future__ import annotations

import copy
import logging
import re
import threading
import tomllib
from pathlib import Path

from . import keys
from .paths import PACKAGE_DIR
from .state import atomic_write

log = logging.getLogger("ally.config")

DEFAULT_TEXT = (PACKAGE_DIR / "default_config.toml").read_text(encoding="utf-8")
DEFAULTS = tomllib.loads(DEFAULT_TEXT)
REPLACE_TABLES = {"hotkeys", "commands"}
NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
STYLE_RE = re.compile(r"^[a-z0-9_]{1,40}$")
STEP_KINDS = ("tool", "say", "shell", "open", "keys", "wait", "ask", "notify")
OS_KEYS = {"mac", "windows", "linux"}

NUM = (int, float)
SPEC = {
    "agent": {"name": str, "port": int, "speak_replies": bool, "announce_start": bool,
              "zoom_target": ("auto", "page", "screen"), "hotkeys_enabled": bool,
              "log_level": ("DEBUG", "INFO", "WARNING", "ERROR")},
    "brain": {"mode": ("auto", "claude", "offline"), "model": str,
              "effort": ("", "low", "medium", "high", "xhigh", "max"), "thinking": ("off", "adaptive"),
              "max_steps": int, "max_tokens": int, "timeout_seconds": NUM, "history_turns": int,
              "fast_path": bool, "share_tab_info": bool, "include_active_app": bool,
              "api_version": str, "system_prompt": str},
    "speech": {"engine": ("system", "cartesia", "off"), "rate": NUM, "voice": str, "max_chars": int,
               "cartesia_voice_id": str, "cartesia_model": str},
    "voice": {"engine": ("auto", "cartesia", "whisper", "off"), "max_seconds": NUM, "silence_seconds": NUM,
              "language": str, "beep": bool, "always_listen": bool, "wake_words": list, "follow_up_seconds": NUM,
              "whisper_model": str},
    "gaze": {"enabled": bool, "camera": int, "fps": int, "pan_speed": NUM},
    "senses": {"vision": bool, "input": bool, "fps": int, "calibrate_seconds": NUM, "solo": list,
               "window_seconds": NUM, "cooldown_seconds": NUM, "min_confidence": NUM, "decline_seconds": NUM,
               "squint_delta": NUM, "squint_weak": NUM, "squint_hold": NUM, "squint_window": NUM, "wink_max": NUM,
               "smile_gate": NUM, "blink_gate": NUM, "pose_gate": NUM, "light_floor": NUM, "lean_ratio": NUM,
               "lean_weak": NUM, "lean_release": NUM, "lean_hold": NUM, "rage_clicks": int, "rage_seconds": NUM,
               "rage_px": NUM, "retype_backspaces": int, "retype_seconds": NUM, "hesitation_seconds": NUM,
               "rearm_seconds": NUM, "post_url": str},
    "profile": {"page_zoom": NUM, "min_font_px": int, "page_styles": list, "screen_magnify": NUM,
                "color_filter": ("none", "invert", "grayscale", "high_contrast")},
    "tools": {"disabled": list, "timeout_seconds": NUM},
    "plugins": {"folder": str},
    "security": {"allowed_extension_ids": list},
}
RANGES = {
    ("agent", "port"): (1024, 65535), ("brain", "max_steps"): (1, 20), ("brain", "max_tokens"): (256, 64000),
    ("brain", "timeout_seconds"): (3, 120), ("brain", "history_turns"): (0, 20), ("speech", "rate"): (0.3, 3.0),
    ("speech", "max_chars"): (100, 200000), ("voice", "max_seconds"): (1, 60),
    ("voice", "silence_seconds"): (0.3, 5), ("voice", "follow_up_seconds"): (0, 60), ("gaze", "camera"): (0, 9),
    ("gaze", "fps"): (5, 30), ("gaze", "pan_speed"): (0.1, 5),
    ("senses", "fps"): (2, 30), ("senses", "calibrate_seconds"): (5, 60), ("senses", "window_seconds"): (1, 30),
    ("senses", "cooldown_seconds"): (0, 600), ("senses", "min_confidence"): (0, 1),
    ("senses", "decline_seconds"): (30, 600),
    ("senses", "lean_ratio"): (1.01, 2), ("senses", "lean_weak"): (1.01, 2), ("senses", "lean_release"): (1.0, 2),
    ("senses", "rage_clicks"): (2, 20), ("senses", "retype_backspaces"): (2, 50),
    ("senses", "hesitation_seconds"): (1, 30),
    ("profile", "page_zoom"): (0, 5), ("profile", "min_font_px"): (0, 72),
    ("profile", "screen_magnify"): (1, 16), ("tools", "timeout_seconds"): (1, 300),
}


class ConfigError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _merge_dict(base: dict, user: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge_dict(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def merge(user: dict) -> dict:
    out = copy.deepcopy(DEFAULTS)
    for k, v in user.items():
        if k in REPLACE_TABLES:
            out[k] = copy.deepcopy(v)
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge_dict(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def parse(text: str) -> dict:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError([f"config.toml syntax error: {e}"])


def _type_ok(value, expected) -> bool:
    if isinstance(expected, tuple) and expected and isinstance(expected[0], str):
        return value in expected
    if expected is bool:
        return isinstance(value, bool)
    if expected is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == NUM:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, expected)


def _expect_text(expected) -> str:
    if isinstance(expected, tuple) and expected and isinstance(expected[0], str):
        return "one of " + ", ".join(f'"{x}"' for x in expected)
    if expected == NUM:
        return "a number"
    return {str: "text in quotes", int: "a whole number", bool: "true or false", list: "a list [...]"}.get(
        expected, str(expected))


def _check_os_value(value, where: str, errors: list) -> None:
    if isinstance(value, str):
        return
    if isinstance(value, dict) and value and set(value) <= OS_KEYS and all(isinstance(v, str) for v in value.values()):
        return
    errors.append(f"{where} must be text, or {{ mac = \"...\", windows = \"...\", linux = \"...\" }}")


def validate(cfg: dict, known_tools: set[str] | None = None) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for a merged config."""
    errors, warnings = [], []
    known_tools = set(known_tools or ())

    for section, value in cfg.items():
        if section not in SPEC and section not in ("hotkeys", "page_styles", "commands"):
            warnings.append(f"unknown section [{section}] (typo?)")
            continue
        if section in SPEC:
            if not isinstance(value, dict):
                errors.append(f"[{section}] must be a table")
                continue
            for key, v in value.items():
                expected = SPEC[section].get(key)
                if expected is None:
                    warnings.append(f"unknown setting {section}.{key} (typo?)")
                    continue
                if not _type_ok(v, expected):
                    errors.append(f"{section}.{key} should be {_expect_text(expected)}, not {v!r}")
                    continue
                rng = RANGES.get((section, key))
                if rng and not (rng[0] <= v <= rng[1]):
                    errors.append(f"{section}.{key} = {v} is outside {rng[0]}..{rng[1]}")

    styles = cfg.get("page_styles", {})
    if not isinstance(styles, dict):
        errors.append("[page_styles] must be a table of name = \"\"\"css\"\"\"")
        styles = {}
    for name, css in styles.items():
        if not STYLE_RE.match(name):
            errors.append(f"page style name '{name}' must use lowercase letters, digits or _")
        if not isinstance(css, str):
            errors.append(f"page_styles.{name} must be CSS text in quotes")
    for s in cfg.get("profile", {}).get("page_styles", []) or []:
        if s not in styles:
            errors.append(f"profile.page_styles has '{s}', which is not in [page_styles]")
    for t in cfg.get("tools", {}).get("disabled", []) or []:
        if not isinstance(t, str):
            errors.append("tools.disabled must be a list of tool names in quotes")
    wake = cfg.get("voice", {}).get("wake_words", ["ally"])
    if isinstance(wake, list) and (not wake or not all(isinstance(w, str) and w.strip() for w in wake)):
        errors.append('voice.wake_words must be a list of words in quotes, e.g. ["ally"]')

    commands = cfg.get("commands", {})
    if not isinstance(commands, dict):
        errors.append("[commands] must be tables like [commands.my_command]")
        commands = {}
    command_hotkeys = {}
    for name, c in commands.items():
        where = f"commands.{name}"
        if not NAME_RE.match(name):
            errors.append(f"{where}: name must be 1-64 letters, digits, _ or -")
        if name in known_tools:
            errors.append(f"{where}: the name '{name}' is already a built-in or plugin tool")
        if not isinstance(c, dict):
            errors.append(f"{where} must be a table")
            continue
        for k in c:
            if k not in ("description", "phrases", "hotkey", "steps", "allow_ai"):
                warnings.append(f"{where}: unknown setting '{k}'")
        if "description" in c and not isinstance(c["description"], str):
            errors.append(f"{where}.description must be text")
        phrases = c.get("phrases", [])
        if not isinstance(phrases, list) or not all(isinstance(p, str) for p in phrases):
            errors.append(f"{where}.phrases must be a list of text")
        if "allow_ai" in c and not isinstance(c["allow_ai"], bool):
            errors.append(f"{where}.allow_ai must be true or false")
        if "hotkey" in c:
            if not isinstance(c["hotkey"], str):
                errors.append(f"{where}.hotkey must be text like \"<mod>+1\"")
            else:
                try:
                    keys.parse_combo(c["hotkey"])
                    command_hotkeys[c["hotkey"]] = name
                except ValueError as e:
                    errors.append(f"{where}.hotkey: {e}")
        steps = c.get("steps")
        if not isinstance(steps, list) or not steps:
            errors.append(f"{where}.steps must be a non-empty list of steps")
            continue
        for i, step in enumerate(steps):
            sw = f"{where}.steps[{i + 1}]"
            if not isinstance(step, dict):
                errors.append(f"{sw} must be like {{ say = \"hi\" }}")
                continue
            kinds = [k for k in STEP_KINDS if k in step]
            if len(kinds) != 1:
                errors.append(f"{sw} needs exactly one of: {', '.join(STEP_KINDS)}")
                continue
            kind = kinds[0]
            extra = set(step) - {kind} - ({"args"} if kind == "tool" else set())
            if extra:
                errors.append(f"{sw}: unexpected {', '.join(sorted(extra))}")
            v = step[kind]
            if kind == "tool":
                if not isinstance(v, str):
                    errors.append(f"{sw}: tool must be a tool name in quotes")
                elif known_tools and v not in known_tools and v not in commands:
                    warnings.append(f"{sw}: no tool named '{v}' right now")
                if "args" in step and not isinstance(step["args"], dict):
                    errors.append(f"{sw}: args must be a table like {{ zoom = 1.5 }}")
            elif kind in ("say", "ask", "notify"):
                if not isinstance(v, str) or not v.strip():
                    errors.append(f"{sw}: {kind} must be non-empty text")
            elif kind in ("shell", "open", "keys"):
                _check_os_value(v, f"{sw}.{kind}", errors)
                if kind == "keys":
                    for combo in (v.values() if isinstance(v, dict) else [v]):
                        if isinstance(combo, str):
                            try:
                                keys.split_combo(keys.expand_mod(combo))
                            except ValueError as e:
                                errors.append(f"{sw}.keys: {e}")
            elif kind == "wait":
                if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 <= v <= 60:
                    errors.append(f"{sw}: wait must be seconds between 0 and 60")

    hotkeys = cfg.get("hotkeys", {})
    if not isinstance(hotkeys, dict):
        errors.append("[hotkeys] must be a table like \"<mod>+r\" = \"read_selection\"")
        hotkeys = {}
    seen = {}
    for combo, action in list(hotkeys.items()) + [(k, f"command {v}") for k, v in command_hotkeys.items()]:
        try:
            norm = tuple(sorted(keys.parse_combo(combo)))
        except ValueError as e:
            errors.append(f"hotkey {e}")
            continue
        if norm in seen and action != "" and seen[norm] != "":
            errors.append(f"hotkey '{combo}' is used twice ({seen[norm]} and {action})")
        if action != "" or norm not in seen:
            seen[norm] = action
        if isinstance(action, dict):
            if not isinstance(action.get("tool"), str):
                errors.append(f"hotkey '{combo}': a table needs tool = \"name\" (and optional args)")
            elif "args" in action and not isinstance(action["args"], dict):
                errors.append(f"hotkey '{combo}': args must be a table")
        elif not isinstance(action, str):
            errors.append(f"hotkey '{combo}' must map to text or {{ tool = ..., args = ... }}")
    return errors, warnings


class ConfigManager:
    """Owns the current settings. `data` is replaced atomically on each good reload."""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict = merge({})
        self.error: str | None = None
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self._mtime: float | None = None
        self._lock = threading.Lock()

    def ensure_file(self) -> bool:
        if self.path.exists():
            return False
        atomic_write(self.path, DEFAULT_TEXT)
        return True

    def read_text(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8")
        except OSError:
            return DEFAULT_TEXT

    def check_text(self, text: str, known_tools: set[str] | None = None) -> tuple[dict | None, list, list]:
        try:
            cfg = merge(parse(text))
        except ConfigError as e:
            return None, e.errors, []
        errors, warnings = validate(cfg, known_tools)
        return (None if errors else cfg), errors, warnings

    def reload(self, known_tools: set[str] | None = None) -> bool:
        """Re-read the file. Returns True if new settings were applied."""
        with self._lock:
            try:
                self._mtime = self.path.stat().st_mtime
            except OSError:
                self._mtime = None
            cfg, errors, warnings = self.check_text(self.read_text(), known_tools)
            self.warnings = warnings
            if cfg is None:
                self.errors = errors
                self.error = "; ".join(errors)
                log.error("config.toml has problems, keeping the last good settings:\n  %s", "\n  ".join(errors))
                return False
            self.data, self.errors, self.error = cfg, [], None
            for w in warnings:
                log.warning("config: %s", w)
            return True

    def changed_on_disk(self) -> bool:
        try:
            return self.path.stat().st_mtime != self._mtime
        except OSError:
            return self._mtime is not None

    def save_text(self, text: str, known_tools: set[str] | None = None) -> tuple[bool, list, list]:
        cfg, errors, warnings = self.check_text(text, known_tools)
        if cfg is None:
            return False, errors, warnings
        atomic_write(self.path, text)
        self.reload(known_tools)
        return True, [], warnings
