from helpers import ROOT  # noqa: F401

import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ally.contracts import EventBus, Risk
from ally.risk import actions
from ally.risk.wire import RiskWire, attach


class ActionTests(unittest.TestCase):
    def test_page_prompts(self):
        for kind in ("scam_popup", "untrusted_site", "payment_field"):
            self.assertEqual(actions.ask_for(Risk(kind)),
                             ("Want me to close this page?", "close_page"))

    def test_remote_prompt(self):
        self.assertEqual(actions.ask_for(Risk("remote_access", app="AnyDesk")),
                         ("Want me to close AnyDesk?", "close_tool"))
        self.assertIn("the remote-control tool", actions.ask_for(Risk("remote_access"))[0])

    def test_redacted_capped(self):
        prompt, _ = actions.ask_for(Risk("remote_access", app="202-555-0101 " + "x" * 80))
        self.assertNotIn("555", prompt)
        self.assertEqual(prompt, "Want me to close " + ("a phone number " + "x" * 80)[:40] + "?")

    def test_unknown_prompt(self):
        self.assertIsNone(actions.ask_for(SimpleNamespace(kind="unknown")))

    def test_close_page(self):
        call = Mock(return_value={"closed": 2})
        self.assertTrue(actions.close_page(call, "https://help.example"))
        call.assert_called_once_with("closeTab", {"url": "https://help.example", "tabId": None})

    def test_page_failure(self):
        for reply in ({}, {"closed": 0}, None, {"closed": "bad"}):
            self.assertFalse(actions.close_page(lambda *a: reply, "https://help.example"))
        self.assertFalse(actions.close_page(Mock(side_effect=RuntimeError), "https://help.example"))

    def test_tool_matching(self):
        killed = []
        snapshot = lambda: [{"name": n} for n in ("chrome.exe", "AnyDesk.exe", "TeamViewer.exe")]
        self.assertEqual(actions.close_tool("AnyDesk", snapshot, killed.append), 1)
        self.assertEqual(killed, ["AnyDesk.exe"])
        for label in ("chrome", "; calc"):
            self.assertEqual(actions.close_tool(label, snapshot, killed.append), 0)
        self.assertEqual(killed, ["AnyDesk.exe"])

    def test_tool_errors(self):
        self.assertEqual(actions.close_tool("AnyDesk", Mock(side_effect=RuntimeError)), 0)
        self.assertEqual(actions.close_tool("AnyDesk", lambda: [{"name": "AnyDesk.exe"}],
                                            Mock(side_effect=RuntimeError)), 0)

    def test_non_windows(self):
        with patch("sys.platform", "linux"), patch.object(actions.subprocess, "run") as run:
            self.assertEqual(actions.close_tool("AnyDesk", lambda: [{"name": "AnyDesk.exe"}]), 0)
            run.assert_not_called()

    def test_windows_kill(self):
        with patch("sys.platform", "win32"), patch.object(actions.subprocess, "run",
                                                         return_value=SimpleNamespace(returncode=0)) as run:
            self.assertEqual(actions.close_tool("AnyDesk", lambda: [{"name": "AnyDesk.exe"}]), 1)
            run.assert_called_once_with(["taskkill", "/IM", "AnyDesk.exe", "/F"],
                                         capture_output=True, timeout=10)

    def test_kill_reports_access_denied_and_keeps_boolean_result(self):
        for stream in ("stdout", "stderr"):
            for output in (b"ERROR: Access is denied.", "ERROR: Access is denied."):
                reply = SimpleNamespace(returncode=1, **{stream: output})
                with self.subTest(stream=stream, output=output), patch("sys.platform", "win32"), \
                        patch.object(actions.subprocess, "run", return_value=reply):
                    self.assertEqual(actions._kill_result("QuickAssist.exe"), actions.KillResult(False, True))
                    self.assertIs(actions._kill("QuickAssist.exe"), False)

    def test_kill_distinguishes_other_results(self):
        for code, output, expected in ((0, b"SUCCESS", actions.KillResult(True)),
                                       (1, b"Process not found", actions.KillResult(False)),
                                       (2, b"Access is denied", actions.KillResult(False))):
            with self.subTest(code=code), patch("sys.platform", "win32"), \
                    patch.object(actions.subprocess, "run", return_value=SimpleNamespace(
                        returncode=code, stdout=output, stderr=b"")):
                self.assertEqual(actions._kill_result("QuickAssist.exe"), expected)

    def test_perform(self):
        risk = Risk("scam_popup", url="https://help.example")
        call = Mock(return_value={"closed": 1})
        self.assertTrue(actions.perform("close_page", risk, call))
        call.reset_mock()
        self.assertFalse(actions.perform("unknown", risk, call))
        call.assert_not_called()
        self.assertTrue(actions.perform("close_tool", Risk("remote_access", app="AnyDesk"), call,
                                       lambda: [{"name": "AnyDesk.exe"}], lambda name: True))


class WarningActionTests(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()
        self.results = []
        self.result_ready = threading.Event()
        self.bus.subscribe("risk_action", self.record)
        self.risk = Risk("scam_popup", url="https://help.example/a")
        self.msg = {"risk": self.risk.to_dict(), "text": "Please pause."}
        self.wires = []

    def record(self, result):
        self.results.append(result)
        self.result_ready.set()

    def wire(self, **kwargs):
        wire = RiskWire(self.bus, snapshot_fn=lambda: [], **kwargs)
        wire.start()
        self.wires.append(wire)
        return wire

    def tearDown(self):
        for wire in self.wires:
            wire.stop()

    def wait_finished(self, wire):
        # Taking the lock also makes the in-flight observation consistent.
        for _ in range(100):
            with wire._lock:
                if not wire._inflight:
                    return
            threading.Event().wait(0.01)
        self.fail("risk ask did not finish")

    def test_yes(self):
        ask = Mock(return_value="yes")
        call = Mock()
        wire = self.wire(ask=ask, call=call)
        with patch.object(actions, "perform", return_value=True) as perform:
            self.bus.publish("risk_warning", self.msg)
            self.assertTrue(self.result_ready.wait(2))
            perform.assert_called_once_with("close_page", self.risk, call,
                                            snapshot=wire.snapshot_fn, kill=wire.kill, tab_id=None)
        offer = ask.call_args.args[0]
        self.assertEqual((offer.context, offer.timeout, offer.options), ("risk", 20.0, ("yes", "no")))
        self.assertEqual(self.results, [{"action": "close_page", "ok": True,
                                         "kind": "scam_popup", "fallback": "none"}])

    def test_declines(self):
        for choice in ("no", "timeout", "dismissed"):
            with self.subTest(choice=choice), patch.object(actions, "perform") as perform:
                wire = self.wire(ask=lambda offer: choice)
                self.bus.publish("risk_warning", self.msg)
                self.wait_finished(wire)
                wire.stop()
                perform.assert_not_called()
                self.assertEqual(self.results, [])

    def test_no_ask(self):
        self.wire()
        with patch.object(actions, "perform") as perform:
            self.bus.publish("risk_warning", self.msg)
            perform.assert_not_called()
        self.assertEqual(self.results, [])

    def test_pending_deduplication(self):
        entered, release = threading.Event(), threading.Event()
        def ask(offer):
            entered.set()
            release.wait(2)
            return "timeout"
        ask_mock = Mock(side_effect=ask)
        wire = self.wire(ask=ask_mock)
        try:
            self.bus.publish("risk_warning", self.msg)
            self.assertTrue(entered.wait(2))
            other = dict(self.msg, risk=Risk("scam_popup", url="https://help.example/b").to_dict())
            self.bus.publish("risk_warning", other)
            ask_mock.assert_called_once()
        finally:
            release.set()
            self.wait_finished(wire)
        self.bus.publish("risk_warning", self.msg)
        self.wait_finished(wire)
        self.assertEqual(ask_mock.call_count, 2)

    def test_banner_and_failure(self):
        for fail in (False, True):
            broadcast = Mock(side_effect=RuntimeError if fail else None)
            wire = self.wire(ask=lambda offer: "yes", call=lambda *a: {"closed": 1}, broadcast=broadcast)
            self.result_ready.clear()
            self.bus.publish("risk_warning", self.msg)
            self.assertTrue(self.result_ready.wait(2))
            self.wait_finished(wire)
            calls = [c.args for c in broadcast.call_args_list]
            self.assertEqual(calls[0], ("exitFullscreen", {"url": self.risk.url}))
            if not fail:  # a failing broadcast stops there and the ask still runs
                self.assertEqual(calls[1], ("showRiskBanner", {"url": self.risk.url, "kind": self.risk.kind,
                                                               "text": self.msg["text"]}))
            wire.stop()

    def test_no_page_banner_while_the_overlay_shows_the_warning(self):
        broadcast = Mock()
        wire = self.wire(ask=lambda offer: "no", broadcast=broadcast, overlay_active=lambda: True)
        self.result_ready.clear()
        self.bus.publish("risk_warning", self.msg)
        self.wait_finished(wire)
        self.assertEqual([c.args[0] for c in broadcast.call_args_list], ["exitFullscreen"])
        wire.stop()

    def test_lifecycle(self):
        ask = Mock(return_value="timeout")
        banner = Mock()
        wire = self.wire(ask=ask, broadcast=banner)
        wire.start()
        self.bus.publish("risk_warning", self.msg)
        self.wait_finished(wire)
        ask.assert_called_once()
        wire.stop()
        self.bus.publish("risk_warning", self.msg)
        ask.assert_called_once()
        self.assertEqual(banner.call_count, 2)  # exitFullscreen + showRiskBanner, once each

    def test_attach_collaborators(self):
        agent = SimpleNamespace(bus=self.bus, proactive=SimpleNamespace(ask=Mock()),
                                bridge=SimpleNamespace(call=Mock(), broadcast=Mock()))
        agent.proactive.ui = SimpleNamespace(show_card=Mock())
        agent.proactive.voice = SimpleNamespace(say=Mock())
        with patch.object(RiskWire, "start"):
            wire = attach(agent)
        self.assertIs(wire.ask, agent.proactive.ask)
        self.assertIs(wire.call, agent.bridge.call)
        self.assertIs(wire.broadcast, agent.bridge.broadcast)
        wire.notify("Closed the page.")
        agent.proactive.ui.show_card.assert_called_once_with("Closed the page.", actions=("OK",), kind="risk")
        wire.say("hi")
        agent.proactive.voice.say.assert_called_with("hi")


class CloseFallbackTests(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()
        self.decisions = queue.Queue()
        self.results = []
        self.bus.subscribe("risk_decision", self.decisions.put)
        self.bus.subscribe("risk_action", self.results.append)
        self.notify, self.say = Mock(), Mock()
        self.call = Mock(return_value={"closed": 1})
        self.snapshot = [{"name": "QuickAssist.exe"}, {"name": "chrome.exe"}]
        self.wire = RiskWire(self.bus, ask=lambda offer: "yes", call=self.call,
                             snapshot_fn=lambda: self.snapshot, kill=lambda name: False,
                             notify=self.notify, say=self.say)
        self.addCleanup(self.wire.stop)
        self.bus.subscribe("risk", lambda risk: self.wire.on_risk_warning(
            {"risk": risk.to_dict(), "text": "Be careful."}))

    def warn(self, risk=None):
        if risk is None:
            self.wire._snapshot = self.snapshot
            self.wire.on_page({"url": "https://chase.com/bank", "tab_id": 42})
        else:
            self.wire.on_risk_warning({"risk": risk.to_dict(), "text": "Be careful."})
        return self.decisions.get(timeout=2)

    def test_access_denied_closes_remembered_bank_tab_and_notifies(self):
        self.wire.kill = actions._kill
        with patch("sys.platform", "win32"), patch.object(actions.subprocess, "run", return_value=SimpleNamespace(
                returncode=1, stdout=b"", stderr=b"Access is denied")), \
                patch.object(actions, "close_page", wraps=actions.close_page) as close_page:
            self.warn()
        close_page.assert_called_once_with(self.call, "https://chase.com/bank", 42)
        self.call.assert_called_once_with("closeTab", {"url": "https://chase.com/bank", "tabId": 42})
        message = "I closed the bank page. I can't close Quick Assist myself. Please click the X on its window."
        self.notify.assert_called_once_with(message)
        self.say.assert_called_once_with(message)
        self.assertEqual(self.results, [{"action": "close_tool", "ok": False,
                                         "kind": "remote_access", "fallback": "closed_page"}])

    def test_no_page_explains_manual_close(self):
        self.warn(Risk("remote_access", app="Quick Assist", source="risk.process"))
        message = "I can't close Quick Assist myself. Please click the X on its window, then hang up the phone."
        self.notify.assert_called_once_with(message)
        self.say.assert_called_once_with(message)
        self.call.assert_not_called()
        self.assertEqual(self.results[0]["fallback"], "none")
        self.assertFalse(self.results[0]["ok"])

    def test_failed_page_close_does_not_claim_it_closed(self):
        self.call.return_value = {"closed": 0}
        self.warn()
        self.assertIn("then hang up the phone", self.notify.call_args.args[0])
        self.assertEqual(self.results[0]["fallback"], "none")
        self.assertFalse(self.results[0]["ok"])

    def test_successful_tool_close_confirms_without_closing_page(self):
        self.wire.kill = Mock(return_value=True)
        self.warn()
        self.wire.kill.assert_called_once_with("QuickAssist.exe")
        self.notify.assert_called_once_with("Closed Quick Assist.")
        self.say.assert_called_once_with("Closed Quick Assist.")
        self.call.assert_not_called()
        self.assertTrue(self.results[0]["ok"])
        self.assertEqual(self.results[0]["fallback"], "none")

    def test_successful_page_close_confirms(self):
        self.warn(Risk("scam_popup", url="https://help.example"))
        self.notify.assert_called_once_with("Closed the page.")
        self.say.assert_called_once_with("Closed the page.")
        self.assertTrue(self.results[0]["ok"])

    def test_confirm_close_uses_the_same_fallback(self):
        self.wire.ask = Mock(side_effect=("no", "yes"))
        self.warn()
        self.assertEqual(self.wire.ask.call_count, 2)
        self.assertEqual(self.wire.ask.call_args.args[0].options, ("Close it", "Keep it open"))
        self.assertIn("I closed the bank page.", self.notify.call_args.args[0])
        self.assertEqual(self.results[0]["fallback"], "closed_page")

    def test_notify_failure_preserves_result_speech_and_yes_decision(self):
        self.notify.side_effect = RuntimeError("no display")
        self.wire.say = Mock(side_effect=RuntimeError("no voice"))
        decision = self.warn()
        self.assertEqual(decision["answer"], "yes")
        self.assertEqual(self.results[0]["fallback"], "closed_page")
        self.wire.say.assert_called_once_with(self.notify.call_args.args[0])

    def test_missing_process_still_protects_the_page(self):
        self.wire.snapshot_fn = lambda: []
        self.warn()
        self.assertEqual(self.results[0]["fallback"], "closed_page")
        self.assertFalse(self.results[0]["ok"])

    def test_guardian_reviewed_joined_risk_still_protects_the_page(self):
        self.warn(Risk("remote_access", "high", url="https://chase.com/bank",
                       app="Quick Assist", source="risk.guardian"))
        self.call.assert_called_once_with("closeTab", {"url": "https://chase.com/bank", "tabId": None})
        self.assertEqual(self.results[0]["fallback"], "closed_page")


class JoinedMultiToolTests(unittest.TestCase):
    def test_a_joined_warning_with_two_tools_closes_both_and_nothing_else(self):
        from ally.contracts import Risk
        from ally.risk import actions

        risk = Risk("remote_access", "high", app="AnyDesk, TeamViewer", source="risk.joined")
        snap = lambda: [{"name": "AnyDesk.exe"}, {"name": "TeamViewer.exe"}, {"name": "chrome.exe"}]  # noqa: E731
        killed = []
        self.assertTrue(actions.perform("close_tool", risk, None, snapshot=snap, kill=killed.append))
        self.assertEqual(sorted(killed), ["AnyDesk.exe", "TeamViewer.exe"])


if __name__ == "__main__":
    unittest.main()
