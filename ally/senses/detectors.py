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
    relative_squint_weak: float = 0.20
    relative_squint_strong: float = 0.35
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
    feature_stats: dict = field(default_factory=dict)

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
        asym = statistics.median(s["squint_l"] - s["squint_r"] for s in good)
        # How steady the left/right difference is, around this person's own difference. Many faces
        # read one eye narrower at rest (asym is subtracted later); that alone must not turn squint off.
        asymmetry = statistics.median(abs(s["squint_l"] - s["squint_r"] - asym) for s in good)
        reliable = mad <= 0.06 and asymmetry <= 0.10 and light >= 40
        why = "" if reliable else "squint readings were unsteady, uneven, or too dim; squint sensing is off"
        asym = statistics.median(s["squint_l"] - s["squint_r"] for s in good)
        feature_values={}
        for key in set.intersection(*(set(s) for s in good)) if good else ():
            vals=[s[key] for s in good if isinstance(s[key],(int,float))]
            if vals:
                med=statistics.median(vals)
                feature_values[key]={"median":med,"mad":statistics.median(abs(v-med) for v in vals),"room":max(0.0,1-med)}
        return cls(round(squint, 4), round(width, 4), round(smile, 4), round(light, 1), len(good), source, t,
                   reliable, why, round(asym, 4), feature_values)

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
                "squint_reliable": self.squint_reliable, "squint_why": self.squint_why, "asym": self.asym,
                "feature_stats": self.feature_stats}

    @classmethod
    def from_dict(cls, d: dict) -> "Baseline | None":
        try:
            b = cls(float(d["squint"]), float(d["width"]), float(d.get("smile", 0)), float(d.get("light", -1)),
                    int(d.get("samples", 0)), str(d.get("source", "calibrated")), float(d.get("created", 0)),
                    bool(d.get("squint_reliable", True)), str(d.get("squint_why", "")), float(d.get("asym", 0.0)),
                    dict(d.get("feature_stats", {})))
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

# Keep a small noise floor because a zero MAD is common in short calibration clips.
PERSONAL_NOISE_FLOOR = 0.02
PERSONAL_HOLD_SECONDS = 1.3


class _RawHeld:
    def __init__(self, tune, kind):
        self.tune, self.kind = tune, kind
        self.points = collections.deque()
        self.fired_at = -1e9
        self.latched = False
        self.gated = ""
        self.delta = 0.0
        self.hold_seconds = tune.squint_hold

    def _held_signal(self, t, score, gate_ok=True, detail=None):
        self.delta = score
        self.gated = "" if gate_ok else "gated"
        while self.points and t - self.points[0][0] > self.tune.squint_window:
            self.points.popleft()
        if not gate_ok:
            self.points.clear()
            self.latched = False
            return None
        if self.latched:
            if score < self.weak_threshold / 2:
                self.latched = False
            else:
                return None
        self.points.append((t, score))
        threshold = self.weak_threshold
        duration = held(self.points, threshold)
        strong = held(self.points, self.strong_threshold) >= self.hold_seconds
        if not strong and duration < self.hold_seconds:
            return None
        if t - self.fired_at < self.tune.rearm_seconds:
            return None
        self.fired_at = t
        self.latched = True
        tier = "strong" if strong else "weak"
        return Signal(self.kind, .75 if strong else .45, t, tier, "vision", detail=detail or {})


class RelativeSquintDetector(_RawHeld):
    def __init__(self, tune):
        super().__init__(tune, "squint")
        self.weak_threshold, self.strong_threshold = tune.relative_squint_weak, tune.relative_squint_strong

    def feed(self, sig, t, base):
        stats = _feature_stats(base)
        value = sig.get("eyeSquint", 0.0)
        median = stats.get("eyeSquint", {}).get("median", 0.1)
        spread = max(stats.get("eyeSquint", {}).get("mad", 0.02), 0.01)
        score = min((value - median) / spread / RELATIVE_SQUINT_SPREAD,
                    (value - median) / max(0.05, 1.0 - median) / RELATIVE_SQUINT_ROOM)
        gate_ok = _pose_ok(sig, self.tune)
        gate_ok &= sig.get("eyeBlink", 0.0) < self.tune.blink_gate
        gate_ok &= sig.get("mouthSmile", 0.0) < self.tune.smile_gate
        difference = abs(sig.get("eyeSquintLeft", value) - sig.get("eyeSquintRight", value))
        gate_ok &= difference < self.tune.wink_max
        down_stats = stats.get("eyeLookDown", {})
        looking_down = sig.get("eyeLookDown", 0.0) > _median(
            stats, "eyeLookDown", 0.0
        ) + max(0.30, 3 * down_stats.get("mad", 0.02))
        looking_down |= sig.get("iris_vertical", 0.5) > 0.75
        gate_ok &= not looking_down
        return self._held_signal(t, score, gate_ok, {"relative_score": round(score, 3)})


class GeometricSquintDetector(_RawHeld):
    def __init__(self, tune):
        super().__init__(tune, "squint")
        self.weak_threshold, self.strong_threshold = .6, 1.0
    def feed(self, sig, t, base):
        stats = _feature_stats(base)
        both_eyes = []
        for side in ("r", "l"):
            aperture = sig.get(f"aperture_{side}", sig.get("aperture", 0.0))
            lower_raise = sig.get(
                f"lower_raise_{side}",
                sig.get(f"lower_height_{side}", sig.get("lower_raise", sig.get("lower_height", 0.0))),
            )
            base_aperture = _median(stats, f"aperture_{side}", sig.get("aperture", 0.0))
            base_lower = _median(
                stats,
                f"lower_raise_{side}",
                _median(stats, f"lower_height_{side}", sig.get("lower_height", 0.0)),
            )
            aperture_drop = aperture <= base_aperture * (1 - self.tune.geometric_aperture_drop)
            lid_raise = lower_raise >= base_lower + self.tune.geometric_lower_rise
            both_eyes.append(aperture_drop and lid_raise)
        blink = sig.get("eyeBlink", 0.0) >= self.tune.blink_gate
        expression = (sig.get("mouthSmile", 0.0) >= self.tune.smile_gate or
                      sig.get("cheekSquint", 0.0) > 0.5 or sig.get("jawOpen", 0.0) > 0.65)
        turned = not _pose_ok(sig, self.tune)
        upper = sig.get("upper_height", 0.0)
        baseline_upper = _median(stats, "upper_height", upper)
        down = sig.get("iris_vertical", 0.5) > 0.8 and upper < baseline_upper - 0.02
        valid = all(both_eyes) and not (blink or expression or turned or down)
        return self._held_signal(t, 1.0, valid, {"geometry": True})


def _feature_stats(base):
    if isinstance(base, dict):
        return base
    return base.feature_stats


def _median(stats, key, default):
    return stats.get(key, {}).get("median", default)


def _mad(stats, key, default):
    return stats.get(key, {}).get("mad", default)


def _pose_ok(sig, tune):
    yaw = abs(sig.get("head_yaw", sig.get("yaw", 0.0)))
    pitch = abs(sig.get("head_pitch", sig.get("pitch", 0.0)))
    return yaw < tune.pose_gate and pitch < tune.pose_gate


def _au_vote(sig, stats):
    """Compare AU7 with the person's own baseline; smiles are explanatory votes."""
    au = sig.get("au")
    if isinstance(au, list) and len(au) > 4:
        au6 = float(au[3])
        au7 = float(au[4])
        au12 = float(au[6]) if len(au) > 6 else None
    elif isinstance(au, dict):
        au6 = float(au.get("6", au.get("AU6", 0.0)))
        au7 = float(au.get("7", au.get("AU7", 0.0)))
        au12 = float(au.get("12", au.get("AU12", 0.0)))
    else:
        return False
    smile_median = _median(stats, "mouthSmile", 0.0)
    smile_spread = max(_mad(stats, "mouthSmile", 0.02), PERSONAL_NOISE_FLOOR)
    smile_explains = sig.get("mouthSmile", 0.0) > smile_median + 2.0 * smile_spread
    au12_median = _median(stats, "au12", 0.0)
    au12_spread = max(_mad(stats, "au12", 0.02), PERSONAL_NOISE_FLOOR)
    au12_explains = au12 is not None and au12 > au12_median + 2.0 * au12_spread
    if smile_explains or au12_explains:
        return False
    au7_vote = au7 > _median(stats, "au7", 0.0) + 2.0 * max(
        _mad(stats, "au7", 0.02), PERSONAL_NOISE_FLOOR
    )
    au6_vote = au6 > _median(stats, "au6", 0.0) + 2.0 * max(
        _mad(stats, "au6", 0.02), PERSONAL_NOISE_FLOOR
    )
    return au7_vote or au6_vote


class PersonalSquintDetector(_RawHeld):
    """Score bilateral lid changes after per-person confounder correction."""

    def __init__(self, tune):
        super().__init__(tune, "squint")
        self.weak_threshold = 2.0
        self.strong_threshold = 3.0
        self.hold_seconds = PERSONAL_HOLD_SECONDS
        self.model = {}

    @classmethod
    def fit(cls, parts, threshold_margin=0.25):
        """Fit a JSON-safe model from labelled calibration feature samples."""
        neutral = _part_samples(parts, "neutral")
        top = _part_samples(parts, "top")
        bottom = _part_samples(parts, "bottom")
        smile = _part_samples(parts, "smile")
        deliberate = _part_samples(parts, "squint")
        if not neutral or not deliberate:
            return {"enabled": False, "why": "calibration needs normal reading and a deliberate squint"}
        baseline = _feature_baseline(neutral)
        coefficients = {}
        predictors = ("mouthSmile", "downward_gaze", "head_pitch")
        for target in ("aperture", "lower_raise"):
            coefficients[target] = _fit_slopes(
                neutral + top + bottom + smile,
                target,
                predictors,
                baseline,
            )
        model = {"baseline": baseline, "coefficients": coefficients}
        neutral_scores = [_personal_score(sample, model) for sample in neutral]
        context_samples = neutral + top + bottom + smile
        context_scores = [_personal_score(sample, model) for sample in context_samples]
        deliberate_scores = [_personal_score(sample, model) for sample in deliberate]
        resting_mean = statistics.mean(neutral_scores)
        resting_sd = max(_standard_deviation(neutral_scores), 0.1)
        deliberate_score = statistics.median(deliberate_scores)
        d_prime = (deliberate_score - resting_mean) / resting_sd
        noise_score = max(_standard_deviation(neutral_scores), 0.1)
        sorted_context = sorted(context_scores)
        tail_index = min(len(sorted_context) - 1, int(len(sorted_context) * 0.995))
        context_tail = sorted_context[tail_index]
        requested_threshold = context_tail + max(float(threshold_margin), 0.5 * noise_score)
        threshold = min(requested_threshold, deliberate_score * 0.5)
        threshold_possible = threshold > context_tail
        enabled = d_prime >= 2.0 and deliberate_score > 0 and threshold_possible
        if d_prime < 2.0:
            why = "your deliberate squint was not distinct from normal variation"
        elif not threshold_possible:
            why = "normal reading and deliberate squint overlapped too much"
        else:
            why = ""
        model.update(
            enabled=enabled,
            why=why,
            d_prime=d_prime,
            resting_mean=resting_mean,
            resting_sd=resting_sd,
            threshold=threshold,
            deliberate_score=deliberate_score,
        )
        return model

    def feed(self, sig, t, base):
        model = base.get("personal_model", {}) if isinstance(base, dict) else base.feature_stats.get(
            "personal_model", self.model
        )
        self.model = model
        if not model.get("enabled"):
            self.points.clear()
            return None
        score = _personal_score(sig, model)
        gate_ok = _pose_ok(sig, self.tune)
        gate_ok &= sig.get("eyeBlink", sig.get("blink", 0.0)) < self.tune.blink_gate
        light = sig.get("light", -1.0)
        gate_ok &= light < 0 or light >= self.tune.light_floor
        iris_diameter = sig.get("iris_diameter", 1.0)
        gate_ok &= iris_diameter > 0
        eye_scores = _personal_eye_scores(sig, model)
        gate_ok &= min(eye_scores) > 0.1
        if sig.get("mouthSmile", 0.0) > 0.85:
            gate_ok = False
        self.weak_threshold = float(model["threshold"])
        self.strong_threshold = max(self.weak_threshold * 1.5, float(model["deliberate_score"]) * 0.75)
        return self._held_signal(t, score, gate_ok, {"personal_score": round(score, 3)})


class HybridPersonalSquintDetector(PersonalSquintDetector):
    """Require a personal score plus blendshape or AU evidence."""

    def __init__(self, tune):
        super().__init__(tune)
        self.legacy_geometric = GeometricSquintDetector(tune)

    def feed(self, sig, t, base):
        stats = _feature_stats(base)
        if "personal_model" not in stats:
            signal = self.legacy_geometric.feed(sig, t, base)
            if signal is None:
                return None
            median = _median(stats, "eyeSquint", 0.0)
            if sig.get("eyeSquint", 0.0) - median >= 0.08:
                return signal
            return None
        score = _personal_score(sig, stats["personal_model"])
        far_past_threshold = score >= float(stats["personal_model"]["threshold"]) * 1.5
        signal = super().feed(sig, t, base)
        if signal is None:
            return None
        value = sig.get("eyeSquint", 0.0)
        median = _median(stats, "eyeSquint", value)
        spread = max(_mad(stats, "eyeSquint", 0.02), PERSONAL_NOISE_FLOOR)
        relative_vote = (value - median) / spread >= 2.0
        if relative_vote or _au_vote(sig, stats):
            return signal
        if far_past_threshold:
            return signal
        return None


class HybridSquintDetector(HybridPersonalSquintDetector):
    """Named hybrid method retained for config and benchmark comparisons."""


def _part_samples(parts, name):
    return list(parts.get(name, []))


def _feature_baseline(samples):
    keys = set.intersection(*(set(sample) for sample in samples))
    result = {}
    for key in keys:
        values = [float(sample[key]) for sample in samples
                  if isinstance(sample.get(key), (int, float))]
        if values:
            median = statistics.median(values)
            mad = statistics.median(abs(value - median) for value in values)
            result[key] = {"median": median, "mad": mad}
    return result


def _predictors(sample, baseline):
    iris = sample.get("iris_vertical", 0.5)
    iris_base = _median(baseline, "iris_vertical", 0.5)
    look_down = sample.get("eyeLookDown", 0.0) - _median(baseline, "eyeLookDown", 0.0)
    gaze_down = max(0.0, iris - iris_base) + look_down
    return {
        "mouthSmile": sample.get("mouthSmile", 0.0) - _median(baseline, "mouthSmile", 0.0),
        "downward_gaze": gaze_down,
        "head_pitch": sample.get("head_pitch", 0.0) - _median(baseline, "head_pitch", 0.0),
    }


def _fit_slopes(samples, target, predictors, baseline):
    """Fit small independent slopes with repeated coordinate updates."""
    values = [(sample, _predictors(sample, baseline)) for sample in samples]
    center = _median(baseline, target, 0.0)
    slopes = {name: 0.0 for name in predictors}
    for _iteration in range(3):
        for name in predictors:
            numerator = 0.0
            denominator = 0.0
            for sample, features in values:
                residual = sample.get(target, center) - center
                for other, slope in slopes.items():
                    if other != name:
                        residual -= slope * features[other]
                numerator += features[name] * residual
                denominator += features[name] ** 2
            if denominator > 1e-8:
                slopes[name] = numerator / denominator
    return slopes


def _personal_score(sample, model):
    baseline = model.get("baseline", {})
    predictors = _predictors(sample, baseline)
    residuals = {}
    for name in ("aperture", "lower_raise"):
        center = _median(baseline, name, 0.0)
        corrected = sample.get(name, center)
        for factor, slope in model.get("coefficients", {}).get(name, {}).items():
            corrected -= slope * predictors.get(factor, 0.0)
        residuals[name] = corrected - center
    aperture_noise = max(_mad(baseline, "aperture", 0.0) * 1.4826, PERSONAL_NOISE_FLOOR)
    lower_noise = max(_mad(baseline, "lower_raise", 0.0) * 1.4826, PERSONAL_NOISE_FLOOR)
    aperture_drop = -residuals["aperture"] / aperture_noise
    lower_rise = residuals["lower_raise"] / lower_noise
    return max(0.0, aperture_drop + lower_rise)


def _personal_eye_scores(sample, model):
    """Return corrected squint evidence for each eye to reject one-eye closures."""
    baseline = model.get("baseline", {})
    if not all(f"aperture_{side}" in baseline for side in ("r", "l")):
        score = _personal_score(sample, model)
        return score, score
    predictors = _predictors(sample, baseline)
    corrections = model.get("coefficients", {})
    scores = []
    for side in ("r", "l"):
        aperture_key = f"aperture_{side}"
        lower_key = f"lower_raise_{side}"
        aperture = sample.get(aperture_key, _median(baseline, aperture_key, 0.0))
        lower_raise = sample.get(lower_key, _median(baseline, lower_key, 0.0))
        for name, value in (("aperture", aperture), ("lower_raise", lower_raise)):
            center = _median(baseline, f"{name}_{side}", _median(baseline, name, 0.0))
            corrected = value
            for factor, slope in corrections.get(name, {}).items():
                corrected -= slope * predictors.get(factor, 0.0)
            deviation = corrected - center
            noise = max(_mad(baseline, f"{name}_{side}", _mad(baseline, name, 0.0)) * 1.4826,
                        PERSONAL_NOISE_FLOOR)
            if name == "aperture":
                aperture_score = -deviation / noise
            else:
                lower_score = deviation / noise
        scores.append(max(0.0, aperture_score + lower_score))
    return scores[0], scores[1]


def _standard_deviation(values):
    if len(values) < 2:
        return 0.0
    return statistics.pstdev(values)


class DistanceLeanDetector(LeanDetector):
    def feed(self, sig, t, base):
        stats = _feature_stats(base)
        baseline_distance = _median(stats, "head_distance", 0.0)
        current_distance = sig.get("head_distance", 0.0)
        if baseline_distance <= 0 or current_distance <= 0:
            if isinstance(base, dict):
                baseline_width = _median(stats, "face_width", 0.0)
                current_width = sig.get("face_width", sig.get("width", 0.0))
                if baseline_width > 0 and current_width > 0:
                    width_ratio = current_width / baseline_width
                    return self._feed_ratio(sig, t, width_ratio)
                return None
            return super().feed(sig, t, base)
        distance_ratio = baseline_distance / current_distance
        return self._feed_ratio(sig, t, distance_ratio)

    def _feed_ratio(self, sig, t, ratio):
        if not _pose_ok(sig, self.tune):
            self.since = None
            return None
        virtual_signal = dict(sig)
        virtual_signal.update(width=ratio, yaw=sig.get("head_yaw", 0.0),
                              pitch=sig.get("head_pitch", 0.0))
        return super().feed(virtual_signal, t, Baseline(0.1, 1.0))


class HybridLeanDetector(DistanceLeanDetector):
    def feed(self, sig, t, base):
        stats = _feature_stats(base)
        baseline_distance = _median(stats, "head_distance", 0.0)
        current_distance = sig.get("head_distance", 0.0)
        baseline_iris = _median(stats, "iris_diameter", 0.0)
        current_iris = sig.get("iris_diameter", 0.0)
        ratios = []
        if baseline_distance > 0 and current_distance > 0:
            ratios.append(baseline_distance / current_distance)
        if baseline_iris > 0 and current_iris > 0:
            ratios.append(current_iris / baseline_iris)
        if not ratios:
            return super().feed(sig, t, base)
        ratio = statistics.median(ratios)
        baseline_width = _median(stats, "face_width", 0.0)
        current_width = sig.get("face_width", sig.get("width", 0.0))
        if baseline_width > 0 and current_width > 0:
            width_ratio = current_width / baseline_width
            if len(ratios) == 1:
                ratio = statistics.median((ratio, width_ratio))
        return self._feed_ratio(sig, t, ratio)


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
