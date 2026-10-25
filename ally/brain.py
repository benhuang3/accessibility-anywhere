"""Decides what to do with a request.

1. Fast path: a [commands] phrase or an obvious request ("zoom in", "stop") runs instantly.
2. Claude: a tool-use loop (tools = registry) with a short rolling history.
3. Offline: if Claude is unreachable, simple rules handle common requests.
"""

from __future__ import annotations

import base64
import difflib
import logging
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime

from . import rules
from .claude import Claude, ClaudeError, text_of
from .registry import ToolError

log = logging.getLogger("ally.brain")
HISTORY_TTL = 600  # forget the conversation after 10 idle minutes


@dataclass
class Reply:
    text: str
    mode: str = "fast"  # fast | claude | offline | busy | none
    actions: list = field(default_factory=list)  # [{"tool", "args", "ok", "text"}]
    error: str = ""

    def to_dict(self) -> dict:
        return {"text": self.text, "mode": self.mode, "actions": self.actions, "error": self.error}


def thinking_param(model: str, setting: str, effort: str) -> dict | None:
    """Fastest valid 'thinking' value for the model (None = leave it out)."""
    if setting != "off" or effort in ("xhigh", "max"):
        return None
    m = model.lower()
    if "haiku-5" in m:
        return {"type": "disabled"}
    if "sonnet-5-5" in m:
        return {"type": "between_tools"}
    return None


class Brain:
    def __init__(self, agent):
        self.agent = agent
        self.history: list[dict] = []
        self.last_turn = 0.0
        self.lock = threading.Lock()
        self.online: bool | None = None
        self.last_error = ""
        self._quirks: dict[str, set] = {}
        self._app_cache = (0.0, None)

    # ---------------------------------------------------------------- setup
    @property
    def cfg(self) -> dict:
        return self.agent.config.data["brain"]

    def client(self) -> Claude:
        b = self.cfg
        return Claude(api_version=b.get("api_version", "2023-06-01"), timeout=float(b.get("timeout_seconds", 20)))

    def mode(self) -> str:
        m = self.cfg.get("mode", "auto")
        if m == "offline":
            return "offline"
        if not self.client().configured:
            return "offline"
        return "claude"

    def system_prompt(self) -> str:
        name = self.agent.config.data["agent"].get("name", "Ally")
        return self.cfg.get("system_prompt", "").replace("{name}", name).strip()

    # ---------------------------------------------------------------- entry
    def handle(self, text: str, source: str = "text") -> Reply:
        text = (text or "").strip()
        if not text:
            return Reply("", mode="none")
        name = self.agent.macros.match_phrase(text) or self._match_tool_name(text, fuzzy=False)
        if name:
            return self._run_actions([(name, {})], "fast")
        mode = self.mode()
        if self.cfg.get("fast_path", True) or mode == "offline":
            m = rules.match(text, strict=True) or rules.closest(text)
            if m:
                return self._run_rule(m, "fast")
        if mode == "offline":
            return self._offline(text, "")
        if not self.lock.acquire(timeout=5):
            return Reply("I'm still working on your last request.", mode="busy")
        try:
            reply = self._converse(text)
            self.online, self.last_error = True, ""
            return reply
        except ClaudeError as e:
            self.online = False
            self.last_error = str(e)
            log.warning("Claude unavailable, using offline rules: %s", e)
            return self._offline(text, str(e))
        finally:
            self.lock.release()

    def _match_tool_name(self, text: str, fuzzy: bool) -> str | None:
        """'what time is it' -> the plug-in tool what_time_is_it (only tools with no required inputs)."""
        t = rules.normalize(text)
        if not t:
            return None
        best, score = None, 0.0
        for tool in self.agent.registry.all():
            if tool.required or tool.name in self.agent.registry.disabled:
                continue
            phrase = tool.name.replace("_", " ").replace("-", " ")
            if phrase == t:
                return tool.name
            if fuzzy:
                s = difflib.SequenceMatcher(None, t, phrase).ratio()
                if s > score:
                    best, score = tool.name, s
        return best if score >= 0.88 else None

    def _offline(self, text: str, why: str) -> Reply:
        m = rules.match(text, strict=False)
        if m:
            reply = self._run_rule(m, "offline")
            reply.error = why
            return reply
        name = self._match_tool_name(text, fuzzy=True)
        if name:
            reply = self._run_actions([(name, {})], "offline")
            reply.error = why
            return reply
        msg = "I'm offline, so I can only do simple things. " if why or self.mode() == "offline" else ""
        return Reply(msg + rules.HELP_TEXT, mode="offline", error=why)

    def _run_rule(self, m: rules.Match, mode: str) -> Reply:
        if m.rule == "help":
            return Reply(self.agent.help_text(), mode=mode)
        return self._run_actions(m.actions, mode)

    def _run_actions(self, actions: list, mode: str) -> Reply:
        done, texts = [], []
        for tool_name, args in actions:
            out = self.agent.call_tool(tool_name, args, source="fast")
            silent = bool(isinstance(out.value, dict) and out.value.get("silent"))
            done.append({"tool": tool_name, "args": args, "ok": out.ok, "text": out.text(), "silent": silent})
            texts.append(out.text())
        return Reply(" ".join(t for t in texts if t), mode=mode, actions=done)

    # ---------------------------------------------------------------- Claude
    def _context(self) -> str:
        a = self.agent
        b = self.cfg
        lines = [f"time: {datetime.now():%A %Y-%m-%d %H:%M}", f"computer: {a.platform.label}"]
        st = a.bridge.status()
        if st["connected"]:
            lines.append(f"chrome: connected, {'in front' if st['focused'] else 'not in front'}")
            tab = st.get("tab") or {}
            if b.get("share_tab_info", True) and tab:
                url = str(tab.get("url") or "")
                try:
                    p = urllib.parse.urlsplit(url)
                    url = f"{p.scheme}://{p.netloc}{p.path}"[:120] if p.scheme in ("http", "https") else url[:40]
                except ValueError:
                    url = ""
                lines.append(f"current tab: {str(tab.get('title') or '')[:100]} ({url})")
            if isinstance(tab.get("zoom"), (int, float)):
                lines.append(f"page zoom: {round(tab['zoom'] * 100)}%")
        else:
            lines.append("chrome: Ally extension not connected (page tools will fail)")
        if b.get("include_active_app", True):
            app = self._front_app()
            if app:
                lines.append(f"app in front: {app}")
        p = a.platform
        lines.append(f"screen magnifier: {'off' if p.magnify_level <= 1 else f'{p.magnify_level:g}x'}; "
                     f"color filter: {p.color}")
        prefs = a.state.get("preferences") or []
        if prefs:
            lines.append("user preferences: " + "; ".join(prefs[-10:]))
        return "<context>\n" + "\n".join(lines) + "\n</context>"

    def _front_app(self) -> str | None:
        ts, app = self._app_cache
        if time.time() - ts < 2:
            return app
        box = {}
        t = threading.Thread(target=lambda: box.setdefault("app", self.agent.platform.frontmost_app()), daemon=True)
        t.start()
        t.join(1.5)
        app = box.get("app")
        self._app_cache = (time.time(), app)
        return app

    def _speed_params(self, body: dict) -> None:
        b = self.cfg
        model = body["model"]
        quirks = self._quirks.get(model, set())
        effort = b.get("effort", "")
        if effort and "no_effort" not in quirks:
            body["output_config"] = {"effort": effort}
        think = thinking_param(model, b.get("thinking", "off"), effort)
        if think and "no_thinking" not in quirks:
            body["thinking"] = think

    def _call(self, client: Claude, body: dict) -> dict:
        try:
            return client.messages(**body)
        except ClaudeError as e:
            if e.status != 400:
                raise
            msg = str(e).lower()
            quirks = self._quirks.setdefault(body["model"], set())
            changed = False
            if "thinking" in body and ("thinking" in msg or "between_tools" in msg or "disabled" in msg):
                body.pop("thinking")
                quirks.add("no_thinking")
                changed = True
            if "output_config" in body and ("effort" in msg or "output_config" in msg):
                body.pop("output_config")
                quirks.add("no_effort")
                changed = True
            if not changed:
                raise
            log.info("model %s rejected a speed setting; retrying without it", body["model"])
            return client.messages(**body)

    def _recent_history(self) -> list[dict]:
        if time.time() - self.last_turn > HISTORY_TTL:
            self.history = []
        n = int(self.cfg.get("history_turns", 4)) * 2
        return list(self.history[-n:]) if n else []

    def _converse(self, text: str) -> Reply:
        messages = self._recent_history() + [{"role": "user", "content": f"{text}\n\n{self._context()}"}]
        reply = self._loop(messages, depth=0)
        note = ""
        if reply.actions:
            note = " [did: " + "; ".join(
                f"{x['tool']}({', '.join(f'{k}={v}' for k, v in x['args'].items())}) -> {x['text'][:80]}"
                for x in reply.actions[:6]) + "]"
        self.history += [{"role": "user", "content": text},
                         {"role": "assistant", "content": ((reply.text or "Done.") + note)[:800]}]
        self.history = self.history[-40:]
        self.last_turn = time.time()
        return reply

    def run_task(self, prompt: str, depth: int = 1) -> Reply:
        """Run a request without touching the conversation (used by `ask` steps in commands)."""
        if self.mode() == "offline":
            m = rules.match(prompt, strict=False)
            if m:
                return self._run_rule(m, "offline")
            raise ToolError("This step needs Claude, and Ally is offline.")
        if depth > 2:
            raise ToolError("Commands are nested too deeply.")
        messages = [{"role": "user", "content": f"{prompt}\n\n{self._context()}"}]
        try:
            return self._loop(messages, depth=depth)
        except ClaudeError as e:
            raise ToolError(f"Claude is unavailable: {e}")

    def _loop(self, messages: list[dict], depth: int) -> Reply:
        a = self.agent
        b = self.cfg
        client = self.client()
        tools = a.registry.ai_definitions(a.config.data)
        if depth >= 2:
            tools = [t for t in tools if getattr(a.registry.get(t["name"]), "source", "") != "config"]
        body_base = {
            "model": b.get("model", "claude-haiku-5-5"),
            "max_tokens": int(b.get("max_tokens", 1024)),
            "system": [{"type": "text", "text": self.system_prompt(), "cache_control": {"type": "ephemeral"}}],
        }
        if tools:
            body_base["tools"] = tools
        actions, final = [], ""
        for _ in range(int(b.get("max_steps", 6))):
            body = dict(body_base, messages=messages)
            self._speed_params(body)
            resp = self._call(client, body)
            content = resp.get("content") or []
            texts = [c.get("text", "") for c in content if c.get("type") == "text" and c.get("text")]
            uses = [c for c in content if c.get("type") == "tool_use"]
            if resp.get("stop_reason") == "tool_use" and uses:
                messages = messages + [{"role": "assistant", "content": content}]
                results = []
                for u in uses:
                    args = u.get("input") or {}
                    out = a.call_tool(u.get("name", ""), args, source="ai", depth=depth)
                    actions.append({"tool": u.get("name"), "args": args, "ok": out.ok, "text": out.text()})
                    block = {"type": "tool_result", "tool_use_id": u.get("id"), "content": out.to_content()}
                    if not out.ok:
                        block["is_error"] = True
                    results.append(block)
                messages = messages + [{"role": "user", "content": results}]
                if texts:
                    final = " ".join(texts)
                continue
            final = " ".join(texts).strip() or final
            if resp.get("stop_reason") == "refusal" and not final:
                final = "I can't help with that one."
            break
        if not final:
            ok = [x["text"] for x in actions if x["ok"]]
            bad = [x["text"] for x in actions if not x["ok"]]
            final = " ".join((ok or bad)[:2]) or "Done."
        return Reply(final.strip(), mode="claude", actions=actions)

    # ---------------------------------------------------------------- one-off questions
    def ask(self, prompt: str, image: bytes | None = None, media_type: str = "image/png",
            max_tokens: int = 600, images: list[tuple[bytes, str]] | None = None) -> str:
        """One question to Claude (no tools). images: several (bytes, media_type) pictures, in order."""
        if self.mode() == "offline":
            raise ToolError("This needs Claude. Add ANTHROPIC_API_KEY to the .env file and check the internet.")
        content = []
        for data, mtype in ([(image, media_type)] if image else []) + list(images or []):
            content.append({"type": "image", "source": {"type": "base64", "media_type": mtype,
                                                         "data": base64.b64encode(data).decode("ascii")}})
        content.append({"type": "text", "text": prompt})
        body = {"model": self.cfg.get("model", "claude-haiku-5-5"), "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": content}]}
        self._speed_params(body)
        try:
            return text_of(self._call(self.client(), body)) or "I couldn't get an answer."
        except ClaudeError as e:
            raise ToolError(f"Claude is unavailable: {e}")
