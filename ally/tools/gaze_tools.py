"""Eye-tracker tools: where the user looks, magnify, calibrate, check, and choose by looking."""

from __future__ import annotations

import sys
from typing import Annotated, Literal

from ..gaze_helper import likely_cause, result_grade, result_lines
from ..registry import ToolError, tool


def _quality(result: dict) -> tuple[str, list[str]]:
    validation = result.get("validation") or {}
    grade = result.get("grade") or result_grade(validation)
    lines = result.get("lines")
    if not lines:
        cause = result.get("cause") or likely_cause(None)
        lines = result_lines(grade, validation, cause)
    return grade, [str(line) for line in lines if line]


def calibration_words(result: dict) -> str:
    """Plain result for a full setup, from the held-out validation numbers."""
    if not result.get("calibrated"):
        why = result.get("why", "")
        if why == "cancelled":
            return "Eye tracker setup cancelled."
        raise ToolError(f"Eye tracker setup didn't work: {why or 'try again'}")
    grade, lines = _quality(result)
    body = " ".join(lines)
    if grade == "poor":
        return f"The eye tracker is set up, but the check was poor. {body}"
    return f"The eye tracker is ready. {body}"


def check_words(result: dict) -> str:
    """Plain result for the 5-dot check. Same words as a full setup."""
    if not result.get("validation") and not result.get("lines"):
        why = result.get("why") or "The eye tracker needs a setup first."
        raise ToolError(str(why))
    grade, lines = _quality(result)
    body = " ".join(lines)
    if grade == "poor":
        return f"Eye tracker check was poor. {body}"
    return f"Eye tracker check. {body}"


def looking_text(info: dict, element: dict | None = None) -> str:
    """Region, the mouse fallback when there is one, and an optional control name."""
    region = info.get("region") or "middle"
    if info.get("source") == "mouse":
        lead = info.get("why") or "Using the mouse pointer."
        text = f"{lead} Region: {region}."
    else:
        text = f"Region: {region}."
    if not element:
        return text
    role = str(element.get("role") or "").strip()
    name = str(element.get("name") or "").strip()
    if role and name:
        return f"{text} {role[:1].upper()}{role[1:]}: {name}."
    if role:
        return f"{text} {role[:1].upper()}{role[1:]}."
    return text


def _element_at(info: dict) -> dict | None:
    if sys.platform != "win32":
        return None
    try:
        from .. import gaze_element

        return gaze_element.describe(int(info["x"]), int(info["y"]))
    except Exception:
        return None


@tool
def where_am_i_looking(ctx):
    """The area of the screen the user is looking at, and on Windows the control under that spot.

    Use for "what am I looking at", "what is this", "that", and "here" when they mean a place
    on the screen. Says so when the answer came from the mouse pointer instead of the eyes.
    """
    info = ctx.agent.gaze.target()
    return looking_text(info, _element_at(info))


@tool(timeout=20)
def magnify_where_i_look(ctx, level: Annotated[float, "Magnification, e.g. 2 = 200%. 0 = keep the current "
                                                      "level (2x if the magnifier is off)."] = 0):
    """Magnify the part of the screen the user is looking at, in any app, using the webcam eye tracker.

    Use for "magnify what I'm looking at", "zoom in where I'm looking", "make this bigger" when the
    user means the spot their eyes are on, or "magnify here". Afterwards the magnified view follows
    their eyes: looking near an edge pans that way.
    """
    looked = ctx.agent.gaze.target()
    x, y = int(looked["x"]), int(looked["y"])
    lvl, left, top = ctx.os.view()
    # The eye tracker sees where the eyes are on the glass; while zoomed in, that shows a smaller area.
    cx, cy = left + x / lvl, top + y / lvl
    info = getattr(ctx.agent.gaze, "last_point", None) or {}
    why = looked.get("why") or ""
    ctx.event(f"eyes at ({x}, {y}) on screen -> centering on ({cx:.0f}, {cy:.0f}); "
              f"source {looked.get('source', 'eyes')}"
              + (f"; {why}" if why else "")
              + f"; {info.get('samples', '?')} samples, steady within {info.get('spread_px', '?')} px",
              kind="gaze")
    level = float(level) if level and level > 1.0 else (lvl if lvl > 1.0 else max(2.0, ctx.os.last_magnify or 2.0))
    done = ctx.os.magnify_at(max(1.0, min(8.0, level)), cx, cy, follow="eyes")
    if looked.get("source") == "mouse" and why:
        return f"{done} {why}"
    return done


@tool(timeout=150)
def calibrate_eye_tracker(ctx):
    """Set up the eye tracker: about a minute of dots, then a check the user was not trained on.

    Use when the user asks to calibrate or set up eye tracking, or when "what I'm looking at"
    doesn't land where they look.
    """
    p = ctx.os
    level = p.view()[0]
    if level > 1.0 or p.other_magnifier_open():
        p.magnify(1.0)  # the dots must be shown unmagnified (this also closes Windows Magnifier)
    ctx.say("Eye tracker setup. Look at each dot. Move only your eyes, and keep your head still. This takes about a minute.")
    try:
        result = ctx.agent.gaze.calibrate()
    finally:
        if level > 1.0:
            p.magnify(level)
    return calibration_words(result)


@tool(timeout=60)
def check_eye_tracker(ctx):
    """Check eye-tracker accuracy with 5 dots (about 10 seconds), without a full setup.

    Use when the user asks to check the eye tracker. Updates the saved validation numbers.
    """
    p = ctx.os
    level = p.view()[0]
    if level > 1.0 or p.other_magnifier_open():
        p.magnify(1.0)
    ctx.say("Checking the eye tracker. Look at each dot.")
    try:
        result = ctx.agent.gaze.check()
    finally:
        if level > 1.0:
            p.magnify(level)
    return check_words(result)


@tool(timeout=60)
def choose_by_looking(ctx, options: Annotated[list[str], "Two to four short choices, such as Yes and No."],
                      timeout: Annotated[float, "Seconds to wait for a look."] = 8):
    """Show each choice as a large card and return the one the user looks at for a moment.

    Use when the user can answer yes or no by looking, or pick among up to four options.
    Two choices use the left and right halves of the screen. Says none if they don't settle.
    """
    if not isinstance(options, (list, tuple)) or not 2 <= len(options) <= 4:
        raise ToolError("Give two, three, or four choices.")
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        timeout = 8.0
    timeout = min(30.0, max(1.0, timeout))
    ctx.say("Look at your choice.")
    choice = ctx.agent.gaze.choose(list(options), timeout=timeout)
    if not choice:
        return "No choice. They didn't look at one for long enough."
    return f"They looked at {choice}."


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
