"""Adapt local page and process rules to the risk event bus."""

import logging
import re
import threading
import time

from ally.contracts import EventBus, Offer, Risk
from . import actions, behavior, contact, domains, fields, joined, process, soft, text
from .guardian import Evidence

log = logging.getLogger("ally")
_ORDER = ("scam_popup", "remote_access", "untrusted_site", "payment_field")
PAUSE_SECONDS = 30


def _evidence(value: str) -> str:
    value = re.sub(r"(?:https?://|www\.)\S+", "a website", text.redact(value),
                   flags=re.IGNORECASE).strip()
    ends = [m.end() for m in re.finditer(r"[.!?](?=\s|$)", value) if m.end() <= 240]
    if ends:
        return value[:ends[-1]]
    if len(value) < 240:
        return value
    # Leave room for the ellipsis, including when the guardian already capped its text at 240.
    end = value.rfind(" ", 0, 240)
    return (value[:end].rstrip() if end >= 0 else "") + "…"


def _page(page) -> dict:
    if not isinstance(page, dict):
        return {}
    clean = {key: page.get(key, "")[:20000] if isinstance(page.get(key, ""), str) else ""
             for key in ("url", "text", "overlay_text")}
    clean.update({key: page.get(key) is True for key in ("fullscreen", "has_tel_link")})
    clean["fields"] = page.get("fields") if isinstance(page.get("fields"), list) else []
    clean["signals"] = page.get("signals") if isinstance(page.get("signals"), dict) else {}
    clean["tab_id"] = page.get("tab_id") if type(page.get("tab_id")) is int else None
    return clean


def _known(url: str) -> bool:
    host = domains.host_of(url)
    trusted = domains.TOP_SITES + tuple(d for ds in domains.BRANDS.values() for d in ds)
    if any(host == d or host.endswith("." + d)
           for d in (domains.registrable_domain(item) for item in trusted) if d):
        return True
    registered = domains.registrable_domain(host)
    name, _, suffix = registered.partition(".")
    return (name in domains._REGIONAL_BRANDS and name in domains.BRANDS
            and suffix in domains._REGIONAL_SUFFIXES)


def _scan(page: dict, remote_session: bool = False) -> list[Risk]:
    if not page:
        return []
    url = page["url"]
    risks = []
    ts = text.score_text(page["text"], url, page["overlay_text"],
                         page["fullscreen"], page["has_tel_link"])
    if ts.severity and not ts.allowlisted:
        risks.append(Risk("scam_popup", ts.severity, _evidence(ts.evidence), url, source="risk.text"))
    domain = domains.check_domain(url)
    if domain.severity:
        risks.append(Risk("untrusted_site", domain.severity, _evidence(domain.evidence), url,
                          source="risk.domain"))
    card = fields.detect_card_fields(page["fields"])
    payment = fields.score_payment(card, _known(url), url.lower().startswith("http:"),
                                   domain.severity is not None, remote_session)
    if payment.severity:
        risks.append(Risk("payment_field", payment.severity, _evidence(payment.evidence), url,
                          source="risk.fields"))
    return _sorted(risks)


def _sorted(risks: list) -> list:
    return sorted(risks, key=lambda r: (r.severity != "high", _ORDER.index(r.kind)))


def scan_page(page: dict) -> list[Risk]:
    return _scan(_page(page))


class Deduper:
    def __init__(self, window_s=1800.0, clock=time.time):
        self.window_s = window_s
        self.clock = clock
        self._seen = {}
        self._lock = threading.Lock()

    def fresh(self, risk) -> bool:
        with self._lock:
            now = self.clock()
            self._seen = {k: at for k, at in self._seen.items() if now - at < self.window_s}
            key = (domains.host_of(risk.url) if risk.url else risk.app, risk.kind)
            if key in self._seen:
                return False
            self._seen[key] = now
            return True

    def forget(self, risk) -> None:
        with self._lock:
            self._seen.pop((domains.host_of(risk.url) if risk.url else risk.app, risk.kind), None)

    def clear(self) -> None:
        with self._lock:
            self._seen.clear()


class RiskWire:
    def __init__(self, bus: EventBus, snapshot_fn=process.snapshot_processes,
                 poll_seconds=10.0, clock=time.time, deduper=None, ask=None, call=None,
                 broadcast=None, kill=actions._kill, guardian=None, highlight=None, screen_size=None,
                 overlay_active=None, review_timeout_s=10.0, contact=None):
        self.contact = contact  # (name, send(text) -> bool) for "Tell <name>" on high warnings, or None
        self.auto_alert = bool(contact and len(contact) > 3 and contact[3])
        self._last_auto_alert = None
        self.bus = bus
        self.overlay_active = overlay_active or (lambda: False)  # the in-page banner only stands in for it
        self.guardian = guardian        # second opinion from a vision model (consent and budget live inside it)
        self.review_timeout_s = review_timeout_s
        self.highlight = highlight      # unused here; marks go to the offer so the overlay draws them
        self.screen_size = screen_size  # () -> (width, height) of the screenshot the guardian saw
        self._marks = {}
        self._tab_ids = {}
        self.ask = ask
        self.call = call
        self.broadcast = broadcast
        self.kill = kill
        self._inflight = set()
        self._unsubscribe = None
        self._unsubscribe_reset = None
        self.snapshot_fn = snapshot_fn
        self.poll_seconds = poll_seconds
        self.clock = clock
        self.deduper = deduper if deduper is not None else Deduper(clock=clock)
        self._lock = threading.Lock()
        self._snapshot = []
        self._watcher = process.RemoteWatcher()
        self._baseline = False
        self.last_page = {"url": "", "card_present": False, "last_scam_text_at": None}
        self._stop = threading.Event()
        self._thread = None

    def on_risk_warning(self, msg) -> None:
        try:
            risk = Risk.from_dict(msg["risk"])
        except Exception:
            return
        alert = self._auto_alert(risk)
        if self.broadcast is not None and risk.url:
            try:
                self.broadcast("exitFullscreen", {"url": risk.url})
                if not self.overlay_active():  # one message, not two: the overlay panel already says it
                    self.broadcast("showRiskBanner", {"url": risk.url, "kind": risk.kind, "text": msg["text"]})
            except Exception:
                log.debug("risk banner failed", exc_info=True)
        proposal = actions.ask_for(risk)
        if self.ask is None or proposal is None:
            return
        key = (domains.host_of(risk.url), risk.kind)
        with self._lock:
            if self._stop.is_set() or key in self._inflight:
                return
            self._inflight.add(key)
            tab_id = self._tab_ids.pop(key, None)
        prompt, action_id = proposal

        def ask_and_act():
            try:
                with self._lock:
                    box = self._marks.pop(key, None)
                offer = Offer(prompt, context="risk", reason=risk, timeout=20.0, highlight=box)
                if risk.source == "risk.joined":
                    # Someone may be controlling the PC while the bank is open: the moment scammers rush. A short
                    # forced pause (Android does the same) gives the person time to think; closing works right away.
                    offer.pause_seconds = PAUSE_SECONDS
                    offer.timeout = 20.0 + PAUSE_SECONDS
                if self.contact and risk.severity == "high":
                    with self._lock:
                        if alert is not None:
                            alert["offer"] = offer
                        self._contact_action(offer, risk, alert)
                if self.ask(offer) == "yes" and not self._stop.is_set():
                    ok = actions.perform(action_id, risk, self.call,
                                         snapshot=self.snapshot_fn, kill=self.kill, tab_id=tab_id)
                    self.bus.publish("risk_action", {"action": action_id, "ok": ok,
                                                     "kind": risk.kind})
            except Exception:
                log.debug("risk offer failed", exc_info=True)
            finally:
                with self._lock:
                    self._inflight.discard(key)

        try:
            threading.Thread(target=ask_and_act, name="risk-ask", daemon=True).start()
        except Exception:
            with self._lock:
                self._inflight.discard(key)

    def _call_url(self) -> str:
        url = self.contact[2] if self.contact and len(self.contact) > 2 else ""
        return url if contact.valid_call_url(url) else ""

    def _contact_action(self, offer, risk, alert) -> None:
        if alert is not None and not self._call_url() and (alert["sent"] or not alert["done"].is_set()):
            offer.extra_action = None
            return
        verb = "Call" if self._call_url() else "Tell"
        offer.extra_action = (f"{verb} {self.contact[0]}", lambda: self._tell(risk, alert))

    def _send_contact(self, risk) -> bool:
        call_url = self._call_url()
        try:
            message = contact.message_for(risk.kind) + (" Tap to join the video call." if call_url else "")
            send = self.contact[1]
            return bool(send(message, call_url) if call_url else send(message))
        except Exception:
            log.debug("trusted contact send failed", exc_info=True)
            return False

    def _auto_alert(self, risk):
        if not self.contact or not self.auto_alert or risk.severity != "high":
            return None
        with self._lock:
            now = self.clock()
            if self._last_auto_alert is not None and now - self._last_auto_alert < 300:
                return None
            self._last_auto_alert = now
        alert = {"sent": False, "done": threading.Event(), "offer": None}

        def send():
            try:
                ok = self._send_contact(risk)
                with self._lock:
                    alert["sent"] = ok
                    alert["done"].set()
                    if alert["offer"] is not None:
                        self._contact_action(alert["offer"], risk, alert)
                self.bus.publish("risk_contact", {"name": self.contact[0], "kind": risk.kind,
                                                 "ok": ok, "call_opened": False, "auto": True})
            finally:
                alert["done"].set()

        try:
            threading.Thread(target=send, name="risk-auto-alert", daemon=True).start()
        except Exception:
            log.debug("trusted contact worker failed", exc_info=True)
            alert["done"].set()
        return alert

    def _tell(self, risk, alert=None) -> str:
        """Runs in a worker when the person taps 'Call/Tell <name>'. Returns the line shown in the panel.
        With a call link: open the video call here and send the helper a push whose button joins it."""
        name = self.contact[0]
        call_url = self._call_url()
        opened = False
        if call_url:
            try:
                if self.call is not None:
                    try:
                        result = self.call("openCall", {"url": call_url, "name": name})
                    except Exception:
                        result = None
                    if not isinstance(result, dict) or not result.get("opened") or result.get("error"):
                        self.call("openUrl", {"url": call_url, "newTab": True})
                else:
                    import webbrowser

                    webbrowser.open(call_url)
                opened = True
            except Exception:
                log.debug("could not open the video call", exc_info=True)
        if alert is not None:
            alert["done"].wait()  # the call opens immediately; only delivery waits for the pending push
        already_sent = alert is not None and alert["sent"]
        ok = already_sent or self._send_contact(risk)
        self.bus.publish("risk_contact", {"name": name, "kind": risk.kind, "ok": ok, "call_opened": opened})
        if call_url and opened and ok:
            next_step = ("Hang up on the other caller first." if risk.kind == "remote_access" else
                         "They already got the alert." if already_sent else "I've asked them to join.")
            return f"Opening a call with {name}. {next_step}"
        return f"I've sent {name} a message." if ok else f"I couldn't reach {name}. Please give them a call."

    def _joined(self):
        page = self.last_page
        jr = joined.joined_risk(joined.running_tools(self._snapshot), page["url"], page["card_present"])
        if jr:
            return Risk("remote_access", "high", _evidence(jr.evidence), page["url"],
                        ", ".join(jr.tools), "risk.joined")
        return None

    def on_page(self, msg: dict) -> None:
        page = _page(msg)
        if not page:
            return
        with self._lock:
            risks = _scan(page, bool(joined.running_tools(self._snapshot)))
            beh = behavior.behavior_flags(page["signals"])
            if beh.suspicious:
                risks = _sorted(risks + [Risk("scam_popup", "medium", beh.evidence, page["url"],
                                              source="risk.behavior")])
            self.last_page.update(url=page["url"],
                                  card_present=fields.detect_card_fields(page["fields"]).present)
            if any(r.kind == "scam_popup" for r in risks):
                self.last_page["last_scam_text_at"] = self.clock()
            combined = self._joined()
            candidates = ([combined] if combined else []) + risks
            chosen = next((r for r in candidates if self.deduper.fresh(r)), None)
            tools = joined.running_tools(self._snapshot)
        extra, provisional = (), False
        if chosen is None and self.guardian is not None and not text.is_allowlisted(page["url"]):
            # Nothing hard enough to warn on. A few weak signs still earn a look, and only a confirmed scam warns.
            phone = "phone" in text.score_text(page["text"], page["url"], page["overlay_text"], False,
                                               page["has_tel_link"]).signals
            extra = soft.soft_flags(page["text"], phone)
            if soft.soft_gate(extra):
                candidate = Risk("scam_popup", "medium", "This page has several warning signs.", page["url"],
                                 source="risk.soft")
                if self.deduper.fresh(candidate):
                    chosen, provisional = candidate, True
        if chosen and self.guardian is not None:
            before = chosen
            chosen = self._second_opinion(chosen, page, risks, beh, tools, extra, provisional)
            if chosen is None and provisional:
                self.deduper.forget(before)
        if chosen:
            with self._lock:
                self._tab_ids[(domains.host_of(chosen.url), chosen.kind)] = page["tab_id"]
            self.bus.publish("risk", chosen)

    def _second_opinion(self, chosen, page, risks, beh, tools, extra=(), provisional=False):
        """Ask the vision model. The local rules stay the floor: it can add an explanation, raise a
        medium warning to high, or hide a medium one it is sure is harmless, never hide a high one."""
        sources = {r.source for r in risks}
        flags = [name for name, source in (("scam_text", "risk.text"), ("lookalike", "risk.domain"),
                                           ("card_fields", "risk.fields")) if source in sources]
        flags += [f for f in (*beh.flags, *extra) if f not in flags]
        if page["fullscreen"] and "fullscreen" not in flags:
            flags.append("fullscreen")
        if tools:
            flags.append("remote_tools")
        evidence = Evidence(chosen.url or page["url"], tuple(flags), page["text"][:1500], tuple(tools), chosen.kind)
        completed, result = threading.Event(), []

        def review():
            try:
                result.append(self.guardian.review(evidence))
            except Exception:
                log.debug("guardian review failed", exc_info=True)
            finally:
                completed.set()

        try:
            threading.Thread(target=review, name="risk-review", daemon=True).start()
        except Exception:
            log.debug("guardian worker failed", exc_info=True)
            return None if provisional else chosen
        if not completed.wait(self.review_timeout_s):
            return None if provisional else chosen
        verdict = result[0] if result else None
        if provisional and (verdict is None or verdict.verdict == "safe"):
            return None  # weak signs plus no confirmation: stay quiet
        if verdict is None:
            return chosen
        if verdict.verdict == "safe":
            return None if chosen.severity != "high" and verdict.confidence >= 0.8 else chosen
        severity = "high" if (verdict.verdict == "scam" and verdict.confidence >= 0.6) else chosen.severity
        upgraded = Risk(chosen.kind, severity, _evidence(verdict.explanation) or chosen.evidence, chosen.url,
                        chosen.app, "risk.guardian")
        self._keep_marks(upgraded, verdict)
        return upgraded

    def _keep_marks(self, risk, verdict) -> None:
        """Remember where on the screen the model saw the red flag, for the offer's highlight."""
        if not verdict.evidence or self.screen_size is None:
            return
        try:
            width, height = self.screen_size()
            x, y, w, h = verdict.evidence[0].box
            box = (int(x * width), int(y * height), max(8, int(w * width)), max(8, int(h * height)))
        except Exception:
            return
        with self._lock:
            if len(self._marks) > 20:
                self._marks.clear()
            self._marks[(domains.host_of(risk.url), risk.kind)] = box

    def info(self) -> dict:
        guardian = self.guardian
        return {"second_opinion": guardian is not None, "budget": guardian.budget.snapshot() if guardian else None}

    def poll_once(self) -> None:
        try:
            snap = self.snapshot_fn()
        except Exception:
            log.debug("risk process snapshot failed", exc_info=True)
            return
        with self._lock:
            previous_tools = set(joined.running_tools(self._snapshot))
            self._snapshot = snap if isinstance(snap, (list, tuple)) else []
            page = self.last_page
            payment_open = page["card_present"] or joined.joined_risk(("context",), page["url"]) is not None
            events = self._watcher.observe(self._snapshot, self.clock(),
                                           page["last_scam_text_at"], payment_open)
            if not self._baseline:
                self._baseline = True
                return
            risks = []
            if set(joined.running_tools(self._snapshot)) - previous_tools:
                combined = self._joined()
                if combined:
                    risks.append(combined)
            risks.extend(Risk("remote_access", e.severity, _evidence(e.evidence),
                              app=e.tool, source="risk.process") for e in events)
            fresh = [r for r in risks if self.deduper.fresh(r)]
        for risk in fresh:
            self.bus.publish("risk", risk)

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                log.debug("risk polling failed", exc_info=True)
            if self._stop.wait(max(0.01, self.poll_seconds)):
                break

    def reset(self):
        """Clear rehearsal/page memory without replenishing the AI spend budget."""
        with self._lock:
            self.deduper.clear()
            self._marks.clear()
            self._tab_ids.clear()
        if self.guardian is not None:
            self.guardian.clear_cache()
            self.guardian.budget.clear_pages()

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            if self._unsubscribe is None:
                self._unsubscribe = self.bus.subscribe("risk_warning", self.on_risk_warning)
            if self._unsubscribe_reset is None:
                self._unsubscribe_reset = self.bus.subscribe("risk_reset", lambda msg: self.reset())
            self._thread = threading.Thread(target=self._loop, name="risk-wire", daemon=True)
            self._thread.start()

    def stop(self):
        with self._lock:
            self._stop.set()
            if self._unsubscribe is not None:
                self._unsubscribe()
                self._unsubscribe = None
            if self._unsubscribe_reset is not None:
                self._unsubscribe_reset()
                self._unsubscribe_reset = None
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=0.5)


def _make_guardian(agent):
    """The vision second opinion, off until the user turns on [risk] screen_review in config.toml."""
    from .guardian import Budget, Guardian

    def cfg() -> dict:
        try:
            return agent.config.data.get("risk", {}) or {}
        except Exception:
            return {}

    size = {"wh": (1920, 1080)}

    def shot():
        from ally.platforms.base import png_to_jpeg_if_possible

        png = agent.platform.screenshot_png()
        try:
            import io
            from PIL import Image

            size["wh"] = Image.open(io.BytesIO(png)).size
        except Exception:
            pass
        return png_to_jpeg_if_possible(png)

    budget = Budget(daily_calls=int(cfg().get("daily_calls", 50)), max_per_hour=int(cfg().get("hourly_calls", 20)),
                    state_path=agent.paths.state_dir / "guardian_budget.json")

    def allowed() -> bool:
        return bool(cfg().get("screen_review", False)) and agent.brain.mode() != "offline"

    model = (getattr(agent.brain, "cfg", {}) or {}).get("model", "claude-haiku-5-5")
    return Guardian(screenshot=shot, budget=budget, consented=allowed, model=model), (lambda: size["wh"])


def _make_contact(agent):
    """("Priya", send) when [risk] trusted_contact and contact_topic are set, else None."""
    try:
        cfg = agent.config.data.get("risk", {}) or {}
    except Exception:
        return None
    name = str(cfg.get("trusted_contact", "")).strip()[:30]
    send = contact.ntfy_sender(cfg.get("contact_topic", ""))
    call_url = str(cfg.get("contact_call_url", "")).strip()
    return (name, send, call_url if contact.valid_call_url(call_url) else "",
            cfg.get("auto_alert", False) is True) if name and send else None


def attach(agent) -> RiskWire:
    bridge = getattr(agent, "bridge", None)
    proactive = getattr(agent, "proactive", None)
    guardian, screen_size = None, None
    try:
        guardian, screen_size = _make_guardian(agent)
    except Exception:
        log.debug("risk second opinion unavailable", exc_info=True)
    wire = RiskWire(agent.bus, ask=getattr(proactive, "ask", None),
                    call=getattr(bridge, "call", None),
                    broadcast=getattr(bridge, "broadcast", None),
                    guardian=guardian, screen_size=screen_size,
                    overlay_active=lambda: getattr(agent, "overlay", None) is not None,
                    contact=_make_contact(agent))
    if hasattr(bridge, "on_page"):
        bridge.on_page = wire.on_page
    wire.start()
    return wire
