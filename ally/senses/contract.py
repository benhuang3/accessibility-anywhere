"""Validation for the public struggle event shape."""

KINDS = ("squint", "lean", "rage_click", "hesitation", "retype", "fused")
TOP_KEYS = {"kind", "confidence", "target", "source", "detail", "ts"}
TARGET_KEYS = {"app", "site", "window", "x", "y", "box"}


def validate_struggle(d: dict) -> list[str]:
    problems = []
    if not isinstance(d, dict):
        return ["struggle must be a dict"]
    if set(d) != TOP_KEYS:
        problems.append("top-level keys must be exactly kind, confidence, target, source, detail, ts")
    if d.get("kind") not in KINDS:
        problems.append("kind must be one of: " + ", ".join(KINDS))
    if type(d.get("confidence")) is not float or not 0 <= d["confidence"] <= 1:
        problems.append("confidence must be a float from 0 to 1")
    target = d.get("target")
    if not isinstance(target, dict) or set(target) != TARGET_KEYS:
        problems.append("target must contain exactly app, site, window, x, y, box")
    if not isinstance(d.get("source"), str):
        problems.append("source must be a string")
    if not isinstance(d.get("detail"), dict):
        problems.append("detail must be a dict")
    if type(d.get("ts")) is not float:
        problems.append("ts must be a float")
    return problems


def to_event(d: dict) -> dict:
    """Return the validated event payload accepted by POST /api/event."""
    problems = validate_struggle(d)
    if problems:
        raise ValueError("; ".join(problems))
    return {**d, "type": "struggle"}
