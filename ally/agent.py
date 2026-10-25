"""The agent process: wires config, tools, brain, browser bridge, hotkeys, speech, spoken yes/no
questions, the always-on listener, the eye tracker and the server."""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
import urllib.parse

from . import __version__, keys
from .brain import Brain, Reply
from .bridge import Bridge
from .config import ConfigManager
from .contracts import ContractError, EventBus, parse_event
from .context import Context
from .habits import HabitMemory
from .gaze import GazeManager
from .hotkeys import HotkeyManager
from .listener import WakeListener
from .log import EventLog, set_level, setup_logging
from .macros import Macros
from .paths import Paths, load_env_file
from .platforms import get_platform
from .proactive import Coordinator, NullUI, NullVoice
from .voice_bus import BusVoice
from .registry import Outcome, PluginLoader, Registry, ToolError
from .senses.manager import Senses
from .server import PortBusy, start_server
from .speech import Speaker
from .state import State, load_or_create_token
from .tools import BUILTIN_MODULES
from .voice import VoiceInput
from .voice_confirm import VoiceConfirm, install as install_voice

log = logging.getLogger("ally")

EXIT_OK, EXIT_RESTART, EXIT_PORT_BUSY = 0, 75, 98


class Agent:
    def __init__(self, home=None, start_hotkeys: bool = True):
        self.paths = Paths(home)
        self.paths.ensure()
        load_env_file(self.paths.env_file)
        setup_logging(self.paths.log_dir)
        self.events = EventLog()
        self.config = ConfigManager(self.paths.config)
        if self.config.ensure_file():
            log.info("created %s with default settings", self.paths.config)
        self.state = State(self.paths.state_file)
        self.token = load_or_create_token(self.paths.token_file)

        self.registry = Registry()
        for m in BUILTIN_MODULES:
            self.registry.load_module(m)
        self.config.reload(self.registry.names())
        self.plugins = PluginLoader(self.registry, self.paths.plugins_dir(self.config.data["plugins"]["folder"]))
        for msg in self.plugins.scan():
            self.events.add("plugin", msg)
        self.config.reload(self._non_config_tools())  # re-check commands now that plug-in tools exist
        self.macros = Macros(self)
        self._apply_settings(initial=True)

        self.platform = get_platform()
        self.speaker = Speaker(self.platform, lambda: self.config.data["speech"],
                               cache_dir=self.paths.cache_dir / "voice")
        rate = self.state.get("speech_rate")
        if isinstance(rate, (int, float)):
            self.speaker.rate_override = float(rate)
        self.bridge = Bridge(on_command=self._extension_command, on_connect=self._extension_connected)
        self.brain = Brain(self)
        self.voice = VoiceInput(self)
        self.listener = WakeListener(self)
        self.senses = Senses(self)  # before gaze: the camera helper sends it face numbers
        # Spoken offers and yes/no answers (say / ask_yes_no for the Brain). Borrows the listener's mic.
        self.confirm = VoiceConfirm(self.speaker, lambda: self.config.data, listener=self.listener,
                                    events=self.events, state=self.state, log_dir=self.paths.log_dir)
        install_voice(self.confirm)
        self.gaze = GazeManager(self)
        g = self.config.data["gaze"]
        self.platform.set_gaze_source(self.gaze.latest, float(g.get("pan_speed", 0.85)),
                                      float(g.get("max_pan_speed", 0.35)))
        self.last_utterance: tuple[float, float] | None = None  # (start, end) of the last spoken request
        # ---- proactive layer (see docs/integration_plan.md): bus -> coordinator -> overlay/voice -> tools.
        # Hayden's senses feeds the bus through on_struggle(); risk and simulate feed it via /api/event.
        self.bus = EventBus()
        self.habits = HabitMemory(self.paths.state_dir / "habits.json",
                                  int(self.proactive_cfg().get("auto_apply_after", 2)))
        self.proactive = Coordinator(self.bus, lambda n, a, src: self.call_tool(n, a, source=src), self.habits,
                                     self.proactive_context, self.proactive_cfg,
                                     ui=NullUI(self.events), voice=NullVoice(self.speaker), events=self.events)
        # Voice asks offers out loud and resolves them with the spoken answer, says "Done. Say undo..." after a
        # fix and listens for "undo" (ally/voice_bus.py).
        self.proactive.voice = BusVoice(self.confirm, self.bus, lambda n, a, src: self.call_tool(n, a, source=src),
                                        snooze=getattr(self.proactive, "snooze", None))
        self.gaze.attach_bus(self.bus)  # offers can be answered by looking at Yes or No ([gaze] answer_offers)
        self.bus.subscribe("offer_result", self._offer_answered)
        self.sensors: list = []  # Hayden's senses run as self.senses; the patch's own sensors are not used
        self.overlay = None      # Ganesh's overlay plugs in here when ally/overlay lands
        self.risk = None         # ally.risk.wire attachment (page/process risk -> bus)
        self.hotkeys = HotkeyManager(self)
        self.start_hotkeys = start_hotkeys
        if not start_hotkeys:
            self.hotkeys.status, self.hotkeys.error = "off", "turned off with --no-hotkeys"
        self.httpd = None
        self.started = time.time()
        self.last_tick = time.time()
        self.exit_code = EXIT_OK
        self._stop = threading.Event()

    # ------------------------------------------------------------- settings
    def _non_config_tools(self) -> set[str]:
        return {t.name for t in self.registry.all() if t.source != "config"}

    def _apply_settings(self, initial: bool = False) -> None:
        cfg = self.config.data
        set_level(cfg["agent"].get("log_level", "INFO"))
        self.registry.disabled = set(cfg["tools"].get("disabled") or [])
        self.registry.default_timeout = float(cfg["tools"].get("timeout_seconds", 15))
        for err in self.macros.rebuild():
            self.events.add("error", err)
        self.plugins.set_folder(self.paths.plugins_dir(cfg["plugins"]["folder"]))
        if not initial:
            if self.start_hotkeys:
                self.hotkeys.apply()
            self.listener.apply()
            self.senses.apply()
            self.gaze.apply()
            threading.Thread(target=self.confirm.prerender, name="voice-prerender", daemon=True).start()
            self.platform.set_gaze_source(self.gaze.latest, float(cfg["gaze"].get("pan_speed", 0.85)),
                                          float(cfg["gaze"].get("max_pan_speed", 0.35)))
            self.push_profile()

    def save_config(self, text: str) -> tuple[bool, list, list]:
        ok, errors, warnings = self.config.save_text(text, self._non_config_tools())
        if ok:
            self._apply_settings()
            self.events.add("config", "Settings saved and applied.")
        return ok, errors, warnings

    def _check_files(self) -> None:
        if self.config.changed_on_disk():
            if self.config.reload(self._non_config_tools()):
                self._apply_settings()
                self.events.add("config", "Settings reloaded from config.toml.")
                log.info("config reloaded")
            else:
                self.events.add("error", "config.toml has a problem (keeping the last good settings): "
                                + (self.config.error or "")[:500])
        changes = self.plugins.scan()
        if changes:
            for msg in changes:
                self.events.add("plugin", msg)
            for err in self.macros.rebuild():
                self.events.add("error", err)

    # ------------------------------------------------------------- actions
    def call_tool(self, name: str, args: dict | None = None, source: str = "tool", depth: int = 0) -> Outcome:
        ctx = Context(self, source=source)
        ctx.depth = depth
        out = self.registry.call(name, args or {}, ctx=ctx)
        arg_txt = ", ".join(f"{k}={v}" for k, v in (args or {}).items())
        self.events.add("tool" if out.ok else "error", f"{name}({arg_txt}) -> {out.text()[:300]}", source=source)
        return out

    def handle_text(self, text: str, source: str = "text", speak: bool = True) -> Reply:
        text = (text or "").strip()
        if not text:
            return Reply("", mode="none")
        self.events.add("heard", text, source=source)
        gen = self.speaker.generation
        reply = self.brain.handle(text, source=source)
        if reply.text:
            self.events.add("reply", reply.text, mode=reply.mode)
        silent = any(a.get("silent") for a in reply.actions)
        if (speak and not silent and self.config.data["agent"].get("speak_replies", True)
                and self.speaker.generation == gen):
            self.speaker.say(reply.text)
        return reply

    def speak_result(self, out: Outcome) -> None:
        if not (isinstance(out.value, dict) and out.value.get("silent")):
            self.speaker.say(out.text())

    def run_action(self, action, source: str = "hotkey") -> None:
        """Run a hotkey value: a {tool, args} table, a command, a tool name, or a sentence."""
        try:
            gen = self.speaker.generation
            if isinstance(action, dict):
                out = self.call_tool(str(action.get("tool", "")), action.get("args") or {}, source=source)
            elif isinstance(action, str) and self.registry.get(action) is not None:
                out = self.call_tool(action, {}, source=source)
            else:
                self.handle_text(str(action), source=source)
                return
            if self.speaker.generation == gen and (not out.ok or self.config.data["agent"].get("speak_replies", True)):
                self.speak_result(out)
        except Exception as e:
            log.exception("action %r failed", action)
            self.events.add("error", f"{action}: {e}")

    def help_text(self) -> str:
        cfg = self.config.data
        lines = [f"I'm {cfg['agent']['name']}. Say or type things like: make it bigger, read this page, "
                 "high contrast, reader mode, magnify the screen, invert colors, or reset."]
        hk = []
        for combo, action in (cfg.get("hotkeys") or {}).items():
            if not action:
                continue
            what = action.get("tool", "") if isinstance(action, dict) else action
            what = " ".join(what.replace("_", " ").rstrip(".").split()[:6])
            hk.append(f"{keys.describe(combo)}: {what}")
        if hk:
            lines.append("Shortcuts: " + "; ".join(hk[:9]) + ".")
        return " ".join(lines)

    # ------------------------------------------------------------- profile
    def profile_payload(self) -> dict:
        cfg = self.config.data
        prof = cfg.get("profile", {})
        return {"styles": cfg.get("page_styles", {}), "globalStyles": prof.get("page_styles") or [],
                "minFontPx": int(prof.get("min_font_px") or 0), "pageZoom": float(prof.get("page_zoom") or 0)}

    def push_profile(self) -> None:
        if self.bridge.connected:
            self.bridge.broadcast("setProfile", self.profile_payload())

    def apply_profile(self, force: bool = False) -> str:
        prof = self.config.data.get("profile", {})
        done = []
        try:
            mag = float(prof.get("screen_magnify") or 1.0)
            if mag > 1.0 or (force and self.platform.magnify_level > 1.0):
                self.platform.magnify(mag)
                done.append("magnifier")
            color = prof.get("color_filter", "none")
            if color != "none" or (force and self.platform.color != "none"):
                self.platform.color_filter(color)
                done.append("colors")
        except ToolError as e:
            self.events.add("error", f"profile: {e}")
        if self.bridge.connected:
            try:
                self.bridge.call("setProfile", dict(self.profile_payload(), applyNow=bool(force)))
                done.append("Chrome")
            except ToolError as e:
                self.events.add("error", f"profile: {e}")
        return "Applied your profile" + (f" ({', '.join(done)})." if done else ".")

    def _extension_connected(self, client) -> None:
        self.events.add("chrome", "Chrome extension connected.")
        self.bridge.call("setProfile", self.profile_payload(), client=client)

    def _extension_command(self, text: str) -> str:
        return self.handle_text(text, source="extension").text

    # ------------------------------------------------------------- status
    def health(self) -> dict:
        stale = time.time() - self.last_tick > 30
        return {"ok": not stale, "name": "ally", "version": __version__, "uptime": round(time.time() - self.started),
                "extension": self.bridge.connected, "brain": self.brain.mode(), "pid": os.getpid()}

    def status(self) -> dict:
        cfg = self.config.data
        ok_voice, voice_why = self.voice.available()
        return {
            "name": cfg["agent"]["name"], "version": __version__, "uptime": round(time.time() - self.started),
            "platform": self.platform.label,
            "brain": {"mode": self.brain.mode(), "model": cfg["brain"]["model"], "online": self.brain.online,
                      "error": self.brain.last_error, "has_key": self.brain.client().configured},
            "browser": self.bridge.status(),
            "hotkeys": self.hotkeys.info(),
            "speech": self.speaker.info(),
            "confirm": self.confirm.info(),
            "voice": {"available": ok_voice, "detail": voice_why, "listening": self.voice.listening},
            "listener": self.listener.info(),
            "gaze": self.gaze.info(),
            "senses": self.senses.info(),
            "proactive": dict(self.proactive.status(), overlay=bool(self.overlay)),
            "risk": self.risk.info() if self.risk else None,
            "screen": {"magnify": self.platform.magnify_level, "color": self.platform.color},
            "config": {"error": self.config.error, "errors": self.config.errors, "warnings": self.config.warnings},
            "plugins": {"folder": str(self.plugins.folder), "errors": self.plugins.errors},
        }

    def tools_info(self) -> list[dict]:
        out = []
        for t in self.registry.all():
            d = t.info()
            d["disabled"] = t.name in self.registry.disabled
            out.append(d)
        return out

    def config_info(self) -> dict:
        return {"text": self.config.read_text(), "path": str(self.config.path), "error": self.config.error,
                "errors": self.config.errors, "warnings": self.config.warnings}

    # ------------------------------------------------------------- lifecycle
    # ------------------------------------------------------------- proactive
    def proactive_cfg(self) -> dict:
        return self.config.data.get("proactive", {}) or {}

    def proactive_context(self) -> dict:
        """What the user is looking at: {"app", "site", "in_browser"} (habit memory is keyed on this)."""
        in_browser = bool(self.bridge.chrome_focused)
        site = (urllib.parse.urlsplit(self.bridge.tab.get("url") or "").hostname or "") if in_browser else ""
        title = ""
        try:
            title = self.platform.frontmost_app() or ""
        except Exception:
            pass
        app = title.rsplit(" - ", 1)[-1] if " - " in title else title  # "Inbox - Google Chrome" -> "Google Chrome"
        zoom = self.bridge.tab.get("zoom") if in_browser else None
        return {"app": app, "site": site, "in_browser": in_browser, "window": title, "zoom": zoom}

    def _offer_answered(self, offer) -> None:
        """Tell the senses about a yes or no, so a "no" pauses those signals for a while."""
        reason = getattr(offer, "reason", None)
        if offer.choice in ("yes", "no") and hasattr(reason, "to_dict"):
            try:
                self.senses.answer(reason.to_dict(), offer.choice)
            except Exception:
                log.debug("senses.answer failed", exc_info=True)

    def on_struggle(self, data: dict) -> None:
        """Bridge: Hayden's senses calls this with a struggle dict; forward it onto the bus."""
        d = dict(data)
        d.setdefault("type", "struggle")
        self.publish_event(d)

    def publish_event(self, data: dict) -> dict:
        """POST /api/event and `run.py simulate`: inject a struggle/risk/answer as if a sensor sent it."""
        try:
            topic, msg = parse_event(data)
        except (ContractError, TypeError, ValueError) as e:
            return {"ok": False, "error": str(e)}
        n = self.bus.publish(topic, msg)
        return {"ok": True, "topic": topic, "delivered": n}

    def start_proactive(self) -> None:
        """Start the proactive coordinator. The overlay plugs in when ally/overlay exists; sensors
        are separate (Hayden's senses via on_struggle, risk/simulate via /api/event)."""
        if self.proactive_cfg().get("overlay", True):
            try:
                from .overlay import Overlay

                ov = Overlay()
                if ov.start():
                    self.overlay = self.proactive.ui = ov
                else:
                    self.events.add("error", ov.error)
            except Exception as e:
                log.debug("overlay unavailable: %s", e)
        self.proactive.start()
        try:
            from .risk.wire import attach

            self.risk = attach(self)
        except Exception as e:
            log.warning("risk protection unavailable: %s", e)
        self.events.add("start", "Proactive help is on.")

    def start(self) -> None:
        port = int(self.config.data["agent"]["port"])
        self.httpd = start_server(self, port)  # raises PortBusy
        log.info("Ally %s listening on http://127.0.0.1:%s (brain: %s)", __version__, port, self.brain.mode())
        self.events.add("start", f"Ally started on {self.platform.label}. Dashboard: http://127.0.0.1:{port}/")
        if self.start_hotkeys:
            self.hotkeys.apply(force=True)
        self.listener.apply()
        self.senses.apply()
        self.gaze.apply()
        if self.proactive_cfg().get("enabled", True):
            self.start_proactive()
        threading.Thread(target=self._housekeeping, name="housekeeping", daemon=True).start()
        threading.Thread(target=self._startup_extras, name="startup", daemon=True).start()

    def _startup_extras(self) -> None:
        self.platform.warm_up()
        threading.Thread(target=self.confirm.prerender, name="voice-prerender", daemon=True).start()
        self.apply_profile()
        if self.config.data["agent"].get("announce_start", True):
            name = self.config.data["agent"]["name"]
            mode = "" if self.brain.mode() == "claude" else " in offline mode"
            hint = ""
            if self.listener.wanted() and self.listener.available()[0]:
                words = self.config.data["voice"].get("wake_words") or ["ally"]
                hint = f" Say {str(words[0]).title()}, then what you need."
            self.speaker.say(f"{name} is ready{mode}.{hint}")

    def _housekeeping(self) -> None:
        n = 0
        while not self._stop.is_set():
            self.last_tick = time.time()
            n += 1
            try:
                self._check_files()
                if n % 3 == 0 and self.start_hotkeys:
                    self.hotkeys.ensure_running()
                if n % 3 == 0:
                    self.gaze.ensure_running()
                    self.senses.input.ensure_running()
                self.senses.tick()
            except Exception:
                log.exception("housekeeping error")
            self._stop.wait(1.0)

    def request_exit(self, code: int = EXIT_OK) -> None:
        self.exit_code = code
        self._stop.set()

    def run_forever(self) -> int:
        try:
            self.start()
        except PortBusy as e:
            log.error("%s. Is Ally already running? Try: python run.py status", e)
            return EXIT_PORT_BUSY
        for sig in ("SIGTERM", "SIGINT", "SIGBREAK"):
            if hasattr(signal, sig):
                try:
                    signal.signal(getattr(signal, sig), lambda *_: self.request_exit(EXIT_OK))
                except (ValueError, OSError):
                    pass
        try:
            while not self._stop.wait(0.5):
                pass
        except KeyboardInterrupt:
            self.exit_code = EXIT_OK
        self.close()
        return self.exit_code

    def close(self) -> None:
        self._stop.set()
        log.info("Ally stopping (code %s)", self.exit_code)
        extra = [self.proactive.stop] + ([self.overlay.stop] if self.overlay else []) + ([self.risk.stop] if self.risk else [])
        if hasattr(self.proactive.voice, "close"):
            extra.append(self.proactive.voice.close)
        for step in (*extra, self.confirm.cancel, self.hotkeys.stop, self.listener.stop, self.senses.stop,
                     self.gaze.stop, self.speaker.stop, self.platform.shutdown, self.bridge.close_all):
            try:
                step()
            except Exception:
                log.exception("shutdown step failed")
        if self.httpd is not None:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except Exception:
                pass
