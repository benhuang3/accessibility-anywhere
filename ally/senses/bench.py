"""Offline benchmark for numeric senses and gaze recordings."""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from pathlib import Path

from . import face
from .detectors import (
    Baseline,
    DistanceLeanDetector,
    GeometricSquintDetector,
    HybridLeanDetector,
    HybridSquintDetector,
    LeanDetector,
    PersonalSquintDetector,
    RelativeSquintDetector,
    Signal,
    SquintDetector,
    Tuning,
)
from .features import extract, robust_baseline

# Reproducible bootstrap intervals make repeated reports directly comparable.
BOOTSTRAP_SEED = 2801
BOOTSTRAP_SAMPLES = 1000
SQUINT_METHODS = {
    "v1": SquintDetector,
    "relative": RelativeSquintDetector,
    "geometric": GeometricSquintDetector,
    "personal": PersonalSquintDetector,
    "hybrid": HybridSquintDetector,
}
LEAN_METHODS = {
    "v1": LeanDetector,
    "distance": DistanceLeanDetector,
    "hybrid": HybridLeanDetector,
}
GROUP_FIELDS = ("eyes", "age", "glasses", "light", "eth")


def _json_rows(path):
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            yield json.loads(line)
        except (TypeError, ValueError):
            continue


def _raw_frame(row):
    raw = row.get("raw", row)
    return isinstance(raw, dict) and all(key in raw for key in ("lm", "bs", "mat", "w", "h"))


def _raw_payload(row):
    return row.get("raw", row) if _raw_frame(row) else {}


def read_recording(path):
    """Read a senses or gaze JSONL file without changing its stored data."""
    meta = {}
    frames = []
    segments = []
    for row in _json_rows(path):
        record_type = row.get("rec", row.get("type", row.get("kind", "")))
        if record_type == "meta":
            meta = row
        elif record_type in ("frame", "helper") and "t" in row and (
                "f" in row or "lm" in row):
            raw = _raw_payload(row)
            frame = row.get("f", {})
            if isinstance(frame, list):
                frame = face.signals(raw["lm"], raw["bs"], raw["mat"],
                                     int(raw["w"]), int(raw["h"])) if raw else {}
            frames.append({"t": float(row["t"]), "f": frame, "raw": raw})
        elif record_type in ("label", "target"):
            expectation = row.get("expect", row.get("pose", "none"))
            if record_type == "target":
                expectation = "lean" if expectation == "close" else "none"
            start = float(row.get("start", row.get("t", 0.0)))
            end = float(row.get("end", start))
            segments.append({
                "name": str(row.get("name", row.get("pose", expectation))),
                "start": start,
                "end": end,
                "expect": expectation,
                "phase": str(row.get("phase", "")),
                "pose": str(row.get("pose", "normal")),
            })
    frames.sort(key=lambda item: item["t"])
    return {"meta": meta, "frames": frames, "segments": segments}


def read_london(path):
    """Read neutral and smiling still rows from the London Face Set JSONL."""
    people = {}
    for row in _json_rows(path):
        if row.get("rec") != "still" or not _raw_frame(row):
            continue
        person = str(row.get("person", "unknown"))
        people.setdefault(person, {})[str(row.get("cond", ""))] = row
    return people


def read_gaze_recording(path):
    """Read a gaze run using the same raw frame and target format as senses recordings."""
    return read_recording(path)


def _frame_features(item):
    raw = item["raw"]
    if raw:
        try:
            features = extract(raw["lm"], raw["bs"], raw["mat"], raw["w"], raw["h"])
        except (IndexError, TypeError, ValueError):
            features = {}
        aus = raw.get("au")
        features["au"] = aus
        if isinstance(aus, list) and len(aus) > 6:
            features["au6"] = float(aus[3])
            features["au7"] = float(aus[4])
            features["au12"] = float(aus[6])
        return features
    values = dict(item["f"])
    if "eyeSquint" not in values:
        left = values.get("squint_l", values.get("eyeSquintLeft", 0.0))
        right = values.get("squint_r", values.get("eyeSquintRight", 0.0))
        values["eyeSquint"] = (float(left) + float(right)) / 2
        values["eyeSquintLeft"] = float(left)
        values["eyeSquintRight"] = float(right)
    values.setdefault("mouthSmile", values.get("smile", 0.0))
    values.setdefault("eyeBlink", values.get("blink", 0.0))
    values.setdefault("head_yaw", values.get("yaw", 0.0))
    values.setdefault("head_pitch", values.get("pitch", 0.0))
    return values


def _legacy_signals(item):
    """Use the live face.signals conversion for faithful raw-frame v1 replay."""
    raw = item["raw"]
    if raw:
        return face.signals(raw["lm"], raw["bs"], raw["mat"], raw["w"], raw["h"],
                            item["f"].get("light") if isinstance(item["f"], dict) else None)
    return dict(item["f"])


def _legacy_baseline(items):
    samples = []
    for item in items:
        values = _legacy_signals(item)
        if values and "squint_l" in values:
            samples.append(values)
    if not samples:
        return Baseline(0.1, 0.3)
    try:
        return Baseline.from_samples(samples, 0.0)
    except ValueError:
        return Baseline(0.1, 0.3)


def _training_segments(segments):
    selected = {"neutral": [], "top": [], "bottom": [], "smile": [], "squint": []}
    first_squint_used = False
    used = set()
    for index, segment in enumerate(segments):
        name = segment["name"].lower()
        phase = segment["phase"].lower()
        expectation = segment["expect"]
        if expectation == "baseline" or name in ("calibration", "neutral"):
            selected["neutral"].append(segment)
        elif "top" in name or "top" in phase:
            selected["top"].append(segment)
        elif "bottom" in name or "bottom" in phase:
            selected["bottom"].append(segment)
        elif "smile" in name:
            selected["smile"].append(segment)
        elif name in ("act_squint", "squint") and not first_squint_used:
            selected["squint"].append(segment)
            first_squint_used = True
        else:
            continue
        used.add(index)
    return selected, used


def _frames_in_segments(frames, segments):
    result = []
    for item in frames:
        if any(segment["start"] <= item["t"] <= segment["end"] for segment in segments):
            values = _frame_features(item)
            if values:
                result.append(values)
    return result


def _fit_recording(record, threshold_margin=0.25):
    training, used = _training_segments(record["segments"])
    raw_training = {
        name: _frames_in_segments(record["frames"], segments)
        for name, segments in training.items()
    }
    raw_available = any(item["raw"] for item in record["frames"])
    model = PersonalSquintDetector.fit(raw_training, threshold_margin) if raw_available else {
        "enabled": False,
        "why": "recording has no raw landmarks",
    }
    raw_baseline = robust_baseline(raw_training["neutral"])
    if model.get("baseline"):
        raw_baseline["personal_model"] = model
    legacy_frames = _frames_in_segments(
        record["frames"], training["neutral"] or training["squint"]
    )
    return used, model, raw_baseline, _legacy_baseline([
        item for item in record["frames"]
        if any(segment["start"] <= item["t"] <= segment["end"]
               for segment in training["neutral"] or training["squint"])
    ]), legacy_frames


def _labels_for(record, used):
    labels = []
    for index, segment in enumerate(record["segments"]):
        if index in used:
            continue
        expectation = segment["expect"]
        name = segment["name"].lower()
        positive = expectation in ("squint", "lean")
        if expectation in ("none", "natural", "squint", "lean"):
            natural_reading = name in ("read_normal", "read_tiny") or "natural" in name
            label = "natural" if natural_reading else expectation
            labels.append((segment, label, positive))
    if not record["segments"] and record["frames"]:
        first = record["frames"][0]["t"]
        last = record["frames"][-1]["t"]
        labels.append(({"name": "whole gaze recording", "start": first, "end": last}, "none", False))
    return labels


def _person_groups(meta):
    return {
        "group_eyes": meta.get("eyes", "unknown"),
        "group_age": meta.get("age", "unknown"),
        "group_glasses": meta.get("glasses", "unknown"),
        "group_light": meta.get("light", "unknown"),
        "group_eth": meta.get("eth", "unknown"),
    }


def _normalise_frame(item, kind):
    values = _legacy_signals(item) if kind == "v1" else _frame_features(item)
    if kind == "v1":
        return values or {}
    aliases = {
        "squint_l": values.get("eyeSquintLeft", values.get("squint_l", 0.0)),
        "squint_r": values.get("eyeSquintRight", values.get("squint_r", 0.0)),
        "smile": values.get("mouthSmile", values.get("smile", 0.0)),
        "blink": values.get("eyeBlink", values.get("blink", 0.0)),
        "yaw": values.get("head_yaw", values.get("yaw", 0.0)),
        "pitch": values.get("head_pitch", values.get("pitch", 0.0)),
        "width": values.get("face_width", values.get("width", 0.3)),
    }
    return {**values, **aliases}


def _detector(kind, method, tuning):
    classes = SQUINT_METHODS if kind == "squint" else LEAN_METHODS
    return classes[method](Tuning(**tuning))


def evaluate(record, kind="squint", tuning=None):
    """Replay all methods and return one row per labelled test segment."""
    if not record["frames"]:
        return []
    options = dict(tuning or {})
    threshold_margin = float(options.pop("personal_threshold_margin", 0.25))
    used, model, raw_baseline, legacy_baseline, _legacy_samples = _fit_recording(
        record, threshold_margin
    )
    labels = _labels_for(record, used)
    methods = SQUINT_METHODS if kind == "squint" else LEAN_METHODS
    rows = []
    for method in methods:
        if method in ("geometric", "personal", "hybrid") and (
                not any(item["raw"] for item in record["frames"]) or not model.get("baseline")):
            rows.extend(_not_applicable(record, method, kind, labels))
            continue
        detector_tuning = {key: value for key, value in options.items()
                           if key in Tuning.__dataclass_fields__}
        detector = _detector(kind, method, detector_tuning)
        if method in ("personal", "hybrid") and model.get("baseline"):
            raw_baseline["personal_model"] = model
        if method == "v1" or (kind == "lean" and not raw_baseline):
            baseline = legacy_baseline
        else:
            baseline = raw_baseline
        signals = []
        for item in record["frames"]:
            values = _normalise_frame(item, method)
            if not values:
                continue
            signal = detector.feed(values, item["t"], baseline)
            if signal is not None:
                signals.append(signal)
        for segment, expectation, positive in labels:
            hits = [signal for signal in signals
                    if segment["start"] <= signal.ts <= segment["end"]]
            false_positive = not positive and bool(hits)
            row = {
                "person": record["meta"].get("alias", record["meta"].get("person", "unknown")),
                "method": method,
                "kind": kind,
                "segment": segment["name"],
                "expect": expectation,
                "hit": int(bool(hits)) if positive else "",
                "strong_hit": int(any(signal.tier == "strong" for signal in hits)) if positive else "",
                "false_positive": int(false_positive) if not positive else "",
                "duration": max(0.0, segment["end"] - segment["start"]),
                "latency": round(hits[0].ts - segment["start"], 3) if positive and hits else "",
                "squint_off": (int(not model.get("enabled"))
                               if kind == "squint" and method in ("personal", "hybrid") else ""),
                **_person_groups(record["meta"]),
            }
            rows.append(row)
    return rows


def _not_applicable(record, method, kind, labels):
    rows = []
    for segment, expectation, positive in labels:
        rows.append({
            "person": record["meta"].get("alias", "unknown"),
            "method": method,
            "kind": kind,
            "segment": segment["name"],
            "expect": expectation,
            "hit": "",
            "strong_hit": "",
            "false_positive": "",
            "duration": max(0.0, segment["end"] - segment["start"]),
            "latency": "",
            "squint_off": "",
            "status": "not applicable: raw landmarks or calibration labels unavailable",
            **_person_groups(record["meta"]),
        })
    return rows


def discover(paths):
    """Yield JSONL files under the supplied recording paths."""
    for value in paths:
        path = Path(value)
        if path.is_dir():
            yield from sorted(path.rglob("*.jsonl"))
        elif path.is_file():
            yield path


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def _bootstrap(values):
    if not values:
        return None, None
    rng = random.Random(BOOTSTRAP_SEED)
    means = []
    for _sample in range(BOOTSTRAP_SAMPLES):
        sample = [rng.choice(values) for _ in values]
        means.append(statistics.mean(sample))
    return _percentile(means, 0.025), _percentile(means, 0.975)


def _person_metrics(rows):
    grouped = {}
    for row in rows:
        key = (row["person"], row["kind"], row["method"])
        metrics = grouped.setdefault(key, {"fp": 0, "none_seconds": 0, "miss": 0,
                                           "positive": 0, "strong": 0, "latency": [],
                                           "natural": 0, "natural_total": 0,
                                           "off": 0, "off_seen": False})
        if row.get("false_positive") != "":
            metrics["fp"] += int(row["false_positive"] or 0)
            metrics["none_seconds"] += float(row.get("duration", 0.0))
        if row.get("hit") != "":
            metrics["positive"] += 1
            metrics["miss"] += 1 - int(row["hit"] or 0)
            metrics["strong"] += int(row.get("strong_hit", 0) or 0)
            if row.get("latency") != "":
                metrics["latency"].append(float(row["latency"]))
        if row.get("expect") == "natural":
            metrics["natural_total"] += 1
            metrics["natural"] += int(bool(row.get("false_positive")))
        if row.get("squint_off") != "":
            metrics["off_seen"] = True
            metrics["off"] = max(metrics["off"], int(row["squint_off"]))
    return grouped


def _group_metrics(person_metrics, rows):
    metadata = {}
    for row in rows:
        metadata.setdefault((row["person"], row["kind"], row["method"]), row)
    results = []
    for attribute in GROUP_FIELDS:
        group_key = f"group_{attribute}"
        groups = {}
        for key, metrics in person_metrics.items():
            row = metadata.get(key, {})
            group = row.get(group_key, "unknown")
            groups.setdefault(group, []).append(metrics)
        for group, people in groups.items():
            fp_rates = [m["fp"] / (m["none_seconds"] / 60) for m in people
                        if m["none_seconds"] > 0]
            miss_rates = [m["miss"] / m["positive"] for m in people if m["positive"] > 0]
            off_rates = [m["off"] for m in people if m["off_seen"]]
            fp_ci = _bootstrap(fp_rates)
            miss_ci = _bootstrap(miss_rates)
            off_ci = _bootstrap(off_rates)
            results.append({
                "attribute": attribute,
                "group": group,
                "people": len(people),
                "fp_rate": statistics.mean(fp_rates) if fp_rates else None,
                "fp_ci": fp_ci,
                "miss_rate": statistics.mean(miss_rates) if miss_rates else None,
                "miss_ci": miss_ci,
                "off_rate": statistics.mean(off_rates) if off_rates else None,
                "off_ci": off_ci,
            })
    return results


def _london_rows(path):
    people = read_london(path)
    group_counts = {}
    for person, conditions in people.items():
        neutral = conditions.get("neutral")
        smiling = conditions.get("smiling")
        if not neutral or not smiling:
            continue
        ethnicity = str(smiling.get("eth", neutral.get("eth", "unknown")))
        baseline = _legacy_signals({"raw": neutral, "f": {}})
        test = _legacy_signals({"raw": smiling, "f": {}})
        if not baseline or not test:
            continue
        delta = ((test["squint_l"] + test["squint_r"]) -
                 (baseline["squint_l"] + baseline["squint_r"])) / 2
        called = delta >= Tuning().squint_weak
        group_counts.setdefault(ethnicity, []).append(bool(called))
    return group_counts


def _write_csv(path, rows):
    fields = ["person", "method", "kind", "segment", "expect", "hit", "strong_hit",
              "false_positive", "duration", "latency", "squint_off", "status",
              "group_eyes", "group_age", "group_glasses", "group_light", "group_eth"]
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def report(paths, out, london=None):
    """Write a segment report and per-person bootstrap summary under out."""
    output_dir = Path(out)
    output_dir.mkdir(parents=True, exist_ok=True)
    recordings = [(path, read_recording(path)) for path in discover(paths)]
    rows = []
    for _path, record in recordings:
        rows.extend(evaluate(record, "squint"))
        rows.extend(evaluate(record, "lean"))
    _write_csv(output_dir / "report.csv", rows)
    per_person = _person_metrics(rows)
    md = ["# Offline senses benchmark", "",
          "Results use people as the sampling unit. Frames without labels are not counted as negatives.", "",
          "## Per person", "",
          "| Person | Signal | Method | FP/min | Hits | Strong hits | Latency | "
          "Natural detections | Squint off |",
          "|---|---|---|---:|---:|---:|---:|---:|---|"]
    for (person, kind, method), metric in sorted(per_person.items()):
        fp_rate = metric["fp"] / (metric["none_seconds"] / 60) if metric["none_seconds"] else 0.0
        latency = statistics.median(metric["latency"]) if metric["latency"] else None
        md.append(f"| {person} | {kind} | {method} | {fp_rate:.3f} | "
                  f"{metric['positive'] - metric['miss']}/{metric['positive']} | {metric['strong']} | "
                  f"{latency if latency is not None else 'n/a'} | "
                  f"{metric['natural']}/{metric['natural_total']} | "
                  f"{'yes' if metric['off'] else 'no' if metric['off_seen'] else 'n/a'} |")
    md.extend(["", "## Group metrics", "",
               "False positives are per minute of labelled none segments. Intervals bootstrap people.", "",
               "| Signal | Method | Attribute | Group | People | FP/min (95% CI) | "
               "Miss (95% CI) | Off (95% CI) |",
               "|---|---|---|---|---:|---:|---:|---:|"])
    for method_kind in sorted({(row["kind"], row["method"]) for row in rows}):
        subset = [row for row in rows if (row["kind"], row["method"]) == method_kind]
        keys = {(row["person"], row["kind"], row["method"]) for row in subset}
        metrics = {key: per_person[key] for key in keys if key in per_person}
        summaries = _group_metrics(metrics, subset)
        gap_values = {}
        for summary in summaries:
            gap_values.setdefault(summary["attribute"], []).append(summary)
            fp = _format_interval(summary["fp_rate"], summary["fp_ci"])
            miss = _format_interval(summary["miss_rate"], summary["miss_ci"])
            off = _format_interval(summary["off_rate"], summary["off_ci"])
            md.append(f"| {method_kind[0]} | {method_kind[1]} | {summary['attribute']} | "
                      f"{summary['group']} | {summary['people']} | {fp} | {miss} | {off} |")
        for attribute, groups in gap_values.items():
            fp_rates = [group["fp_rate"] for group in groups if group["fp_rate"] is not None]
            miss_rates = [group["miss_rate"] for group in groups if group["miss_rate"] is not None]
            off_rates = [group["off_rate"] for group in groups if group["off_rate"] is not None]
            fp_gap = max(fp_rates) - min(fp_rates) if len(fp_rates) > 1 else None
            miss_gap = max(miss_rates) - min(miss_rates) if len(miss_rates) > 1 else None
            off_gap = max(off_rates) - min(off_rates) if len(off_rates) > 1 else None
            md.append(f"| {method_kind[0]} | {method_kind[1]} | {attribute} gap | worst-to-best | "
                      f"n/a | {_format_interval(fp_gap, (None, None))} | "
                      f"{_format_interval(miss_gap, (None, None))} | "
                      f"{_format_interval(off_gap, (None, None))} |")
    md.extend(["", "## London Face Set smile false positives", "",
               "Only v1 has a neutral photo baseline; methods needing calibration are not applicable.", "",
               "| Method | Ethnicity | People | Smiles called squints | Share |",
               "|---|---|---:|---:|---:|"])
    if london and Path(london).is_file():
        for group, calls in sorted(_london_rows(london).items()):
            md.append(f"| v1 | {group} | {len(calls)} | {sum(calls)} | "
                      f"{sum(calls) / len(calls):.3f} |")
            for method in ("relative", "geometric", "personal", "hybrid"):
                md.append(f"| {method} | {group} | {len(calls)} | n/a | n/a |")
    else:
        md.append("| n/a | n/a | 0 | n/a | n/a |")
    raw_count = sum(bool(item["raw"]) for _path, record in recordings for item in record["frames"])
    people = sorted({record["meta"].get("alias", path.stem) for path, record in recordings})
    model_rows = [row for row in rows
                  if row["kind"] == "squint" and row["method"] == "personal"
                  and row.get("squint_off") != ""]
    model_people = {row["person"] for row in model_rows}
    enabled_people = {row["person"] for row in model_rows if row["squint_off"] == 0}
    md.extend(["", "## Data and recommendation", "",
               f"Recordings: {len(recordings)} files; {len(people)} aliases; "
               f"{raw_count} raw landmark frames.",
               f"Personal model fit for {len(model_people)} people; enabled for "
               f"{len(enabled_people)}.",
               "Personal and hybrid methods need labelled calibration and deliberate squint samples.",
               "London stills test smile leakage only; they contain no labelled squints.",
               "Keep v1 as the default until raw labelled volunteer recordings support a comparison.", ""])
    (output_dir / "report.md").write_text("\n".join(md), encoding="utf-8")
    return rows


def _format_interval(value, interval):
    if value is None:
        return "n/a"
    if interval[0] is None:
        return f"{value:.3f} (n/a)"
    return f"{value:.3f} ({interval[0]:.3f} to {interval[1]:.3f})"


def tune(paths):
    """Leave one person out while selecting personal and hybrid score margins."""
    records = [(path, read_recording(path)) for path in discover(paths)]
    usable = set()
    for path, record in records:
        _used, model, _stats, _baseline, _legacy = _fit_recording(record)
        if model.get("enabled"):
            usable.add(record["meta"].get("alias", path.stem))
    if len(usable) < 3:
        raise SystemExit("Refusing to recommend values from fewer than 3 people with usable calibration.")
    grids = {"personal": (0.15, 0.25, 0.4), "hybrid": (0.15, 0.25, 0.4)}
    output = []
    for held_out in sorted(usable):
        training = [(path, record) for path, record in records
                    if record["meta"].get("alias", path.stem) != held_out]
        validation = [(path, record) for path, record in records
                      if record["meta"].get("alias", path.stem) == held_out]
        for method, margins in grids.items():
            best = None
            for margin in margins:
                tuning = {"personal_threshold_margin": margin}
                train_rows = [row for _path, record in training
                              for row in evaluate(record, "squint", tuning)
                              if row["method"] == method]
                false_positives = sum(int(row["false_positive"] or 0)
                                      for row in train_rows if row["false_positive"] != "")
                hits = sum(int(row["hit"] or 0) for row in train_rows if row["hit"] != "")
                if false_positives == 0 and (best is None or hits > best[0]):
                    best = (hits, margin)
            if best is None:
                continue
            test_rows = [row for _path, record in validation
                         for row in evaluate(record, "squint", {"personal_threshold_margin": best[1]})
                         if row["method"] == method]
            hits = sum(int(row["hit"] or 0) for row in test_rows if row["hit"] != "")
            positives = sum(row["hit"] != "" for row in test_rows)
            false_positives = sum(int(row["false_positive"] or 0)
                                  for row in test_rows if row["false_positive"] != "")
            output.append((held_out, method, best[1], hits, positives, false_positives))
    print("| Held-out person | Method | Margin | Hits/positive | False positives |")
    print("|---|---|---:|---:|---:|")
    for held_out, method, margin, hits, positives, false_positives in output:
        print(f"| {held_out} | {method} | {margin:.2f} | {hits}/{positives} | {false_positives} |")
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m ally.senses.bench")
    subparsers = parser.add_subparsers(dest="command", required=True)
    report_parser = subparsers.add_parser("report")
    report_parser.add_argument("paths", nargs="+")
    report_parser.add_argument("--out", required=True)
    report_parser.add_argument("--london")
    tune_parser = subparsers.add_parser("tune")
    tune_parser.add_argument("paths", nargs="+")
    args = parser.parse_args(argv)
    if args.command == "report":
        report(args.paths, args.out, args.london)
    else:
        tune(args.paths)


if __name__ == "__main__":
    main()
