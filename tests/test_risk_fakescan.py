import queue
import unittest
from unittest.mock import Mock

from helpers import ROOT  # noqa: F401

from ally.contracts import EventBus, Risk
from ally.risk import actions, contact, warning_for
from ally.risk.copy import load_copy
from ally.risk.fakescan import FakeScanWatcher, SCARE_TOOLS
from ally.risk.wire import Deduper, RiskWire


class FakeScanWatcherTests(unittest.TestCase):
    def test_no_remote_tool_never_warns(self):
        watcher = FakeScanWatcher()
        self.assertEqual(watcher.observe([], (), 0), [])
        self.assertEqual(watcher.observe(list(SCARE_TOOLS), (), 10), [])

    def test_new_scare_tools_warn_once_per_appearance(self):
        watcher = FakeScanWatcher()
        self.assertEqual(watcher.observe([], ("AnyDesk",), 0), [])
        self.assertEqual(watcher.observe(list(SCARE_TOOLS), ("AnyDesk",), 10), sorted(SCARE_TOOLS))
        self.assertEqual(watcher.observe(list(SCARE_TOOLS), ("AnyDesk",), 20), [])

    def test_preexisting_tools_and_first_remote_snapshot_are_baselines(self):
        for already_observed in (False, True):
            with self.subTest(already_observed=already_observed):
                watcher = FakeScanWatcher()
                if already_observed:
                    watcher.observe(["eventvwr.exe"], (), 0)
                self.assertEqual(watcher.observe(["eventvwr.exe"], ("AnyDesk",), 10), [])
                self.assertEqual(watcher.observe(["eventvwr.exe"], ("AnyDesk",), 20), [])

    def test_baseline_resets_when_remote_tool_closes(self):
        watcher = FakeScanWatcher()
        watcher.observe([], ("AnyDesk",), 0)
        watcher.observe(["netstat.exe"], (), 10)
        self.assertEqual(watcher.observe(["eventvwr.exe"], ("Quick Assist",), 20), [])
        self.assertEqual(watcher.observe(["eventvwr.exe", "netstat.exe"], ("Quick Assist",), 30),
                         ["netstat.exe"])

    def test_common_shells_and_ally_collectors_never_warn(self):
        watcher = FakeScanWatcher()
        watcher.observe([], ("AnyDesk",), 0)
        names = ["cmd.exe", "powershell.exe", "conhost.exe", "tasklist.exe", "taskkill.exe"]
        self.assertEqual(watcher.observe(names, ("AnyDesk",), 10), [])
        self.assertTrue(set(names).isdisjoint(SCARE_TOOLS))

    def test_names_are_case_insensitive_and_junk_is_ignored(self):
        watcher = FakeScanWatcher()
        watcher.observe(["NETSTAT.EXE"], ("AnyDesk",), 0)
        self.assertEqual(watcher.observe(["netstat.exe", " EVENTVWR.EXE ", "eventvwr.exe", None, 42],
                                         ("AnyDesk",), 10), ["eventvwr.exe"])
        self.assertEqual(watcher.observe(["NETSTAT.EXE", "EVENTVWR.EXE"], ("AnyDesk",), 20), [])


class FakeScanWireTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.snap = []
        self.bus = EventBus()
        self.risks, self.offers, self.results, self.messages = [], [], [], []
        self.decisions = queue.Queue()
        self.bus.subscribe("risk", self.risks.append)
        self.bus.subscribe("risk_action", self.results.append)
        self.bus.subscribe("risk_decision", self.decisions.put)
        self.wire = None

    def make_wire(self, answers=None, **kwargs):
        def ask(offer):
            self.offers.append(offer)
            return next(answers, "timeout")

        self.wire = RiskWire(self.bus, snapshot_fn=Mock(side_effect=lambda: self.snap),
                             clock=lambda: self.now,
                             ask=kwargs.pop("ask", ask if answers is not None else None),
                             kill=kwargs.pop("kill", Mock(return_value=True)),
                             notify=self.messages.append, **kwargs)
        self.addCleanup(self.wire.stop)
        if self.wire.ask is not None:
            self.bus.subscribe("risk_warning", self.wire.on_risk_warning)
            self.bus.subscribe("risk", lambda risk: self.bus.publish(
                "risk_warning", {"risk": risk.to_dict(), "text": warning_for(risk)[0]}))
        return self.wire

    def poll(self, *names):
        self.snap = [{"name": name} for name in names]
        self.now += 10
        self.wire.poll_once()

    def fake_scans(self):
        return [risk for risk in self.risks if risk.source == "risk.fakescan"]

    def test_event_viewer_emits_high_warning_from_the_same_snapshot(self):
        wire = self.make_wire()
        self.assertEqual(wire.poll_seconds, 10.0)
        self.poll()
        self.poll("ANYDESK.EXE")
        self.poll("ANYDESK.EXE", "EVENTVWR.EXE")
        risk, = self.fake_scans()
        self.assertEqual((risk.kind, risk.severity, risk.app), ("remote_access", "high", "AnyDesk"))
        self.assertEqual(risk.url, "")
        self.assertEqual(risk.evidence, "Event Viewer")
        self.assertEqual(wire.snapshot_fn.call_count, 3)
        self.assertIn("just opened Event Viewer.", warning_for(risk)[0])

    def test_event_viewer_and_mmc_in_one_poll_emit_one_event_viewer_warning(self):
        self.make_wire()
        self.poll("AnyDesk.exe")
        self.poll("AnyDesk.exe", "mmc.exe", "eventvwr.exe")
        risk, = self.fake_scans()
        self.assertEqual(risk.evidence, "Event Viewer")
        self.assertIn("just opened Event Viewer.", warning_for(risk)[0])

    def test_each_poll_chooses_the_most_specific_new_scare_tool(self):
        priority = ("eventvwr.exe", "tree.com", "netstat.exe", "msconfig.exe",
                    "msinfo32.exe", "regedit.exe", "mmc.exe")
        for index, scare in enumerate(priority):
            with self.subTest(scare=scare):
                self.make_wire()
                self.risks.clear()
                self.poll("AnyDesk.exe")
                self.poll("AnyDesk.exe", *reversed(priority[index:]))
                risk, = self.fake_scans()
                self.assertEqual(risk.evidence, SCARE_TOOLS[scare])

    def test_later_mmc_is_the_same_trick_but_netstat_still_warns(self):
        self.make_wire()
        self.poll("AnyDesk.exe")
        self.poll("AnyDesk.exe", "eventvwr.exe")
        self.poll("AnyDesk.exe", "mmc.exe")
        self.assertEqual([risk.evidence for risk in self.fake_scans()], ["Event Viewer"])
        self.now += 120
        self.poll("AnyDesk.exe")
        self.poll("AnyDesk.exe", "mmc.exe")
        self.poll("AnyDesk.exe", "mmc.exe", "netstat.exe")
        self.assertEqual([risk.evidence for risk in self.fake_scans()],
                         ["Event Viewer", "a list of network connections"])

    def test_mmc_can_warn_in_a_new_remote_session(self):
        self.make_wire()
        self.poll("AnyDesk.exe")
        self.poll("AnyDesk.exe", "eventvwr.exe")
        self.poll()
        self.poll("AnyDesk.exe")
        self.poll("AnyDesk.exe", "mmc.exe")
        self.assertEqual([risk.evidence for risk in self.fake_scans()],
                         ["Event Viewer", "Event Viewer or another system console"])

    def test_pending_fake_scan_and_confirmation_suppress_other_scare_tools(self):
        entered, release = queue.Queue(), queue.Queue()

        def ask(offer):
            self.offers.append(offer)
            entered.put(offer)
            return release.get(timeout=3)

        self.make_wire(ask=ask)
        self.addCleanup(release.put, "timeout")
        self.poll("AnyDesk.exe")
        self.poll("AnyDesk.exe", "eventvwr.exe")
        self.assertEqual(entered.get(timeout=2).reason.evidence, "Event Viewer")
        self.now += 120
        self.poll("AnyDesk.exe", "eventvwr.exe", "netstat.exe")
        self.assertEqual(len(self.fake_scans()), 1)
        release.put("no")
        self.assertEqual(entered.get(timeout=2).prompt,
                         "Are you sure? Scammers often say it's safe to continue.")
        self.now += 120
        self.poll("AnyDesk.exe", "eventvwr.exe", "netstat.exe", "regedit.exe")
        self.assertEqual(len(self.fake_scans()), 1)
        release.put("no")
        self.assertEqual(self.decisions.get(timeout=2)["snooze_seconds"], 1800)
        self.poll("AnyDesk.exe", "eventvwr.exe")
        self.poll("AnyDesk.exe", "eventvwr.exe", "netstat.exe")
        self.assertEqual(entered.get(timeout=2).reason.evidence, "a list of network connections")
        self.assertEqual(len(self.fake_scans()), 2)
        release.put("timeout")
        self.assertEqual(self.decisions.get(timeout=2)["answer"], "timeout")

    def test_no_remote_and_preexisting_scare_tools_do_not_warn(self):
        self.make_wire()
        self.poll("eventvwr.exe")
        self.poll("AnyDesk.exe", "eventvwr.exe", "netstat.exe")
        self.poll("AnyDesk.exe", "eventvwr.exe", "netstat.exe")
        self.assertEqual(self.fake_scans(), [])

    def test_netstat_warns_separately_but_reopening_event_viewer_is_deduped(self):
        self.make_wire()
        self.poll("AnyDesk.exe")
        self.poll("AnyDesk.exe", "eventvwr.exe")
        self.poll("AnyDesk.exe", "eventvwr.exe", "netstat.exe")
        self.poll("AnyDesk.exe")
        self.poll("AnyDesk.exe", "eventvwr.exe", "netstat.exe")
        self.assertEqual([risk.evidence for risk in self.fake_scans()],
                         ["Event Viewer", "a list of network connections"])
        self.assertNotEqual(*(Deduper.key(risk) for risk in self.fake_scans()))

    def test_warning_for_uses_exact_fake_scan_copy_and_safe_descriptions(self):
        copy = load_copy()["fake_scan"]
        self.assertEqual(copy["title"], "This is a common scam trick.")
        self.assertEqual([action["id"] for action in copy["actions"]], ["close_tool", "explain"])
        self.assertEqual(copy["spoken"].count("."), 1)
        self.assertNotIn("?", copy["spoken"])
        for scare in (*SCARE_TOOLS.values(), "Call 202-555-0101 {tool}"):
            risk = Risk("remote_access", "high", scare, app="AnyDesk", source="risk.fakescan")
            expected = scare if scare in SCARE_TOOLS.values() else "a Windows system tool"
            self.assertEqual(warning_for(risk),
                             (copy["title"] + " " + copy["body"].format(scare=expected), copy["spoken"]))

    def check_contact_offer(self, call_url, label):
        send, call, killed = Mock(return_value=True), Mock(return_value={"opened": True}), []
        self.make_wire(iter(("yes",)), contact=("Priya", send, call_url),
                       call=call, kill=killed.append)
        self.poll("QuickAssist.exe")
        self.poll("QuickAssist.exe", "eventvwr.exe")
        self.assertEqual(self.decisions.get(timeout=2)["snooze_seconds"], 0)
        offer = self.offers[-1]
        self.assertEqual(offer.prompt, "Want me to close Quick Assist?")
        self.assertEqual(actions.ask_for(offer.reason)[1], "close_tool")
        self.assertEqual(offer.extra_action[0], label)
        self.assertEqual(killed, ["QuickAssist.exe"])
        self.assertEqual(self.results[-1], {"action": "close_tool", "ok": True,
                                            "kind": "remote_access", "fallback": "none"})
        offer.extra_action[1]()
        self.assertEqual(send.call_count, 1)
        if call_url:
            call.assert_called_once_with("openCall", {"url": call_url, "name": "Priya"})
        else:
            call.assert_not_called()

    def test_offer_closes_only_the_remote_tool_and_has_tell(self):
        self.check_contact_offer("", "Tell Priya")

    def test_offer_has_call_with_a_configured_room(self):
        self.check_contact_offer("https://meet.google.com/abc-defg-hij", "Call Priya")

    def test_confirmation_and_cooldown_apply_per_scare_tool(self):
        wire = self.make_wire(iter(("no", "no", "timeout")))
        self.poll("AnyDesk.exe")
        self.poll("AnyDesk.exe", "eventvwr.exe")
        decision = self.decisions.get(timeout=2)
        self.assertEqual((decision["answer"], decision["snooze_seconds"]), ("no", 1800))
        self.assertEqual(decision["key"], Deduper.key(self.fake_scans()[0]))
        self.assertEqual(len(self.offers), 2)
        self.assertEqual(self.offers[1].prompt, "Are you sure? Scammers often say it's safe to continue.")
        wire.kill.assert_not_called()
        self.now += 120
        self.poll("AnyDesk.exe")
        self.poll("AnyDesk.exe", "eventvwr.exe", "netstat.exe")
        self.assertEqual(self.decisions.get(timeout=2)["snooze_seconds"], 120)
        self.assertEqual(len(self.fake_scans()), 2)

    def test_failed_close_and_auto_alert_use_existing_flow(self):
        sent = queue.Queue()
        wire = self.make_wire(iter(("yes",)), contact=("Priya", lambda message: sent.put(message) or True,
                                                       "", True), kill=Mock(return_value=False))
        self.poll("QuickAssist.exe")
        self.poll("QuickAssist.exe", "eventvwr.exe")
        self.assertEqual(self.decisions.get(timeout=2)["snooze_seconds"], 120)
        self.assertEqual(sent.get(timeout=2), contact.message_for("remote_access"))
        wire.kill.assert_called_once_with("QuickAssist.exe")
        self.assertEqual(self.results[-1]["fallback"], "none")
        self.assertIn("Please click the X", self.messages[-1])


if __name__ == "__main__":
    unittest.main()
