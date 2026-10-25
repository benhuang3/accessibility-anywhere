"""Choosing a Chrome tab for "go to the other tab", "next tab", "tab 3", "the gmail tab" (docs/tabs_and_pointing.md).

Pure functions over the extension's tabList answer.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

SYNONYMS = {"email": {"mail", "gmail", "outlook", "inbox", "yahoo"}, "mail": {"gmail", "outlook", "inbox"},
            "video": {"youtube"}, "videos": {"youtube"}, "news": {"news", "bbc", "cnn", "nytimes"},
            "map": {"maps"}, "calendar": {"calendar"}, "facebook": {"facebook", "fb"}}
DROP = {"the", "my", "a", "tab", "page", "website", "site", "window", "one", "that", "with", "on"}


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", str(text or "").lower()) if w not in DROP]


def _site_words(url: str) -> set[str]:
    host = urlsplit(url or "").hostname or ""
    return {p for p in host.split(".") if p not in ("www", "com", "org", "net", "gov", "co", "uk", "edu", "")}


def tab_words(tab: dict) -> set[str]:
    return set(_words(tab.get("title", ""))) | _site_words(tab.get("url", ""))


def window_tabs(listed: dict) -> list[dict]:
    """The tabs of the window the user is in, left to right."""
    tabs = listed.get("tabs") or []
    cur = [t for t in tabs if t.get("current")] or tabs
    return sorted(cur, key=lambda t: t.get("index", 0))


def active(listed: dict) -> dict | None:
    return next((t for t in window_tabs(listed) if t.get("active")), None)


def choose(listed: dict, which: str = "other", name: str = "", number: int = 0) -> dict:
    """{"tab": dict | None, "why": "ok" | "only_one" | "no_number" | "no_name" | "no_other"}."""
    tabs = window_tabs(listed)
    here = active(listed)
    if name.strip():
        want = _words(name)
        if not want:
            return {"tab": None, "why": "no_name"}
        best, score = None, 0.0
        for t in listed.get("tabs") or []:
            have = tab_words(t)
            got = sum(1 for w in want if w in have or have & SYNONYMS.get(w, set())) / len(want)
            if got > score or (got == score and best is not None and t is here):
                best, score = t, got
        return {"tab": best, "why": "ok"} if best is not None and score >= 0.5 else {"tab": None, "why": "no_name"}
    if number:
        if 1 <= number <= len(tabs):
            return {"tab": tabs[number - 1], "why": "ok"}
        return {"tab": None, "why": "no_number"}
    if len(listed.get("tabs") or []) < 2:
        return {"tab": None, "why": "only_one"}
    if which == "other":
        ids = {t["id"]: t for t in listed.get("tabs") or []}
        for tid in listed.get("recent") or []:
            if tid in ids and (here is None or tid != here["id"]):
                return {"tab": ids[tid], "why": "ok"}
        which = "previous"  # no history yet: the one to the left
    if here is None or len(tabs) < 2:
        return {"tab": None, "why": "no_other"}
    i = tabs.index(here)
    pick = {"next": (i + 1) % len(tabs), "previous": (i - 1) % len(tabs), "first": 0, "last": len(tabs) - 1}
    return {"tab": tabs[pick.get(which, (i - 1) % len(tabs))], "why": "ok"}


def short_title(tab: dict, limit: int = 50) -> str:
    title = " ".join(str(tab.get("title") or "").split()) or (urlsplit(tab.get("url", "")).hostname or "a tab")
    return title if len(title) <= limit else title[:limit].rsplit(" ", 1)[0] + "..."


def where(box: dict, view: dict) -> str:
    """Spoken position of a box on the screen: "near the top right", "in the middle of the screen"."""
    vw, vh = float(view.get("w") or 1), float(view.get("h") or 1)
    cx = (box.get("x", 0) + box.get("w", 0) / 2) / vw
    cy = (box.get("y", 0) + box.get("h", 0) / 2) / vh
    row = "top" if cy < 0.34 else "bottom" if cy > 0.66 else "middle"
    col = "left" if cx < 0.34 else "right" if cx > 0.66 else ""
    if row == "middle" and not col:
        return "in the middle of the screen"
    if row == "middle":
        return f"on the {col}"
    return f"near the {row} {col}".strip() if col else f"near the {row}"
