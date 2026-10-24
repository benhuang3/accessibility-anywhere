"""Replay recorded matrix runs through the real Senses code, and search thresholds offline.

    python -m ally.senses.evaluate report PATHS
    python -m ally.senses.evaluate tune PATHS

PATHS are JSONL files from ally.senses.matrix, or a folder of them. Replay drives
ally.senses.manager.Senses (on_face, on_input, tick, subscribe) with the run's own timestamps
and the baseline from its calibration segment. Nothing is written to the real state folder,
and config files are never changed.

tune refuses to recommend thresholds from fewer than 2 people. Held-out false positives and
false negatives come from leave-one-person-out, so the printed numbers are not the training fit.
"""

from __future__ import annotations

import argparse
import collections
import copy
import csv
import dataclasses
import io
import json
import shutil
import sys
import tempfile
import time
import types
from dataclasses import dataclass, field
from pathlib import Path

from ..config import DEFAULTS
from ..log import EventLog
from .detectors import Baseline, Tuning
from .manager import Senses

# Thresholds the tuner may move. Other [senses] keys stay as they were during the recording.
TUNE_KEYS = (
    "squint_delta", "squint_weak", "squint_hold", "wink_max", "smile_gate", "blink_gate",
    "pose_gate", "light_floor", "lean_ratio", "lean_weak", "lean_release", "lean_hold",
)
# Coordinate search grid. Weak/strong pairs are also tried together (see _expand) because
# raising only the strong line still leaves a weak signal, and a weak pair still fuses.
GRIDS: dict[str, tuple[float, ...]] = {
    "squint_delta": (0.12, 0.16, 0.20, 0.24, 0.30, 0.40),
    "squint_weak": (0.06, 0.10, 0.12, 0.16, 0.22, 0.28),
    "squint_hold": (0.8, 1.2, 1.5, 2.0, 3.0),
    "wink_max": (0.08, 0.15, 0.25, 0.40),
    "smile_gate": (0.20, 0.35, 0.50, 0.70),
    "blink_gate": (0.35, 0.50, 0.70),
    "pose_gate": (12.0, 20.0, 35.0),
    "light_floor": (20.0, 40.0, 70.0),
    "lean_ratio": (1.08, 1.12, 1.15, 1.22, 1.35),
    "lean_weak": (1.04, 1.08, 1.10, 1.18, 1.28),
    "lean_release": (1.02, 1.05, 1.08, 1.12),
    "lean_hold": (1.0, 1.5, 2.0, 3.0),
}
# One step toward "more sensitive" (more likely to call a calm moment a struggle).
_MARGIN_STEPS = (
    ("squint_delta", -0.02), ("squint_weak", -0.02), ("squint_hold", -0.25),
    ("wink_max", 0.05), ("smile_gate", 0.05), ("blink_gate", 0.1), ("pose_gate", 5.0),
    ("light_floor", -10.0), ("lean_ratio", -0.02), ("lean_weak", -0.02),
    ("lean_release", -0.02), ("lean_hold", -0.5),
)
_EXPECT_KINDS = {"squint": {"squint"}, "lean": {"lean"}, "struggle": {"squint", "lean"}}


@dataclass
class Run:
    path: Path
    alias: str
    glasses: str
    light: str
    senses: dict
    labels: list[dict]
    faces: list[tuple[float, dict]]
    inputs: list[dict]


@dataclass
class Totals:
    struggle_fp: int = 0
    struggle_fn: int = 0
    signal_fp: int = 0
    signal_fp_by: collections.Counter = field(default_factory=collections.Counter)
    signal_fn_by: collections.Counter = field(default_factory=collections.Counter)
    latencies: list[float] = field(default_factory=list)
    clear_seconds: float = 0.0
    natural_struggles: int = 0
    natural_signals: int = 0
    segments: list[dict] = field(default_factory=list)
    error: str = ""

    def add(self, other: "Totals") -> None:
        self.struggle_fp += other.struggle_fp
        self.struggle_fn += other.struggle_fn
        self.signal_fp += other.signal_fp
        self.signal_fp_by.update(other.signal_fp_by)
        self.signal_fn_by.update(other.signal_fn_by)
        self.latencies.extend(other.latencies)
        self.clear_seconds += other.clear_seconds
        self.natural_struggles += other.natural_struggles
        self.natural_signals += other.natural_signals
        self.segments.extend(other.segments)

    @property
    def mean_latency(self) -> float:
        return sum(self.latencies) / len(self.latencies) if self.latencies else 0.0

    @property
    def key(self) -> tuple:
        """Lower is better: no struggle false positives, then fewer misses, then fewer stray signals, then faster."""
        if self.error:
            return (10 ** 9, 10 ** 9, 10 ** 9, 10 ** 9)
        return (self.struggle_fp, self.struggle_fn, self.signal_fp, self.mean_latency)


class _Capture(EventLog):
    """Copies each signal and struggle as Senses records it (synchronously, with the real timestamp)."""

    def __init__(self):
        super().__init__(size=100000)
        self.senses = None
        self.signals: list[dict] = []
        self.struggles: list[dict] = []

    def add(self, kind, text, **data):
        item = super().add(kind, text, **data)
        if kind == "sense" and self.senses is not None and self.senses.last_signal:
            self.signals.append(dict(self.senses.last_signal))
        struggle = data.get("struggle")
        if kind == "struggle" and isinstance(struggle, dict):
            self.struggles.append(dict(struggle))
        return item


class _Alive:
    """Stand-in for the input helper process so hesitation's clock runs without pynput."""

    def poll(self):
        return None


def expand_paths(paths: list[str | Path]) -> list[Path]:
    out = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            out.extend(sorted(p for p in path.rglob("*.jsonl") if p.is_file()))  # the recorder nests runs
        else:
            out.append(path)
    return out


def load_run(path: Path) -> Run:
    meta = {"alias": path.stem, "glasses": "", "light": "", "senses": {}}
    labels, faces, inputs = [], [], []
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        rec = obj.get("rec")
        if rec == "meta":
            meta = obj
        elif rec == "label":
            labels.append(obj)
        elif rec == "helper" and isinstance(obj.get("f"), dict):
            try:
                faces.append((float(obj["t"]), obj["f"]))
            except (KeyError, TypeError, ValueError):
                continue
        elif rec == "input":
            inputs.append(obj)
    senses = meta.get("senses") if isinstance(meta.get("senses"), dict) else {}
    return Run(path, str(meta.get("alias") or path.stem), str(meta.get("glasses") or ""),
               str(meta.get("light") or ""), senses, labels, faces, inputs)


def load_runs(paths: list[str | Path]) -> list[Run]:
    runs = []
    for path in expand_paths(paths):
        if not path.exists():
            raise FileNotFoundError(path)
        runs.append(load_run(path))
    return runs


def _calibration(run: Run) -> tuple[list[dict], float]:
    chosen = None
    for label in run.labels:
        if label.get("expect") == "baseline" or label.get("name") == "calibration":
            chosen = label
            break
    if chosen is None:
        return [], 0.0
    start, end = float(chosen["start"]), float(chosen["end"])
    samples = [sig for t, sig in run.faces if start <= t < end]
    return samples, end


def _config_for(run: Run, tune: Tuning | None) -> dict:
    cfg = copy.deepcopy(DEFAULTS["senses"])
    cfg.update(run.senses or {})
    if tune is not None:
        for key in TUNE_KEYS:
            cfg[key] = getattr(tune, key)
    cfg["vision"] = True
    cfg["input"] = True
    return cfg


def _signature(tune: Tuning) -> tuple:
    return tuple(round(float(getattr(tune, key)), 5) for key in TUNE_KEYS)


def legal(tune: Tuning) -> bool:
    if not (0 < tune.squint_weak < tune.squint_delta < 1):
        return False
    if not (0 < tune.squint_hold <= 10):
        return False
    if not (1.0 <= tune.lean_release < tune.lean_weak <= tune.lean_ratio <= 2):
        return False
    if tune.lean_hold <= 0:
        return False
    if min(tune.wink_max, tune.smile_gate, tune.blink_gate, tune.pose_gate) <= 0:
        return False
    if tune.light_floor < 0:
        return False
    return True


def _expand(tune: Tuning, key: str, value: float) -> list[Tuning]:
    """The plain step, plus a coupled step so the weak line moves with the strong one."""
    found: list[Tuning] = []
    seen = set()

    def keep(trial: Tuning) -> None:
        if not legal(trial):
            return
        sig = _signature(trial)
        if sig in seen or sig == _signature(tune):
            return
        seen.add(sig)
        found.append(trial)

    keep(dataclasses.replace(tune, **{key: value}))
    if key == "squint_delta":
        for weak in (min(tune.squint_weak, value - 0.02), round(value * 0.75, 4), round(value * 0.6, 4)):
            keep(dataclasses.replace(tune, squint_delta=value, squint_weak=round(float(weak), 4)))
    if key == "lean_ratio":
        for weak in (min(tune.lean_weak, value - 0.01), round(1 + (value - 1) * 0.75, 4)):
            release = min(tune.lean_release, round(float(weak) - 0.02, 4))
            keep(dataclasses.replace(tune, lean_ratio=value, lean_weak=round(float(weak), 4),
                                     lean_release=round(float(release), 4)))
    return found


def _covers(struggle: dict, kind: str) -> bool:
    kinds = {struggle.get("kind")}
    for part in (struggle.get("detail") or {}).get("signals") or []:
        if isinstance(part, dict):
            kinds.add(part.get("kind"))
    return kind in kinds


def _inside(label: dict, t: float) -> bool:
    return float(label["start"]) <= t < float(label["end"])


def _score_label(label: dict, signals: list[dict], struggles: list[dict], source: str) -> dict | None:
    expect = str(label.get("expect") or "")
    if expect in ("gap", "baseline") or label.get("partial"):
        return None
    sigs = [s for s in signals if _inside(label, float(s["ts"]))]
    strugs = [s for s in struggles if _inside(label, float(s.get("ts", -1)))]
    start = float(label["start"])
    seconds = max(0.0, float(label["end"]) - start)
    row = {
        "file": source, "segment": str(label.get("name") or ""), "expect": expect, "seconds": round(seconds, 3),
        "struggles": len(strugs), "signals": len(sigs), "struggle_fp": 0, "struggle_fn": 0,
        "signal_fp": 0, "signal_fn": 0, "latency_s": "",
    }
    if expect == "natural":
        row["natural_struggles"] = len(strugs)
        row["natural_signals"] = len(sigs)
        return row
    wanted = _EXPECT_KINDS.get(expect, set())
    fp_n = 0
    fp_by: list[tuple[str, str]] = []
    for sig in sigs:
        if sig["kind"] not in wanted:
            fp_n += 1
            fp_by.append((str(sig["kind"]), str(sig.get("tier") or "")))
    fn_by: list[tuple[str, str]] = []
    for kind in sorted(wanted):
        saw = [s for s in sigs if s["kind"] == kind]
        strong = any(s.get("tier") == "strong" for s in saw)
        weak = any(s.get("tier") == "weak" for s in saw)
        if not strong:
            fn_by.append((kind, "strong"))
        if not strong and not weak:
            fn_by.append((kind, "weak"))
    matched = True
    times: list[float] = []
    if expect == "squint":
        matched = any(s["kind"] == "squint" for s in sigs) or any(_covers(s, "squint") for s in strugs)
        times = [float(s["ts"]) for s in sigs if s["kind"] == "squint"]
        times += [float(s["ts"]) for s in strugs if _covers(s, "squint")]
    elif expect == "lean":
        matched = any(s["kind"] == "lean" for s in sigs) or any(_covers(s, "lean") for s in strugs)
        times = [float(s["ts"]) for s in sigs if s["kind"] == "lean"]
        times += [float(s["ts"]) for s in strugs if _covers(s, "lean")]
    elif expect == "struggle":
        matched = bool(strugs)
        times = [float(s["ts"]) for s in strugs]
    struggle_fp = len(strugs) if expect == "none" else 0
    struggle_fn = 0 if matched or expect == "none" else 1
    row.update(struggle_fp=struggle_fp, struggle_fn=struggle_fn, signal_fp=fp_n, signal_fn=len(fn_by),
               _fp_by=fp_by, _fn_by=fn_by)
    if times and expect in _EXPECT_KINDS:
        row["latency_s"] = round(max(0.0, min(times) - start), 3)
    return row


def _totals_from_rows(rows: list[dict]) -> Totals:
    out = Totals()
    for row in rows:
        if row["expect"] == "natural":
            out.natural_struggles += row["natural_struggles"]
            out.natural_signals += row["natural_signals"]
            out.segments.append(row)
            continue
        out.struggle_fp += row["struggle_fp"]
        out.struggle_fn += row["struggle_fn"]
        out.signal_fp += row["signal_fp"]
        for kind, tier in row.get("_fp_by") or []:
            out.signal_fp_by[(kind, tier)] += 1
        for kind, tier in row.get("_fn_by") or []:
            out.signal_fn_by[(kind, tier)] += 1
        if row["latency_s"] != "":
            out.latencies.append(float(row["latency_s"]))
        if row["expect"] == "none":
            out.clear_seconds += float(row["seconds"])
        out.segments.append(row)
    return out


def replay(run: Run, tune: Tuning | None, home: Path) -> Totals:
    """One run through a fresh Senses. `home` is a temporary state directory, never the real one."""
    samples, cal_end = _calibration(run)
    try:
        base = Baseline.from_samples(samples, cal_end or 0.0, "calibrated")
    except ValueError as e:
        return Totals(error=str(e))
    state = home / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "senses_baseline.json").write_text(json.dumps(base.to_dict()), encoding="utf-8")
    cfg = _config_for(run, tune)
    log = _Capture()
    agent = types.SimpleNamespace(
        config=types.SimpleNamespace(data={"senses": cfg, "gaze": {"enabled": False}}),
        paths=types.SimpleNamespace(state_dir=state),
        events=log, platform=types.SimpleNamespace(frontmost_app=lambda: None),
        bridge=None, on_struggle=None, gaze=None)
    senses = Senses(agent)
    log.senses = senses
    senses.input.proc = _Alive()
    delivered: list[dict] = []
    senses.subscribe(delivered.append)

    timeline = []
    for t, sig in run.faces:
        if t >= cal_end:
            timeline.append((t, 1, "face", sig))
    for msg in run.inputs:
        try:
            t = float(msg.get("t"))
        except (TypeError, ValueError):
            continue
        body = {k: v for k, v in msg.items() if k != "rec"}
        timeline.append((t, 0, "input", body))
    if timeline:
        t0 = min(item[0] for item in timeline)
        t1 = max(item[0] for item in timeline)
        t = t0
        while t <= t1 + 1e-9:
            timeline.append((t, 2, "tick", None))
            t += 1.0
    timeline.sort()
    for t, _order, kind, payload in timeline:
        if kind == "face":
            senses.on_face(t, payload)
        elif kind == "input":
            senses.on_input(payload)
        else:
            try:
                senses.tick(t)
            except TypeError:
                senses.tick()
    # Subscribers run on a worker thread. The event log already has every struggle; wait briefly
    # so the delivered list can catch up, then keep whichever list is complete.
    if len(delivered) < len(log.struggles):
        deadline = time.monotonic() + 0.2
        while time.monotonic() < deadline and len(delivered) < len(log.struggles):
            time.sleep(0.005)
    struggles = delivered if len(delivered) >= len(log.struggles) else list(log.struggles)
    rows = []
    for label in run.labels:
        row = _score_label(label, log.signals, struggles, run.path.name)
        if row is not None:
            rows.append(row)
    return _totals_from_rows(rows)


def score_runs(runs: list[Run], tune: Tuning | None = None, home: Path | None = None) -> Totals:
    own = home is None
    if home is None:
        home = Path(tempfile.mkdtemp(prefix="ally-replay-"))
    totals = Totals()
    try:
        for run in runs:
            one = replay(run, tune, home)
            if one.error:
                totals.error = one.error
                continue
            totals.add(one)
    finally:
        if own:
            shutil.rmtree(home, ignore_errors=True)
    return totals


def search(runs: list[Run], passes: int = 2) -> Tuning:
    """Coordinate search. Objective: zero struggle false positives, then fewest misses, then fewest stray signals, then latency."""
    tune = Tuning.from_config(_config_for(runs[0], None))
    home = Path(tempfile.mkdtemp(prefix="ally-tune-"))
    cache: dict[tuple, Totals] = {}
    try:
        def scored(trial: Tuning) -> Totals:
            sig = _signature(trial)
            if sig not in cache:
                cache[sig] = score_runs(runs, trial, home)
            return cache[sig]

        best = scored(tune)
        for _ in range(passes):
            improved = False
            for key, grid in GRIDS.items():
                for value in grid:
                    for trial in _expand(tune, key, value):
                        trial_score = scored(trial)
                        if trial_score.key < best.key:
                            tune, best = trial, trial_score
                            improved = True
            if not improved:
                break
        return tune
    finally:
        shutil.rmtree(home, ignore_errors=True)


def leave_one_out(runs: list[Run]) -> tuple[int, int, list[tuple[str, Totals]]]:
    """Tune on everyone except one person, score that person, and sum the held-out counts."""
    by: dict[str, list[Run]] = {}
    for run in runs:
        by.setdefault(run.alias, []).append(run)
    held_fp = held_fn = 0
    details = []
    for person in sorted(by):
        train = [run for run in runs if run.alias != person]
        if not train:
            continue
        tune = search(train)
        held = score_runs(by[person], tune)
        held_fp += held.struggle_fp
        held_fn += held.struggle_fn
        details.append((person, held))
    return held_fp, held_fn, details


def margin(runs: list[Run], tune: Tuning) -> str:
    """How far the nearest threshold can move toward a false positive before one appears."""
    home = Path(tempfile.mkdtemp(prefix="ally-margin-"))
    try:
        base_fp = score_runs(runs, tune, home).struggle_fp
        nearest: tuple[float, str, float] | None = None
        for key, step in _MARGIN_STEPS:
            current = float(getattr(tune, key))
            trial_v = current
            for _ in range(8):
                trial_v = round(trial_v + step, 4)
                if trial_v <= 0:
                    break
                trial = dataclasses.replace(tune, **{key: trial_v})
                if not legal(trial):
                    break
                fp = score_runs(runs, trial, home).struggle_fp
                if fp > base_fp:
                    dist = abs(trial_v - current)
                    if nearest is None or dist < nearest[0]:
                        nearest = (dist, key, trial_v)
                    break
    finally:
        shutil.rmtree(home, ignore_errors=True)
    if nearest is None:
        return "Margin to the nearest false positive: none within the searched range."
    dist, key, value = nearest
    return (f"Margin to the nearest false positive: {key} can move {dist:g} "
            f"before a new false positive (it starts at {value:g}).")


def toml_snippet(tune: Tuning) -> str:
    lines = ["[senses]"]
    for key in TUNE_KEYS:
        value = getattr(tune, key)
        if isinstance(value, float):
            text = f"{value:.4f}".rstrip("0").rstrip(".")
            lines.append(f"{key} = {text or '0'}")
        else:
            lines.append(f"{key} = {value}")
    return "\n".join(lines) + "\n"


def people(runs: list[Run]) -> set[str]:
    return {run.alias for run in runs}


def recommendation(runs: list[Run]) -> tuple[int, str]:
    if len(people(runs)) < 2:
        return 2, "Need recordings from at least 2 people to recommend thresholds.\n"
    tune = search(runs)
    held_fp, held_fn, _details = leave_one_out(runs)
    text = "\n".join([
        "Thresholds fit on everyone (do not treat this fit as the proof):",
        toml_snippet(tune).rstrip(),
        f"Held-out struggle false positives: {held_fp}",
        f"Held-out false negatives: {held_fn}",
        margin(runs, tune),
        "The held-out counts are leave-one-person-out. Copy the snippet by hand if you want it in config.toml.",
        "",
    ])
    return 0, text


def _fmt(n: float) -> str:
    return f"{n:.2f}"


def fp_per_minute(pairs: list[tuple[Run, Totals]]) -> list[dict]:
    buckets: dict[tuple[str, str], dict] = {}
    for run, totals in pairs:
        for seg in totals.segments:
            if seg["expect"] != "none":
                continue
            key = (run.glasses, run.light)
            bucket = buckets.setdefault(key, {"fp": 0, "seconds": 0.0})
            bucket["fp"] += seg["struggle_fp"]
            bucket["seconds"] += float(seg["seconds"])
    rows = []
    for (glasses, light), bucket in sorted(buckets.items()):
        minutes = bucket["seconds"] / 60.0
        rate = (bucket["fp"] / minutes) if minutes else 0.0
        rows.append({"glasses": glasses, "light": light, "minutes": round(minutes, 3),
                     "struggle_fp": bucket["fp"], "per_minute": round(rate, 3)})
    return rows


def render(pairs: list[tuple[Run, Totals]]) -> tuple[str, str]:
    """Markdown report and CSV text."""
    lines = ["# Senses validation", "",
             "A struggle counts in the segment that contains its timestamp. Rests and calibration are not scored. "
             "Natural reading is reported on its own and is not a false positive or a miss. "
             "False positives per minute use only segments labeled no struggle, including free use.", ""]
    lines.append("## Per run")
    lines.append("")
    lines.append("| file | person | glasses | light | struggle FP | misses | stray signals | mean latency s |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    grand = Totals()
    for run, totals in pairs:
        if totals.error:
            lines.append(f"| {run.path.name} | {run.alias} | {run.glasses} | {run.light} | baseline failed: {totals.error} | | | |")
            continue
        grand.add(totals)
        lat = _fmt(totals.mean_latency) if totals.latencies else ""
        lines.append(f"| {run.path.name} | {run.alias} | {run.glasses} | {run.light} | {totals.struggle_fp} | "
                     f"{totals.struggle_fn} | {totals.signal_fp} | {lat} |")
    lines.append("")
    lines.append("## False positives per minute")
    lines.append("")
    lines.append("| glasses | light | minutes | struggle false positives | per minute |")
    lines.append("| --- | --- | --- | --- | --- |")
    rate_rows = fp_per_minute(pairs)
    if not rate_rows:
        lines.append("| | | 0 | 0 | 0 |")
    for row in rate_rows:
        lines.append(f"| {row['glasses']} | {row['light']} | {row['minutes']} | {row['struggle_fp']} | {row['per_minute']} |")
    lines.append("")
    lines.append("## Segments")
    lines.append("")
    lines.append("| file | segment | expect | seconds | struggles | struggle FP | misses | stray signals | latency s |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for _run, totals in pairs:
        for seg in totals.segments:
            lines.append(f"| {seg['file']} | {seg['segment']} | {seg['expect']} | {seg['seconds']} | {seg['struggles']} | "
                         f"{seg['struggle_fp']} | {seg['struggle_fn']} | {seg['signal_fp']} | {seg['latency_s']} |")
    lines.append("")
    lines.append("## Signal false positives and misses")
    lines.append("")
    lines.append("| kind | tier | false positives | misses |")
    lines.append("| --- | --- | --- | --- |")
    kinds = sorted(set(grand.signal_fp_by) | set(grand.signal_fn_by))
    if not kinds:
        lines.append("| | | 0 | 0 |")
    for kind, tier in kinds:
        lines.append(f"| {kind} | {tier} | {grand.signal_fp_by[(kind, tier)]} | {grand.signal_fn_by[(kind, tier)]} |")
    lines.append("")
    lines.append("## Natural reading (not scored as right or wrong)")
    lines.append("")
    lines.append(f"Struggles during natural reading: {grand.natural_struggles}. "
                 f"Signals during natural reading: {grand.natural_signals}.")
    lines.append("")
    md = "\n".join(lines) + "\n"

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["file", "person", "glasses", "light", "segment", "expect", "seconds", "struggles",
                     "struggle_fp", "struggle_fn", "signals", "signal_fp", "signal_fn", "latency_s"])
    for run, totals in pairs:
        for seg in totals.segments:
            writer.writerow([seg["file"], run.alias, run.glasses, run.light, seg["segment"], seg["expect"],
                             seg["seconds"], seg["struggles"], seg["struggle_fp"], seg["struggle_fn"],
                             seg["signals"], seg["signal_fp"], seg["signal_fn"], seg["latency_s"]])
    for row in rate_rows:
        writer.writerow(["", "", row["glasses"], row["light"], "__fp_per_minute__", "none",
                         round(row["minutes"] * 60, 3), row["struggle_fp"], row["struggle_fp"], 0, "", "", "",
                         row["per_minute"]])
    return md, buf.getvalue()


def _pairs(runs: list[Run]) -> list[tuple[Run, Totals]]:
    home = Path(tempfile.mkdtemp(prefix="ally-report-"))
    try:
        return [(run, replay(run, None, home)) for run in runs]
    finally:
        shutil.rmtree(home, ignore_errors=True)


def write_report(paths: list[str | Path], dest: Path, runs: list[Run] | None = None) -> str:
    runs = list(runs) if runs is not None else load_runs(paths)
    if not runs:
        raise FileNotFoundError("no JSONL recordings found")
    md, table = render(_pairs(runs))
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "senses-report.md").write_text(md, encoding="utf-8")
    (dest / "senses-report.csv").write_text(table, encoding="utf-8")
    return md


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ally.senses.evaluate")
    sub = parser.add_subparsers(dest="cmd", required=True)
    rep = sub.add_parser("report", help="score recordings and write senses-report.md and .csv")
    rep.add_argument("paths", nargs="+")
    rep.add_argument("--out", default=".", help="folder for the report (default: the current folder)")
    tun = sub.add_parser("tune", help="search thresholds and print a [senses] snippet")
    tun.add_argument("paths", nargs="+")
    args = parser.parse_args(argv)
    try:
        runs = load_runs(args.paths)
    except FileNotFoundError as e:
        print(f"Can't read recordings: {e}")
        return 2
    if not runs:
        print("No JSONL recordings found.")
        return 2
    if args.cmd == "report":
        md = write_report(args.paths, Path(args.out), runs)
        print(md)
        print(f"Wrote {Path(args.out) / 'senses-report.md'} and senses-report.csv")
        return 0
    code, text = recommendation(runs)
    print(text, end="")
    return code


if __name__ == "__main__":
    sys.exit(main())
