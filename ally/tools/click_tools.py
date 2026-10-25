"""Click by voice or by number on the current web page (docs/click_by_voice.md)."""

from __future__ import annotations

import logging
import re
import time
from typing import Annotated

from ..pagekit import clicks
from ..pagekit import tabs as tabkit
from ..pagekit.core import PageSession
from ..registry import ToolError, tool

log = logging.getLogger("ally.tools.clicks")
NAV_WORDS = {"back": "back", "go back": "back", "previous page": "back", "forward": "forward",
             "reload": "reload", "refresh": "reload", "reload the page": "reload", "refresh the page": "reload"}
NAV_SAID = {"back": "went back", "forward": "went forward", "reload": "reloaded the page"}


def _call(session: PageSession, action: str, **args):
    try:
        return session.call(action, **args)
    except ToolError as e:
        if "Unknown action" in str(e) or "isn't loaded" in str(e):
            raise ToolError("This version of the Ally extension can't click for you yet. "
                            "Reload it at chrome://extensions.") from e
        raise


def _confirm(ctx, question: str) -> bool | None:
    """True / False from a spoken (or on-screen) yes/no; None when Ally can't ask out loud. The deadline stays
    inside the tool's time limit, so a late "yes" can't click after Ally already gave up."""
    vc = getattr(getattr(ctx, "agent", None), "confirm", None)
    if vc is None or not hasattr(vc, "ask"):
        return None
    ans = vc.ask(question, kind="question", deadline=time.time() + 25)
    return getattr(ans, "value", ans) == "yes"


def _do_click(ctx, session: PageSession, label: str, href: str, text: str = "", **target) -> str:
    kind = clicks.careful(f"{label} | {text}", href)
    question = None
    if kind:
        question = f"That {kind}. Click {clicks.short_label(label, 40) or 'it'}?"
    elif not label.strip():
        kind, question = "has no name", "I can't tell what that button does. Click it anyway?"
    if question:
        ok = _confirm(ctx, question)
        if ok is None:
            return f"That {kind}, so please click {clicks.short_label(label) or 'it'} yourself."
        if not ok:
            return "OK, I didn't click it."
    out = _call(session, "clickDo", **target) or {}
    name = clicks.short_label(out.get("label") or label) or "it"
    if out.get("kind") == "focus":
        return f"I put the cursor in {name}. Start typing."
    return f"Clicked {name}."


@tool(timeout=15)
def show_numbers(ctx, on: bool = True):
    """Show a number on every button, link and box on the current web page, so the user can say "click 7".

    Use for "show numbers", "number the buttons", "what can I click". on=false hides them.
    """
    session = PageSession(ctx)
    if not on:
        out = _call(session, "stepOff", step="clicks", opts={}) or {}
        return "Numbers are off." if out.get("said") else "No numbers are showing."
    out = _call(session, "clickShow") or {}
    n = len(out.get("items") or [])
    if not n:
        return "I don't see anything to click on the screen."
    return f"Showing {n} numbers. Say click and a number, like click 1."


@tool(timeout=40)
def click_number(ctx, n: Annotated[int, "The number on the badge, 1 to 99."]):
    """Click the button or link with this number (after show_numbers). Also use it when the user answers
    "Which number?" with just a number."""
    session = PageSession(ctx)
    state = _call(session, "clickState") or {}
    if not state.get("shown"):
        return "Say show numbers first, then click a number."
    target = next((i for i in state.get("items") or [] if int(i.get("n", 0)) == int(n)), None)
    if target is None:
        return f"There's no number {int(n)} on the screen."
    return _do_click(ctx, session, str(target.get("label", "")), str(target.get("href") or ""),
                     str(target.get("text") or ""), n=int(n))


@tool(timeout=40)
def click_text(ctx, label: Annotated[str, "The words on the button or link, e.g. 'sign in'."]):
    """Click the button or link on the current web page whose words match what the user said.

    Use for "click sign in", "press continue", "tap the search button". If several match, shows numbers on
    just those and asks which. "click back" / "click reload" fall back to the browser's own buttons.
    """
    session = PageSession(ctx)
    listed = _call(session, "clickList") or {}
    found = clicks.match(label, listed.get("items") or [], listed.get("view") or {})
    items = found["items"]
    if not items:
        nav = NAV_WORDS.get(" ".join(clicks.label_tokens(label)))
        if nav:
            ctx.browser.call("navigate", action=nav)
            return f"There's no {label.strip()} button here, so I {NAV_SAID[nav]}."
        return f"I don't see {label.strip()} on the screen. Say show numbers to see what you can click."
    if len(items) > 1:
        _call(session, "clickShow", only=[i["id"] for i in items])
        return f"I see {len(items)} called {clicks.short_label(items[0].get('label', ''), 40)}. Which number?"
    best = items[0]
    return _do_click(ctx, session, str(best.get("label", "")), str(best.get("href") or ""),
                     str(best.get("text") or ""), id=best["id"], gen=listed.get("gen") or 0)


def _find(session: PageSession, target: str, prefer: tuple[str, ...], shortest: bool = True) -> tuple[dict, list[dict]]:
    found = _call(session, "highlightFind") or {}
    items = found.get("items") or []
    for kinds in (prefer, ("control", "area", "section")):
        pool = [i for i in items if i.get("kind") in kinds]
        best = clicks.match(target, pool, found.get("view") or {}, shortest=shortest)["items"]
        if best:
            return found, best
    return found, []


@tool(timeout=20)
def show_where(ctx, target: Annotated[str, "What the user wants to click, e.g. 'sign in' or 'the search box'. "
                                           "Empty = show numbers on everything."] = ""):
    """Show the user where to click: a yellow box and arrow around the button, link or box they asked about.

    Use for "where do I click to sign in", "where is the search box", "show me the cart button". Nothing is
    clicked. With no target, shows numbers on everything clickable.
    """
    if not target.strip():
        return show_numbers(ctx)
    session = PageSession(ctx)
    found, best = _find(session, target, ("control",))
    log.info("show_where %r -> %d of %d (%s)", target, len(best), len(found.get("items") or []),
             [b.get("label") for b in best[:3]])
    if not best:
        return f"I don't see {target.strip()} on this page. Say show numbers to see what you can click."
    best = best[:5]
    label = clicks.short_label(best[0].get("label", ""), 40)
    shown = _call(session, "highlightShow", ids=[b["id"] for b in best], gen=found.get("gen") or 0, mode="point",
                  note=f"Click here: {label}") or {}
    boxes = shown.get("boxes") or [best[0]]
    place = tabkit.where(boxes[0], shown.get("view") or found.get("view") or {})
    if len(best) > 1:
        return f"I see {len(best)} called {label}. I put yellow boxes around them."
    return f"It's {place}. I put a yellow box around it. Say click {label.lower()} to click it."


@tool(timeout=20)
def highlight_area(ctx, target: Annotated[str, "The part of the page, e.g. 'main text', 'menu', 'the section "
                                                "about history'."]):
    """Highlight one part of the current web page: the rest is dimmed and the part outlined.

    Use for "highlight the main text", "highlight the menu", "highlight the section about prices".
    """
    session = PageSession(ctx)
    # "the section about history" -> "history": headings rarely say "section"
    target = re.sub(r"^(?:the )?(?:section|part|bit|area|paragraph)s? (?:about|on|with|called|for|of) ", "",
                    target.strip(), flags=re.I) or target
    found, best = _find(session, target, ("area", "section"), shortest=False)
    # Several menus or areas match ("menu" on Wikipedia: Main, Contents, Appearance...): the site's main one.
    best.sort(key=lambda b: not re.search(r"\b(main|primary|site|global)\b", str(b.get("label", "")), re.I))
    log.info("highlight_area %r -> %s", target, [(b.get("kind"), b.get("label")) for b in best[:3]])
    if not best:
        return f"I don't see {target.strip()} on this page."
    shown = _call(session, "highlightShow", ids=[best[0]["id"]], gen=found.get("gen") or 0, mode="spotlight") or {}
    if not shown.get("shown"):
        return f"I couldn't highlight {target.strip()}."
    return f"Highlighted {clicks.short_label(best[0].get('label', ''), 40)}. Say stop highlighting to clear it."


@tool(timeout=10)
def clear_highlight(ctx):
    """Remove the yellow boxes or spotlight from show_where and highlight_area."""
    out = _call(PageSession(ctx), "highlightClear") or {}
    log.info("clear_highlight -> %s", out.get("said"))
    return "Cleared the highlight." if out.get("said") else "Nothing is highlighted."


__all__ = ["show_numbers", "click_number", "click_text", "show_where", "highlight_area", "clear_highlight"]
