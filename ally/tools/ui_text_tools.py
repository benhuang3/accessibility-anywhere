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


@tool
def enlarge_text(ctx, goal: Annotated[str, "'bigger' to magnify, or a replacement string to show instead"] = "bigger",
                 level: Annotated[float, "Magnification for native apps (2 = 200%). 0 = pick a sensible default."] = 0.0):
    """Make the text in the program the user is looking at bigger (any app), or replace it with clearer words.

    Use when the user says "make this bigger", "enlarge this", or wants the words in front of them
    swapped for something easier to read. Chrome gets a real in-page edit: goal="bigger" zooms the
    page, and any other goal rewrites the tab's title text in memory. Every other app is magnified
    on the element the user is working in (whole screen if that can't be found). Replacing the words
    in a non-Chrome app needs the reading-card overlay and isn't available on every system yet; if it
    isn't, this says so. Pass goal="bigger" to enlarge, or goal=<the words to show> to replace.
    After a rewrite or reading card, say restore_ui_text to undo it; say reset to undo zoom and
    magnifier too.
    """
    bigger = (goal or "bigger").strip().lower() == "bigger"

    if ctx.browser.connected and ctx.browser.focused:
        if bigger:
            z = ctx.browser.call("zoomStep", direction="in")
            pct = round((z or 1.0) * 100)
            _record(ctx, {"tier": "chrome-zoom"})
            return {"tier": "chrome-zoom", "detail": f"{pct}%", "message": f"Made the page bigger ({pct}%)."}
        got = ctx.browser.call("setTabTitle", title=goal)
        shown = (got or {}).get("title", goal)
        _record(ctx, {"tier": "chrome-rewrite", "selector": "title"})
        return {"tier": "chrome-rewrite", "detail": shown, "message": f'Showing "{shown}" instead.'}

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
    ctx.state.set("ui_text_edits", [])
    if problems and not done:
        raise ToolError(problems[0])
    return "Put the text back the way it was."
