from helpers import ROOT  # noqa: F401

import ast
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ally.contracts import EventBus, Risk, RISK_KINDS
from ally.habits import HabitMemory
from ally.proactive import Coordinator, NullUI
from ally.risk import warning_for
from ally.risk import copy as warning_copy
from ally.risk.wire import Deduper, RiskWire, attach, scan_page

CARD = [{"autocomplete": "cc-number"}]
SCAM = {"url": "https://help.example", "text":
        "Microsoft Windows virus infected. Call support now at 202-555-0101."}


class WarningTests(unittest.TestCase):
    def test_all_kinds(self):
        for kind in RISK_KINDS:
            for value in warning_for(Risk(kind)):
                self.assertTrue(value)
                self.assertNotRegex(value, r"\d{7,}|!")

    def test_joined(self):
        r = Risk("remote_access", app="AnyDesk", source="risk.joined")
        wording = warning_copy.render("joined_remote_payment")
        self.assertEqual(warning_for(r), (wording.title + " " + wording.body, wording.spoken))

    def test_brand(self):
        self.assertIn("paypal.com", warning_for(Risk("untrusted_site", url="https://paypa1.com"))[0])

    def test_garbage(self):
        for value in (None, object(), SimpleNamespace(kind="wrong")):
            self.assertEqual(warning_for(value), ("This page may not be safe. Please be careful.",) * 2)

    def test_render_failure(self):
        with patch.object(warning_copy, "render", side_effect=ValueError):
            self.assertIn("Please be careful", warning_for(Risk("scam_popup"))[0])

    def test_purity(self):
        tree = ast.parse((ROOT / "ally/risk/__init__.py").read_text())
        self.assertFalse(any(isinstance(n, ast.ImportFrom) and n.module == "ally.contracts"
                             for n in ast.walk(tree)))

    def test_coordinator(self):
        bus, ui = EventBus(), NullUI()
        with tempfile.TemporaryDirectory() as d:
            coord = Coordinator(bus, lambda *a: None, HabitMemory(Path(d) / "habits.json"),
                                lambda: {}, ui=ui)
            coord.start(watch_context=False)
            try:
                risk = Risk("scam_popup", "high")
                bus.publish("risk", risk)
                self.assertTrue(coord.wait_idle())
                self.assertIn(("card", warning_for(risk)[0], ("OK",), "risk"), ui.shown)
            finally:
                coord.stop()


class ScanTests(unittest.TestCase):
    def test_scam(self):
        risk = scan_page(SCAM)[0]
        self.assertEqual((risk.kind, risk.severity), ("scam_popup", "high"))

    def test_domain(self):
        self.assertEqual(scan_page({"url": "https://paypa1.com"})[0].kind, "untrusted_site")

    def test_payment(self):
        self.assertEqual(scan_page({"url": "https://shop.example", "fields": CARD})[0].kind,
                         "payment_field")

    def test_clean(self):
        self.assertEqual(scan_page({"url": "https://recipes.example", "text": "A cake recipe"}), [])
        self.assertEqual(scan_page(dict(SCAM, url="https://support.microsoft.com")), [])

    def test_junk(self):
        for page in (None, [], 7, {}, {"url": [], "text": {}, "fields": None},
                     {"fields": (CARD,)}, {"fields": [None, 3]}):
            self.assertEqual(scan_page(page), [])

    def test_cap(self):
        self.assertEqual(scan_page({"text": "a" * 20000 + SCAM["text"]}), [])

    def test_known(self):
        for host in ("support.microsoft.com", "shop.ebay.com", "google.co.uk"):
            self.assertEqual(scan_page({"url": "https://" + host, "fields": CARD}), [])

    def test_evidence_and_order(self):
        risks = scan_page(dict(SCAM, url="https://paypa1.com", fields=CARD))
        self.assertEqual([r.kind for r in risks], ["scam_popup", "untrusted_site", "payment_field"])
        for risk in risks:
            self.assertLessEqual(len(risk.evidence), 200)
            self.assertNotRegex(risk.evidence, r"\d{7,}|https?://")


class WireTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.bus = EventBus()
        self.seen = []
        self.bus.subscribe("risk", self.seen.append)
        self.snap = []
        self.wire = RiskWire(self.bus, lambda: self.snap, clock=lambda: self.now)

    def test_deduper(self):
        d = Deduper(10, lambda: self.now)
        r = Risk("scam_popup", url="https://help.example/a")
        self.assertTrue(d.fresh(r))
        self.assertFalse(d.fresh(Risk("scam_popup", url="https://help.example/b")))
        self.now = 10
        self.assertTrue(d.fresh(r))
        self.assertTrue(d.fresh(Risk("remote_access", app="AnyDesk")))
        self.assertFalse(d.fresh(Risk("remote_access", app="AnyDesk")))
        self.now = 20
        d.fresh(r)
        self.assertEqual(len(d._seen), 1)

    def test_page_window(self):
        self.wire.on_page(SCAM)
        self.wire.on_page(SCAM)
        self.assertEqual(len(self.seen), 1)
        self.now = 1800
        self.wire.on_page(SCAM)
        self.assertEqual(len(self.seen), 2)

    def test_one_per_scan(self):
        self.wire.on_page(dict(SCAM, fields=CARD))
        self.assertEqual(len(self.seen), 1)
        self.assertEqual(self.seen[0].kind, "scam_popup")

    def test_process(self):
        self.wire.poll_once()
        self.assertEqual(self.seen, [])
        self.snap = [{"name": "AnyDesk.exe"}]
        self.wire.poll_once()
        self.wire.poll_once()
        self.assertEqual(len(self.seen), 1)
        self.assertEqual(self.seen[0].app, "AnyDesk")

    def test_baseline_with_tool(self):
        self.snap = [{"name": "AnyDesk.exe"}]
        self.wire.poll_once()
        self.assertEqual(self.seen, [])

    def test_joined_page(self):
        self.snap = [{"name": "AnyDesk.exe"}]
        self.wire.poll_once()
        self.wire.on_page({"type": "event", "name": "page", "url": "https://chase.com"})
        self.assertEqual(self.seen[0].source, "risk.joined")
        self.assertEqual(self.seen[0].severity, "high")

    def test_joined_on_process(self):
        self.wire.on_page({"url": "https://chase.com"})
        self.wire.poll_once()
        self.snap = [{"name": "AnyDesk.exe"}]
        self.wire.poll_once()
        self.assertEqual(self.seen[0].source, "risk.joined")
        self.assertTrue(all(r.severity == "high" for r in self.seen))

    def test_snapshot_error(self):
        self.wire.snapshot_fn = lambda: 1 / 0
        self.wire.poll_once()
        self.assertEqual(self.seen, [])

    def test_remote_payment_context(self):
        self.snap = [{"name": "AnyDesk.exe"}]
        self.wire.poll_once()
        self.wire.on_page({"url": "https://shop.example", "fields": CARD})
        self.assertEqual(self.seen[0].source, "risk.joined")

    def test_attach(self):
        for bridge in (None, SimpleNamespace(), SimpleNamespace(on_page=None)):
            with patch.object(RiskWire, "start") as start:
                wire = attach(SimpleNamespace(bus=self.bus, bridge=bridge))
                start.assert_called_once()
                if hasattr(bridge, "on_page"):
                    self.assertEqual(bridge.on_page, wire.on_page)

    def test_lifecycle(self):
        self.wire.stop()
        self.wire.start()
        thread = self.wire._thread
        self.wire.start()
        self.assertIs(thread, self.wire._thread)
        self.assertTrue(thread.daemon)
        self.wire.stop()
        self.wire.stop()
        self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
