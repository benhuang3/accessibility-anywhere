"""Eye-tracker tools: magnify where the user looks, calibrate, and choose what the magnifier follows."""

from __future__ import annotations

from typing import Annotated, Literal

from ..registry import ToolError, tool


@tool(timeout=20)
def magnify_where_i_look(ctx, level: Annotated[float, "Magnification, e.g. 2 = 200%. 0 = keep the current "
                                                      "level (2x if the magnifier is off)."] = 0):
    """Magnify the part of the screen the user is looking at, in any app, using the webcam eye tracker.

    Use for "magnify what I'm looking at", "zoom in where I'm looking", "make this bigger" when the
    user means the spot their eyes are on, or "magnify here". Afterwards the magnified view follows
    their eyes: looking near an edge pans that way.
    """
    x, y = ctx.agent.gaze.point_for_request()
    lvl, left, top = ctx.os.view()
    # The eye tracker sees where the eyes are on the glass; while zoomed in, that shows a smaller area.
    cx, cy = left + x / lvl, top + y / lvl
    info = getattr(ctx.agent.gaze, "last_point", None) or {}
    ctx.event(f"eyes at ({x}, {y}) on screen -> centering on ({cx:.0f}, {cy:.0f}); "
              f"{info.get('samples', '?')} samples, steady within {info.get('spread_px', '?')} px", kind="gaze")
    level = float(level) if level and level > 1.0 else (lvl if lvl > 1.0 else max(2.0, ctx.os.last_magnify or 2.0))
    return ctx.os.magnify_at(max(1.0, min(8.0, level)), cx, cy, follow="eyes")


@tool(timeout=150)
def calibrate_eye_tracker(ctx):
    """Set up the eye tracker: dots appear on the screen and the user looks at each one (about 20 seconds).

    Use when the user asks to calibrate or set up eye tracking, or when "what I'm looking at"
    doesn't land where they look.
    """
    p = ctx.os
    level = p.view()[0]
    if level > 1.0 or p.other_magnifier_open():
        p.magnify(1.0)  # the dots must be shown unmagnified (this also closes Windows Magnifier)
    ctx.say("Eye tracker setup. Look at each dot until it moves.")
    try:
        result = ctx.agent.gaze.calibrate()
    finally:
        if level > 1.0:
            p.magnify(level)
    if not result.get("calibrated"):
        why = result.get("why", "")
        if why == "cancelled":
            return "Eye tracker setup cancelled."
        raise ToolError(f"Eye tracker setup didn't work: {why}")
    err = int(result.get("error_px") or 0)
    quality = "good" if err < 120 else ("okay" if err < 220 else "rough; more light on your face helps")
    return f"The eye tracker is ready. Accuracy is {quality}."


@tool
def eye_tracking(ctx, on: bool):
    """Turn the webcam eye tracker on or off (off = the camera is not used). Lasts until Ally restarts."""
    ctx.agent.gaze.set_enabled(bool(on))
    if on:
        return "Eye tracking is on."
    if ctx.agent.gaze.camera_wanted():
        return "Eye tracking is off. The camera is still used for face sensing; say: turn off the camera."
    return "Eye tracking is off. The camera isn't being used."


@tool
def magnifier_follow(ctx, target: Literal["eyes", "mouse"]):
    """Choose what the screen magnifier follows: the user's eyes (look near an edge to pan) or the mouse."""
    if target == "eyes" and ctx.agent.gaze.status() != "tracking":
        raise ctx.agent.gaze.problem()
    return ctx.os.set_follow(target)
