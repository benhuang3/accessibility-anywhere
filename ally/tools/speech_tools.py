"""Speech tools: stop talking, change speed, voice input."""

from __future__ import annotations

from typing import Annotated

from ..registry import tool


@tool(ai=False)
def stop_speaking(ctx):
    """Stop reading out loud right away."""
    ctx.stop_speaking()
    return "Stopped."


@tool
def set_speech_rate(ctx, rate: Annotated[float, "0.5 = slow, 1.0 = normal, 2.0 = fast."]):
    """Change how fast Ally talks. The setting is kept after restarts."""
    r = max(0.3, min(3.0, float(rate)))
    ctx.agent.speaker.rate_override = r
    ctx.state.set("speech_rate", r)
    return f"Speech speed set to {r:g}."


@tool(ai=False, timeout=60)
def listen(ctx):
    """Listen for a spoken request (press the hotkey again to stop early)."""
    return {"message": ctx.agent.voice.toggle(), "silent": True}  # never talk over the microphone
