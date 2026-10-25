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
    wink_max: float = 0.12          # left - right this far from the person's own resting difference: a wink
    smile_gate: float = 0.35        # mean mouthSmile above this: ignore squint (cheeks narrow the eyes)
    blink_gate: float = 0.5         # mean eyeBlink above this: drop the frame, if it's brief (a blink)...
    blink_max: float = 0.4          # ...lasting under this many seconds; a held squint also scores high
    blink_closed: float = 0.8       # mean eyeBlink above this: eyes shut, always dropped
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
    relative_squint_weak: float = 0.45
    relative_squint_strong: float = 0.75
    geometric_aperture_drop: float = 0.12
    geometric_lower_rise: float = 0.035
    lean_distance_ratio: float = 1.15

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
    squint_reliable: bool = True
    squint_why: str = ""
    asym: float = 0.0  # resting eyeSquintLeft - eyeSquintRight; most faces read one eye narrower

    MIN_SAMPLES = 30

    def wink(self, sig: dict) -> float:
        """How far one eye is narrowed beyond the person's usual difference between the eyes."""
        return abs(sig["squint_l"] - sig["squint_r"] - self.asym)

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
        scores = [(s["squint_l"] + s["squint_r"]) / 2 for s in good]
        mad = statistics.median(abs(v - squint) for v in scores)
        asymmetry = statistics.median(abs(s["squint_l"] - s["squint_r"]) for s in good)
        reliable = mad <= 0.06 and asymmetry <= 0.10 and light >= 40
        why = "" if reliable else "squint readings were unsteady, uneven, or too dim; squint sensing is off"
        asym = statistics.median(s["squint_l"] - s["squint_r"] for s in good)
        return cls(round(squint, 4), round(width, 4), round(smile, 4), round(light, 1), len(good), source, t,
                   reliable, why, round(asym, 4))

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
                "samples": self.samples, "source": self.source, "created": self.created,
                "squint_reliable": self.squint_reliable, "squint_why": self.squint_why, "asym": self.asym}

    @classmethod
    def from_dict(cls, d: dict) -> "Baseline | None":
        try:
            b = cls(float(d["squint"]), float(d["width"]), float(d.get("smile", 0)), float(d.get("light", -1)),
                    int(d.get("samples", 0)), str(d.get("source", "calibrated")), float(d.get("created", 0)),
                    bool(d.get("squint_reliable", True)), str(d.get("squint_why", "")), float(d.get("asym", 0.0)))
        except (KeyError, TypeError, ValueError):
            return None
        return b if 0 <= b.squint <= 0.6 and 0.05 <= b.width <= 0.95 else None


def gate(sig: dict, tune: Tuning, blink_held: float = 0.0, wink: float | None = None) -> str:
    """Why this frame can't be trusted for squint ("" = it can).

    blink_held: seconds the blink score has stayed at or above blink_gate. A blink is over within
    blink_max; a hard squint pushes the score that high too, but for as long as it's held.
    wink: one eye's narrowing beyond the person's usual left/right difference (Baseline.wink, smoothed
    by SquintDetector); without it, the raw difference between the eyes.
    """
    if sig["blink"] >= tune.blink_closed or (sig["blink"] >= tune.blink_gate and blink_held < tune.blink_max):
        return "blink"
    if abs(sig["yaw"]) > tune.pose_gate or abs(sig["pitch"]) > tune.pose_gate:
        return "turned"
    if 0 <= sig.get("light", -1) < tune.light_floor:
        return "dark"
    if sig["smile"] >= tune.smile_gate:
        return "smile"
    if (abs(sig["squint_l"] - sig["squint_r"]) if wink is None else wink) > tune.wink_max:
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
        self.blink_start: float | None = None  # start of the current run of high blink scores
        self.blink_last = -1e9
        self.asyms: collections.deque = collections.deque(maxlen=5)  # about 0.5 s of left-right differences
        self.winking = False

    def blink_held(self, sig: dict, t: float) -> float:
        """Seconds the blink score has been high, allowing dips of up to 0.3 s (a held squint flickers)."""
        if sig["blink"] < self.tune.blink_gate:
            return 0.0 if t - self.blink_last > 0.3 else t - (self.blink_start or t)
        if self.blink_start is None or t - self.blink_last > 0.3:
            self.blink_start = t
        self.blink_last = t
        return t - self.blink_start

    def feed(self, sig: dict, t: float, base: Baseline) -> Signal | None:
        tune = self.tune
        # Smoothed over ~0.5 s: one frame's jitter shouldn't restart a held squint or let a wink through.
        self.asyms.append(sig["squint_l"] - sig["squint_r"] - base.asym)
        wink = abs(statistics.median(self.asyms))
        # Hysteresis: an eye reopens slowly, so a wink lasts until the difference is back under half
        # wink_max (a real squint narrows both eyes and stays well under that).
        if wink > tune.wink_max:
            self.winking = True
        elif wink < tune.wink_max / 2:
            self.winking = False
        self.gated = gate(sig, tune, self.blink_held(sig, t), float("inf") if self.winking else wink)
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
        # Leaning in close raises the squint score by itself (+0.16 on one real face at 1.7x width,
        # without squinting), so while leaning only a strong squint counts.
        leaning = sig["width"] / base.width >= tune.lean_weak
        weak = not leaning and held(self.points, tune.squint_weak) >= tune.squint_hold
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
        # No blink gate: face width comes from the cheeks, and squinting while leaning in (eyes partly
        # closed) is the struggle that matters most.
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


# Raw-number candidates. Input keys match ally.senses.features.extract; the legacy classes above
# remain byte-for-byte behaviorally unchanged and are still the defaults.
RELATIVE_SQUINT_SPREAD = 3.0
RELATIVE_SQUINT_ROOM = 0.70
GEOMETRIC_APERTURE_DROP = 0.12
GEOMETRIC_LOWER_RISE = 0.035
LEAN_DISTANCE_RATIO = 1.15


class _RawHeld:
    def __init__(self, tune, kind):
        self.tune, self.kind = tune, kind
        self.points = collections.deque()
        self.fired_at = -1e9

    def _held_signal(self, t, score, gate_ok=True, detail=None):
        while self.points and t-self.points[0][0] > self.tune.squint_window:
            self.points.popleft()
        if not gate_ok:
            self.points.clear()
            return None
        self.points.append((t, score))
        threshold = self.weak_threshold
        duration = held(self.points, threshold)
        strong = held(self.points, self.strong_threshold) >= self.tune.squint_hold
        if not strong and duration < self.tune.squint_hold:
            return None
        if t-self.fired_at < self.tune.rearm_seconds:
            return None
        self.fired_at = t
        tier = "strong" if strong else "weak"
        return Signal(self.kind, .75 if strong else .45, t, tier, "vision", detail=detail or {})


class RelativeSquintDetector(_RawHeld):
    def __init__(self, tune):
        super().__init__(tune, "squint")
        self.weak_threshold, self.strong_threshold = tune.relative_squint_weak, tune.relative_squint_strong

    def feed(self, sig, t, base):
        med = base.get("eyeSquint", {}).get("median", base.squint) if isinstance(base, dict) else base.squint
        mad = base.get("eyeSquint", {}).get("mad", .02) if isinstance(base, dict) else .02
        room = max(.05, 1-med)
        value = sig.get("eyeSquint", (sig.get("eyeSquintLeft", 0)+sig.get("eyeSquintRight", 0))/2)
        z = (value-med)/max(mad, .01)
        norm = (value-med)/room
        score = min(z/RELATIVE_SQUINT_SPREAD, norm/RELATIVE_SQUINT_ROOM)
        gate_ok = abs(sig.get("head_yaw",0)) < self.tune.pose_gate and abs(sig.get("head_pitch",0)) < self.tune.pose_gate
        gate_ok &= sig.get("eyeBlink",0) < self.tune.blink_gate and sig.get("mouthSmile",0) < self.tune.smile_gate
        gate_ok &= abs(sig.get("eyeSquintLeft",value)-sig.get("eyeSquintRight",value)) < self.tune.wink_max
        return self._held_signal(t, score, gate_ok, {"relative_score": round(score, 3)})


class GeometricSquintDetector(_RawHeld):
    def __init__(self, tune):
        super().__init__(tune, "squint")
        self.weak_threshold, self.strong_threshold = .6, 1.0
    def feed(self, sig, t, base):
        def b(k, default=0):
            return base.get(k, {}).get("median", default) if isinstance(base,dict) else default
        both=[]
        for side in ("l","r"):
            ap=sig.get("aperture_"+side, sig.get("aperture",0)); low=sig.get("lower_height_"+side,sig.get("lower_height",0))
            both.append(ap <= b("aperture_"+side,ap)*(1-self.tune.geometric_aperture_drop) and low >= b("lower_height_"+side,low)+self.tune.geometric_lower_rise)
        down = sig.get("iris_vertical", .5) > .8 and sig.get("upper_height",0) < b("upper_height",sig.get("upper_height",0))-.02
        blink=sig.get("eyeBlink",0) >= self.tune.blink_gate or sig.get("aperture",1)<.04
        expr=sig.get("mouthSmile",0)>=self.tune.smile_gate or sig.get("cheekSquint",0)>.5 or sig.get("jawOpen",0)>.65
        turn=abs(sig.get("head_yaw",0))>=self.tune.pose_gate or abs(sig.get("head_pitch",0))>=self.tune.pose_gate
        return self._held_signal(t, 1.0, all(both) and not (down or blink or expr or turn), {"geometry": True})


class HybridSquintDetector(GeometricSquintDetector):
    def feed(self,sig,t,base):
        g=super().feed(sig,t,base)
        if g is None: return None
        # Geometry is the harder gate; relative evidence must also be present.
        value=sig.get("eyeSquint",0); med=base.get("eyeSquint",{}).get("median",0) if isinstance(base,dict) else base.squint
        if value-med < .08: return None
        return g


class DistanceLeanDetector(LeanDetector):
    def feed(self,sig,t,base):
        ratio=sig.get("head_distance",0)
        b=base.get("head_distance",{}).get("median",0) if isinstance(base,dict) else 0
        if not ratio or not b: return super().feed(sig,t,base)
        s=dict(sig); s["width"]=base.width*ratio/b if isinstance(base,Baseline) else ratio
        return super().feed(s,t,base)


class HybridLeanDetector(DistanceLeanDetector):
    def feed(self,sig,t,base):
        bdist=base.get("head_distance",{}).get("median",0) if isinstance(base,dict) else 0
        dw=sig.get("head_distance",0)/bdist if bdist else 1.0
        wr=sig.get("width",0)/(base.width if isinstance(base,Baseline) else base.get("face_width",{}).get("median",1))
        s=dict(sig); s["width"]=(max(dw,wr))*base.width if isinstance(base,Baseline) else max(dw,wr)
        return super(DistanceLeanDetector,self).feed(s,t,base)


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
