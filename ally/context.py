"""The `ctx` object every tool receives. It is the plug-in API:

    ctx.config      current settings (dict, read-only)
    ctx.state       persistent key/value store: ctx.state.get(k) / ctx.state.set(k, v)
    ctx.browser     call(action, **args) into Chrome; .connected, .focused, .tab
    ctx.os          the OS adapter (magnify, color_filter, screenshot_png, open, notify, ...)
    ctx.say(text)   speak out loud          ctx.stop_speaking()
    ctx.ask(prompt, image=None) -> str      one-off question to Claude (no tools)
    ctx.run(tool_name, **args) -> Outcome   call another tool
    ctx.log         a logging.Logger
"""

from __future__ import annotations

import logging


class BrowserAPI:
    def __init__(self, bridge):
        self._bridge = bridge

    @property
    def connected(self) -> bool:
        return self._bridge.connected

    @property
    def focused(self) -> bool:
        return self._bridge.chrome_focused

    @property
    def tab(self) -> dict:
        return self._bridge.tab

    def call(self, action: str, /, timeout: float = 8.0, **args):
        # Positional-only: some extension actions take an argument named "action" (navigate).
        return self._bridge.call(action, args, timeout=timeout)


class Context:
    def __init__(self, agent, source: str = "tool"):
        self._agent = agent
        self.source = source  # "ai", "hotkey", "command", "dashboard", "cli", ...
        self.depth = 0  # nesting level of commands that call Claude
        self.browser = BrowserAPI(agent.bridge)
        self.os = agent.platform
        self.state = agent.state
        self.log = logging.getLogger("ally.plugin")

    @property
    def config(self) -> dict:
        return self._agent.config.data

    @property
    def agent(self):
        return self._agent

    def say(self, text: str, wait: bool = False, style: dict | None = None) -> bool:
        """style: emotion, speed, volume, cache, language (see ally/speech.py)."""
        return self._agent.speaker.say(text, wait=wait, style=style)

    def stop_speaking(self) -> None:
        self._agent.speaker.stop()

    def ask(self, prompt: str, image: bytes | None = None, media_type: str = "image/png",
            max_tokens: int = 600, images: list | None = None) -> str:
        return self._agent.brain.ask(prompt, image=image, media_type=media_type, max_tokens=max_tokens,
                                     images=images)

    def run(self, tool_name: str, **args):
        return self._agent.call_tool(tool_name, args, source=self.source, depth=self.depth)

    def event(self, text: str, kind: str = "plugin") -> None:
        self._agent.events.add(kind, text)
