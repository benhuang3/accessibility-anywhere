"""Runs the page steps in Chrome: order, verify, back-off and the spoken summary (see docs/page_steps.md).

Each step is its own extension action; this module only decides what to call and in which order, so
every rule here can be tested with a fake browser.
"""

from __future__ import annotations

import logging

from ..registry import ToolError

log = logging.getLogger("ally.pagekit")

STEP_ORDER = ("clutter", "menus", "text", "buttons")
CLUTTER_KINDS = ("cookie", "promo", "chat", "ads", "share", "motion")
FORM_CLUTTER = ("cookie", "promo", "chat")
ZOOM_STEPS = (1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0, 5.0)
DEFAULTS = {"text_goal_px": 20, "target_goal_px": 44, "max_zoom": 2.5, "sticky_budget": 0.25}
MAX_ZOOM_BACKOFFS = 2


def settings(ctx) -> dict:
    cfg = getattr(ctx, "config", None) or {}
    return dict(DEFAULTS, **(cfg.get("page_enlarge") or {}))


def ordered(steps) -> list[str]:
    """The canonical order: clutter, menus, text, buttons. Unknown names are refused."""
    steps = list(steps or ())
    bad = [s for s in steps if s not in STEP_ORDER]
    if bad:
        raise ToolError(f"Unknown page step: {', '.join(bad)}. Choose from: {', '.join(STEP_ORDER)}.")
    return [s for s in STEP_ORDER if s in steps]


def audit_levels(current: float, max_zoom: float) -> list[float]:
    levels = [round(current, 3)] + [z for z in ZOOM_STEPS if current + 0.01 < z <= max_zoom + 1e-6]
    return levels


def steps_for(page_type: str, requested=STEP_ORDER) -> dict:
    """Simplified mode policy: which steps (and options) suit this kind of page."""
    want = set(requested)
    if page_type == "excluded":
        return {"steps": [], "opts": {}, "reader": False}
    if page_type == "article":
        return {"steps": [s for s in ("clutter", "menus") if s in want],
                "opts": {"menus": {"mode": "strip"}}, "reader": True}
    if page_type == "form":
        return {"steps": [s for s in ("clutter", "text", "buttons") if s in want],
                "opts": {"clutter": {"kinds": list(FORM_CLUTTER)}}, "reader": False}
    return {"steps": [s for s in STEP_ORDER if s in want], "opts": {"menus": {"mode": "strip"}}, "reader": False}


def join_said(parts) -> str:
    parts = [p for p in parts if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


class PageSession:
    """One page in Chrome: the tab is looked up once and every call is sent to that tab."""

    def __init__(self, ctx, timeout: float = 20.0):
        self.ctx = ctx
        self.timeout = timeout
        if not getattr(ctx.browser, "connected", False):
            raise ToolError("Chrome isn't connected. Load the Ally extension, then try again.")
        info = ctx.browser.call("pageInfo", timeout=timeout)
        if not isinstance(info, dict) or "tabId" not in info:
            raise ToolError("This version of the Ally extension can't do that yet. Reload it at chrome://extensions.")
        self.info = info
        self.tab_id = info["tabId"]

    def call(self, action: str, timeout: float | None = None, **args):
        return self.ctx.browser.call(action, timeout=timeout or self.timeout, tabId=self.tab_id, **args)

    def zoom(self) -> float:
        return float(self.call("pageInfo").get("zoom") or 1.0)


def choose_text_zoom(session: PageSession, cfg: dict, aim: str = "text") -> dict:
    """Measure the page at Chrome's zoom steps and pick the biggest zoom that doesn't break it."""
    from .zoom import choose_zoom

    current = float(session.info.get("zoom") or 1.0)
    audit = session.call("auditAtZooms", timeout=60, levels=audit_levels(current, float(cfg["max_zoom"])),
                         maxZoom=float(cfg["max_zoom"]))
    audit = dict(audit or {}, article=session.info.get("type") == "article")
    goals = {"text_px": cfg["text_goal_px"], "target_px": cfg["target_goal_px"], "aim": aim,
             "sticky_budget": cfg["sticky_budget"], "max_zoom": cfg["max_zoom"]}
    choice = choose_zoom(audit, goals)
    choice["original"] = float(audit.get("original") or current)
    return choice


def _text_on(session: PageSession, cfg: dict, report: dict) -> None:
    choice = choose_text_zoom(session, cfg)
    original = choice["original"]
    zoom = float(choice["zoom"])
    if zoom <= original + 0.01:
        # Already as big as the goal needs: step up to the next level that doesn't break the page.
        higher = [z for z in choice.get("safe_levels") or [] if z > original + 0.01]
        if higher:
            zoom = min(higher)
    report["zoom_choice"] = choice
    session.call("stepOn", step="text", opts={"zoom": zoom})
    report["zoom"] = zoom
    report["original_zoom"] = original
    if zoom > original + 0.01:
        report["said"].append(f"made the page bigger ({round(zoom * 100)}%)")
    else:
        report["said"].append("kept the page at its size because bigger would break it")
    if choice.get("phone_layout"):
        report["notes"].append("The menu may now be a button in the top corner.")
    if choice.get("reader_suggested"):
        report["offer_reader"] = True


def _back_off(session: PageSession, applied: list[str], report: dict, budget: float) -> dict:
    """Undo this run's most recent changes until verify passes. Never touches steps on before the run."""
    verdict = report["verify"]
    for step in reversed(applied):
        if verdict.get("ok"):
            break
        if step == "buttons":
            session.call("stepOff", step="buttons")
            report["backed_off"].append("buttons")
        elif step == "text":
            original = report.get("original_zoom", 1.0)
            for _ in range(MAX_ZOOM_BACKOFFS):
                current = session.zoom()
                lower = [z for z in ZOOM_STEPS if original + 0.01 < z < current - 0.01]
                target = max(lower) if lower else original
                if abs(target - current) < 0.01:
                    break
                session.call("setZoomSettled", zoom=target)
                report["zoom"] = target
                report["backed_off"].append(f"zoom {round(target * 100)}%")
                verdict = session.call("pageVerify", stickyBudget=budget)
                if verdict.get("ok"):
                    break
            if not verdict.get("ok") and abs(session.zoom() - original) > 0.01:
                # Still broken after the small steps: Ally's zoom made it worse, so put the user's zoom back.
                session.call("setZoomSettled", zoom=original)
                report["zoom"] = original
                report["backed_off"].append(f"zoom {round(original * 100)}%")
                verdict = session.call("pageVerify", stickyBudget=budget)
            report["verify"] = verdict
            continue
        elif step == "menus":
            session.call("stepOff", step="menus")
            report["backed_off"].append("menus")
        else:
            continue  # hiding clutter only removes things; it isn't what broke the layout
        verdict = session.call("pageVerify", stickyBudget=budget)
        report["verify"] = verdict
    return verdict


def run_steps(ctx, steps, on: bool = True, opts: dict | None = None, verify: bool = True,
              session: PageSession | None = None) -> dict:
    """Turn page steps on (in canonical order, then verify and back off) or off (in reverse order)."""
    names = ordered(steps)
    opts = opts or {}
    cfg = settings(ctx)
    session = session or PageSession(ctx)
    report = {"tab_id": session.tab_id, "steps": names, "on": on, "said": [], "notes": [], "problems": [],
              "applied": [], "backed_off": [], "verify": None, "offer_reader": False}
    if not on:
        for step in reversed(names):
            try:
                r = session.call("stepOff", step=step, opts=dict(opts.get(step) or {}, restoreZoom=step == "text"))
                report["said"].append((r or {}).get("said", ""))
                report["applied"].append(step)
            except ToolError as e:
                report["problems"].append(str(e))
        return report
    for step in names:
        try:
            if step == "text":
                _text_on(session, cfg, report)
            else:
                r = session.call("stepOn", step=step, opts=dict(opts.get(step) or {}))
                report["said"].append((r or {}).get("said", ""))
            report["applied"].append(step)
        except ToolError as e:
            log.warning("page step %s failed: %s", step, e)
            report["problems"].append(str(e))
    if verify and report["applied"]:
        report["verify"] = session.call("pageVerify", stickyBudget=cfg["sticky_budget"])
        if not report["verify"].get("ok"):
            _back_off(session, report["applied"], report, cfg["sticky_budget"])
    return report


def summary(report: dict, done_fallback: str = "Done.") -> str:
    """One or two short spoken sentences describing what happened."""
    said = join_said(report["said"])
    if report["on"]:
        text = f"I {said}." if said else ""
    else:
        text = "Put the page back." if report["applied"] else ""
    if report["backed_off"]:
        text += " I undid part of it because it was breaking the page."
    if report["notes"]:
        text += " " + " ".join(report["notes"])
    if report["problems"] and not report["applied"]:
        raise ToolError(report["problems"][0])
    if report["problems"]:
        text += f" One part didn't work: {report['problems'][0]}"
    return (text.strip() or done_fallback)[:400]
