"""Ally: an always-on, OS-level accessibility agent.

Plug-in authors only need this:

    from ally import tool, ToolError

    @tool
    def my_tool(ctx, size: float = 2.0):
        "What the tool does, in plain words. Claude reads this."
        ...
"""

__version__ = "1.0.0"

from .registry import ImageResult, ToolError, tool  # noqa: E402,F401

__all__ = ["tool", "ToolError", "ImageResult", "__version__"]
