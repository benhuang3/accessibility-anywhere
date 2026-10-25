"""Hotkey strings, e.g. "<ctrl>+<alt>+r" (same format as pynput's GlobalHotKeys).

"<mod>" is Ally shorthand for Ctrl+Alt.
"""

from __future__ import annotations

import platform

# Key names accepted inside <...> (pynput.keyboard.Key members).
SPECIAL_KEYS = set(
    "alt alt_l alt_r alt_gr backspace caps_lock cmd cmd_l cmd_r ctrl ctrl_l ctrl_r delete down end "
    "enter esc home left page_down page_up right shift shift_l shift_r space tab up "
    "media_play_pause media_volume_mute media_volume_down media_volume_up media_previous media_next "
    "insert menu num_lock pause print_screen scroll_lock".split()
) | {f"f{i}" for i in range(1, 21)}

MODIFIERS = {"alt", "alt_l", "alt_r", "alt_gr", "cmd", "cmd_l", "cmd_r", "ctrl", "ctrl_l", "ctrl_r",
             "shift", "shift_l", "shift_r"}

STANDALONE_OK = {f"f{i}" for i in range(13, 21)} | {
    "media_play_pause", "media_volume_mute", "media_volume_down", "media_volume_up",
    "media_previous", "media_next", "pause", "scroll_lock"}


def system_name() -> str:
    return platform.system()  # "Windows", "Linux"


def expand_mod(combo: str, system: str | None = None) -> str:
    system = system or system_name()
    return combo.replace("<mod>", "<ctrl>+<alt>")


def split_combo(keys: str) -> list[str]:
    """Split on '+' exactly like pynput.keyboard.HotKey.parse ('<ctrl>++' -> ['<ctrl>', '+'])."""
    parts, start = [], 0
    for i, c in enumerate(keys):
        if c == "+" and i != start:
            parts.append(keys[start:i])
            start = i + 1
    if start == len(keys):
        raise ValueError(f"'{keys}' ends with '+'")
    parts.append(keys[start:])
    return parts


def parse_combo(combo: str, system: str | None = None) -> list[str]:
    """Validate a hotkey string. Returns its normalized parts or raises ValueError."""
    text = expand_mod(combo.strip(), system)
    if not text:
        raise ValueError("empty hotkey")
    parts = split_combo(text)
    out = []
    for p in parts:
        if len(p) == 1:
            out.append(p.lower())
        elif len(p) > 2 and p[0] == "<" and p[-1] == ">":
            name = p[1:-1].lower()
            if name not in SPECIAL_KEYS and not name.isdigit():
                raise ValueError(f"unknown key <{name}> in '{combo}'")
            out.append(f"<{name}>")
        else:
            raise ValueError(f"'{p}' in '{combo}' is not a key (use one character or a name like <ctrl>)")
    if len(set(out)) != len(out):
        raise ValueError(f"'{combo}' repeats a key")
    names = [p[1:-1] for p in out if p.startswith("<") and len(p) > 2]
    mods = [n for n in names if n in MODIFIERS]
    normal = [p for p in out if not (p.startswith("<") and len(p) > 2 and p[1:-1] in MODIFIERS)]
    if not normal:
        raise ValueError(f"'{combo}' needs a normal key, not only modifiers")
    if not mods and not all(p[1:-1] in STANDALONE_OK for p in normal if p.startswith("<")):
        raise ValueError(f"'{combo}' needs a modifier like <ctrl> so normal typing isn't hijacked")
    if not mods and any(len(p) == 1 for p in normal):
        raise ValueError(f"'{combo}' needs a modifier like <ctrl> so normal typing isn't hijacked")
    return out


def describe(combo: str, system: str | None = None) -> str:
    """Human-friendly label, e.g. '<ctrl>+<alt>+r' -> 'Ctrl+Alt+R'."""
    system = system or system_name()
    names = {"cmd": "Win", "alt": "Alt",
             "ctrl": "Ctrl", "shift": "Shift", "esc": "Esc", "space": "Space", "enter": "Enter"}
    try:
        parts = split_combo(expand_mod(combo, system))
    except ValueError:
        return combo
    out = []
    for p in parts:
        if p.startswith("<") and p.endswith(">") and len(p) > 2:
            n = p[1:-1].lower()
            out.append(names.get(n, n.replace("_", " ").title()))
        else:
            out.append(p.upper())
    return "+".join(out)
