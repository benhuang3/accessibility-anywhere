"""[commands] from config.toml: each becomes a tool, a set of phrases, and optionally a hotkey."""

from __future__ import annotations

import difflib
import logging
import time

from . import rules
from .registry import Tool, ToolError

log = logging.getLogger("ally.commands")
OS_KEY = {"macos": "mac", "windows": "windows", "linux": "linux"}
STEP_KINDS = ("tool", "say", "shell", "open", "keys", "wait", "ask", "notify")


def pick_os(value, platform_name: str):
    if isinstance(value, dict):
        return value.get(OS_KEY.get(platform_name, platform_name))
    return value


class Macros:
    def __init__(self, agent):
        self.agent = agent
        self._phrases: dict[str, str] = {}

    @property
    def commands(self) -> dict:
        return self.agent.config.data.get("commands", {}) or {}

    def rebuild(self) -> list[str]:
        tools, phrases = [], {}
        for name, c in self.commands.items():
            desc = (c.get("description") or f"Run the '{name}' command.").strip()
            said = c.get("phrases") or []
            if said:
                desc += " The user may trigger it by saying: " + ", ".join(f'"{p}"' for p in said[:5]) + "."
            tools.append(Tool(name=name, description=desc, func=self._runner(name), properties={}, required=[],
                              ai=bool(c.get("allow_ai", True)), timeout=120, takes_ctx=True, source="config"))
            for p in list(said) + [name.replace("_", " ").replace("-", " ")]:
                key = rules.normalize(p)
                if key:
                    phrases[key] = name
        self._phrases = phrases
        return self.agent.registry.replace_source("config", tools)

    def _runner(self, name: str):
        def run(ctx):
            return self.run_steps(name, ctx)
        run.__name__ = name
        return run

    def match_phrase(self, text: str) -> str | None:
        t = rules.normalize(text)
        if not t:
            return None
        if t in self._phrases:
            return self._phrases[t]
        best, score = None, 0.0
        for phrase, name in self._phrases.items():
            s = difflib.SequenceMatcher(None, t, phrase).ratio()
            if s > score:
                best, score = name, s
        return best if score >= 0.88 else None

    def run_steps(self, name: str, ctx) -> str:
        c = self.commands.get(name)
        if c is None:
            raise ToolError(f"The command '{name}' no longer exists.")
        agent = self.agent
        steps = c.get("steps") or []
        last = ""
        for i, step in enumerate(steps, 1):
            kind = next((k for k in STEP_KINDS if k in step), None)
            value = step.get(kind)
            more = i < len(steps)
            if kind == "tool":
                out = agent.call_tool(value, step.get("args") or {}, source="command", depth=getattr(ctx, "depth", 0))
                if not out.ok:
                    raise ToolError(f"{name}, step {i} ({value}): {out.error}")
                last = out.text()
            elif kind == "say":
                agent.speaker.say(value, wait=more)
                last = value
            elif kind == "ask":
                depth = getattr(ctx, "depth", 0) + 1
                reply = agent.brain.run_task(value, depth=depth)
                last = reply.text
            elif kind == "shell":
                cmd = pick_os(value, agent.platform.name)
                if not cmd:
                    last = f"skipped on {agent.platform.label}"
                    continue
                out = agent.platform.shell(cmd)
                last = out or f"Ran: {cmd}"
            elif kind == "open":
                target = pick_os(value, agent.platform.name)
                if not target:
                    continue
                if target.startswith(("http://", "https://")) and agent.bridge.connected:
                    agent.bridge.call("openUrl", {"url": target, "newTab": True})
                else:
                    agent.platform.open(target)
                last = f"Opened {target}"
            elif kind == "keys":
                combo = pick_os(value, agent.platform.name)
                if combo:
                    agent.platform.press_keys(combo)
            elif kind == "wait":
                time.sleep(max(0.0, min(60.0, float(value))))
            elif kind == "notify":
                agent.platform.notify(agent.config.data["agent"]["name"], value)
                last = value
        return last or f"Done: {name.replace('_', ' ')}."
