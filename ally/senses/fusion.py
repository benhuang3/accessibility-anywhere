"""Signals -> one Struggle event. One signal alone means little, so by default help is offered only
when two different signals agree within a few seconds (a weak squint plus leaning in, or a squint
plus repeated clicks). Kinds listed in [senses] solo may report on their own when strong.

The Struggle shape follows the team contract (Code Foundation, ally/contracts.py when merged):
    {kind, confidence 0-1, target {app, site, window, x, y, box}, source, detail, ts}
kind is squint | lean | rage_click | hesitation | retype | fused.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .detectors import Signal

KINDS = ("squint", "lean", "rage_click", "hesitation", "retype", "fused")


@dataclass
class Struggle:
    kind: str
    confidence: float
    target: dict = field(default_factory=dict)
    source: str = "senses"
    detail: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "confidence": self.confidence, "target": dict(self.target),
                "source": self.source, "detail": dict(self.detail), "ts": self.ts}

    def describe(self) -> str:
        parts = self.detail.get("signals") or [{"kind": self.kind, "tier": self.detail.get("tier", "")}]
        what = " + ".join(f"{p.get('tier', '')} {p['kind']}".strip() for p in parts)
        where = self.target.get("site") or self.target.get("app") or "the screen"
        return f"struggle ({what}) on {where}, confidence {self.confidence:.2f}"


class Fuser:
    def __init__(self, window: float = 4.0, cooldown: float = 30.0, solo=(), min_confidence: float = 0.5):
        self.window, self.cooldown, self.min_confidence = window, cooldown, min_confidence
        self.solo = set(solo)
        self.recent: dict[str, Signal] = {}  # newest signal per kind
        self.last_emit = -1e9

    def configure(self, window: float, cooldown: float, solo, min_confidence: float) -> None:
        self.window, self.cooldown, self.min_confidence = window, cooldown, min_confidence
        self.solo = set(solo)

    def add(self, sig: Signal) -> Struggle | None:
        """Returns a Struggle (without a target; the caller adds where it happened) or None."""
        t = sig.ts
        self.recent = {k: s for k, s in self.recent.items() if t - s.ts <= self.window}
        old = self.recent.get(sig.kind)
        if old is None or sig.confidence >= old.confidence or t - old.ts > 1.0:
            self.recent[sig.kind] = sig
        if t - self.last_emit < self.cooldown:
            return None
        parts = sorted(self.recent.values(), key=lambda s: -s.confidence)
        if len(parts) >= 2:
            miss = 1.0
            for p in parts:
                miss *= 1.0 - p.confidence
            conf, kind = min(0.99, 1.0 - miss), "fused"
        elif sig.kind in self.solo and sig.tier == "strong":
            conf, kind = sig.confidence, sig.kind
        else:
            return None
        if conf < self.min_confidence:
            return None
        self.last_emit = t
        self.recent.clear()
        src = {p.source for p in parts}
        source = "simulate" if "simulate" in src else ("senses" if len(src) > 1 else f"senses.{src.pop()}")
        detail = {"signals": [{"kind": p.kind, "tier": p.tier, "confidence": p.confidence, **p.detail}
                              for p in parts]}
        if kind != "fused":
            detail["tier"] = sig.tier
        pointer = next((p for p in parts if p.x is not None), None)
        target = {"x": pointer.x, "y": pointer.y} if pointer else {}
        return Struggle(kind, round(conf, 2), target, source, detail, t)
