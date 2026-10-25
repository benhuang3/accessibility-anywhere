"""Build and restore the numeric data learned during one face setup."""
from __future__ import annotations

import json
import math
import time

MAX_BYTES = 64 * 1024


def _numbers(value):
    if isinstance(value, dict):
        return {str(k): _numbers(v) for k, v in value.items()
                if not any(word in str(k).lower() for word in ("landmark", "frame", "image", "jpeg", "bitmap"))
                and not ("sample" in str(k).lower() and isinstance(v, (dict, list, tuple)))}
    if isinstance(value, (list, tuple)):
        return [_numbers(v) for v in value]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        n = float(value)
        if not math.isfinite(n):
            raise ValueError("face profile contains a non-finite number")
        return value
    if value is None or isinstance(value, (str, bool)):
        return value
    raise ValueError("face profile must contain JSON-safe numeric data")


def build(gaze=None, senses=None, au=None, meta=None) -> dict:
    gaze_data = None
    if gaze:
        gaze_data = gaze
    baseline = None
    personal = None
    if senses:
        if isinstance(senses, dict):
            baseline = senses.get("baseline")
            personal = senses.get("personal")
        elif hasattr(senses, "baseline"):
            baseline_obj = senses.baseline
            baseline = baseline_obj.to_dict() if baseline_obj else None
            personal = (baseline_obj.feature_stats or {}).get("personal_model") if baseline_obj else None
        elif hasattr(senses, "to_dict"):
            baseline = senses.to_dict()
        if isinstance(baseline, dict) and personal is None:
            personal = (baseline.get("feature_stats") or {}).get("personal_model")
    au_baseline = None
    if isinstance(au, dict):
        au_baseline = au.get("baseline") if isinstance(au.get("baseline"), dict) else au
    elif au is not None and hasattr(au, "baseline"):
        au_baseline = au.baseline
    elif au is not None:
        au_baseline = au
    if not gaze_data and not baseline and not au:
        raise ValueError("nothing usable to save in the face profile")
    now = time.time()
    result = {"version": 1, "created": now, "updated": now,
              "meta": {k: (meta or {}).get(k) for k in ("camera", "screen", "screen_mm", "app_version")},
              "gaze": gaze_data, "senses": {"baseline": baseline, "personal": personal},
              "au": {"baseline": au_baseline},
              "setup": {"seconds": (meta or {}).get("seconds"), "steps": (meta or {}).get("steps", [])}}
    result = _numbers(result)
    if not result["gaze"] and not result["senses"]["baseline"] and not result["au"]["baseline"]:
        raise ValueError("nothing usable to save in the face profile")
    if len(json.dumps(result, separators=(",", ":"), allow_nan=False).encode("utf-8")) >= MAX_BYTES:
        raise ValueError("face profile must be smaller than 64 KB")
    return result


def apply(agent, profile) -> list[str]:
    restored = []
    if not isinstance(profile, dict):
        return restored
    try:
        from . import gaze_model
        model = profile.get("gaze")
        if model and tuple(model.get("screen", ())) == tuple(getattr(agent.gaze, "screen", ())):
            gaze_model.save(model, agent.gaze.calibration_path)
            agent.gaze.send("reload")
            restored.append("Eye tracking calibration restored.")
    except Exception:
        pass
    try:
        senses = profile.get("senses") or {}
        baseline = senses.get("baseline")
        if baseline:
            from .senses.detectors import Baseline
            from .state import atomic_write
            obj = Baseline.from_dict(baseline)
            if obj:
                data = obj.to_dict()
                if senses.get("personal"):
                    data.setdefault("feature_stats", {})["personal_model"] = senses["personal"]
                atomic_write(agent.senses.baseline_path, json.dumps(data, separators=(",", ":")))
                obj.feature_stats.setdefault("personal_model", senses.get("personal"))
                if hasattr(agent.senses, "reload_baseline"):
                    agent.senses.reload_baseline()
                else:
                    reload_baseline = getattr(agent.senses, "_load_baseline", None)
                    agent.senses.baseline = reload_baseline() if callable(reload_baseline) else obj
                    if hasattr(agent.senses, "_build"):
                        agent.senses._build()
                restored.append("Face sensing baseline restored.")
    except Exception:
        pass
    try:
        au_baseline = (profile.get("au") or {}).get("baseline")
        if au_baseline:
            from .state import atomic_write
            atomic_write(agent.paths.state_dir / "au_baseline.json",
                         json.dumps(au_baseline, separators=(",", ":")))
            restored.append("Action unit baseline restored.")
    except Exception:
        pass
    return restored
