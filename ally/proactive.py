"""The proactive brain: turns Struggle/Risk events into offers, actions and habits.

    bus "struggle" --> fuse (window) --> pick accommodation for this app/site
                   --> already a habit here?  apply silently-ish ("like last time")
                   --> else Offer: overlay bubble + voice "Want me to make this bigger?"
                   --> yes: run the Ally tool, remember it.  no: remember, back off.
    bus "risk"     --> warning card + spoken warning (Ganesh owns the wording in ally/risk/)
    context change --> auto-apply habits for the new app/site

Owner: Ben H. The UI (overlay) and voice parts are plug-ins with tiny interfaces, see NullUI / NullVoice.
"""

from __future__ import annotations

import collections
import logging
import queue
import threading
import time
from typing import Callable

from .contracts import Accommodation, EventBus, Offer, Risk, Struggle
from .habits import HabitMemory, context_key

log = logging.getLogger("ally")

DEFAULTS = {
    "enabled": True, "offer_timeout_seconds": 20.0, "cooldown_seconds": 30.0, "fusion_window_seconds": 4.0,
    "min_confidence": 0.6, "auto_apply_after": 2, "voice_confirm": True, "overlay": True,
    "senses_behavior": True, "senses_vision": False, "camera_index": 0, "context_poll_seconds": 1.5,
}


# ---------------------------------------------------------------- what to do about each struggle
SEEING = ("squint", "lean", "retype")
CLICKING = ("rage_click",)
# Page-step fixes measure and verify the page, so replaying them on every switch back to Chrome would be
# slow; and Chrome already remembers the zoom per site.
PAGE_TOOLS = ("enlarge_text", "enlarge_buttons", "simplified_mode")
SIMPLE_OFFER = Accommodation("simple_site", "make this site simpler", "simplified_mode", {})
SIMPLE_WINDOW_SECONDS = 300
SIMPLE_OFFER_EVERY_SECONDS = 24 * 3600
REPLAY_EVERY_SECONDS = 600


def trusted_source(s: Struggle) -> bool:
    """Struggles that already passed the senses' own confidence gate ("senses", "senses.vision", ...,
    and the senses' own simulator, "simulated")."""
    src = s.source or ""
    return src == "senses" or src.startswith("senses.") or src == "simulated"


def fused_kinds(s: Struggle | None) -> set[str]:
    """The signal kinds behind a struggle: a "fused" one lists them in detail["signals"]."""
    if s is None:
        return set()
    kinds = {p.get("kind") for p in (s.detail or {}).get("signals", []) if isinstance(p, dict)}
    kinds.discard(None)
    return kinds or {s.kind}


def pick_accommodation(kind: str, in_browser: bool, signals=()) -> Accommodation:
    """Map a struggle to one concrete Ally tool call. Edit freely, the keys feed habit memory."""
    if in_browser and kind in ("squint", "lean", "fused", "rage_click", "retype"):
        kinds = set(signals) or {kind}
        seeing, clicking = bool(kinds & set(SEEING)), bool(kinds & set(CLICKING))
        if kind == "fused" and not (seeing or clicking):
            seeing = True
        if seeing and clicking:
            return Accommodation("easier_both", "make this page bigger and easier to click", "enlarge_text",
                                 {"goal": "bigger", "buttons": True, "in_browser": True})
        if clicking:
            return Accommodation("easier_clicking", "make the buttons bigger", "enlarge_buttons", {})
        return Accommodation("easier_reading", "make this page easier to read", "enlarge_text",
                             {"goal": "bigger", "in_browser": True})
    if kind in ("squint", "lean", "fused"):
        return Accommodation("magnify_2x", "magnify the screen", "magnify_screen", {"level": 2.0})
    if kind == "rage_click":
        return (Accommodation("easier_clicking", "make the buttons bigger", "enlarge_buttons", {})
                if in_browser else
                Accommodation("magnify_2x", "make things bigger so they're easier to click",
                              "magnify_screen", {"level": 2.0}))
    if kind == "hesitation":
        return (Accommodation("read_page", "read this page to you", "read_page")
                if in_browser else
                Accommodation("describe_screen", "tell you what's on the screen", "describe_screen",
                              {"question": "Briefly explain what is on screen and what the user can do next."}))
    if kind == "retype":
        return Accommodation("magnify_150", "make the screen a bit bigger", "magnify_screen", {"level": 1.5})
    raise ValueError(kind)


def known_keys() -> set[str]:
    """Every habit key the current code can produce; habits stored under other keys are old and skipped."""
    keys = {SIMPLE_OFFER.key}
    for kind in ("squint", "lean", "fused", "rage_click", "hesitation", "retype"):
        for in_browser in (True, False):
            for signals in ((), ("squint", "rage_click")):
                keys.add(pick_accommodation(kind, in_browser, signals).key)
    return keys


OFFER_PROMPTS = {
    "squint": "Looks hard to read. Want me to {label}?",
    "lean": "Want me to {label}?",
    "fused": "Looks hard to read. Want me to {label}?",
    "rage_click": "Having trouble clicking? Want me to {label}?",
    "hesitation": "Want me to {label}?",
    "retype": "Want me to {label}?",
    "simple": "This site looks hard to use. Want me to {label}?",
}


def fuse(events: list[Struggle]) -> float:
    """Noisy-OR over the strongest event per kind: two weak signals beat one."""
    best: dict[str, float] = {}
    for e in events:
        best[e.kind] = max(best.get(e.kind, 0.0), e.confidence)
    p = 1.0
    for c in best.values():
        p *= (1.0 - c)
    return 1.0 - p


# ---------------------------------------------------------------- UI / voice interfaces
class NullUI:
    """Headless stand-in for the overlay (tests, Linux CI). Ben W's overlay implements the same 4 methods."""

    def __init__(self, events=None):
        self.events = events
        self.shown: list = []

    def highlight(self, box):
        self.shown.append(("highlight", box))

    def show_card(self, text: str, actions=("OK",), kind: str = "info"):
        self.shown.append(("card", text, tuple(actions), kind))

    def offer(self, offer: Offer):
        self.shown.append(("offer", offer.prompt))

    def clear(self):
        self.shown.append(("clear",))


class NullVoice:
    """Headless stand-in for voice_confirm.VoiceConfirm: say() goes to the agent's speaker if any."""

    def __init__(self, speaker=None):
        self.speaker = speaker
        self.said: list[str] = []

    def say(self, text: str):
        self.said.append(text)
        if self.speaker is not None:
            try:
                self.speaker.say(text)
            except Exception:
                log.exception("say failed")

    def ask(self, offer: Offer):
        self.say(offer.prompt)  # can't hear; overlay click / API answer resolves it


# ---------------------------------------------------------------- coordinator
class Coordinator:
    def __init__(self, bus: EventBus, call_tool: Callable, memory: HabitMemory,
                 context_fn: Callable[[], dict], cfg_fn: Callable[[], dict] = lambda: {},
                 ui=None, voice=None, events=None):
        self.bus = bus
        self.call_tool = call_tool          # (name, args, source) -> Outcome
        self.memory = memory
        self.context_fn = context_fn        # -> {"app":..., "site":..., "in_browser": bool}
        self.cfg_fn = cfg_fn
        self.ui = ui or NullUI()
        self.voice = voice or NullVoice()
        self.events = events
        self._q: queue.Queue = queue.Queue()
        self._recent: collections.deque = collections.deque(maxlen=50)
        self._last_offer_at: dict[str, float] = {}
        self._site_history: dict[str, collections.deque] = {}  # per context: (ts, kind); offers don't clear it
        self._simple_offered_at: dict[str, float] = {}
        self._replayed_at: dict[str, float] = {}
        self._snoozed_until: dict[str, float] = {}
        self._known = known_keys()
        self.pending: Offer | None = None
        self.history: collections.deque = collections.deque(maxlen=30)
        self._stop = threading.Event()
        self._unsubs: list = []
        self._threads: list[threading.Thread] = []
        self._ctx_key = None

    # config with defaults
    def cfg(self, k):
        return (self.cfg_fn() or {}).get(k, DEFAULTS[k])

    def _log(self, kind: str, text: str, **kw):
        if self.events is not None:
            self.events.add(kind, text, **kw)
        log.debug("proactive %s: %s", kind, text)

    # -------------------------------------------------------- lifecycle
    def start(self, watch_context: bool = True):
        self._unsubs += [self.bus.subscribe("struggle", lambda m: self._q.put(("struggle", m))),
                         self.bus.subscribe("risk", lambda m: self._q.put(("risk", m))),
                         self.bus.subscribe("answer", self._on_answer)]
        t = threading.Thread(target=self._worker, name="proactive", daemon=True)
        t.start()
        self._threads.append(t)
        if watch_context:
            t2 = threading.Thread(target=self._watch_context, name="proactive-context", daemon=True)
            t2.start()
            self._threads.append(t2)

    def stop(self):
        self._stop.set()
        self._q.put(("stop", None))
        for u in self._unsubs:
            u()
        if self.pending:
            self.pending.resolve("dismissed", "shutdown")

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """Tests: block until the queue is drained and no offer is pending."""
        end = time.time() + timeout
        while time.time() < end:
            if self._q.unfinished_tasks == 0 and self.pending is None:
                return True
            time.sleep(0.02)
        return False

    # -------------------------------------------------------- inputs
    def _on_answer(self, msg: dict):
        off = self.pending
        if off and (msg.get("offer_id") in (None, "", off.id)):
            off.resolve(msg["choice"], msg.get("by", "api"))

    def _worker(self):
        while not self._stop.is_set():
            kind, msg = self._q.get()
            try:
                if kind == "struggle":
                    self.handle_struggle(msg)
                elif kind == "risk":
                    self.handle_risk(msg)
            except Exception:
                log.exception("proactive handler failed")
            finally:
                self._q.task_done()

    def _context(self, s: Struggle | None = None) -> tuple[str, bool]:
        c = {}
        try:
            c = self.context_fn() or {}
        except Exception:
            log.exception("context_fn failed")
        app, site = c.get("app", ""), c.get("site", "")
        if s is not None:  # the sensor may know better
            app, site = s.target.app or app, s.target.site or site
        return context_key(app, site), bool(c.get("in_browser") or site)

    # -------------------------------------------------------- struggle
    def handle_struggle(self, s: Struggle):
        if not self.cfg("enabled"):
            return
        now = time.time()
        self._recent.append(s)
        window = float(self.cfg("fusion_window_seconds"))
        recent = [e for e in self._recent if now - e.ts <= window]
        score = fuse(recent)
        kinds = {e.kind for e in recent}
        self._log("struggle", f"{s.kind} {s.confidence:.2f} (fused {score:.2f} from {', '.join(sorted(kinds))})",
                  source=s.source)
        # One gate, not two: the senses already require two agreeing signals (or a strong solo one) before
        # they send a struggle, so theirs pass as they are; other senders (run.py simulate, the API) are fused here.
        if not trusted_source(s) and score < float(self.cfg("min_confidence")):
            return
        if self.pending is not None:
            return
        ctx, in_browser = self._context(s)
        if now - self._last_offer_at.get(ctx, 0) < float(self.cfg("cooldown_seconds")):
            return
        # vision signals (squint/lean) say "hard to see"; use the strongest kind to pick the fix
        lead = max(recent, key=lambda e: e.confidence).kind if len(kinds) > 1 else s.kind
        signals = set().union(*(fused_kinds(e) for e in recent))
        hist = self._site_history.setdefault(ctx, collections.deque(maxlen=50))
        hist.extend((now, k) for k in signals)
        acc = pick_accommodation(lead, in_browser, signals)
        if in_browser and self._wants_simple(ctx, now):
            acc, lead = SIMPLE_OFFER, "simple"
            self._simple_offered_at[ctx] = now
        self._recent.clear()
        self._last_offer_at[ctx] = now

        if self.snoozed(ctx):
            self._log("proactive", f"skipped offer on {ctx}: snoozed after a reset")
            return
        if any(a.key == acc.key for a in self._habits(ctx)):
            self.apply(acc, ctx, why="habit")
            self.voice.say(f"I'll {acc.label}, like last time.")
            return
        if self.memory.declined_recently(ctx, acc.key):
            self._log("proactive", f"skipped offer to {acc.label} on {ctx}: declined recently")
            return
        box = s.target.box
        prompt = OFFER_PROMPTS.get(lead, "Want me to {label}?").format(label=acc.label)
        offer = Offer(prompt, accommodation=acc, context=ctx, reason=s,
                      timeout=float(self.cfg("offer_timeout_seconds")), highlight=box)
        choice = self.ask(offer)
        learn = ctx != "unknown"  # don't build habits when we can't tell where the user is
        if choice == "yes":
            self.apply(acc, ctx, why="accepted")
            if learn:
                self.memory.record(ctx, acc, True)
        elif choice == "no" and learn:
            self.memory.record(ctx, acc, False)

    def ask(self, offer: Offer) -> str:
        self.pending = offer
        try:
            self.bus.publish("offer", offer)
            self._log("offer", offer.prompt, offer=offer.to_dict())
            if offer.highlight:
                self.ui.highlight(offer.highlight)
            self.ui.offer(offer)
            if self.cfg("voice_confirm"):
                threading.Thread(target=self._voice_ask, args=(offer,), name="voice-ask", daemon=True).start()
            choice = offer.wait()
            self._log("offer", f"answer: {choice} ({offer.answered_by})", offer=offer.to_dict())
            self.bus.publish("offer_result", offer)
            self.history.append(offer.to_dict())
            return choice
        finally:
            try:
                self.ui.clear()
            except Exception:
                log.exception("ui.clear failed")
            self.pending = None

    def _voice_ask(self, offer: Offer):
        try:
            self.voice.ask(offer)
        except Exception:
            log.exception("voice ask failed")

    def apply(self, acc: Accommodation, ctx: str, why: str = ""):
        out = self.call_tool(acc.tool, dict(acc.args), "proactive")
        ok = getattr(out, "ok", True)
        self.bus.publish("applied", {"accommodation": acc.to_dict(), "context": ctx, "why": why, "ok": ok})
        self._log("applied" if ok else "error", f"{acc.label} on {ctx} ({why})")
        return out

    # -------------------------------------------------------- risk
    def handle_risk(self, r: Risk):
        if not self.cfg("enabled"):
            return
        try:
            from .risk import warning_for  # Ganesh's wording, when ally/risk exposes it

            text, spoken = warning_for(r)
        except Exception:  # risk wording not wired yet: a plain, safe fallback
            text = f"This looks risky: {r.kind.replace('_', ' ')}. Be careful."
            spoken = text
        self._log("risk", f"{r.kind} [{r.severity}] {r.evidence}", url=r.url)
        self.bus.publish("risk_warning", {"risk": r.to_dict(), "text": text})
        self.ui.show_card(text, actions=("OK",), kind="risk")
        self.voice.say(spoken)

    # -------------------------------------------------------- habits on context change
    def check_context(self):
        ctx, _ = self._context()
        if ctx == self._ctx_key:
            return
        self._ctx_key = ctx
        self.bus.publish("context", ctx)
        accs = self._habits(ctx)
        now = time.time()
        if accs and (self.snoozed(ctx) or now - self._replayed_at.get(ctx, 0) < REPLAY_EVERY_SECONDS):
            return  # just came back to this site, or the user reset it: don't redo the work every Alt+Tab
        if accs and any(a.tool in PAGE_TOOLS for a in accs) and self._already_enlarged():
            accs = [a for a in accs if a.tool not in PAGE_TOOLS]
        if accs:
            self._replayed_at[ctx] = now
        for acc in accs:
            self.apply(acc, ctx, why="habit")
        if accs:
            self.voice.say(f"I'll {accs[0].label}, like last time." if len(accs) == 1
                           else "I've set things up the way you like here.")

    # -------------------------------------------------------- helpers for habits and Simplified mode
    def _habits(self, ctx: str) -> list[Accommodation]:
        """Habits to auto-apply here: only keys the current code makes, and never Simplified mode (that needs
        an explicit "every time" yes, which the extension remembers)."""
        return [a for a in self.memory.auto_apply(ctx) if a.key in self._known and a.key != SIMPLE_OFFER.key]

    def _wants_simple(self, ctx: str, now: float) -> bool:
        """Two or more different kinds of struggle on this site within a few minutes, at most once a day."""
        if ctx == "unknown" or now - self._simple_offered_at.get(ctx, 0) < SIMPLE_OFFER_EVERY_SECONDS:
            return False
        kinds = {k for t, k in self._site_history.get(ctx, ()) if now - t <= SIMPLE_WINDOW_SECONDS}
        return len(kinds) >= 2

    def _already_enlarged(self) -> bool:
        try:
            c = self.context_fn() or {}
        except Exception:
            return False
        zoom = c.get("zoom")
        return isinstance(zoom, (int, float)) and abs(zoom - 1.0) > 0.01

    def snooze(self, ctx: str | None = None, seconds: float = 1800) -> str:
        """After a reset: no habits or offers on this app/site for a while, so the reset sticks."""
        ctx = ctx or self._context()[0]
        self._snoozed_until[ctx] = time.time() + seconds
        return ctx

    def snoozed(self, ctx: str) -> bool:
        return time.time() < self._snoozed_until.get(ctx, 0)

    def _watch_context(self):
        while not self._stop.wait(float(self.cfg("context_poll_seconds"))):
            if not self.cfg("enabled"):
                continue
            try:
                self.check_context()
            except Exception:
                log.exception("context watch failed")

    def status(self) -> dict:
        return {"pending": self.pending.to_dict() if self.pending else None, "context": self._ctx_key,
                "recent_offers": list(self.history)[-5:]}
