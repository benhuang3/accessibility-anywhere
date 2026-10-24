"""GNOME custom keyboard shortcuts for Ally (makes hotkeys work on Wayland, where pynput can't listen).

Each [hotkeys] entry becomes a GNOME shortcut that runs `run.py tool ...` or `run.py send ...`.
Uses org.gnome.settings-daemon.plugins.media-keys custom-keybindings (keys: name, binding, command).
"""

from __future__ import annotations

import ast
import json
import shlex
import subprocess
import sys

from . import keys
from .paths import CODE_ROOT, Paths

LIST_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
ITEM_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
BASE = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"
PREFIX = BASE + "ally-"

MODS = {"ctrl": "<Control>", "ctrl_l": "<Control>", "ctrl_r": "<Control>", "alt": "<Alt>", "alt_l": "<Alt>",
        "alt_r": "<Alt>", "shift": "<Shift>", "shift_l": "<Shift>", "shift_r": "<Shift>", "cmd": "<Super>",
        "cmd_l": "<Super>", "cmd_r": "<Super>"}
CHARS = {"=": "equal", "-": "minus", "+": "plus", ",": "comma", ".": "period", "/": "slash", ";": "semicolon",
         "'": "apostrophe", "[": "bracketleft", "]": "bracketright", "\\": "backslash", "`": "grave"}
SPECIAL = {"space": "space", "enter": "Return", "esc": "Escape", "tab": "Tab", "up": "Up", "down": "Down",
           "left": "Left", "right": "Right", "home": "Home", "end": "End", "page_up": "Page_Up",
           "page_down": "Page_Down", "delete": "Delete", "backspace": "BackSpace", "insert": "Insert",
           "print_screen": "Print", "pause": "Pause"}


def gnome_binding(combo: str) -> str:
    out_mods, key = [], ""
    for p in keys.split_combo(keys.expand_mod(combo, "Linux")):
        if len(p) > 2 and p.startswith("<") and p.endswith(">"):
            name = p[1:-1].lower()
            if name in MODS:
                out_mods.append(MODS[name])
            elif name.startswith("f") and name[1:].isdigit():
                key = name.upper()
            elif name in SPECIAL:
                key = SPECIAL[name]
            else:
                raise ValueError(f"GNOME can't use the key <{name}>")
        else:
            key = CHARS.get(p, p.lower())
    if not key:
        raise ValueError(f"'{combo}' has no normal key")
    return "".join(dict.fromkeys(out_mods)) + key


def command_for(action, paths: Paths, tool_names: set[str]) -> str:
    base = [sys.executable, str(CODE_ROOT / "run.py")]
    if paths.home != CODE_ROOT:
        base += ["--home", str(paths.home)]
    if isinstance(action, dict):
        args = base + ["tool", str(action.get("tool", "")), json.dumps(action.get("args") or {})]
    elif action in tool_names:
        args = base + ["tool", action]
    else:
        args = base + ["send", str(action)]
    return " ".join(shlex.quote(a) for a in args)


def _gsettings(*args: str) -> str:
    cp = subprocess.run(["gsettings", *args], capture_output=True, text=True, timeout=10)
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or f"gsettings {' '.join(args)} failed")
    return cp.stdout.strip()


def _current_list() -> list[str]:
    raw = _gsettings("get", LIST_SCHEMA, "custom-keybindings")
    if raw.startswith("@as "):
        raw = raw[4:]
    try:
        value = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        value = []
    return [v for v in value if isinstance(v, str)]


def _set_list(items: list[str]) -> None:
    _gsettings("set", LIST_SCHEMA, "custom-keybindings", "[" + ", ".join(repr(i) for i in items) + "]")


def install(cfg: dict, paths: Paths, tool_names: set[str]) -> list[str]:
    """Replace Ally's GNOME shortcuts with the current [hotkeys] and command hotkeys."""
    remove()
    entries = [(k, v) for k, v in (cfg.get("hotkeys") or {}).items() if v]
    entries += [(c["hotkey"], name) for name, c in (cfg.get("commands") or {}).items()
                if isinstance(c, dict) and c.get("hotkey")]
    current = _current_list()
    done = []
    for i, (combo, action) in enumerate(entries):
        path = f"{PREFIX}{i}/"
        target = f"{ITEM_SCHEMA}:{path}"
        label = action.get("tool") if isinstance(action, dict) else str(action)
        binding = gnome_binding(combo)
        _gsettings("set", target, "name", f"Ally: {label[:40]}")
        _gsettings("set", target, "command", command_for(action, paths, tool_names))
        _gsettings("set", target, "binding", binding)
        current.append(path)
        done.append(f"{binding} -> {label}")
    _set_list(current)
    return done


def remove() -> int:
    current = _current_list()
    ours = [p for p in current if p.startswith(PREFIX)]
    for path in ours:
        target = f"{ITEM_SCHEMA}:{path}"
        for key in ("name", "command", "binding"):
            try:
                _gsettings("reset", target, key)
            except RuntimeError:
                pass
    if ours:
        _set_list([p for p in current if not p.startswith(PREFIX)])
    return len(ours)
