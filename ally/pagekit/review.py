"""AI check and fix after the page steps: a checker looks at the page before and after, a fixer proposes
small safe changes, the checker looks again. After MAX_FIXES failed fixes the page is put back.

    deterministic steps + verify --> checker --ok--> done
                                       | not ok
                                       v
                              fixer --> apply --> checker ... (MAX_FIXES times) --> revert

The fixer can only: change the text size, turn one of this run's steps off, or add CSS from a short list of
layout properties (the extension checks every rule again; see extension/steps/fix.js).
"""

from __future__ import annotations

import base64
import json
import logging
import re

from ..registry import ToolError

log = logging.getLogger("ally.pagekit")

MAX_FIXES = 3
MAX_ACTIONS = 6
FIX_PROPERTIES = {
    "font-size", "line-height", "letter-spacing", "word-spacing", "max-width", "min-width", "width", "height",
    "min-height", "max-height", "padding", "padding-top", "padding-bottom", "padding-left", "padding-right",
    "margin", "margin-top", "margin-bottom", "margin-left", "margin-right", "display", "flex-wrap",
    "flex-direction", "flex-basis", "flex-shrink", "flex-grow", "grid-template-columns", "gap", "position",
    "overflow", "overflow-x", "overflow-y", "white-space", "word-break", "overflow-wrap", "float", "text-align",
    "box-sizing", "columns",
}
FIX_ENUMS = {
    "display": {"block", "inline-block", "flex", "grid", "none", "inline-flex"},
    "position": {"static", "relative"},
    "overflow": {"visible", "auto"}, "overflow-x": {"visible", "auto"}, "overflow-y": {"visible", "auto"},
    "float": {"none"},
}
VALUE_RE = re.compile(r"^[a-z0-9.%#(),\s-]{1,40}$", re.I)
BAD_SELECTOR = re.compile(r"[{};@<>\\]|ally-", re.I)
TEXT_LEVELS = (1.0, 1.15, 1.25, 1.5, 1.75, 2.0, 2.5)  # text scales the fixer may pick (1.0 = the page's own size)

REVIEW_PROMPT = """You check, and if needed fix, a web page that an accessibility helper changed for an older adult with
poor eyesight. Image 1 is the page BEFORE the change. Image 2 is the page NOW.
The helper tried to: {goal}. Current text size {scale}x the page's own; steps on: {steps}.
Automatic layout check now: {verify}

First list only the things that got WORSE for the person, compared with BEFORE. Something is worse when:
- text or controls overlap, are misaligned or jumbled, or text is cut off at the LEFT or RIGHT edge
- something the person needs is no longer reachable at all (not just moved)
- the page is now confusing: headings, text and controls are out of order or squashed
These are NOT worse, never list them:
- the change didn't go as far as hoped (only a little bigger, some buttons the same size)
- content moved further down because things got bigger (scrolling down still shows it)
- a sidebar or menu folded into a menu button or moved, because the page got bigger (sites do this)
- pop-ups, cookie banners, ads and chat bubbles being gone
- Ally's own black "Ally menu" bar at the top and its "Simple mode on" chip

If something got worse, also give at most {max_actions} small, safe actions that fix exactly that. Prefer the smallest
change: one targeted CSS rule, or turning off the one step that caused it. Keep the text as big as you can; only make
it smaller if text overlaps or runs off the edge. Never hide the main text, menus, forms or headings. Never zoom.
Allowed actions (JSON objects):
- {{"type": "text_size", "level": one of {levels}}}  (how much bigger than the page's own text; 1.0 = its own size)
- {{"type": "step_off", "step": one of {steps}}}
- {{"type": "css", "selector": "<a selector from the outline>", "property": "<one of: {properties}>", "value": "<plain value>"}}
CSS values are plain (e.g. "100%", "auto", "1.5", "18px", "wrap"); display may be block, inline-block, flex, grid,
inline-flex or none (none only for a decorative or duplicate part); position may be static or relative; overflow may
be visible or auto.
Page outline (selectors you may use; box = left, top, width, height in CSS px):
{outline}

Answer with JSON only, no other text:
{{"worse": ["each problem in a few plain words, and where it is"], "actions": [ ... ], "why": "one short sentence"}}
"""


def parse_json(text: str) -> dict:
    """The first JSON object in Claude's answer (it sometimes adds words or code fences)."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("no JSON in the answer")
    return json.loads(m.group(0))


def valid_action(a, steps_on: list[str], max_scale: float) -> dict | None:
    """A cleaned action, or None if it isn't allowed."""
    if not isinstance(a, dict):
        return None
    kind = a.get("type")
    if kind == "text_size":
        try:
            level = float(a.get("level"))
        except (TypeError, ValueError):
            return None
        if level not in TEXT_LEVELS or level > max_scale + 1e-6:
            return None
        return {"type": "text_size", "level": level}
    if kind == "step_off":
        step = a.get("step")
        return {"type": "step_off", "step": step} if step in steps_on else None
    if kind == "css":
        sel = str(a.get("selector") or "").strip()
        prop = str(a.get("property") or "").strip().lower()
        val = str(a.get("value") or "").strip().lower()
        if not sel or len(sel) > 200 or BAD_SELECTOR.search(sel):
            return None
        if prop not in FIX_PROPERTIES or not VALUE_RE.match(val) or re.search(r"url|expression|import|!", val):
            return None
        if prop in FIX_ENUMS and val not in FIX_ENUMS[prop]:
            return None
        return {"type": "css", "selector": sel, "property": prop, "value": val}
    return None


def _short(obj, limit: int) -> str:
    return json.dumps(obj, separators=(",", ":"), default=str)[:limit]


def _verify_text(v: dict | None) -> str:
    if not v:
        return "not run"
    bad = [k for k, c in (v.get("checks") or {}).items() if not c.get("ok")]
    return "passed" if v.get("ok") else f"failed ({', '.join(bad)})"


class Cancelled(Exception):
    """A newer request on the same tab took over; stop without touching the page."""


class Review:
    """One AI check-and-fix loop on one page (a PageSession). Each round is one Claude call that says what
    got worse and, if anything did, how to fix it."""

    def __init__(self, ctx, session, cfg: dict, goal: str, step_opts: dict | None = None, cancel=None):
        self.ctx = ctx
        self.session = session
        self.cfg = cfg
        self.goal = goal
        self.step_opts = step_opts or {}  # how each step was turned on, to turn it back on when undoing a round
        self.cancel = cancel  # a threading.Event set when a newer request on this tab starts
        self.rules: list[dict] = []  # the fixer's CSS rules currently on the page
        self.scale = float(session.info.get("textScale") or 1.0)  # the text step's scale right now
        self.before: bytes | None = None
        self.outline = None
        self.log: list[str] = []

    @staticmethod
    def available(ctx) -> bool:
        return callable(getattr(ctx, "ask", None))

    def _check_cancel(self) -> None:
        if self.cancel is not None and self.cancel.is_set():
            raise Cancelled()

    def shot(self) -> bytes:
        out = self.session.call("pageScreenshot", timeout=15)
        return base64.b64decode(out["data"])

    def capture_before(self) -> bool:
        try:
            self.before = self.shot()
            return True
        except (ToolError, KeyError, ValueError) as e:
            log.info("no before screenshot, skipping the AI check: %s", e)
            return False

    def round(self, steps_on: list[str], verify: dict | None) -> dict:
        """Look at the page now; returns {ok, problems, actions}."""
        self._check_cancel()
        if self.outline is None:
            self.outline = (self.session.call("pageOutline", timeout=20) or {}).get("items") or []
        after = self.shot()
        prompt = REVIEW_PROMPT.format(
            goal=self.goal, scale=self.scale, steps=", ".join(steps_on) or "none",
            verify=_verify_text(verify), max_actions=MAX_ACTIONS,
            levels=", ".join(str(z) for z in TEXT_LEVELS if z <= self.cfg["max_text_scale"]),
            properties=", ".join(sorted(FIX_PROPERTIES)), outline=_short(self.outline, 5000))
        self._check_cancel()
        answer = parse_json(self.ctx.ask(prompt, images=[(self.before, "image/jpeg"), (after, "image/jpeg")],
                                         max_tokens=800))
        if "worse" in answer:
            problems = [str(p)[:200] for p in answer.get("worse") or [] if str(p).strip()][:6]
            ok = not problems
        else:  # {"ok": ..., "problems": [...]} also understood
            problems = [str(p)[:200] for p in answer.get("problems") or []][:6]
            ok = bool(answer.get("ok"))
        actions = [] if ok else [a for a in (valid_action(x, steps_on, float(self.cfg["max_text_scale"]))
                                             for x in (answer.get("actions") or [])[:MAX_ACTIONS]) if a]
        self.log.append(f"look: {'ok' if ok else '; '.join(problems) or 'not ok'}"
                        + ("" if ok else f" -> {len(actions)} fix(es): {str(answer.get('why', ''))[:100]}"))
        return {"ok": ok, "problems": problems, "actions": actions}

    def apply(self, actions: list[dict], steps_on: list[str]) -> int:
        """Apply the fixer's actions; returns how many worked. CSS rules go in one batch (the "fix" step)."""
        done = 0
        css = [{k: a[k] for k in ("selector", "property", "value")} for a in actions if a["type"] == "css"]
        for a in actions:
            try:
                if a["type"] == "text_size":
                    if a["level"] <= 1.0:
                        self.session.call("stepOff", step="text", opts={})
                    else:
                        self.session.call("stepOn", step="text", opts={"scale": a["level"], "goalPx": self.cfg["text_goal_px"]})
                    self.scale = a["level"]
                    done += 1
                elif a["type"] == "step_off":
                    self.session.call("stepOff", step=a["step"], opts={})
                    if a["step"] in steps_on:
                        steps_on.remove(a["step"])
                    done += 1
            except ToolError as e:
                self.log.append(f"action failed: {e}")
        if css:
            try:
                self.session.call("stepOn", step="fix", opts={"rules": css})
                self.rules += css
                done += len(css)
            except ToolError as e:  # one bad rule refuses the batch; try them one by one
                for rule in css:
                    try:
                        self.session.call("stepOn", step="fix", opts={"rules": [rule]})
                        self.rules.append(rule)
                        done += 1
                    except ToolError as e2:
                        self.log.append(f"css refused: {e2}")
                self.log.append(f"css batch refused: {e}")
        return done

    def snapshot(self, steps_on: list[str]) -> dict:
        return {"rules": list(self.rules), "scale": self.scale, "steps": list(steps_on)}

    def restore(self, snap: dict, steps_on: list[str]) -> None:
        """Undo a fix round that made things worse: the CSS rules, the text size and any step it turned off."""
        try:
            self.session.call("stepOff", step="fix", opts={})
            self.rules = []
            if snap["rules"]:
                self.session.call("stepOn", step="fix", opts={"rules": snap["rules"]})
                self.rules = list(snap["rules"])
            for step in snap["steps"]:
                if step not in steps_on and step != "text":
                    self.session.call("stepOn", step=step, opts=dict(self.step_opts.get(step) or {}))
                    steps_on.append(step)
            if "text" in snap["steps"] and abs(snap["scale"] - self.scale) > 0.01:
                if snap["scale"] > 1.0:
                    self.session.call("stepOn", step="text", opts={"scale": snap["scale"], "goalPx": self.cfg["text_goal_px"]})
                else:
                    self.session.call("stepOff", step="text", opts={})
                self.scale = snap["scale"]
                if "text" not in steps_on:
                    steps_on.append("text")
            self.log.append("undid that round: it made things worse")
        except ToolError as e:
            self.log.append(f"couldn't undo the round: {e}")

    def run(self, steps_on: list[str], verify: dict | None) -> dict:
        """The loop. Returns {ok, fixes, problems, gave_up, log}. Never reverts by itself (the caller does).
        Raises Cancelled if a newer request on this tab took over."""
        result = {"ok": False, "fixes": 0, "problems": [], "gave_up": False, "log": self.log}
        best = None  # (problem count, snapshot, problems) of the best state seen so far
        for attempt in range(MAX_FIXES + 1):
            look = self.round(steps_on, verify)
            if look["ok"]:
                result["ok"] = True
                break
            count = len(look["problems"]) or 1
            actions = look["actions"]
            if best is not None and count > best[0]:
                self.restore(best[1], steps_on)  # this round made it worse: back to the best state
                look["problems"], actions = best[2], []
            else:
                best = (count, self.snapshot(steps_on), look["problems"])
            result["problems"] = look["problems"]
            if attempt == MAX_FIXES:
                result["gave_up"] = True
                break
            self._check_cancel()
            if actions and self.apply(actions, steps_on):
                result["fixes"] += 1
            verify = self.session.call("pageVerify", stickyBudget=self.cfg["sticky_budget"])
        return result
