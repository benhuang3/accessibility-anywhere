"""Pick a Chrome zoom level from a zoom audit (extension/steps/enlarge.js auditAtZooms).

Every audit entry is measured in CSS pixels at its zoom, so text and target sizes are multiplied by the
zoom here. A level is only used if it doesn't make the page worse than it was at the original zoom.
"""

from __future__ import annotations

PHONE_VW = 800  # below this many CSS px most sites switch to their phone layout
SQUEEZE_VW = 1000  # 800-1000 CSS px: the cramped "tablet" layout many sites handle badly
SMALL_TEXT_SHARE = 0.2  # at most this share of the text may stay below the goal
TARGET_SHARE = 0.8  # at least this share of buttons and links must reach the goal

DEFAULT_GOALS = {"text_px": 20, "target_px": 44, "aim": "text", "sticky_budget": 0.25, "max_zoom": 2.5}


def _same(a: float, b: float) -> bool:
    return abs(a - b) < 0.001


def _baseline(audits: list[dict], original: float | None) -> dict:
    if original is not None:
        for a in audits:
            if _same(a["zoom"], original):
                return a
    return min(audits, key=lambda a: a["zoom"])


def _is_safe(level: dict, base: dict, budget: float) -> bool:
    if level is base:
        return True
    if level.get("overflowPx", 0) > base.get("overflowPx", 0) + 8:
        return False
    if level.get("cutOffText", 0) > base.get("cutOffText", 0):
        return False
    if set(level.get("clipped") or []) - set(base.get("clipped") or []):
        return False
    if level.get("stickyCover", 0) > max(budget, base.get("stickyCover", 0) + 0.02):
        return False
    return len(set(level.get("covered") or []) - set(base.get("covered") or [])) <= 1


def _text_ok(level: dict, goal_px: float) -> bool:
    zoom = level["zoom"]
    sizes = level.get("textSizes") or []
    if not sizes:
        return level.get("textPx", 0) * zoom >= goal_px
    below = 0.0
    for px, share in sizes:  # sorted by size; share is cumulative
        if px * zoom < goal_px:
            below = share
    return below <= SMALL_TEXT_SHARE


def _targets_ok(level: dict, goal_px: float) -> bool:
    zoom = level["zoom"]
    small = level.get("smallTargets") or []
    total = max(level.get("targets", 0), len(small))
    if total == 0:
        return True
    failing = sum(1 for t in small if min(t.get("w", 0), t.get("h", 0)) * zoom < goal_px)
    if 44 * zoom < goal_px:  # the targets measure() didn't list are at least 44 CSS px
        failing += total - len(small)
    return (total - failing) / total >= TARGET_SHARE


def choose_zoom(audit: dict, goals: dict) -> dict:
    g = {**DEFAULT_GOALS, **(goals or {})}
    audits = sorted(audit.get("audits") or [], key=lambda a: a["zoom"])
    original = audit.get("original")
    if not audits:
        return {"zoom": original or 1, "met": False, "reason": "couldn't measure the page", "phone_layout": False,
                "reader_suggested": bool(audit.get("article")), "safe_levels": []}
    base = _baseline(audits, original)
    if original is None:
        original = base["zoom"]
    max_zoom = float(g["max_zoom"])
    levels = [a for a in audits if a["zoom"] <= max_zoom + 0.001 and a["zoom"] >= original - 0.001]
    safe = [a for a in levels if _is_safe(a, base, float(g["sticky_budget"]))]
    if g["aim"] == "buttons":
        meets = lambda a: _targets_ok(a, float(g["target_px"]))  # noqa: E731
    else:
        meets = lambda a: _text_ok(a, float(g["text_px"]))  # noqa: E731
    good = [a for a in safe if meets(a)]
    safe_levels = [a["zoom"] for a in safe]
    article = bool(audit.get("article"))

    if good:
        pick = good[0]
        reason = "already big enough" if pick is base else "big enough and the page still fits"
        vw = pick.get("vw", 0)
        if PHONE_VW <= vw <= SQUEEZE_VW:
            phone = next((a for a in good if a.get("vw", PHONE_VW) < PHONE_VW), None)
            if phone is not None:
                pick = phone
                reason = "uses the site's simpler narrow layout"
        return {"zoom": pick["zoom"], "met": True, "reason": reason,
                "phone_layout": pick.get("vw", PHONE_VW) < PHONE_VW and not _same(pick["zoom"], base["zoom"]),
                "reader_suggested": False, "safe_levels": safe_levels}

    best = max(safe, key=lambda a: a["zoom"]) if safe else None
    if best is None:
        return {"zoom": original, "met": False, "reason": "every bigger size breaks the page",
                "phone_layout": False, "reader_suggested": article, "safe_levels": []}
    reason = "this is the biggest size before the page breaks" if best is not base else "every bigger size breaks the page"
    return {"zoom": best["zoom"], "met": False, "reason": reason,
            "phone_layout": best.get("vw", PHONE_VW) < PHONE_VW and best is not base,
            "reader_suggested": article, "safe_levels": safe_levels}


ZOOM_STEPS = (1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0, 5.0)


def share_below(text_sizes, goal_px: float, zoom: float) -> float:
    """Share of the page's text that is still smaller than goal_px on screen at this zoom.
    text_sizes: [[css_px, share_at_or_below], ...] sorted by size, from AllyPage.measure()."""
    share = 0.0
    for px, at_or_below in text_sizes or []:
        if float(px) * zoom < goal_px - 0.01:
            share = float(at_or_below)
    return share


def predict_zoom(metrics: dict, current: float, goal_px: float, max_zoom: float) -> dict:
    """The smallest Chrome zoom step at which the text reaches the goal, from one measurement at the current
    zoom (CSS sizes don't change with zoom, so zooming multiplies them). No page changes needed; the caller
    applies it and lets verify step back if the page breaks. Returns {zoom, met}."""
    sizes = metrics.get("textSizes") or ([[metrics["textPx"], 1.0]] if metrics.get("textPx") else [])
    if not sizes:
        return {"zoom": current, "met": True}
    if share_below(sizes, goal_px, current) <= SMALL_TEXT_SHARE:
        return {"zoom": current, "met": True}
    for z in ZOOM_STEPS:
        if z <= current + 0.01 or z > max_zoom + 1e-6:
            continue
        if share_below(sizes, goal_px, z) <= SMALL_TEXT_SHARE:
            return {"zoom": z, "met": True}
    top = max([z for z in ZOOM_STEPS if z <= max_zoom + 1e-6] or [current])
    return {"zoom": max(top, current), "met": False}


def next_step(current: float, max_zoom: float) -> float:
    """One Chrome zoom step bigger than current, never above max_zoom."""
    for z in ZOOM_STEPS:
        if z > current + 0.01:
            return min(z, max_zoom) if z <= max_zoom + 1e-6 else current
    return current
