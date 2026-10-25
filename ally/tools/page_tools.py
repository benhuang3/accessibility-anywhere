"""Web page steps: hide clutter, simplify menus, bigger buttons, check the layout, and Simplified mode.

Each tool is one step from docs/page_steps.md; they work alone and together. The extension does the page
work; ally/pagekit/core.py decides the order, checks the result and undoes what broke the page.
"""

from __future__ import annotations

from typing import Annotated, Literal

from ..pagekit.core import CLUTTER_KINDS, STEP_ORDER, PageSession, join_said, run_steps, steps_for, summary
from ..registry import ToolError, tool

ClutterKind = Literal["cookie", "promo", "chat", "ads", "share", "motion"]
StepName = Literal["clutter", "menus", "text", "buttons"]


def _say_first(ctx, text: str) -> None:
    say = getattr(ctx, "say", None)
    if callable(say):
        try:
            say(text)
        except Exception:
            pass


@tool(timeout=150)
def hide_clutter(ctx, kinds: Annotated[list[ClutterKind] | None, "Which kinds to hide; leave out for all."] = None,
                 on: bool = True):
    """Hide clutter on the current web page: cookie banners, sign-up pop-ups, chat bubbles, ads, share bars, animation.

    Use when the user says there are too many pop-ups, ads or things in the way, or asks to clean up the page.
    Never clicks Accept or Reject and never hides the page's own important messages. on=false shows them again.
    """
    kinds = list(kinds or CLUTTER_KINDS)
    bad = [k for k in kinds if k not in CLUTTER_KINDS]
    if bad:
        raise ToolError(f"Unknown kind: {', '.join(bad)}. Choose from: {', '.join(CLUTTER_KINDS)}.")
    report = run_steps(ctx, ["clutter"], on=on, opts={"clutter": {"kinds": kinds}}, verify=False)
    if on and not join_said(report["said"]):
        return "I didn't find any pop-ups or ads to hide on this page."
    return summary(report, "Showing everything again." if not on else "Done.")


@tool(timeout=40)
def simplify_menus(ctx, on: bool = True):
    """Show the website's menu as one big, simple list, and make small dropdown lists easier to choose from.

    Use when the user can't use a menu, the menu keeps closing, or they can't find where to go on a site.
    The site itself isn't changed. on=false closes it.
    """
    report = run_steps(ctx, ["menus"], on=on, opts={"menus": {"mode": "panel"}}, verify=False)
    return summary(report, "Opened the menu." if on else "Closed the menu.")


@tool(timeout=150)
def enlarge_buttons(ctx, on: bool = True):
    """Make buttons, links and form boxes on the current web page bigger and easier to click, without zooming.

    Use when the user keeps missing buttons, has trouble clicking or tapping, or says the buttons are too small.
    Checks the page still looks right and undoes it if not. on=false puts them back.
    """
    report = run_steps(ctx, ["buttons"], on=on)
    if on and "buttons" in report["backed_off"]:
        return "Bigger buttons would break this page, so I left them as they were."
    return summary(report, "Made the buttons bigger." if on else "Buttons are back to normal.")


@tool(timeout=30)
def check_page_layout(ctx):
    """Check whether the current web page still looks right after making it bigger or simpler.

    Use when the user asks if the page looks right, or something seems cut off or covered. Changes nothing.
    """
    session = PageSession(ctx)
    v = session.call("pageVerify")
    if v.get("ok"):
        return "The page looks fine: nothing is cut off or covered."
    c = v.get("checks") or {}
    problems = []
    if not c.get("overflow", {}).get("ok", True):
        problems.append("part of the page goes off the side")
    if not c.get("clipped", {}).get("ok", True):
        problems.append("some text is cut off")
    if not c.get("sticky", {}).get("ok", True):
        problems.append("bars at the edges cover too much of the screen")
    if not c.get("covered", {}).get("ok", True):
        problems.append("some buttons are covered")
    return f"Something's off: {join_said(problems)}. Say reset to put the page back."


@tool(timeout=240)
def make_page_easier(ctx, steps: Annotated[list[StepName], "Steps to run: clutter, menus, text, buttons."],
                     on: bool = True):
    """Run several page steps together: hide clutter, simplify menus, bigger text, bigger buttons.

    Use when the user asks for more than one of these at once. Runs them in a safe order, checks the
    page still looks right, and undoes the last change if it broke something. on=false turns them off.
    """
    report = run_steps(ctx, steps, on=on)
    return summary(report)


@tool(timeout=240)
def simplified_mode(ctx, on: bool = True,
                    remember: Annotated[bool, "Only after the user said yes to doing this every time on this site."] = False,
                    everywhere: Annotated[bool, "With on=false: turn simple mode off on every site."] = False):
    """Make the current website simpler: hide clutter, a big menu, bigger text and buttons, chosen for the page.

    Use when the user says a site is too busy, confusing or hard to use, or asks for simple mode. Pages that
    handle passwords or payments are left alone. Ask before remember=true ("Do this every time on this site?").
    on=false turns it off (everywhere=true for all sites).
    """
    if not on:
        session = PageSession(ctx)
        session.call("stepOff", step="simple", opts={"forget": bool(remember or everywhere)})
        if everywhere:
            session.call("simpleForgetAll")
            return "Simple mode is off on every site."
        return "Simple mode is off. The page is back to normal."
    session = PageSession(ctx)
    info = session.info
    if info.get("type") == "excluded":
        why = (info.get("reasons") or ["this page shouldn't be changed"])[0]
        return f"I won't simplify this page because {why}. Say 'make it bigger' if you just want it bigger."
    plan = steps_for(info.get("type", "general"))
    _say_first(ctx, "One moment, I'm making this page simpler.")
    session.call("stepOn", step="simple", opts={"working": True})
    try:
        # Articles go to the reader view, which replaces what's on screen, so there's nothing to check by eye.
        report = run_steps(ctx, plan["steps"], opts=plan["opts"], session=session, review=False if plan["reader"] else None,
                           after_revert=lambda: session.call("stepOff", step="simple", opts={}))
        if report.get("reverted"):
            return summary(report)
        if plan["reader"]:
            session.call("stepOn", step="reader", opts={"withStrip": "menus" in plan["steps"]})
            report["said"].append("opened the article in big, clear text")
    finally:
        session.call("stepOn", step="simple", opts={"working": False})
    applied = report["applied"] + (["reader"] if plan["reader"] else [])
    session.call("stepOn", step="simple", opts={"steps": applied, "remember": bool(remember),
                                                "scale": report.get("scale") or 0})
    text = summary(report, "Simple mode is on.")
    if report.get("offer_reader") and not plan["reader"]:
        text += " This page doesn't get much bigger without breaking. Want just the article in big text?"
    tail = " I'll do this every time on this site." if remember else ""
    return f"Simple mode is on. {text}{tail} Say 'simple mode off' to undo."


@tool(timeout=40)
def open_reader(ctx, on: bool = True):
    """Show just the article on the current page in big, clear text, keeping headings, links and lists.

    Use for long articles or news stories that are hard to read. on=false closes it.
    """
    session = PageSession(ctx)
    session.call("stepOn" if on else "stepOff", step="reader", opts={})
    return "Showing the article in big, clear text. Press Escape or say close to go back." if on else "Closed the article view."


__all__ = ["hide_clutter", "simplify_menus", "enlarge_buttons", "check_page_layout", "make_page_easier",
           "simplified_mode", "open_reader", "STEP_ORDER"]
