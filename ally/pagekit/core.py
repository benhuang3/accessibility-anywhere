"""Runs the page steps in Chrome: order, verify, back-off and the spoken summary (see docs/page_steps.md).

Each step is its own extension action; this module only decides what to call and in which order, so
every rule here can be tested with a fake browser.
"""

from __future__ import annotations

import logging
import threading

from ..registry import ToolError

log = logging.getLogger("ally.pagekit")

STEP_ORDER = ("clutter", "menus", "text", "buttons")
CLUTTER_KINDS = ("cookie", "promo", "chat", "ads", "share", "motion")
FORM_CLUTTER = ("cookie", "promo", "chat")
ZOOM_STEPS = (1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0, 5.0)
DEFAULTS = {"text_goal_px": 20, "target_goal_px": 44, "max_zoom": 2.5, "max_text_scale": 2.0, "sticky_budget": 0.25,
            "ai_check": True, "ai_background": True}
LAYOUT_STEPS = ("clutter", "menus", "text", "buttons")  # what the AI checker looks at afterwards
GOALS = {"clutter": "hide pop-ups, ads and other clutter", "menus": "add a big, simple menu",
         "text": "make the text bigger", "buttons": "make the buttons bigger"}
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

    def call(self, action: str, /, timeout: float | None = None, **args):
        return self.ctx.browser.call(action, timeout=timeout or self.timeout, tabId=self.tab_id, **args)

    def zoom(self) -> float:
        return float(self.call("pageInfo").get("zoom") or 1.0)


def choose_text_scale(session: PageSession, cfg: dict, direction: str = "") -> dict:
    """How much bigger the page's own text should get (never zoom): predicted from one measurement of its
    sizes; direction "up" = one step bigger than now, "down" = one step smaller. Verify and back-off
    afterwards catch a size that breaks the page."""
    from .zoom import next_scale, predict_scale

    current = float(session.info.get("textScale") or 1.0)
    top = float(cfg["max_text_scale"])
    if direction == "down":
        return {"scale": next_scale(current, top, up=False), "original": current, "met": True, "why": "one step smaller"}
    metrics = (session.call("pageBaseline") or {}).get("metrics") or {}
    guess = predict_scale(metrics, current, float(cfg["text_goal_px"]), top)
    scale, why = guess["scale"], "predicted"
    if direction == "up" and scale <= current + 0.01:
        scale, why = next_scale(current, top), "one step bigger"
    return {"scale": scale, "original": current, "met": guess["met"], "why": why,
            "reader_suggested": not guess["met"] and session.info.get("type") == "article"}


def _text_on(session: PageSession, cfg: dict, report: dict, direction: str = "") -> None:
    choice = choose_text_scale(session, cfg, direction)
    original, scale = choice["original"], float(choice["scale"])
    report["text_choice"] = choice
    report["original_scale"] = original
    if scale <= 1.0 + 0.01:
        session.call("stepOff", step="text", opts={})
        report["scale"] = 1.0
        report["said"].append("put the text back to its normal size" if original > 1.01 else "left the text at its size")
        return
    session.call("stepOn", step="text", opts={"scale": scale, "goalPx": cfg["text_goal_px"]})
    report["scale"] = scale
    if scale > original + 0.01:
        report["said"].append(f"made the text bigger ({round(scale * 100)}%)")
    elif scale < original - 0.01:
        report["said"].append(f"made the text a bit smaller ({round(scale * 100)}%)")
    else:
        report["said"].append("the text is already as big as it gets without breaking the page")
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
            from .zoom import next_scale

            for _ in range(MAX_ZOOM_BACKOFFS):
                current = float(report.get("scale") or 1.0)
                target = next_scale(current, 99, up=False)
                if target <= report.get("original_scale", 1.0) + 0.01:
                    break
                session.call("stepOn", step="text", opts={"scale": target})
                report["scale"] = target
                report["said"] = [f"made the text bigger ({round(target * 100)}%)" if x.startswith("made the text bigger")
                                  else x for x in report["said"]]
                report["backed_off"].append(f"text {round(target * 100)}%")
                verdict = session.call("pageVerify", stickyBudget=budget)
                if verdict.get("ok"):
                    break
            if not verdict.get("ok"):
                # Still broken: bigger text made it worse, so put the page's own sizes back.
                session.call("stepOff", step="text", opts={})
                report["scale"] = 1.0
                report["backed_off"].append("text")
                report["said"] = [x for x in report["said"] if not x.startswith("made the text bigger")]
                report["notes"].append("Bigger text would break this page, so I left it at its size. "
                                       "Try 'reader mode' for just the text.")
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


def revert(session: PageSession, report: dict) -> None:
    """Undo everything this run did: its steps (newest first), the AI fixes and the zoom."""
    for step in reversed(report["applied"]):
        try:
            session.call("stepOff", step=step, opts={})
        except ToolError as e:
            log.warning("revert %s failed: %s", step, e)
    try:
        session.call("stepOff", step="fix", opts={})
    except ToolError as e:
        log.warning("revert failed: %s", e)
    report["reverted"] = True


_reviews: dict[int, threading.Event] = {}  # tab id -> cancel flag of the AI check running there
_reviews_lock = threading.Lock()


def _claim_tab(tab_id: int) -> threading.Event:
    """A new request on this tab: cancel the AI check still running there and hand out a fresh flag."""
    flag = threading.Event()
    with _reviews_lock:
        old = _reviews.get(tab_id)
        if old is not None:
            old.set()
        _reviews[tab_id] = flag
    return flag


def _say(ctx, text: str) -> None:
    say = getattr(ctx, "say", None)
    if callable(say):
        try:
            say(text)
        except Exception:
            log.debug("say failed", exc_info=True)


def _review(ctx, session: PageSession, cfg: dict, report: dict, reviewer, after_revert=None,
            background: bool = False) -> None:
    """The AI check-and-fix loop after the deterministic steps; reverts the page if it can't get it right.
    In the background it speaks only when it changed something."""
    from .review import Cancelled

    steps_on = [s for s in report["applied"] if s not in report["backed_off"]]
    reviewer.scale = float(report.get("scale") or reviewer.scale)
    try:
        result = reviewer.run(steps_on, report["verify"])
    except Cancelled:
        report["review"] = {"cancelled": True}
        return
    except (ToolError, ValueError, KeyError) as e:  # offline, Claude down, or an answer that isn't JSON
        log.info("AI check skipped: %s", e)
        report["review"] = {"skipped": str(e)[:200]}
        return
    report["review"] = result
    log.info("AI check on %s: %s", session.info.get("url", "?"), " | ".join(result["log"]))
    if reviewer.cancel is not None and reviewer.cancel.is_set():
        return  # a newer request took over while Claude was answering
    if result["gave_up"]:
        revert(session, report)
        if after_revert is not None:
            try:
                after_revert()
            except ToolError as e:
                log.warning("after revert: %s", e)
        if background:
            _say(ctx, "That didn't look right on this page, so I put it back the way it was.")
    elif result["fixes"] and background:
        _say(ctx, "I adjusted the page a little so it fits better.")


def run_steps(ctx, steps, on: bool = True, opts: dict | None = None, verify: bool = True,
              session: PageSession | None = None, review: bool | None = None, after_revert=None) -> dict:
    """Turn page steps on (in canonical order, then verify and back off, then the AI check and fix) or off
    (in reverse order). review=None: run the AI check when Claude is available and [page_enlarge] ai_check."""
    names = ordered(steps)
    opts = opts or {}
    cfg = settings(ctx)
    session = session or PageSession(ctx)
    report = {"tab_id": session.tab_id, "steps": names, "on": on, "said": [], "notes": [], "problems": [],
              "applied": [], "backed_off": [], "verify": None, "offer_reader": False, "review": None,
              "reverted": False, "start_zoom": float(session.info.get("zoom") or 1.0)}
    cancel = _claim_tab(session.tab_id)
    if not on:
        for step in reversed(names):
            try:
                r = session.call("stepOff", step=step, opts=dict(opts.get(step) or {}))
                report["said"].append((r or {}).get("said", ""))
                report["applied"].append(step)
            except ToolError as e:
                report["problems"].append(str(e))
        try:
            session.call("stepOff", step="fix", opts={})  # the AI fixes belonged to what was just turned off
        except ToolError:
            pass
        return report
    reviewer = None
    if verify and (review if review is not None else cfg.get("ai_check", True)) and any(s in LAYOUT_STEPS for s in names):
        from .review import Review

        if Review.available(ctx):
            goal = "; ".join(GOALS[s] for s in names if s in GOALS)
            reviewer = Review(ctx, session, cfg, goal, step_opts=opts, cancel=cancel)
            if not reviewer.capture_before():
                reviewer = None
    for step in names:
        try:
            if step == "text":
                t = opts.get("text") or {}
                _text_on(session, cfg, report, direction=t.get("direction") or ("up" if t.get("step_up") else ""))
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
        if reviewer is not None:
            if cfg.get("ai_background", True):
                # Reply now; the check runs behind and speaks only if it fixes or reverts something.
                report["review"] = {"pending": True}
                t = threading.Thread(target=_review, args=(ctx, session, cfg, report, reviewer, after_revert, True),
                                     name="page-review", daemon=True)
                report["review_thread"] = t
                t.start()
            else:
                _review(ctx, session, cfg, report, reviewer, after_revert)
    return report


def summary(report: dict, done_fallback: str = "Done.") -> str:
    """One or two short spoken sentences describing what happened."""
    if report.get("reverted"):
        return ("I tried to make this page easier, but it kept looking wrong, so I put it back the way it was. "
                "Try 'reader mode' for just the text.")
    said = join_said(report["said"])
    if report["on"]:
        text = f"I {said}." if said else ""
    else:
        text = "Put the page back." if report["applied"] else ""
    if report["backed_off"] and "text" not in report["backed_off"]:
        text += " I undid part of it because it was breaking the page."
    review = report.get("review") or {}
    if review.get("ok") and review.get("fixes"):
        text += " I checked how it looks and fixed it."
    elif review.get("ok"):
        text += " I checked it and it looks right."
    if report["notes"]:
        text += " " + " ".join(report["notes"])
    if report["problems"] and not report["applied"]:
        raise ToolError(report["problems"][0])
    if report["problems"]:
        text += f" One part didn't work: {report['problems'][0]}"
    return (text.strip() or done_fallback)[:400]
