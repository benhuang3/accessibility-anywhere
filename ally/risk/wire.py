"""Adapt local page and process rules to the risk event bus."""

import logging
import re
import threading
import time

from ally.contracts import EventBus, Risk
from . import domains, fields, joined, process, text

log = logging.getLogger("ally")
_ORDER = ("scam_popup", "remote_access", "untrusted_site", "payment_field")


def _evidence(value: str) -> str:
    return re.sub(r"(?:https?://|www\.)\S+", "a website", text.redact(value),
                  flags=re.IGNORECASE)[:200]


def _page(page) -> dict:
    if not isinstance(page, dict):
        return {}
    clean = {key: page.get(key, "")[:20000] if isinstance(page.get(key, ""), str) else ""
             for key in ("url", "text", "overlay_text")}
    clean.update({key: page.get(key) is True for key in ("fullscreen", "has_tel_link")})
    clean["fields"] = page.get("fields") if isinstance(page.get("fields"), list) else []
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


class RiskWire:
    def __init__(self, bus: EventBus, snapshot_fn=process.snapshot_processes,
                 poll_seconds=10.0, clock=time.time, deduper=None):
        self.bus = bus
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
            self.last_page.update(url=page["url"],
                                  card_present=fields.detect_card_fields(page["fields"]).present)
            if any(r.kind == "scam_popup" for r in risks):
                self.last_page["last_scam_text_at"] = self.clock()
            combined = self._joined()
            candidates = ([combined] if combined else []) + risks
            chosen = next((r for r in candidates if self.deduper.fresh(r)), None)
        if chosen:
            self.bus.publish("risk", chosen)

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

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="risk-wire", daemon=True)
            self._thread.start()

    def stop(self):
        with self._lock:
            self._stop.set()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=0.5)


def attach(agent) -> RiskWire:
    wire = RiskWire(agent.bus)
    bridge = getattr(agent, "bridge", None)
    if hasattr(bridge, "on_page"):
        bridge.on_page = wire.on_page
    wire.start()
    return wire
