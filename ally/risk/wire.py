"""Adapt local page and process rules to the risk event bus."""

import logging
import math
import re
import secrets
import struct
import threading
import time
import unicodedata
import urllib.parse

from ally.contracts import EventBus, Offer, Risk
from . import actions, behavior, callwindow, contact, domains, fakescan, fields, joined, process, soft, text
from .copy import load_copy, render
from .guardian import Evidence
from .remote_touch import RemoteTouch
from .screenshare import ScreenShare

log = logging.getLogger("ally")
_ORDER = ("scam_popup", "remote_access", "untrusted_site", "payment_field")
PAUSE_SECONDS = 30
HOLD_SECONDS = 120
TIMEOUT_SNOOZE_SECONDS = 120
DECLINE_SNOOZE_SECONDS = 1800


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
    def __init__(self, window_s=HOLD_SECONDS, clock=time.time):
        self.window_s = window_s
        self.clock = clock
        self._seen = {}
        self._lock = threading.Lock()

    @staticmethod
    def key(risk):
        if isinstance(risk, tuple):
            return risk
        if risk.source == "risk.fakescan":
            return (risk.app, risk.kind, risk.source, risk.evidence)
        return (domains.host_of(risk.url) if risk.url else risk.app, risk.kind)

    def fresh(self, risk) -> bool:
        with self._lock:
            now = self.clock()
            self._seen = {k: until for k, until in self._seen.items() if now < until}
            key = self.key(risk)
            if key in self._seen:
                return False
            self._seen[key] = now + self.window_s
            return True

    def snooze(self, key, seconds) -> None:
        with self._lock:
            self._seen[self.key(key)] = self.clock() + seconds

    def forget(self, key) -> None:
        with self._lock:
            self._seen.pop(self.key(key), None)

    def clear(self) -> None:
        with self._lock:
            self._seen.clear()


class RiskWire:
    def __init__(self, bus: EventBus, snapshot_fn=process.snapshot_processes,
                 poll_seconds=10.0, clock=time.time, deduper=None, ask=None, call=None,
                 broadcast=None, kill=actions._kill, guardian=None, marker=None, screen_size=None,
                 overlay_active=None, review_timeout_s=10.0, contact=None, notify=None, say=None,
                 screen_capture=None, screen_share=True, done=None, set_reserved=None, remote_touch=None):
        self.set_reserved = set_reserved
        self.call_window_rect = None
        self._call_window_stop = threading.Event()
        self._call_window_thread = None
        self.screen_capture = screen_capture
        self.screen_share = screen_share
        self._screen_share = None
        self.contact = contact  # (name, send, Meet URL, auto_alert, service, Spacecall URL), or None
        self.auto_alert = bool(contact and len(contact) > 3 and contact[3])
        self._last_auto_alert = None
        self.bus = bus
        self.overlay_active = overlay_active or (lambda: False)  # the in-page banner only stands in for it
        self.guardian = guardian        # second opinion from a vision model (consent and budget live inside it)
        self.review_timeout_s = review_timeout_s
        self.marker = marker            # physical primary-screen pixels, through the Tk queue
        self.screen_size = screen_size  # () -> (width, height) of the screenshot the guardian saw
        self._marks = {}
        self._tab_ids = {}
        self._page_questions = {}
        self._closed_tabs = set()
        self._remote_touch = remote_touch or RemoteTouch()
        self._helper_call_open = False
        self._helper_call_generation = 0
        self._helper_announced = False
        self._helper_dimensions = None
        self._helper_dimensions_until = 0
        self.ask = ask
        self.call = call
        self.broadcast = broadcast
        self.kill = kill
        self.done = done or notify or (lambda message: None)
        self.notify = notify or (lambda message: None)
        self.say = say or (lambda message: None)
        self._inflight = set()
        self._generation = 0
        self._unsubscribe = None
        self._unsubscribe_reset = None
        self.snapshot_fn = snapshot_fn
        self.poll_seconds = poll_seconds
        self.clock = clock
        self.deduper = deduper if deduper is not None else Deduper(clock=clock)
        self._lock = threading.Lock()
        self._snapshot = []
        self._watcher = process.RemoteWatcher()
        self._fake_scan_watcher = fakescan.FakeScanWatcher()
        self._event_viewer_warned = False
        self._baseline = False
        self.last_page = {"url": "", "card_present": False, "last_scam_text_at": None}
        self._stop = threading.Event()
        self._thread = None
        self._unsubscribe_helper = self.bus.subscribe("risk_call_ended", self._helper_call_ended)

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
        key = Deduper.key(risk)
        with self._lock:
            if self._stop.is_set() or key in self._inflight:
                return
            self._inflight.add(key)
            generation = self._generation
            self.deduper.snooze(key, HOLD_SECONDS)
            tab_id = self._tab_ids.pop(key, None)
            if tab_id in self._closed_tabs:
                self._inflight.discard(key)
                return
            question = {"risk": risk, "tab_id": tab_id, "cancelled": threading.Event(), "offer": None}
            self._page_questions[key] = question

        def ask_offer(offer):
            with self._lock:
                if question["cancelled"].is_set():
                    return "dismissed"
                question["offer"] = offer
            return self.ask(offer)

        prompt, action_id = proposal

        def ask_and_act():
            answer, snooze_seconds = "timeout", TIMEOUT_SNOOZE_SECONDS
            try:
                with self._lock:
                    if generation != self._generation or self._stop.is_set() or question["cancelled"].is_set():
                        return
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
                answer = ask_offer(offer)
                with self._lock:
                    current = (generation == self._generation and not self._stop.is_set()
                               and not question["cancelled"].is_set())
                called = offer.choice == "dismissed" and offer.answered_by == "call"
                if called:
                    answer, snooze_seconds = "dismissed", HOLD_SECONDS
                if answer == "no" and current:
                    wording = render("confirm_close")
                    confirmation = Offer(wording.title + " " + wording.body,
                                         options=tuple(a.label for a in wording.actions),
                                         context="risk", reason=risk, timeout=20.0)
                    answer = ask_offer(confirmation)
                    if answer == "no":
                        snooze_seconds = DECLINE_SNOOZE_SECONDS
                if answer not in ("yes", "no") and not called:
                    answer = "timeout"
                with self._lock:
                    current = (generation == self._generation and not self._stop.is_set()
                               and not question["cancelled"].is_set())
                if answer == "yes" and current:
                    ok, fallback = self._perform_action(action_id, risk, tab_id)
                    self.bus.publish("risk_action", {"action": action_id, "ok": ok,
                                                     "kind": risk.kind, "fallback": fallback})
                    # Page reports can repeat while the tool remains open; avoid another prompt straight away.
                    snooze_seconds = HOLD_SECONDS if action_id == "close_tool" and not ok else 0
            except Exception:
                answer, snooze_seconds = "timeout", TIMEOUT_SNOOZE_SECONDS
                log.debug("risk offer failed", exc_info=True)
            finally:
                with self._lock:
                    current = generation == self._generation and not question["cancelled"].is_set()
                    if self._page_questions.get(key) is question:
                        self._page_questions.pop(key, None)
                    if question["cancelled"].is_set():
                        self._inflight.discard(key)
                    if current:
                        if snooze_seconds:
                            self.deduper.snooze(key, snooze_seconds)
                        else:
                            self.deduper.forget(key)
                        self._inflight.discard(key)
                if current:
                    self.bus.publish("risk_decision", {"key": key, "answer": answer,
                                                       "snooze_seconds": snooze_seconds})

        try:
            threading.Thread(target=ask_and_act, name="risk-ask", daemon=True).start()
        except Exception:
            with self._lock:
                self._inflight.discard(key)
                self._page_questions.pop(key, None)
                self.deduper.snooze(key, TIMEOUT_SNOOZE_SECONDS)

    def _perform_action(self, action_id, risk, tab_id) -> tuple[bool, str]:
        ok = actions.perform(action_id, risk, self.call,
                             snapshot=self.snapshot_fn, kill=self.kill, tab_id=tab_id)
        fallback, message_key = "none", None
        if ok:
            message_key = "closed_tool" if action_id == "close_tool" else "closed_page"
        elif action_id == "close_tool":
            # Guardian review changes the source of a joined warning but retains its payment-page URL.
            if (risk.source in ("risk.joined", "risk.guardian")
                    and (risk.url or tab_id is not None)
                    and actions.close_page(self.call, risk.url, tab_id)):
                fallback = "closed_page"
            message_key = "tool_close_failed_page" if fallback == "closed_page" else "tool_close_failed"
        if message_key:
            try:
                tool = " ".join(text.redact(risk.app or "the remote-control tool").split())[:80]
                message = load_copy()["confirm_close"][message_key].format(tool=tool)
            except Exception:
                log.debug("risk result wording failed", exc_info=True)
            else:
                for callback in (self.notify, self.say):
                    try:
                        callback(message)
                    except Exception:
                        log.debug("risk result message failed", exc_info=True)
        return ok, fallback

    def _call_url(self) -> str:
        spacecall_url = self._spacecall_url()
        if spacecall_url:
            return spacecall_url
        url = self.contact[2] if self.contact and len(self.contact) > 2 else ""
        return url if contact.valid_call_url(url) else ""

    def _spacecall_url(self) -> str:
        if self.contact and len(self.contact) > 5 and self.contact[4] == "spacecall":
            url = self.contact[5]
            if contact.valid_spacecall_url(url):
                return url
        return ""

    def _call_urls(self, risk) -> tuple[str, str]:
        base = self._spacecall_url()
        if not base:
            url = self._call_url()
            return url, url
        room = secrets.token_urlsafe(16)

        def url(role):
            fragment = urllib.parse.urlencode({"room": room, "role": role, "name": self.contact[0],
                                               "kind": risk.kind}, quote_via=urllib.parse.quote)
            if self.screen_share is False:
                fragment += "&screen=off"
            return base + "#" + fragment

        return url("elder"), url("helper")

    def _open_call(self, url) -> bool:
        if not url:
            return False
        try:  # the eye tracker gives up the webcam first, so the call page can open it
            self.bus.publish("risk_call_starting", {"name": self.contact[0] if self.contact else ""})
        except Exception:
            log.debug("couldn't announce the call")
        try:
            if self.call is not None:
                args = {"url": url, "name": self.contact[0]}
                base = self._spacecall_url()
                if base:
                    parts = urllib.parse.urlsplit(base)
                    args.update(service="spacecall", spacecallOrigin=f"https://{parts.netloc}")
                try:
                    result = self.call("openCall", args)
                except Exception:
                    result = None
                if not isinstance(result, dict) or not result.get("opened") or result.get("error"):
                    self.call("openUrl", {"url": url, "newTab": True})
            else:
                import webbrowser

                webbrowser.open(url)
            return True
        except Exception:
            log.debug("could not open the video call")  # exceptions can contain private room URLs
            try:  # no call after all: the eye tracker takes its camera back now, not after 45 minutes
                self.bus.publish("risk_call_ended", {"service": "none"})
            except Exception:
                pass
            return False

    def _publish_contact(self, risk, ok, opened, auto=False) -> None:
        event = {"name": self.contact[0], "kind": risk.kind, "ok": ok, "call_opened": opened}
        if auto:
            event["auto"] = True
        if self._spacecall_url():
            event["service"] = "spacecall"
        self.bus.publish("risk_contact", event)

    def on_call_ended(self, msg) -> None:
        service = msg.get("service")
        if service in ("meet", "spacecall"):
            self._stop_call_window()
            self._stop_screen_share()
            self.bus.publish("risk_call_ended", {"service": service})

    def _helper_call_opened(self, generation=None) -> None:
        with self._lock:
            if generation is not None and generation != self._helper_call_generation:
                return  # the window ended while the open request was returning
            self._helper_call_open = True
            self._helper_announced = False
            self._helper_dimensions = None
            self._remote_touch.reset()

    def _helper_call_ended(self, msg=None) -> None:
        with self._lock:
            self._helper_call_generation += 1
            self._helper_call_open = False
            self._remote_touch.reset()
            if self.marker is not None:
                try:
                    self.marker("clear", 0, 0)
                except Exception:
                    pass

    def on_helper_action(self, msg) -> None:
        """Accept only bounded call gestures; malformed bridge messages are inert."""
        try:
            if not isinstance(msg, dict):
                return
            action = msg.get("action")
            if action not in ("close_tab", "hl_start", "hl_move", "hl_end",
                              "touch_start", "touch_move", "touch_end"):
                return
            name = msg.get("helper_name", "Your helper")
            if not isinstance(name, str):
                return
            name = " ".join("".join(ch for ch in name if not unicodedata.category(ch).startswith("C")).split())[:30]
            name = name or "Your helper"
            if action == "close_tab":
                if msg.get("ok") is not True:
                    return
                self._helper_closed_page(msg.get("tab_id"))
                message = f"{name} closed that page for you."
            else:
                x, y = msg.get("x"), msg.get("y")
                if (type(x) not in (int, float) or type(y) not in (int, float)
                        or not math.isfinite(x) or not math.isfinite(y) or not 0 <= x <= 1 or not 0 <= y <= 1
                        or ("ok" in msg and msg["ok"] is not True)):
                    return
                with self._lock:
                    if not self._helper_call_open or self._stop.is_set():
                        return
                    phase = action.split("_", 1)[1]
                    try:
                        if action.startswith("hl_"):
                            self._helper_marker(phase, x, y)
                        elif self._remote_touch.handle(phase, x, y):
                            self._helper_marker("ripple", x, y)
                    except Exception:
                        log.debug("helper drawing unavailable")
                    if self._helper_announced:
                        return
                    self._helper_announced = True
                message = f"{name} is helping on your screen."
            for callback in (self.done, self.say):
                try:
                    callback(message)
                except Exception:
                    log.debug("helper action message failed", exc_info=True)
        except Exception:
            log.debug("helper action ignored", exc_info=True)

    def _helper_closed_page(self, tab_id):
        with self._lock:
            tab_id = tab_id if type(tab_id) is int else self.last_page.get("tab_id")
            if tab_id is not None:
                self._closed_tabs.add(tab_id)
            url = self.last_page["url"]
            for key, question in self._page_questions.items():
                risk = question["risk"]
                if not risk.url:
                    continue
                if (tab_id is not None and question["tab_id"] is not None
                        and tab_id != question["tab_id"]):
                    continue
                if question["tab_id"] is None and url and risk.url != url:
                    continue
                question["cancelled"].set()
                offer = question["offer"]
                if offer is not None:
                    offer.resolve("dismissed", "helper")
                self.deduper.snooze(key, HOLD_SECONDS)
            if tab_id is None or self.last_page.get("tab_id") == tab_id:
                self.last_page.update(url="", card_present=False, last_scam_text_at=None)

    def _helper_marker(self, phase, x, y) -> None:
        if self.marker is None:
            return
        now = time.monotonic()
        if self._helper_dimensions is None or now >= self._helper_dimensions_until:
            if self.screen_capture is not None:
                raw = self.screen_capture()
                if isinstance(raw, tuple):
                    raw = raw[0]
                if isinstance(raw, bytes) and raw[:8] == b"\x89PNG\r\n\x1a\n":
                    width, height = struct.unpack(">II", raw[16:24])
                else:
                    width, height = raw.size
            elif self.screen_size is not None:
                width, height = self.screen_size()
            else:
                return
            if not (0 < width <= 100000 and 0 < height <= 100000):
                return
            self._helper_dimensions = (width, height)
            self._helper_dimensions_until = now + 1
        width, height = self._helper_dimensions
        # Whole primary capture in physical pixels, never the scaled transport JPEG or work area.
        self.marker(phase, min(width - 1, round(x * width)), min(height - 1, round(y * height)))

    def _stop_call_window(self):
        with self._lock:
            self._call_window_stop.set()
            self.call_window_rect = None
            if self.set_reserved is not None:
                self.set_reserved(None)
            thread, self._call_window_thread = self._call_window_thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=0.5)

    def _start_call_window(self):
        self._stop_call_window()
        with self._lock:
            if self._stop.is_set():
                return
            stopped = self._call_window_stop = threading.Event()

            def watch():
                deadline = time.monotonic() + 2 * 60 * 60
                try:
                    while not stopped.is_set() and time.monotonic() < deadline:
                        try:
                            rect = callwindow.keep_on_top()
                        except Exception:
                            rect = None
                        with self._lock:
                            if stopped.is_set():
                                return
                            self.call_window_rect = rect
                            if self.set_reserved is not None:
                                self.set_reserved(rect)
                        if stopped.wait(1.0):
                            return
                finally:
                    with self._lock:
                        if self._call_window_stop is stopped and not stopped.is_set():
                            self.call_window_rect = None
                            if self.set_reserved is not None:
                                self.set_reserved(None)

            self._call_window_thread = threading.Thread(target=watch, name="call-window", daemon=True)
            self._call_window_thread.start()

    def _stop_screen_share(self):
        with self._lock:
            share, self._screen_share = self._screen_share, None
            if share is not None:
                share.stop()

    def _start_screen_share(self, url):
        self._stop_screen_share()
        if not self._spacecall_url() or not self.screen_share or self.screen_capture is None:
            return
        try:
            room = urllib.parse.parse_qs(urllib.parse.urlsplit(url).fragment)["room"][0]
            with self._lock:
                if self._stop.is_set():
                    return
                self._screen_share = ScreenShare("https://maincloud.spacetimedb.com", "ally-call",
                                                 room, self.screen_capture)
                self._screen_share.start()
        except Exception:
            log.debug("could not start screen sharing")

    def _contact_action(self, offer, risk, alert) -> None:
        if alert is not None and not self._call_url() and (alert["sent"] or not alert["done"].is_set()):
            offer.extra_action = None
            return
        verb = "Call" if self._call_url() else "Tell"
        offer.extra_action = (f"{verb} {self.contact[0]}", lambda: self._tell(risk, alert, offer))

    def _send_contact(self, message, call_url="") -> bool:
        try:
            send = self.contact[1]
            return bool(send(message, call_url) if call_url else send(message))
        except Exception:
            log.debug("trusted contact send failed")  # sender exceptions can contain private room URLs
            return False

    def _auto_alert(self, risk):
        if not self.contact or not self.auto_alert or risk.severity != "high":
            return None
        with self._lock:
            now = self.clock()
            if self._last_auto_alert is not None and now - self._last_auto_alert < 300:
                return None
            self._last_auto_alert = now
        alert = {"sent": False, "done": threading.Event(), "offer": None, "call_urls": None,
                 "call_sent": False}

        def send():
            try:
                message = contact.message_for(risk.kind, call_available=bool(self._call_url()))
                ok = self._send_contact(message)
                with self._lock:
                    alert["sent"] = ok
                    alert["done"].set()
                    if alert["offer"] is not None:
                        self._contact_action(alert["offer"], risk, alert)
                self._publish_contact(risk, ok, False, auto=True)
            finally:
                alert["done"].set()

        try:
            threading.Thread(target=send, name="risk-auto-alert", daemon=True).start()
        except Exception:
            log.debug("trusted contact worker failed", exc_info=True)
            alert["done"].set()
        return alert

    def _tell(self, risk, alert=None, offer=None) -> str:
        """Runs in a worker when the person taps 'Call/Tell <name>'. Returns the line shown in the panel.
        With a call link: open the video call here and send the helper a push whose button joins it."""
        name = self.contact[0]
        if alert is not None:
            with self._lock:
                if alert["call_urls"] is None:
                    alert["call_urls"] = self._call_urls(risk)
                call_url, helper_url = alert["call_urls"]
        else:
            call_url, helper_url = self._call_urls(risk)
        self._stop_screen_share()
        opened = self._open_call(call_url)
        if opened:
            self.deduper.snooze(risk, HOLD_SECONDS)
            if offer is not None:
                offer.resolve("dismissed", "call")
            if self._spacecall_url():
                self._start_call_window()
            self._start_screen_share(call_url)
        if alert is not None:
            alert["done"].wait()  # the call opens immediately; only delivery waits for the pending push
        already_sent = alert is not None and alert["call_sent" if call_url else "sent"]
        message = contact.message_for(risk.kind)
        if helper_url:
            message = ("They're calling you through Ally. Tap to join." if alert is not None else
                       message + " Tap to join the video call.")
        ok = already_sent or self._send_contact(message, helper_url)
        if alert is not None and call_url:
            alert["call_sent"] = ok
        self._publish_contact(risk, ok, opened)
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
            if page["tab_id"] in self._closed_tabs:
                return
            risks = _scan(page, bool(joined.running_tools(self._snapshot)))
            beh = behavior.behavior_flags(page["signals"])
            if beh.suspicious:
                risks = _sorted(risks + [Risk("scam_popup", "medium", beh.evidence, page["url"],
                                              source="risk.behavior")])
            self.last_page.update(url=page["url"], tab_id=page["tab_id"],
                                  card_present=fields.detect_card_fields(page["fields"]).present)
            if any(r.kind == "scam_popup" for r in risks):
                self.last_page["last_scam_text_at"] = self.clock()
            combined = self._joined()
            candidates = ([combined] if combined else []) + risks
            chosen = next((r for r in candidates
                           if Deduper.key(r) not in self._inflight and self.deduper.fresh(r)), None)
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
                with self._lock:
                    if Deduper.key(candidate) not in self._inflight and self.deduper.fresh(candidate):
                        chosen, provisional = candidate, True
        if chosen and self.guardian is not None:
            before = chosen
            chosen = self._second_opinion(chosen, page, risks, beh, tools, extra, provisional)
            if chosen is None and provisional:
                self.deduper.forget(before)
        if chosen:
            with self._lock:
                if page["tab_id"] in self._closed_tabs:
                    return
                self._tab_ids[Deduper.key(chosen)] = page["tab_id"]
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
            tools = joined.running_tools(self._snapshot)
            now = self.clock()
            page = self.last_page
            payment_open = page["card_present"] or joined.joined_risk(("context",), page["url"]) is not None
            events = self._watcher.observe(self._snapshot, now,
                                           page["last_scam_text_at"], payment_open)
            names = [item.get("name") for item in self._snapshot if isinstance(item, dict)]
            scare_tools = self._fake_scan_watcher.observe(names, tools, now)
            if not tools:
                self._event_viewer_warned = False
            if not self._baseline:
                self._baseline = True
                return
            risks = []
            if set(tools) - previous_tools:
                combined = self._joined()
                if combined:
                    risks.append(combined)
            risks.extend(Risk("remote_access", e.severity, _evidence(e.evidence),
                              app=e.tool, source="risk.process") for e in events)
            fresh = [r for r in risks
                     if Deduper.key(r) not in self._inflight and self.deduper.fresh(r)]
            pending_fake_scan = any(len(key) > 2 and key[2] == "risk.fakescan" for key in self._inflight)
            if not pending_fake_scan:
                for scare in fakescan.SCARE_TOOLS:
                    if scare not in scare_tools or (scare == "mmc.exe" and self._event_viewer_warned):
                        continue
                    risk = Risk("remote_access", "high", _evidence(fakescan.SCARE_TOOLS[scare]),
                                app=tools[0], source="risk.fakescan")
                    if self.deduper.fresh(risk):
                        fresh.append(risk)
                        if scare == "eventvwr.exe":
                            self._event_viewer_warned = True
                        break
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
            self._generation += 1
            self._inflight.clear()
            self.deduper.clear()
            self._marks.clear()
            self._tab_ids.clear()
            self._event_viewer_warned = False
        if self.guardian is not None:
            self.guardian.clear_cache()
            self.guardian.budget.clear_pages()

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            if self._unsubscribe_helper is None:
                self._unsubscribe_helper = self.bus.subscribe("risk_call_ended", self._helper_call_ended)
            if self._unsubscribe is None:
                self._unsubscribe = self.bus.subscribe("risk_warning", self.on_risk_warning)
            if self._unsubscribe_reset is None:
                self._unsubscribe_reset = self.bus.subscribe("risk_reset", lambda msg: self.reset())
            self._thread = threading.Thread(target=self._loop, name="risk-wire", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        self._stop_call_window()
        self._helper_call_ended()
        with self._lock:
            if self._unsubscribe_helper is not None:
                self._unsubscribe_helper()
                self._unsubscribe_helper = None
            if self._screen_share is not None:
                self._screen_share.stop()
                self._screen_share = None
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
    spacecall_url = cfg.get("spacecall_url", "")
    spacecall_url = spacecall_url if contact.valid_spacecall_url(spacecall_url) else ""
    send = contact.ntfy_sender(cfg.get("contact_topic", ""), spacecall_url=spacecall_url)
    call_url = str(cfg.get("contact_call_url", "")).strip()
    return (name, send, call_url if contact.valid_call_url(call_url) else "",
            cfg.get("auto_alert", False) is True, cfg.get("call_service", "meet"), spacecall_url) if name and send else None


def attach(agent) -> RiskWire:
    bridge = getattr(agent, "bridge", None)
    proactive = getattr(agent, "proactive", None)
    def notify(message):
        # Looked up on each use: the overlay can replace NullUI after the wire attaches.
        ui = getattr(getattr(agent, "proactive", None), "ui", None)
        if ui is not None:
            # An existing risk card rejects info cards, including after an API answer.
            ui.show_card(message, actions=("OK",), kind="risk")

    def done(message):
        ui = getattr(getattr(agent, "proactive", None), "ui", None)
        if ui is not None:
            ui.show_card(message, actions=("OK",), kind="done")

    def marker(phase, x, y):
        ui = getattr(getattr(agent, "proactive", None), "ui", None)
        draw = getattr(ui, "marker", None)
        if draw is not None:
            draw(phase, x, y)

    def set_reserved(rect):
        ui = getattr(getattr(agent, "proactive", None), "ui", None)
        reserve = getattr(ui, "set_reserved", None)
        if reserve is not None:
            reserve(rect)

    def say(message):
        voice = getattr(getattr(agent, "proactive", None), "voice", None)
        if voice is not None and hasattr(voice, "say"):
            voice.say(message)

    guardian, screen_size = None, None
    try:
        guardian, screen_size = _make_guardian(agent)
    except Exception:
        log.debug("risk second opinion unavailable", exc_info=True)
    risk_config = (getattr(getattr(agent, "config", None), "data", {}) or {}).get("risk", {}) or {}
    wire = RiskWire(agent.bus, ask=getattr(proactive, "ask", None),
                    call=getattr(bridge, "call", None),
                    broadcast=getattr(bridge, "broadcast", None),
                    guardian=guardian, screen_size=screen_size,
                    overlay_active=lambda: getattr(agent, "overlay", None) is not None,
                    contact=_make_contact(agent), notify=notify, say=say, done=done, marker=marker,
                    set_reserved=set_reserved,
                    screen_capture=lambda: ScreenShare.capture_screen(lambda: agent.platform.screenshot_png()),
                    screen_share=risk_config.get("screen_share", True) is True)
    # Observe successful opens here so the separate call-window lifecycle code stays independent.
    open_call = wire._open_call

    def open_helper_call(url):
        with wire._lock:
            generation = wire._helper_call_generation
        opened = open_call(url)
        if opened:
            wire._helper_call_opened(generation)
        return opened

    wire._open_call = open_helper_call
    if hasattr(bridge, "on_page"):
        bridge.on_page = wire.on_page
        bridge.on_call_ended = wire.on_call_ended
        bridge.on_helper_action = wire.on_helper_action
    wire.start()
    return wire
