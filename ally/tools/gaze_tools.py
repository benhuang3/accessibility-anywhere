"""Eye-tracker tools: where the user looks, magnify, calibrate, check, and choose by looking."""

from __future__ import annotations

import re
import sys
from typing import Annotated, Literal

from .. import gaze_model
from ..gaze_helper import (likely_cause, pose_count, result_grade, result_lines, retune_check,
                           retune_sentence, setup_duration)
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
    """Plain result for the 5-dot check, including how far off it was before and after re-tuning."""
    if not result.get("validation") and not result.get("lines"):
        why = result.get("why") or "The eye tracker needs a setup first."
        raise ToolError(str(why))
    grade, lines = _quality(result)
    body = " ".join(lines)
    sentence = retune_sentence(result.get("error_before"), result.get("error_after"))
    if sentence and sentence not in body:
        body = f"{body} {sentence}".strip()
    if grade == "poor":
        return f"Eye tracker check was poor. {body}"
    return f"Eye tracker check. {body}"


def _save_check_retune(ctx, result: dict) -> dict:
    """Re-tune the saved model from the 5 check dots and tell the camera helper to load it."""
    samples = result.get("samples")
    if not result.get("calibrated") or not isinstance(samples, list) or len(samples) < 3:
        out = dict(result)
        out.pop("samples", None)
        return out
    gaze = ctx.agent.gaze
    model = gaze_model.load(gaze.calibration_path, getattr(gaze, "screen", None))
    if not model:
        out = dict(result)
        out.pop("samples", None)
        return out
    tuned = retune_check(model, samples)
    try:
        gaze_model.save(tuned["model"], gaze.calibration_path)
    except OSError as e:
        raise ToolError(f"couldn't save the eye tracker check ({e})")
    gaze.send("reload")
    out = dict(result)
    out.pop("samples", None)
    out["error_before"] = tuned["error_before"]
    out["error_after"] = tuned["error_after"]
    out["validation"] = tuned["validation"]
    out["grade"] = tuned["grade"]
    cause = out.get("cause") or likely_cause(None)
    score = dict(tuned["validation"] or {})
    if tuned["error_after"] is not None:
        score["error_px"] = tuned["error_after"]
    lines = result_lines(tuned["grade"], score, cause)
    sentence = retune_sentence(tuned["error_before"], tuned["error_after"])
    if sentence:
        lines.append(sentence)
    out["lines"] = lines
    out["cause"] = cause
    return out


# Windows often reports these with no name for Chrome, Electron, and the taskbar. That is not a control.
_UNKNOWN_ROLES = {"client", "pane", "control"}


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
    if not name and role.lower() in _UNKNOWN_ROLES:
        return text
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


LOOK_ZOOM = 1.5  # "magnify what I'm looking at" when no level is named and the magnifier is off


@tool(timeout=20)
def magnify_where_i_look(ctx, level: Annotated[float, "Magnification, e.g. 2 = 200%. 0 = keep the current "
                                                      "level (1.5x if the magnifier is off)."] = 0):
    """Magnify the part of the screen the user is looking at, in any app, using the webcam eye tracker.

    Use for "magnify what I'm looking at", "zoom in where I'm looking", "make this bigger" when the
    user means the spot their eyes are on, "magnify here", or "help me read this". Afterwards the
    magnified view is a reading aid: as they read along a line it pans at their pace, looking back
    to the left after a line returns to where the line began, and looking near an edge pans that way.
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
    level = float(level) if level and level > 1.0 else (lvl if lvl > 1.0 else LOOK_ZOOM)
    done = ctx.os.magnify_at(max(1.0, min(8.0, level)), cx, cy, follow="eyes")
    if looked.get("source") == "mouse" and why:
        return f"{done} {why}"
    return done


@tool(timeout=210)
def calibrate_eye_tracker(ctx):
    """Set up the eye tracker: 9 dots, then sit back a little and lean in a little, then a check.

    Use when the user asks to calibrate or set up eye tracking, or when "what I'm looking at"
    doesn't land where they look. With calibration_poses = 1 it is the 9 dots only.
    About 45 seconds with the head-position rounds, about 30 seconds without them.
    """
    p = ctx.os
    level = p.view()[0]
    if level > 1.0 or p.other_magnifier_open():
        p.magnify(1.0)  # the dots must be shown unmagnified (this also closes Windows Magnifier)
    poses = pose_count(ctx.agent.config.data.get("gaze", {}).get("calibration_poses", 3))
    extra = "" if poses <= 1 else " Then sit back a little, and lean in a little."
    takes = setup_duration(poses).rstrip(".").lower()
    ctx.say("Eye tracker setup. Look at each dot. Move only your eyes, and keep your head still."
            f"{extra} This takes {takes}.")
    try:
        result = ctx.agent.gaze.calibrate(timeout=180 if poses >= 3 else 120)
    finally:
        if level > 1.0:
            p.magnify(level)
    return calibration_words(result)


@tool(timeout=60)
def check_eye_tracker(ctx):
    """Check eye-tracker accuracy with 5 dots (about 10 seconds), without a full setup.

    Use when the user asks to check the eye tracker, or after they have moved. Re-tunes the
    saved model from those dots and says the error before and after.
    """
    p = ctx.os
    level = p.view()[0]
    if level > 1.0 or p.other_magnifier_open():
        p.magnify(1.0)
    ctx.say("Checking the eye tracker. Look at each dot.")
    try:
        result = dict(ctx.agent.gaze.check() or {})
    finally:
        if level > 1.0:
            p.magnify(level)
    return check_words(_save_check_retune(ctx, result))


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
    """Choose what the screen magnifier follows: the user's eyes (it moves along as they read; look near an edge to pan) or the mouse."""
    if target == "eyes" and ctx.agent.gaze.status() != "tracking":
        raise ctx.agent.gaze.problem()
    return ctx.os.set_follow(target)


# Named speeds. "medium" is the calm default. slower/faster step from whatever is saved now.
PAN_SPEEDS = {"slow": 0.4, "medium": 0.85, "fast": 1.5}
PAN_MIN, PAN_MAX = 0.1, 2.0


def resolve_pan_speed(speed, current: float) -> float:
    """slower, faster, slow, medium, fast, or a number from 0.1 to 2.0."""
    if isinstance(speed, str):
        word = speed.strip().lower()
        if word in PAN_SPEEDS:
            return PAN_SPEEDS[word]
        if word == "slower":
            return max(PAN_MIN, round(float(current) - 0.15, 2))
        if word == "faster":
            return min(PAN_MAX, round(float(current) + 0.15, 2))
        try:
            speed = float(word)
        except ValueError:
            raise ToolError("Say slower, faster, slow, medium, fast, or a number from 0.1 to 2.")
    try:
        value = float(speed)
    except (TypeError, ValueError):
        raise ToolError("Say slower, faster, slow, medium, fast, or a number from 0.1 to 2.")
    if isinstance(speed, bool) or not PAN_MIN <= value <= PAN_MAX:
        raise ToolError("Eye-follow speed must be from 0.1 to 2.")
    return round(value, 2)


def replace_toml_key(text: str, section: str, key: str, literal: str) -> str:
    """Replace `key = ...` inside [section]. The comment on that line, and every other line, stay.

    The key is added at the end of the section when it is missing.
    """
    lines = text.splitlines(keepends=True)
    header = f"[{section}]"
    start = None
    for i, line in enumerate(lines):
        if line.strip() == header:
            start = i + 1
            break
    if start is None:
        extra = "" if (not text or text.endswith("\n")) else "\n"
        return text + extra + f"[{section}]\n{key} = {literal}\n"
    pattern = re.compile(rf"^(\s*){re.escape(key)}\s*=")
    end = len(lines)
    for i in range(start, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            end = i
            break
        raw = lines[i].rstrip("\r\n")
        code, hash_mark, comment = raw.partition("#")
        if not pattern.match(code):
            continue
        newline = lines[i][len(raw):]
        body = f"{pattern.match(code).group(1)}{key} = {literal}"
        if hash_mark:
            gap = code[len(code.rstrip()):] or " "
            lines[i] = f"{body}{gap}#{comment}{newline}"
        else:
            lines[i] = f"{body}{newline}"
        return "".join(lines)
    lines.insert(end, f"{key} = {literal}\n")
    return "".join(lines)


def _save_gaze_key(ctx, key: str, literal: str) -> None:
    """Write one [gaze] value back to config.toml, then reload it."""
    cm = ctx.agent.config
    text = cm.read_text()
    updated = replace_toml_key(text, "gaze", key, literal)
    save = getattr(ctx.agent, "save_config", None)
    if save is not None:
        ok, errors, _warnings = save(updated)
    else:
        ok, errors, _warnings = cm.save_text(updated)
    if not ok:
        raise ToolError("Couldn't save that setting: " + "; ".join(errors))


def _fmt_speed(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text if "." in text else text + ".0"


@tool
def eye_follow_speed(ctx, speed: Annotated[str, "slower, faster, slow, medium, fast, or a number from 0.1 to 2.0."]):
    """Set how fast the magnified view pans when the user looks toward an edge, and how eagerly it keeps up while they read.

    Use for "follow my eyes slower", "follow my eyes faster", or when they name a speed.
    Applies immediately and is saved, so it stays after Ally restarts.
    """
    current = float(ctx.agent.config.data.get("gaze", {}).get("pan_speed", 0.85))
    value = resolve_pan_speed(speed, current)
    _save_gaze_key(ctx, "pan_speed", _fmt_speed(value))
    platform = getattr(ctx.agent, "platform", None)
    if platform is not None:
        platform.set_gaze_source(ctx.agent.gaze.latest, value)
    if isinstance(speed, str) and speed.strip().lower() == "slower":
        return f"The view will follow your eyes more slowly ({_fmt_speed(value)})."
    if isinstance(speed, str) and speed.strip().lower() == "faster":
        return f"The view will follow your eyes a little faster ({_fmt_speed(value)})."
    return f"The view will follow your eyes at speed {_fmt_speed(value)}."


@tool
def show_gaze(ctx, on: bool):
    """Show or hide the dot that marks where the eye tracker thinks the user is looking.

    Use for "show where I am looking" and "hide the eye dot". The dot is click-through.
    It is drawn by the camera helper on each gaze point, and the choice is saved.
    """
    _save_gaze_key(ctx, "show_dot", "true" if on else "false")
    ctx.agent.gaze.set_show_dot(bool(on))
    if on:
        return "The eye dot is on. It sits where the tracker thinks you are looking."
    return "The eye dot is hidden."
