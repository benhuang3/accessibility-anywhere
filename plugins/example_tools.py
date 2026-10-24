"""Example plug-in. Copy this file to add your own tools.

Every .py file in this folder is loaded at start and reloaded the moment you save it.
A broken file never stops Ally: the error shows in the dashboard and the old version keeps working.

How a tool works:
- The function name is the tool name. The docstring is what Claude reads to decide when to use it,
  so say plainly what it does and when to use it.
- Type hints become the input schema. Use Annotated[type, "description"] to explain a parameter,
  and Literal["a", "b"] for a fixed set of choices. Parameters with defaults are optional.
- The first parameter `ctx` (optional) gives you Ally's helpers:
    ctx.browser.call("getText") ...  ctx.os.magnify(2) ...  ctx.say("hi")  ctx.ask("question")
    ctx.run("other_tool", arg=1)     ctx.state.get/set(...)  ctx.config[...]  ctx.log.info(...)
- Return a short sentence (it is spoken to the user) or a dict. Raise ToolError("...") for problems.
- @tool(ai=False) hides a tool from Claude; hotkeys, commands and the dashboard can still run it.
"""

from datetime import datetime
from typing import Annotated, Literal

from ally import ToolError, tool


@tool
def what_time_is_it(ctx):
    """Tell the user the current time and date. Use when they ask what time or day it is."""
    now = datetime.now()
    hour = now.strftime("%I").lstrip("0") or "12"  # portable: %-I doesn't work on Windows
    return f"It's {hour}:{now:%M %p} on {now:%A, %B} {now.day}."


@tool
def night_mode(ctx, on: bool = True):
    """Make the screen easier on the eyes at night: dark web pages plus a grayscale screen filter.

    Use when the user says the screen is too bright or it's late at night.
    """
    if on:
        ctx.run("restyle_page", style="dark")
        result = ctx.run("color_filter", kind="grayscale")
        extra = "" if result.ok else " (Screen filter not available here: " + result.error + ")"
        return "Night mode on." + extra
    ctx.run("restyle_page", style="dark", on=False)
    ctx.run("color_filter", kind="none")
    return "Night mode off."


@tool
def spell_out(ctx, text: Annotated[str, "The word or short text to spell letter by letter."],
              speed: Literal["slow", "normal"] = "slow"):
    """Spell a word out loud letter by letter. Useful for names, codes and passwords on screen."""
    letters = [c for c in text.strip() if not c.isspace()]
    if not letters:
        raise ToolError("Give me something to spell.")
    sep = ", " if speed == "normal" else ". "
    ctx.say(sep.join(letters))
    return f"Spelling {text.strip()[:40]}."
