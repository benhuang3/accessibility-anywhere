"""Cross-cutting tools: smart zoom, reset (panic button), preferences, help."""

from __future__ import annotations

import webbrowser
from typing import Annotated

from ..platforms.base import next_level
from ..registry import ToolError, tool


def zoom_target(ctx) -> str:
    mode = ctx.config["agent"].get("zoom_target", "auto")
    if mode in ("page", "screen"):
        return mode
    return "page" if (ctx.browser.connected and ctx.browser.focused) else "screen"


@tool(ai=False)
def zoom_in(ctx):
    """Make things bigger one step: the web page if Chrome is in front, otherwise the whole screen."""
    if zoom_target(ctx) == "page":
        z = ctx.browser.call("zoomStep", direction="in")
        return f"Page zoom {round(z * 100)}%."
    return ctx.os.magnify(next_level(ctx.os.magnify_level, True))


@tool(ai=False)
def zoom_out(ctx):
    """Make things smaller one step: the web page if Chrome is in front, otherwise the whole screen."""
    if zoom_target(ctx) == "page":
        z = ctx.browser.call("zoomStep", direction="out")
        return f"Page zoom {round(z * 100)}%."
    return ctx.os.magnify(next_level(ctx.os.magnify_level, False))


@tool(ai=False)
def zoom_to(ctx, level: float):
    """Set zoom to an exact level (1.5 = 150%) on the page or the screen, whichever is in front."""
    if zoom_target(ctx) == "page":
        z = ctx.browser.call("setZoom", factor=max(0.25, min(5.0, float(level))))
        return f"Page zoom {round(z * 100)}%."
    return ctx.os.magnify(max(1.0, float(level)))


@tool
def reset_all(ctx):
    """Undo every visual change: page zoom, page styles, reader view, screen magnifier, color filters.

    This is the panic button. Use it when the user says reset, undo everything, or back to normal.
    Saved settings in the user's profile apply again to pages opened later.
    """
    done, problems = [], []
    ctx.stop_speaking()
    if ctx.browser.connected:
        try:
            ctx.browser.call("resetPage")
            done.append("page")
        except ToolError as e:
            problems.append(str(e))
        try:
            ctx.browser.call("restoreText")
        except ToolError as e:
            problems.append(str(e))
    p = ctx.os
    try:
        p.clear_text_overlays()
    except ToolError as e:
        problems.append(str(e))
    try:
        if p.magnify_level > 1.0:
            p.magnify(1.0)
            done.append("magnifier")
        if p.color != "none":
            p.color_filter("none")
            done.append("colors")
        if abs(p.text_scale_factor - 1.0) > 1e-3:
            p.text_scale(1.0)
            done.append("text size")
    except ToolError as e:
        problems.append(str(e))
    if problems and not done:
        raise ToolError("Couldn't reset: " + problems[0])
    return "Everything is back to normal." if not problems else "Reset what I could. " + problems[0]


@tool
def apply_profile(ctx):
    """Re-apply the user's saved profile from config.toml (zoom, minimum text size, styles, magnifier)."""
    return ctx.agent.apply_profile(force=True)


@tool
def remember_preference(ctx, note: Annotated[str, "A short preference in plain words, e.g. 'likes 150% zoom on news sites'."]):
    """Remember something about how the user likes things, for future requests.

    Use when the user says "always...", "I prefer...", or "remember that...". The note is shown
    to you with every later request. Keep it short.
    """
    note = " ".join(note.split())[:200]
    if not note:
        raise ToolError("The note is empty.")
    prefs = list(ctx.state.get("preferences") or [])
    if note not in prefs:
        prefs.append(note)
    ctx.state.set("preferences", prefs[-30:])
    return f"I'll remember: {note}"


@tool
def forget_preferences(ctx):
    """Forget every preference saved with remember_preference."""
    ctx.state.set("preferences", [])
    return "I forgot your saved preferences."


@tool(ai=False)
def open_dashboard(ctx):
    """Open Ally's control panel in the web browser."""
    port = ctx.config["agent"]["port"]
    url = f"http://127.0.0.1:{port}/"
    if ctx.browser.connected:
        try:
            ctx.browser.call("openUrl", url=url, newTab=True)
            return "Opened the Ally dashboard."
        except ToolError:
            pass
    webbrowser.open(url)
    return "Opened the Ally dashboard."


@tool(ai=False)
def show_help(ctx):
    """Say what Ally can do and list the keyboard shortcuts."""
    text = ctx.agent.help_text()
    ctx.say(text)
    return text
