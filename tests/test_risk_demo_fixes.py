"""Offline regression coverage for the risk demo fixes."""

from helpers import ROOT

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from ally.contracts import EventBus, Risk
from ally.risk import actions, domains, joined, warning_for
from ally.risk.copy import render
from ally.risk.guardian import Budget, Evidence, Guardian, Mark, Verdict
from ally.risk.wire import RiskWire, _evidence, scan_page

SCAM = {"url": "https://help.example/alert", "text":
        "Microsoft Windows virus infected. Call support now at 202-555-0101."}
WEAK = {"url": "https://help.example/refund", "text":
        "Download AnyDesk for remote support. Do not tell anyone about this refund."}
REVIEW = json.dumps(dict(verdict="scam", confidence=0.9, explanation="This may be a trick.",
                         actions=["close_page"]))


class EvidenceTests(unittest.TestCase):
    def test_long_explanation_ends_at_last_complete_sentence(self):
        first = "This page may be a trick. "
        second = "Please pause and close this page before sharing any personal details."
        self.assertEqual(_evidence(first + second + " Another unfinished sentence" * 12), first + second)

    def test_without_punctuation_ends_at_a_word(self):
        value = "Please pause before sharing details " * 20
        result = _evidence(value)
        self.assertLessEqual(len(result), 240)
        self.assertTrue(result.endswith("…"))
        self.assertTrue(value.startswith(result[:-1] + " "))
        self.assertEqual(_evidence("x" * 300), "…")

    def test_exact_sentence_boundary(self):
        for punctuation in (".", "!", "?"):
            sentence = "word " * 47 + "Done" + punctuation
            self.assertEqual(len(sentence), 240)
            self.assertEqual(_evidence(sentence), sentence)
            self.assertEqual(_evidence(sentence + " More text follows."), sentence)

    def test_redaction_is_kept(self):
        result = _evidence("Do not call 202-555-0101 or visit https://evil.example/help. "
                           "Please close this page. " + "More words " * 30)
        self.assertNotIn("202", result)
        self.assertNotIn("evil.example", result)
        self.assertIn("a phone number", result)
        self.assertIn("a website", result)
        self.assertTrue(result.endswith("Please close this page."))


class CopyTests(unittest.TestCase):
    def test_non_bank_lookalikes_are_brand_neutral(self):
        for url, brand in (("https://netf1ix.com", "netflix"),
                           ("https://micros0ft.com", "microsoft")):
            with self.subTest(brand=brand):
                self.assertNotIn(brand, joined.BANK_BRANDS)
                self.assertEqual(domains.check_domain(url).brand, brand)
                visual, spoken = warning_for(Risk("untrusted_site", url=url))
                usual = domains.BRANDS[brand][0]
                self.assertIn("The real site is " + usual + ".", visual)
                self.assertNotIn("bank", (visual + spoken).lower())

    def test_page_tricks_copy_and_actions(self):
        risk = Risk("scam_popup", source="risk.behavior")
        wording = render("page_tricks")
        self.assertEqual(warning_for(risk), (wording.title + " " + wording.body, wording.spoken))
        self.assertIn("stopping you from leaving", wording.body)
        self.assertIn("taking over the screen", wording.body)
        self.assertNotIn("call", wording.body + wording.spoken)
        self.assertNotIn("?", wording.spoken)
        self.assertEqual([a.id for a in wording.actions], ["close_page", "explain"])


class ReviewTests(unittest.TestCase):
    def slow_review(self, page):
        finished = threading.Event()

        def review(evidence):
            time.sleep(0.4)
            finished.set()
            return Verdict("scam", "", 0.9, (), "A model explanation.",
                           (Mark("flag", (0.1, 0.1, 0.2, 0.2)),), ("close_page",))

        guardian = Mock(review=review)
        bus, seen = EventBus(), []
        bus.subscribe("risk", seen.append)
        wire = RiskWire(bus, guardian=guardian, review_timeout_s=0.04, screen_size=lambda: (100, 100))
        start = time.monotonic()
        wire.on_page(page)
        elapsed = time.monotonic() - start
        self.assertGreaterEqual(elapsed, 0.03)
        self.assertLess(elapsed, 0.3)
        return wire, seen, finished

    def test_timeout_keeps_the_rule_risk_and_ignores_late_verdict(self):
        wire, seen, finished = self.slow_review(SCAM)
        rule = scan_page(SCAM)[0]
        self.assertEqual(len(seen), 1)
        self.assertEqual((seen[0].source, seen[0].severity, seen[0].evidence),
                         (rule.source, rule.severity, rule.evidence))
        self.assertTrue(finished.wait(1))
        self.assertEqual(len(seen), 1)
        self.assertEqual(wire._marks, {})

    def test_timeout_on_weak_signs_stays_quiet(self):
        self.assertEqual(scan_page(WEAK), [])
        wire, seen, finished = self.slow_review(WEAK)
        self.assertEqual(seen, [])
        self.assertEqual(wire.deduper._seen, {})
        self.assertTrue(finished.wait(1))
        self.assertEqual(seen, [])

    def test_default_model_client_has_short_timeout_and_no_retries(self):
        with patch("ally.claude.Claude") as client:
            client.return_value.messages.return_value = {"content": [{"type": "text", "text": REVIEW}]}
            Guardian()._ask_model("system", "text", None, 700)
        client.assert_called_once_with(timeout=8.0, retries=0)


class TabTests(unittest.TestCase):
    def test_close_page_sends_tab_id(self):
        call = Mock(return_value={"closed": 1})
        self.assertTrue(actions.close_page(call, SCAM["url"], tab_id=42))
        call.assert_called_once_with("closeTab", {"url": SCAM["url"], "tabId": 42})

    def test_tab_id_travels_from_page_to_confirmed_action(self):
        bus, seen, done = EventBus(), [], threading.Event()
        bus.subscribe("risk", seen.append)
        bus.subscribe("risk_action", lambda msg: done.set())
        call = Mock(return_value={"closed": 1})
        wire = RiskWire(bus, snapshot_fn=lambda: [], ask=lambda offer: "yes", call=call)
        wire.start()
        try:
            wire.on_page(dict(SCAM, tab_id=42))
            # An unrelated page must not replace the warning's source tab.
            wire.on_page({"url": "https://recipes.example/", "tab_id": 99})
            bus.publish("risk_warning", {"risk": seen[0].to_dict(), "text": "Pause."})
            self.assertTrue(done.wait(2))
            call.assert_called_once_with("closeTab", {"url": SCAM["url"], "tabId": 42})
        finally:
            wire.stop()


class ResetTests(unittest.TestCase):
    def test_reset_clears_dedupe_marks_and_tabs_so_the_same_page_warns_again(self):
        bus, seen = EventBus(), []
        bus.subscribe("risk", seen.append)
        wire = RiskWire(bus)
        wire.on_page(dict(SCAM, tab_id=42))
        wire._marks[("help.example", "scam_popup")] = (1, 2, 3, 4)
        wire.on_page(SCAM)
        self.assertEqual(len(seen), 1)
        wire.reset()
        self.assertEqual((wire._marks, wire._tab_ids), ({}, {}))
        wire.on_page(SCAM)
        self.assertEqual(len(seen), 2)

    def test_reset_clears_cache_and_persisted_cooldowns_but_keeps_spend(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "budget.json"
            budget = Budget(state_path=path)
            model = Mock(return_value=(REVIEW, {"input_tokens": 7, "output_tokens": 3}))
            guardian = Guardian(ask_model=model, consented=lambda: True, budget=budget)
            evidence = Evidence(SCAM["url"], ("scam_text",))
            guardian.review(evidence)
            key = next(iter(budget._pages))
            before = budget.snapshot()
            self.assertTrue(guardian.review(evidence).cached)
            self.assertEqual(budget.allow(key), (False, "cooldown"))
            RiskWire(EventBus(), guardian=guardian).reset()
            self.assertEqual(guardian._cache, {})
            self.assertEqual(budget.snapshot(), before)
            restored = Budget(state_path=path)
            self.assertEqual(restored.snapshot(), before)
            self.assertTrue(restored.allow(key)[0])
            guardian.review(evidence)
            self.assertEqual(model.call_count, 2)
            self.assertEqual(budget.snapshot()["calls_today"], 2)

    def test_bus_reset_subscription_lifecycle(self):
        bus = EventBus()
        wire = RiskWire(bus, snapshot_fn=lambda: [])
        with patch.object(wire, "reset", wraps=wire.reset) as reset:
            try:
                wire.start()
                wire.start()
                wire.on_page(SCAM)
                bus.publish("risk_reset")
                reset.assert_called_once_with()
                self.assertEqual(wire.deduper._seen, {})
                wire.stop()
                bus.publish("risk_reset")
                reset.assert_called_once_with()
                wire.start()
                bus.publish("risk_reset")
                self.assertEqual(reset.call_count, 2)
            finally:
                wire.stop()


@unittest.skipUnless(shutil.which("node"), "Node.js is needed for the extension behavior check")
class ExtensionTests(unittest.TestCase):
    def test_close_tab_and_page_event(self):
        # Run the real service worker with in-memory Chrome APIs and no network connection.
        script = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const noopEvent = { addListener() {} };
let listener, tabs, removed;
const chrome = {
  storage: { local: { get: () => new Promise(() => {}) } },
  windows: { onFocusChanged: noopEvent },
  tabs: {
    onActivated: noopEvent, onZoomChange: noopEvent, onUpdated: noopEvent,
    async query() { return tabs; },
    async get(id) { const tab = tabs.find(t => t.id === id); if (!tab) throw Error("gone"); return tab; },
    async remove(ids) { removed.push(...(Array.isArray(ids) ? ids : [ids])); },
  },
  commands: { onCommand: noopEvent },
  runtime: {
    onMessage: { addListener(fn) { listener = fn; } },
    onStartup: noopEvent, onInstalled: noopEvent,
  },
  alarms: { async get() { return {}; }, onAlarm: noopEvent },
};
const context = vm.createContext({ chrome, URL, clearTimeout, clearInterval });
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
const closeTab = vm.runInContext("ACTIONS.closeTab", context);
(async () => {
  const url = "https://help.example/alert#old";
  for (const [tabId, current, expected] of [
    [42, "https://help.example/new-path#new", [42]],
    [42, "https://other.example/alert", [7]],
    [42, "http://help.example/alert", [7]],
    [42, "https://help.example:444/alert", [7]],
    [99, "https://other.example/alert", [7]],
    [null, "https://other.example/alert", [7]],
    ["42", "https://help.example/new-path", [7]],
  ]) {
    removed = [];
    tabs = [{ id: 42, url: current }, { id: 7, url: "https://help.example/alert#new" },
            { id: 8, url: "https://help.example/alert?different=1" }, { id: 9 }];
    const result = await closeTab({ url, tabId });
    assert.deepEqual(removed, expected);
    assert.equal(result.closed, expected.length);
  }
  removed = [];
  tabs = [{ id: 1, url: "https://help.example/alert#a" },
          { id: 2, url: "https://help.example/alert#b" }];
  assert.equal((await closeTab({ url })).closed, 2);
  assert.deepEqual(removed, [1, 2]);
  tabs = [];
  assert.equal((await closeTab({ url, tabId: 42 })).closed, 0);
  vm.runInContext("send = msg => { globalThis.forwarded = msg; return true; }", context);
  listener({ kind: "pageRisk", url, tab_id: 123 }, { tab: { id: 42 } }, () => {});
  assert.equal(context.forwarded.tab_id, 42);
  assert.equal(context.forwarded.name, "page");
  listener({ kind: "pageRisk", url }, {}, () => {});
  assert.equal(context.forwarded.tab_id, undefined);
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
        result = subprocess.run([shutil.which("node"), "-e", script, str(ROOT / "extension/background.js")],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
