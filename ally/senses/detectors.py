"""Struggle detectors. Pure Python (no camera, no numpy), so every rule here is unit tested.

Face: squint and lean, measured as a change from the person's own baseline, because raw MediaPipe
scores differ between people, cameras and runtimes. Frames are ignored while the person blinks,
smiles, turns away, winks, or sits in the dark (smiles narrow the eyes like a squint does).
Mouse and keyboard: rage clicks, retyping (backspace bursts), and hesitation (pointer resting).

Each detector returns a Signal when it fires; fusion.py decides whether that is a struggle.
Starting thresholds come from the team's Vision & Sensing research and are unverified on real
faces: tune them live in [senses] in config.toml.
"""

from __future__ import annotations

import collections
import math
import statistics
from dataclasses import dataclass, field


@dataclass
class Tuning:
    squint_delta: float = 0.20      # strong squint: score above baseline by this much...
    squint_weak: float = 0.12       # ...or this much for a weak one
    squint_hold: float = 1.5        # seconds above it, within the last squint_window seconds
    squint_window: float = 3.0
    wink_max: float = 0.15          # |left - right| above this is a wink, not a squint
    smile_gate: float = 0.35        # mean mouthSmile above this: ignore squint (cheeks narrow the eyes)
    blink_gate: float = 0.5         # mean eyeBlink above this: drop the frame
    pose_gate: float = 20.0         # degrees of turn or nod beyond which face numbers aren't trusted
    light_floor: float = 40.0       # mean face brightness (0-255) below this: too dark to judge squint
    lean_ratio: float = 1.15        # face width / baseline width: about 13% closer to the screen
    lean_weak: float = 1.10
    lean_release: float = 1.08
    lean_hold: float = 2.0
    rage_clicks: int = 4            # clicks within rage_seconds and rage_px of each other
    rage_seconds: float = 2.0
    rage_px: float = 30.0
    retype_backspaces: int = 5      # backspaces within retype_seconds, then typing again
    retype_seconds: float = 3.0
    hesitation_seconds: float = 4.0  # pointer resting this long with no click (adapts after 20 rests)
    rearm_seconds: float = 20.0     # a detector that fired waits this long before firing again

    @classmethod
    def from_config(cls, cfg: dict) -> "Tuning":
        t = cls()
        for k in t.__dataclass_fields__:
            v = cfg.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                setattr(t, k, type(getattr(t, k))(v))
        return t


@dataclass
class Signal:
    kind: str                # squint | lean | rage_click | hesitation | retype
    confidence: float        # 0-1
    ts: float
    tier: str = "strong"     # strong | weak
    source: str = "vision"   # vision | input | simulate
    x: int | None = None     # screen point, for mouse signals
    y: int | None = None
    detail: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- face
@dataclass
class Baseline:
    """The person's resting face numbers, from ~15 s of reading normally. Numbers only, never images."""

    squint: float
    width: float
    smile: float = 0.0
    light: float = -1.0
    samples: int = 0
    source: str = "calibrated"  # calibrated | auto
    created: float = 0.0

    MIN_SAMPLES = 30

    @classmethod
    def from_samples(cls, samples: list[dict], t: float, source: str = "calibrated") -> "Baseline":
        """Median of usable frames. Raises ValueError (in plain words) if they don't make a sane baseline."""
        good = [s for s in samples if s.get("blink", 0) < 0.5 and abs(s.get("yaw", 0)) < 25
                and abs(s.get("pitch", 0)) < 25 and s.get("width", 0) > 0]
        if len(good) < cls.MIN_SAMPLES:
            raise ValueError(f"saw your face clearly in only {len(good)} frames; face the screen with light on it")
        squint = statistics.median((s["squint_l"] + s["squint_r"]) / 2 for s in good)
        width = statistics.median(s["width"] for s in good)
        smile = statistics.median(s.get("smile", 0.0) for s in good)
        if squint > 0.6:
            raise ValueError("your eyes looked narrowed the whole time; relax and read normally, then try again")
        if not 0.05 <= width <= 0.95:
            raise ValueError("your face looked too far or too close; sit where you normally sit")
        if smile > 0.5:
            raise ValueError("you were smiling a lot; keep a neutral face for the setup")
        light = statistics.median(s.get("light", -1.0) for s in good)
        return cls(round(squint, 4), round(width, 4), round(smile, 4), round(light, 1), len(good), source, t)

    def drift(self, sig: dict, dt: float, tau: float = 300.0) -> None:
        """Follow slow posture and light changes (minutes), never a squint or lean in progress."""
        s = (sig["squint_l"] + sig["squint_r"]) / 2
        if abs(s - self.squint) > 0.08 or abs(sig["width"] / self.width - 1) > 0.05:
            return
        a = min(1.0, max(0.0, dt) / tau)
        self.squint += a * (s - self.squint)
        self.width += a * (sig["width"] - self.width)

    def to_dict(self) -> dict:
        return {"squint": self.squint, "width": self.width, "smile": self.smile, "light": self.light,
                "samples": self.samples, "source": self.source, "created": self.created}

    @classmethod
    def from_dict(cls, d: dict) -> "Baseline | None":
        try:
            b = cls(float(d["squint"]), float(d["width"]), float(d.get("smile", 0)), float(d.get("light", -1)),
                    int(d.get("samples", 0)), str(d.get("source", "calibrated")), float(d.get("created", 0)))
        except (KeyError, TypeError, ValueError):
            return None
        return b if 0 <= b.squint <= 0.6 and 0.05 <= b.width <= 0.95 else None


def gate(sig: dict, tune: Tuning) -> str:
    """Why this frame can't be trusted for squint ("" = it can)."""
    if sig["blink"] >= tune.blink_gate:
        return "blink"
    if abs(sig["yaw"]) > tune.pose_gate or abs(sig["pitch"]) > tune.pose_gate:
        return "turned"
    if 0 <= sig.get("light", -1) < tune.light_floor:
        return "dark"
    if sig["smile"] >= tune.smile_gate:
        return "smile"
    if abs(sig["squint_l"] - sig["squint_r"]) > tune.wink_max:
        return "wink"
    return ""


def held(points: collections.deque, threshold: float, max_gap: float = 0.5) -> float:
    """Seconds the value stayed at or above threshold, from (t, value) points (gaps count up to max_gap)."""
    total, prev = 0.0, None
    for t, v in points:
        if prev is not None and v >= threshold and prev[1] >= threshold:
            total += min(max_gap, t - prev[0])
        prev = (t, v)
    return total


class SquintDetector:
    def __init__(self, tune: Tuning):
        self.tune = tune
        self.points: collections.deque = collections.deque()  # (t, squint above baseline)
        self.fired_at = -1e9
        self.latched = False
        self.delta = 0.0
        self.gated = ""

    def feed(self, sig: dict, t: float, base: Baseline) -> Signal | None:
        tune = self.tune
        self.gated = gate(sig, tune)
        if self.gated == "blink":
            return None  # a blink is a brief dropout, not a reason to reset
        while self.points and t - self.points[0][0] > tune.squint_window:
            self.points.popleft()
        if self.gated:
            self.points.clear()  # smiling, turned away, winking or dark: start over
            return None
        self.delta = (sig["squint_l"] + sig["squint_r"]) / 2 - base.squint
        self.points.append((t, self.delta))
        if self.latched:  # hysteresis: the eyes must relax before another squint counts
            if self.delta < tune.squint_weak / 2:
                self.latched = False
            elif t - self.fired_at < tune.rearm_seconds:
                return None
        strong = held(self.points, tune.squint_delta) >= tune.squint_hold
        weak = held(self.points, tune.squint_weak) >= tune.squint_hold
        if not (strong or weak):
            return None
        recent = [v for _t, v in self.points]
        mean = sum(recent) / len(recent)
        if strong:
            conf, tier = 0.6 + min(0.35, max(0.0, mean - tune.squint_delta) * 2), "strong"
        else:
            conf, tier = 0.35 + min(0.2, max(0.0, mean - tune.squint_weak) * 2), "weak"
        self.fired_at, self.latched = t, True
        self.points.clear()
        return Signal("squint", round(conf, 2), t, tier, "vision",
                      detail={"delta": round(mean, 3), "baseline": round(base.squint, 3)})


class LeanDetector:
    def __init__(self, tune: Tuning):
        self.tune = tune
        self.since: float | None = None   # when the face first got close enough (weak level)
        self.last_t = 0.0
        self.fired: str = ""              # "" | "weak" | "strong" while still leaning in
        self.fired_at = -1e9
        self.ratio = 1.0

    def feed(self, sig: dict, t: float, base: Baseline) -> Signal | None:
        tune = self.tune
        if sig["blink"] >= tune.blink_gate:
            return None
        if abs(sig["yaw"]) > tune.pose_gate or abs(sig["pitch"]) > tune.pose_gate:
            self.since = None  # a turned face looks narrower: don't judge distance
            return None
        self.ratio = r = sig["width"] / base.width
        gap = t - self.last_t > 0.5
        self.last_t = t
        if r <= tune.lean_release:  # sat back: the next lean counts again
            self.since, self.fired = None, ""
            return None
        if r < tune.lean_weak:  # between release and weak (hysteresis): the hold starts over
            self.since = None
            return None
        if self.since is None or gap:
            self.since = t
        if t - self.since < tune.lean_hold:
            return None
        tier = "strong" if r >= tune.lean_ratio else "weak"
        if self.fired == "strong" or self.fired == tier:
            return None  # already reported this lean (a weak one may still grow into a strong one)
        if not self.fired and t - self.fired_at < tune.rearm_seconds:
            return None
        self.fired, self.fired_at = tier, t
        if tier == "strong":
            conf = 0.6 + min(0.35, (r - tune.lean_ratio) * 2)
        else:
            conf = 0.35 + min(0.2, (r - tune.lean_weak) * 2)
        return Signal("lean", round(conf, 2), t, tier, "vision",
                      detail={"ratio": round(r, 3), "closer_pct": round((1 - 1 / r) * 100)})


# --------------------------------------------------------------------------- mouse and keyboard
class RageClickDetector:
    def __init__(self, tune: Tuning):
        self.tune = tune
        self.clicks: collections.deque = collections.deque()  # (t, x, y)
        self.fired_at = -1e9
        self.fired_tier = ""

    def click(self, t: float, x: int, y: int) -> Signal | None:
        tune = self.tune
        self.clicks.append((t, x, y))
        while self.clicks and t - self.clicks[0][0] > tune.rage_seconds:
            self.clicks.popleft()
        near = [c for c in self.clicks if math.hypot(c[1] - x, c[2] - y) <= tune.rage_px]
        recent = t - self.fired_at < 2 * tune.rage_seconds
        if len(near) >= tune.rage_clicks and not (recent and self.fired_tier == "strong"):
            conf, tier = 0.65 + min(0.3, 0.1 * (len(near) - tune.rage_clicks)), "strong"
        elif (not recent and len(near) >= tune.rage_clicks - 1 and t - near[0][0] <= 0.75 * tune.rage_seconds
              and t - near[0][0] > 0.6):  # 3 quick clicks, but not a triple-click (select a paragraph)
            conf, tier = 0.4, "weak"
        else:
            return None
        self.fired_at, self.fired_tier = t, tier
        if tier == "strong":
            self.clicks.clear()
        return Signal("rage_click", round(conf, 2), t, tier, "input", x, y,
                      {"clicks": len(near), "seconds": round(t - near[0][0], 2)})


class RetypeDetector:
    """Deleting a lot and typing it again: a backspace burst followed by new typing."""

    def __init__(self, tune: Tuning):
        self.tune = tune
        self.backspaces: collections.deque = collections.deque()
        self.burst_at: float | None = None
        self.burst_n = 0
        self.typed_after = 0
        self.fired_at = -1e9
        self.keys = self.total_bs = 0  # lifetime counts, for this person's usual backspace share
        self.recent_keys: collections.deque = collections.deque(maxlen=40)
        self.normal_keys = self.normal_bs = 0

    def key(self, t: float, backspace: bool) -> Signal | None:
        tune = self.tune
        self.keys += 1
        self.recent_keys.append(bool(backspace))
        if backspace:
            self.total_bs += 1
            self.backspaces.append(t)
            while self.backspaces and t - self.backspaces[0] > tune.retype_seconds:
                self.backspaces.popleft()
            if len(self.backspaces) >= tune.retype_backspaces:
                self.burst_at, self.burst_n, self.typed_after = t, len(self.backspaces), 0
            return self._rolling_signal(t)
        if self.burst_at is None:
            return self._rolling_signal(t)
        if t - self.burst_at > 2 * tune.retype_seconds:
            self.burst_at = None
            return self._rolling_signal(t)
        self.typed_after += 1
        if self.typed_after < 2 or t - self.fired_at < tune.rearm_seconds:
            return self._rolling_signal(t)
        usual = self.total_bs / self.keys if self.keys >= 200 else 0.15
        n = self.burst_n
        self.burst_at, self.fired_at = None, t
        self.backspaces.clear()
        conf = 0.45 + min(0.3, 0.05 * (n - tune.retype_backspaces)) - (0.1 if usual > 0.25 else 0.0)
        return Signal("retype", round(conf, 2), t, "strong" if conf >= 0.5 else "weak", "input",
                      detail={"backspaces": n, "usual_share": round(usual, 2)})

    def _rolling_signal(self, t: float) -> Signal | None:
        # Learn only keys outside a detected burst; this avoids teaching the baseline from struggle.
        if self.burst_at is None and len(self.recent_keys) == 40:
            self.normal_keys += 1
            self.normal_bs += int(self.recent_keys[-1])
        if self.normal_keys < 300 or len(self.recent_keys) < 40:
            return None
        n = sum(self.recent_keys)
        usual = self.normal_bs / self.normal_keys
        if n < 5 or n / 40 <= 2 * usual or t - self.fired_at < self.tune.rearm_seconds:
            return None
        self.fired_at = t
        return Signal("retype", 0.4, t, "weak", "input",
                      detail={"backspaces": n, "usual_share": round(usual, 2), "window": 40})


class HesitationDetector:
    """The pointer moved somewhere, then rested with no click or key for a while."""

    STILL_PX = 6

    def __init__(self, tune: Tuning):
        self.tune = tune
        self.pos: tuple[int, int] | None = None
        self.rest_since: float | None = None
        self.moved = False      # the pointer travelled before this rest (not just parked all along)
        self.fired = False
        self.pauses: collections.deque = collections.deque(maxlen=200)  # rests that ended in a click
        self.clickable: bool | None = None

    def threshold(self) -> float:
        """The person's own 95th-percentile pause before a click, once there are 20 of them."""
        if len(self.pauses) < 20:
            return self.tune.hesitation_seconds
        p95 = sorted(self.pauses)[int(0.95 * (len(self.pauses) - 1))]
        return min(10.0, max(2.0, p95 * 1.2))

    def move(self, t: float, x: int, y: int) -> None:
        if self.pos is None or math.hypot(x - self.pos[0], y - self.pos[1]) > self.STILL_PX:
            self.moved = self.pos is not None
            self.pos, self.rest_since, self.fired = (x, y), t, False
            self.clickable = None

    def click(self, t: float, x: int, y: int) -> None:
        if self.rest_since is not None and self.moved and not self.fired:
            self.pauses.append(t - self.rest_since)
        self.pos, self.rest_since, self.moved, self.fired = (x, y), t, False, False

    def key(self, t: float) -> None:
        self.rest_since, self.moved = t, False

    def tick(self, t: float) -> Signal | None:
        if self.rest_since is None or not self.moved or self.fired or self.pos is None:
            return None
        if self.clickable is False:
            return None
        waited, limit = t - self.rest_since, self.threshold()
        if waited < limit or waited > limit + 5:  # long gone: they walked away or are reading
            return None
        self.fired = True
        return Signal("hesitation", round(min(0.5, 0.3 + 0.05 * (waited - limit)), 2), t, "weak", "input",
                      self.pos[0], self.pos[1], {"seconds": round(waited, 1), "threshold": round(limit, 1)})
