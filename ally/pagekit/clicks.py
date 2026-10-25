"""Click by voice: matching what the user said to the buttons and links on screen (docs/click_by_voice.md).

Pure functions. The extension lists the clickable things on screen with their labels; these pick one.
"""

from __future__ import annotations

import math
import re

DROP = set("the a an on button buttons link links tab tabs box field icon option please".split())
CAREFUL = [
    ("pays for something", r"\b(buy|pay|payment|purchase|checkout|check out|place (?:my |your )?order|order now|"
                           r"subscribe now|donate|add to cart)\b"),
    ("sends something", r"\b(send|transfer|wire|submit (?:payment|order))\b"),
    ("deletes something", r"\b(delete|remove|erase|unsubscribe|cancel (?:my |your )?(?:account|subscription))\b"),
    ("signs you out", r"\b(sign out|log out|logout|signout)\b"),
    ("confirms something", r"\bconfirm\b"),
]
ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
        "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
NUMBER_WORDS = {w: i for i, w in enumerate(ONES)} | dict(TENS)
for _t, _v in TENS.items():
    for _i in range(1, 10):
        NUMBER_WORDS[f"{_t} {ONES[_i]}"] = _v + _i
        NUMBER_WORDS[f"{_t}-{ONES[_i]}"] = _v + _i
NUMBER_WORDS |= {"to": 2, "too": 2, "for": 4, "won": 1, "ate": 8}  # how speech-to-text hears small numbers
NUMBER_RE = r"(?:\d{1,2}|" + "|".join(sorted((re.escape(w) for w in NUMBER_WORDS), key=len, reverse=True)) + ")"
MATCH_MIN = 0.5


def parse_number(text: str) -> int | None:
    t = " ".join(str(text or "").lower().split())
    if t.isdigit():
        n = int(t)
        return n if 1 <= n <= 99 else None
    n = NUMBER_WORDS.get(t)
    return n if n and 1 <= n <= 99 else None


def _stem(w: str) -> str:
    """Light stemming so "renewing" finds "Renew" and "passports" finds "Passport"."""
    if len(w) > 5 and w.endswith("ing"):
        return w[:-3]
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def label_tokens(text: str) -> list[str]:
    return [_stem(w) for w in re.findall(r"[a-z0-9]+", str(text or "").lower().replace("'", "")) if w not in DROP]


def _score(spoken: list[str], label: str) -> float:
    have = label_tokens(label)
    if not spoken or not have:
        return 0.0
    if have == spoken:
        return 3.0
    if have[: len(spoken)] == spoken:
        return 2.0
    if all(w in have for w in spoken):
        return 1.5
    return sum(1 for w in spoken if w in have) / len(spoken)


def _centre_distance(item: dict, view: dict) -> float:
    cx, cy = item.get("x", 0) + item.get("w", 0) / 2, item.get("y", 0) + item.get("h", 0) / 2
    return math.hypot(cx - float(view.get("w") or 0) / 2, cy - float(view.get("h") or 0) / 2)


def match(spoken: str, items: list[dict], view: dict | None = None, shortest: bool = True) -> dict:
    """The targets that best match `spoken`: {"items": [...], "score": float}. Several items = a real tie
    (same score and same label), for "which one?". No items = nothing close enough. shortest=False keeps every
    tied item even when their labels differ (the caller breaks the tie)."""
    want = label_tokens(spoken)
    scored = [(_score(want, str(i.get("label", ""))), i) for i in items]
    scored = [(s, i) for s, i in scored if s >= MATCH_MIN]
    if not scored:
        return {"items": [], "score": 0.0}
    best = max(s for s, _ in scored)
    top = [i for s, i in scored if s == best]
    top.sort(key=lambda i: _centre_distance(i, view or {}))
    labels = {" ".join(label_tokens(i.get("label", ""))) for i in top}
    if shortest and len(top) > 1 and len(labels) > 1 and best < 3.0:
        # Different labels with the same partial score: prefer the shortest label (closest to what was said).
        shortest = min(len(label_tokens(i.get("label", ""))) for i in top)
        top = [i for i in top if len(label_tokens(i.get("label", ""))) == shortest]
    return {"items": top, "score": best}


def careful(label: str, href: str = "") -> str | None:
    """What a click would do that deserves a question first ("pays for something"), or None."""
    text = " ".join(str(label or "").lower().split())
    for kind, pattern in CAREFUL:
        if re.search(pattern, text):
            return kind
    if re.search(r"/(checkout|payment|logout|signout)\b", str(href or "").lower()):
        return "pays for something" if "pay" in href.lower() or "checkout" in href.lower() else "signs you out"
    return None


def short_label(label: str, limit: int = 60) -> str:
    text = " ".join(str(label or "").split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "..."
