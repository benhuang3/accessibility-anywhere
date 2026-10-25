"""Page-behavior flags for risk tier 0, from the counters the Chrome extension sends.

Scam pages trap the Back button, refuse to let you leave, take over the whole screen or spam pop-ups. Ordinary
sites do one of these now and then (a video goes full screen, a document asks before you close it), so a single
behavior never counts. Pure standard library.
"""

from dataclasses import dataclass

_KEYS = ("pushState", "replaceState", "backTrap", "beforeunload", "fullscreenRequests", "keyboardLock",
         "contextMenuBlocked", "dialogs", "popups", "pointerLock")

FLAG_MEANINGS = {
    "history_trap": "the page puts itself back in the Back button history",
    "exit_trap": "the page asks you to confirm before leaving",
    "fullscreen": "the page took over the whole screen",
    "keyboard_lock": "the page is trying to capture the keyboard",
    "context_block": "the page blocks the right-click menu",
    "dialog_spam": "the page keeps opening pop-up message boxes",
    "popups": "the page keeps opening new windows",
    "pointer_lock": "the page is trying to capture the mouse",
}


@dataclass(frozen=True)
class Behavior:
    flags: tuple[str, ...]
    suspicious: bool
    evidence: str


def clean_signals(raw) -> dict:
    """Only the known keys, numbers clamped to 0..99, booleans kept as 0/1. Anything else is ignored."""
    out = {}
    if not isinstance(raw, dict):
        return out
    for key in _KEYS:
        value = raw.get(key)
        if isinstance(value, bool):
            out[key] = int(value)
        elif isinstance(value, (int, float)) and value == value:
            out[key] = max(0, min(99, int(value)))
    return out


def behavior_flags(raw) -> Behavior:
    s = clean_signals(raw)
    flags = []
    if s.get("backTrap", 0) >= 1 or s.get("pushState", 0) >= 5:
        flags.append("history_trap")
    if s.get("beforeunload", 0) >= 1:
        flags.append("exit_trap")
    if s.get("fullscreenRequests", 0) >= 1:
        flags.append("fullscreen")
    if s.get("keyboardLock", 0) >= 1:
        flags.append("keyboard_lock")
    if s.get("contextMenuBlocked", 0) >= 1:
        flags.append("context_block")
    if s.get("dialogs", 0) >= 2:
        flags.append("dialog_spam")
    if s.get("popups", 0) >= 2:
        flags.append("popups")
    if s.get("pointerLock", 0) >= 1:
        flags.append("pointer_lock")
    # Two different tricks together is what a scam page looks like; one alone is just a busy website.
    trapped = {"history_trap", "exit_trap", "keyboard_lock", "dialog_spam", "popups", "pointer_lock"} & set(flags)
    suspicious = len(set(flags)) >= 3 or (len(trapped) >= 1 and len(set(flags)) >= 2)
    evidence = ("This page is using tricks to keep you from leaving it." if suspicious
                else "No unusual page behavior.")
    return Behavior(tuple(flags), suspicious, evidence)
