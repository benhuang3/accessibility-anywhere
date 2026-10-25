"""Shared message formats and the in-process event bus for the proactive layer.

Everyone codes against THIS file. If you need a new field, add it here (with a default) and tell the team.

    senses/  (Hayden)  --publish-->  Struggle
    risk/    (Ganesh)  --publish-->  Risk
    proactive.py (Ben H) subscribes, decides, and creates an Offer
    overlay/ (Ben W) + voice_confirm.py (Ganesh) show the Offer and call offer.resolve("yes"|"no")

Topics on the bus: "struggle", "risk", "offer", "offer_result", "applied", "context".
"""

from __future__ import annotations

import itertools
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

log = logging.getLogger("ally")

STRUGGLE_KINDS = ("squint", "lean", "rage_click", "hesitation", "retype", "fused")
RISK_KINDS = ("scam_popup", "untrusted_site", "remote_access", "payment_field")
SEVERITIES = ("low", "medium", "high")
CHOICES = ("yes", "no", "timeout", "dismissed")


class ContractError(ValueError):
    pass


def _clamp01(x) -> float:
    try:
        return max(0.0, min(1.0, float(x)))
    except (TypeError, ValueError):
        raise ContractError("confidence must be a number from 0 to 1")


@dataclass
class Target:
    """Where the struggle happened. Fill what you know; everything is optional."""
    app: str = ""            # e.g. "Google Chrome", "Microsoft Word"
    site: str = ""           # host only, e.g. "chase.com" ("" outside the browser)
    window: str = ""         # window title
    x: int | None = None     # screen point of interest (cursor / gaze), physical pixels
    y: int | None = None
    box: tuple[int, int, int, int] | None = None   # (left, top, width, height) to highlight

    @classmethod
    def from_dict(cls, d: dict | None) -> "Target":
        d = d or {}
        box = d.get("box")
        if box is not None:
            if not (isinstance(box, (list, tuple)) and len(box) == 4):
                raise ContractError("target.box must be [left, top, width, height]")
            box = tuple(int(v) for v in box)
        return cls(app=str(d.get("app", ""))[:120], site=str(d.get("site", ""))[:200],
                   window=str(d.get("window", ""))[:200],
                   x=int(d["x"]) if d.get("x") is not None else None,
                   y=int(d["y"]) if d.get("y") is not None else None, box=box)


@dataclass
class Struggle:
    kind: str                                  # one of STRUGGLE_KINDS
    confidence: float = 0.7                    # 0..1
    target: Target = field(default_factory=Target)
    source: str = ""                           # "vision", "behavior", "simulate", ...
    detail: dict = field(default_factory=dict)  # free-form debug info (blendshape scores etc.)
    ts: float = field(default_factory=time.time)

    def __post_init__(self):
        if self.kind not in STRUGGLE_KINDS:
            raise ContractError(f"struggle kind must be one of {', '.join(STRUGGLE_KINDS)}")
        self.confidence = _clamp01(self.confidence)

    @classmethod
    def from_dict(cls, d: dict) -> "Struggle":
        return cls(kind=str(d.get("kind", "")), confidence=d.get("confidence", 0.7),
                   target=Target.from_dict(d.get("target")), source=str(d.get("source", ""))[:40],
                   detail=dict(d.get("detail") or {}), ts=float(d.get("ts") or time.time()))

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Risk:
    kind: str                       # one of RISK_KINDS
    severity: str = "medium"        # low | medium | high
    evidence: str = ""              # human-readable why, e.g. "page says 'call Microsoft support now'"
    url: str = ""
    app: str = ""
    source: str = ""
    ts: float = field(default_factory=time.time)

    def __post_init__(self):
        if self.kind not in RISK_KINDS:
            raise ContractError(f"risk kind must be one of {', '.join(RISK_KINDS)}")
        if self.severity not in SEVERITIES:
            raise ContractError("severity must be low, medium or high")

    @classmethod
    def from_dict(cls, d: dict) -> "Risk":
        return cls(kind=str(d.get("kind", "")), severity=str(d.get("severity", "medium")),
                   evidence=str(d.get("evidence", ""))[:500], url=str(d.get("url", ""))[:500],
                   app=str(d.get("app", ""))[:120], source=str(d.get("source", ""))[:40],
                   ts=float(d.get("ts") or time.time()))

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Accommodation:
    """A concrete fix = one Ally tool call. key is what habit memory stores."""
    key: str                         # stable id, e.g. "zoom_in", "page_zoom_150"
    label: str                       # spoken/shown, e.g. "make it bigger"
    tool: str                        # Ally tool name
    args: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


_offer_ids = itertools.count(1)


class Offer:
    """A yes/no question shown on the overlay AND asked by voice. First answer wins."""

    def __init__(self, prompt: str, options=("yes", "no"), accommodation: Accommodation | None = None,
                 context: str = "", reason: Any = None, timeout: float = 8.0, highlight=None):
        self.id = next(_offer_ids)
        self.prompt = prompt
        self.options = tuple(options)
        self.accommodation = accommodation
        self.context = context
        self.reason = reason            # the Struggle or Risk that caused it
        self.timeout = timeout
        self.highlight = highlight      # (l, t, w, h) or None
        self.created = time.time()
        self.choice: str | None = None
        self.answered_by = ""
        self._done = threading.Event()
        self._lock = threading.Lock()

    def resolve(self, choice: str, by: str = "") -> bool:
        """Answer the offer. Returns False if someone already answered."""
        choice = choice if choice in CHOICES else "no"
        with self._lock:
            if self.choice is not None:
                return False
            self.choice, self.answered_by = choice, by
        self._done.set()
        return True

    def wait(self, timeout: float | None = None) -> str:
        if not self._done.wait(self.timeout if timeout is None else timeout):
            self.resolve("timeout", "timer")
        return self.choice or "timeout"

    @property
    def done(self) -> bool:
        return self._done.is_set()

    def to_dict(self) -> dict:
        return {"id": self.id, "prompt": self.prompt, "options": list(self.options), "context": self.context,
                "accommodation": self.accommodation.to_dict() if self.accommodation else None,
                "timeout": self.timeout, "highlight": self.highlight, "choice": self.choice,
                "answered_by": self.answered_by}


class EventBus:
    """Tiny thread-safe pub/sub. Handlers run on the publisher's thread, so keep them fast
    (put work on your own queue/thread). A crashing handler is logged, never propagated."""

    def __init__(self):
        self._subs: dict[str, list[Callable[[Any], None]]] = {}
        self._lock = threading.Lock()

    def subscribe(self, topic: str, handler: Callable[[Any], None]) -> Callable[[], None]:
        with self._lock:
            self._subs.setdefault(topic, []).append(handler)

        def unsubscribe():
            with self._lock:
                if handler in self._subs.get(topic, []):
                    self._subs[topic].remove(handler)
        return unsubscribe

    def publish(self, topic: str, message: Any = None) -> int:
        with self._lock:
            calls = [(h, message) for h in self._subs.get(topic, [])]
            if topic != "*":  # "*" subscribers get (topic, message) for everything (dashboard/logging)
                calls += [(h, (topic, message)) for h in self._subs.get("*", [])]
        for h, msg in calls:
            try:
                h(msg)
            except Exception:
                log.exception("bus handler for %r failed", topic)
        return len(calls)


def parse_event(data: dict):
    """Turn a JSON body from POST /api/event into ("struggle", Struggle) / ("risk", Risk) / ("answer", ...)."""
    t = str(data.get("type", ""))
    if t == "struggle":
        return t, Struggle.from_dict(data)
    if t == "risk":
        return t, Risk.from_dict(data)
    if t == "answer":
        choice = str(data.get("choice", ""))
        if choice not in CHOICES:
            raise ContractError("choice must be yes, no, timeout or dismissed")
        return t, {"offer_id": data.get("offer_id"), "choice": choice}
    raise ContractError("type must be struggle, risk or answer")
