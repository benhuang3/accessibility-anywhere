"""Voice for the proactive layer: the object the Coordinator gets as voice= (same calls as NullVoice).

    say(text)    speak a line; while a question is open, it waits until the question is over
    ask(offer)   speak the Offer's question, listen, and resolve the Offer (first answer wins)
    warn(risk, spoken)   a risk warning, said just before that risk's question

What it adds around ally/voice_confirm.py:
- Risk ordering. RiskWire (ally/risk/wire.py) asks its risk Offer on another thread, often before the
  Coordinator says the warning. A risk question waits briefly for its warning and says both as one, so
  the question never cuts the warning off or comes first.
- After a fix the user accepted ("applied" on the bus): "Done. Say undo to put it back.", then a short
  listen for "undo" (anything else heard goes on as a normal request). Undo runs reset_all.
- "Stop asking me" on an ordinary offer calls snooze(context); on a risk question it is a plain no.
- While a helper video call is open (risk_contact with call_opened), voice asks nothing: what the user
  says is for the helper, and Ally's voice would go into the call. The buttons still work.

Bus handlers run on the publisher's thread, so they only queue work for this module's own thread.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable

from .voice_confirm import PROMPTS

log = logging.getLogger("ally.voice_bus")

LEAD_WAIT_SECONDS = 1.0     # how long a risk question waits for its warning
LEAD_FRESH_SECONDS = 2.0    # a warning said this recently counts as already said
CALL_QUIET_SECONDS = 600.0  # no spoken questions this long after a helper call opens (no "call ended" event yet)
UNDO_SECONDS = 6.0          # listen this long for "undo" after "Done."


class BusVoice:
    def __init__(self, confirm, bus=None, call_tool: Callable | None = None,
                 snooze: Callable[[str], object] | None = None, undo_seconds: float = UNDO_SECONDS):
        self.confirm = confirm
        self.bus = bus
        self.call_tool = call_tool  # (name, args, source) -> Outcome
        self.snooze = snooze        # (context) -> anything; Coordinator.snooze when it exists
        self.undo_seconds = undo_seconds
        self._lock = threading.Lock()
        self._later: list[str] = []          # lines said while a question was open
        self._awaiting_lead = 0              # risk questions waiting for their warning
        self._lead: str | None = None
        self._lead_ready = threading.Event()
        self._last_line = ("", 0.0)          # (text, monotonic time) of the last line said
        self._quiet_until = 0.0
        self._jobs: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._unsubs: list = []
        self._worker = threading.Thread(target=self._work, name="voice-bus", daemon=True)
        self._worker.start()
        if bus is not None:
            self._unsubs = [bus.subscribe("applied", lambda m: self._jobs.put(("applied", m))),
                            bus.subscribe("risk_contact", self._on_contact)]

    # ------------------------------------------------------------ the Coordinator's calls
    def say(self, text: str, style: str = "calm") -> bool:
        text = (text or "").strip()
        if not text:
            return False
        with self._lock:
            if self._awaiting_lead and self._lead is None:
                self._lead = text  # a risk question is waiting: this is its warning, said together
                self._lead_ready.set()
                return True
            if self.confirm.asking:
                self._later.append(text)  # never talk over an open question
                return True
            self._last_line = (text, time.monotonic())
        return self.confirm.say(text, style)

    def warn(self, risk=None, spoken: str | None = None, **_kw) -> None:
        """The risk warning, as a statement only: the risk's Offer asks the question."""
        statement = spoken or self.confirm.risk_text(risk)[0]
        self.say(statement, "warning")

    def ask(self, offer) -> None:
        """Called on the Coordinator's voice-ask thread; blocking here is fine."""
        if self.quiet:
            log.info("helper call open: not asking %r out loud", getattr(offer, "prompt", ""))
            return
        risk = getattr(offer, "context", "") == "risk"
        lead = self._wait_for_lead() if risk else ""
        self.confirm.speaker.wait(15)  # let a line already playing (often the warning) finish
        try:
            ans = self.confirm.confirm_offer(offer, lead=lead)
        finally:
            self._flush_later()
        if getattr(ans, "intent", "") == "STOP_ASKING" and not risk and self.snooze is not None:
            try:
                self.snooze(getattr(offer, "context", None) or None)
            except Exception:
                log.exception("snooze failed")

    # ------------------------------------------------------------ helpers
    @property
    def quiet(self) -> bool:
        return time.monotonic() < self._quiet_until

    def _wait_for_lead(self) -> str:
        """The warning for this risk question: wait briefly for it unless it was just said."""
        text, at = self._last_line
        if text and time.monotonic() - at < LEAD_FRESH_SECONDS:
            return ""  # already said (and still playing, or just finished): ask right after it
        with self._lock:
            self._awaiting_lead += 1
            if self._awaiting_lead == 1:
                self._lead = None
                self._lead_ready.clear()
        try:
            self._lead_ready.wait(LEAD_WAIT_SECONDS)
            with self._lock:
                lead, self._lead = self._lead or "", None
                self._lead_ready.clear()
            return lead
        finally:
            with self._lock:
                self._awaiting_lead -= 1

    def _flush_later(self) -> None:
        with self._lock:
            lines, self._later = self._later, []
        for line in lines:
            self.confirm.say(line, "calm", wait=True)

    def _on_contact(self, msg) -> None:
        if isinstance(msg, dict) and msg.get("call_opened"):
            self._quiet_until = time.monotonic() + CALL_QUIET_SECONDS
            if self.confirm.asking:
                self.confirm.cancel()  # the buttons still answer the Offer

    # ------------------------------------------------------------ after a fix
    def _work(self) -> None:
        while not self._stop.is_set():
            try:
                kind, msg = self._jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if kind == "applied":
                    self._after_fix(msg)
            except Exception:
                log.exception("voice after a fix failed")

    def _after_fix(self, msg) -> None:
        if not isinstance(msg, dict) or msg.get("why") != "accepted":
            return  # habits announce themselves ("I'll ..., like last time")
        if self.quiet:
            return
        if not msg.get("ok", True):
            self.confirm.say(PROMPTS["failed"], "sorry")
            return
        self.confirm.say(PROMPTS["done"], "done", wait=True)
        if self.call_tool is None or self.undo_seconds <= 0:
            return
        heard = self.confirm.listen_for({"UNDO"}, self.undo_seconds)
        if heard is None or heard.kind != "UNDO":
            return
        out = self.call_tool("reset_all", {}, "voice")  # stops speech and (os-layer) snoozes the site
        if getattr(out, "ok", True):
            self.confirm.undone()
        else:
            self.confirm.say(PROMPTS["failed"], "sorry")

    def close(self) -> None:
        self._stop.set()
        for u in self._unsubs:
            try:
                u()
            except Exception:
                pass
        self._unsubs = []
