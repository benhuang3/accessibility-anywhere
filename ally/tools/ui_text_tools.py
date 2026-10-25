"""Make the text of whatever program is in front bigger, or replace it with clearer words.

Chrome gets a real in-page edit (zoom, or the title/an element rewritten in memory). Every other
app is magnified on the part the user is looking at, with a big reading card as the fallback.
Every change is recorded so reset_all and restore_ui_text can put things back.
"""

from __future__ import annotations

from typing import Annotated

from ..registry import ToolError, tool


def _record(ctx, entry: dict) -> None:
    edits = list(ctx.state.get("ui_text_edits") or [])
    edits.append(entry)
    ctx.state.set("ui_text_edits", edits[-50:])


def undo_native_rewrites(ctx) -> list[str]:
    """Put back the text of native text boxes that enlarge_text rewrote, newest first, and forget the
    other edits (the caller undoes those). Returns the problems; edits that failed are kept so a later
    restore can try again. Used by restore_ui_text and reset_all."""
    failed, problems = [], []
    for e in reversed(list(ctx.state.get("ui_text_edits") or [])):
        if e.get("tier") == "native-rewrite" and not ctx.os.restore_text_at(e.get("bounds"), e.get("old", "")):
            failed.append(e)
    if failed:
        problems.append("Couldn't put back the text in the other program (is its window still open?).")
    ctx.state.set("ui_text_edits", list(reversed(failed)))
    return problems


def _enlarge_page(ctx, buttons: bool, direction: str = "up") -> dict | None:
    """The page steps in Chrome: bigger (or smaller) text, never zoom (+ bigger buttons), then verify. None
    if the extension can't (an older version)."""
    from ..pagekit.core import PageSession, run_steps, summary

    try:
        session = PageSession(ctx)
    except ToolError:
        return None
    # "Enlarge this" always means bigger than now: the predicted size, or one step up if that's reached.
    report = run_steps(ctx, ["text", "buttons"] if buttons else ["text"], session=session,
                       opts={"text": {"direction": direction}})
    _record(ctx, {"tier": "chrome-text", "scale": report.get("scale")})
    pct = round((report.get("scale") or 1.0) * 100)
    text = summary(report, "Made the text bigger." if direction == "up" else "Made the text smaller.")
    if report.get("offer_reader"):
        text += " Want just the article in big text?"
    return {"tier": "chrome-text", "detail": f"{pct}%", "message": text}


@tool(timeout=200)
def enlarge_text(ctx, goal: Annotated[str, "'bigger' or 'smaller', or a replacement string to show instead"] = "bigger",
                 level: Annotated[float, "Magnification for native apps (2 = 200%). 0 = pick a sensible default."] = 0.0,
                 buttons: Annotated[bool, "Also make the page's buttons bigger (Chrome)."] = False,
                 in_browser: Annotated[bool, "Treat Chrome as the target even if it isn't in front."] = False):
    """Make the text in the program the user is looking at bigger (any app), or replace it with clearer words.

    Use when the user says "make the text bigger", "make this bigger", "enlarge this", "too small", "I can't
    read this" (or "smaller" for goal="smaller"), or wants the words in front of them swapped for something
    easier to read. In Chrome this makes the page's own font sizes bigger, never zooms (only "zoom in"
    zooms): it measures the text, picks a size that reaches a comfortable size and checks the layout
    still works; asking again goes one step bigger, buttons=true also makes the buttons bigger. Any other
    goal rewrites the tab's title text in memory. Every other app is magnified
    on the element the user is working in (whole screen if that can't be found). Replacing the words
    in a non-Chrome app writes them into a short editable text box when possible (Windows), and
    otherwise shows them on a big reading card; if neither works here, this says so. Pass goal="bigger" to enlarge, or goal=<the words to show> to replace.
    After a rewrite or reading card, say restore_ui_text to undo it; say reset to undo zoom and
    magnifier too.
    """
    word = (goal or "bigger").strip().lower()
    bigger, smaller = word == "bigger", word == "smaller"

    if ctx.browser.connected and (ctx.browser.focused or in_browser):
        if bigger or smaller:
            smart = _enlarge_page(ctx, buttons, "up" if bigger else "down")
            if smart is not None:
                return smart
            raise ToolError("The Ally extension needs an update to change text sizes. Reload it at chrome://extensions.")
        got = ctx.browser.call("setTabTitle", title=goal)
        shown = (got or {}).get("title", goal)
        _record(ctx, {"tier": "chrome-rewrite", "selector": "title"})
        return {"tier": "chrome-rewrite", "detail": shown, "message": f'Showing "{shown}" instead.'}

    if smaller:  # outside Chrome the magnifier is the only way: one step down
        from ..platforms.base import next_level

        return {"tier": "screen-magnify", "detail": "smaller",
                "message": ctx.os.magnify(next_level(ctx.os.magnify_level, False))}
    if bigger:
        lvl = float(level) or 2.0
        box = ctx.os.read_focused_box()
        if box:
            x, y, w, h = box
            msg = ctx.os.magnify_at(lvl, x + w / 2, y + h / 2)
            _record(ctx, {"tier": "screen-magnify"})
            return {"tier": "screen-magnify", "detail": "focused", "message": msg}
        msg = ctx.os.magnify(lvl)
        _record(ctx, {"tier": "screen-magnify"})
        return {"tier": "screen-magnify", "detail": "whole-screen", "message": msg}

    native = ctx.os.replace_focused_text(goal)
    if native:
        _record(ctx, {"tier": "native-rewrite", "bounds": list(native["bounds"]), "old": native["old"]})
        return {"tier": "native-rewrite", "detail": goal,
                "message": f'Changed the text to "{goal}". Say restore to put it back.'}
    box = ctx.os.read_focused_box()
    msg = ctx.os.show_text_overlay(box, goal)
    _record(ctx, {"tier": "overlay"})
    return {"tier": "overlay", "detail": goal, "message": msg}


@tool
def restore_ui_text(ctx):
    """Undo text changes made by enlarge_text: put Chrome's title and rewritten text back, and clear reading cards.

    Use when the user wants the words back to normal but doesn't want to reset zoom or styles too.
    """
    done, problems = [], []
    if ctx.browser.connected:
        try:
            ctx.browser.call("restoreText")
            done.append("page text")
        except ToolError as e:
            problems.append(str(e))
    try:
        ctx.os.clear_text_overlays()
        done.append("reading cards")
    except ToolError as e:
        problems.append(str(e))
    problems += undo_native_rewrites(ctx)
    if problems and not done:
        raise ToolError(problems[0])
    return "Put the text back the way it was." if not problems else "Put back what I could. " + problems[0]
