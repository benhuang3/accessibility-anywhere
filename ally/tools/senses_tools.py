"""Struggle-sensing tools: set up the face baseline, turn sensing on or off, the debug HUD, and
simulated signals for demos and for building the brain without a webcam."""

from __future__ import annotations

from typing import Annotated, Literal

from ..registry import ToolError, tool


@tool(timeout=60)
def calibrate_face_sensing(ctx):
    """Learn the user's resting face (about 15 seconds of reading normally) so squinting and leaning
    toward the screen can be noticed. Use for "set up face sensing" or when Ally offers help too often
    or never notices squinting."""
    secs = int(ctx.agent.config.data["senses"].get("calibrate_seconds", 15))
    ctx.say(f"Face setup. Read the screen normally for {secs} seconds, with a relaxed face.")
    result = ctx.agent.senses.calibrate(secs)
    if not result.get("ok"):
        raise ToolError(f"Face setup didn't work: {result.get('why', '')}")
    return "Face setup is done. I'll notice when you squint or lean in."


@tool
def struggle_sensing(ctx, on: bool, what: Literal["all", "camera", "mouse_keyboard"] = "all"):
    """Turn struggle sensing on or off: the camera (squint, lean) and/or mouse and keyboard
    (repeated clicks, retyping). Use for "stop watching me" or "turn off the camera". Lasts until Ally restarts."""
    s = ctx.agent.senses
    if what in ("all", "camera"):
        s.set_vision(on)
    if what in ("all", "mouse_keyboard"):
        s.set_input(on)
    part = {"all": "Struggle sensing", "camera": "Face sensing", "mouse_keyboard": "Mouse and keyboard sensing"}[what]
    if on:
        return f"{part} is on."
    camera_free = what != "mouse_keyboard" and not ctx.agent.gaze.camera_wanted()
    return f"{part} is off." + (" The camera isn't being used." if camera_free else "")


@tool(ai=False)
def senses_hud(ctx, on: bool = True):
    """Show or hide the live senses window: the camera picture with each signal, its baseline and the gates."""
    if on and ctx.agent.senses.vision_status() in ("off", "error"):
        raise ToolError("Face sensing isn't running, so there's nothing to show. Turn it on first.")
    ctx.agent.senses.set_hud(on)
    return "Senses window shown." if on else "Senses window hidden."


@tool(ai=False)
def simulate_struggle(ctx, kind: Annotated[str, "demo (a weak squint plus a weak lean), squint, lean, "
                                                "rage_click, hesitation or retype"] = "demo",
                      tier: Literal["strong", "weak"] = "strong"):
    """Pretend a struggle signal fired (no webcam needed). Two signals within a few seconds fuse into a
    struggle; "demo" sends both at once."""
    d = ctx.agent.senses.simulate(kind.strip().lower(), tier=tier)
    if d is None:
        return f"Simulated a {tier} {kind} signal (it needs a second signal to count as a struggle)."
    return f"Simulated struggle: {d['kind']}, confidence {d['confidence']:.2f}."
