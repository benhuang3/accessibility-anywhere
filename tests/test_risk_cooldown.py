import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from helpers import ROOT  # noqa: F401

from ally.contracts import EventBus, Risk
from ally.overlay.layout import button_labels
from ally.overlay.overlay import Overlay
from ally.risk.wire import Deduper, PAUSE_SECONDS, RiskWire, attach


SCAM = {"url": "https://help.example/scam", "tab_id": 42,
        "text": "Microsoft Windows virus infected. Call support now at 202-555-0101."}


class CooldownTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.bus = EventBus()
        self.risks, self.offers, self.actions = [], [], []
        self.decisions = queue.Queue()
        self.bus.subscribe("risk", self.risks.append)
        self.bus.subscribe("risk_action", self.actions.append)
        self.bus.subscribe("risk_decision", self.decisions.put)
        self.wire = None

    def make_wire(self, answers=(), ask=None, **kwargs):
        answers = iter(answers)

        def record(offer):
            self.offers.append(offer)
            return ask(offer) if ask else next(answers, "timeout")

        self.wire = RiskWire(self.bus, snapshot_fn=lambda: [], clock=lambda: self.now,
                             ask=record, call=Mock(return_value={"closed": 1}), **kwargs)
        self.bus.subscribe("risk_warning", self.wire.on_risk_warning)
        self.bus.subscribe("risk", lambda risk: self.bus.publish(
            "risk_warning", {"risk": risk.to_dict(), "text": "Be careful."}))
        self.addCleanup(self.wire.stop)
        return self.wire

    def warn(self):
        self.wire.on_page(SCAM)
        return self.decisions.get(timeout=2)

    def test_yes_forgets_the_page_and_preserves_the_tab_action(self):
        wire = self.make_wire(("yes", "yes"))
        self.assertEqual(self.warn()["snooze_seconds"], 0)
        wire.call.assert_called_once_with("closeTab", {"url": SCAM["url"], "tabId": 42})
        self.warn()
        self.assertEqual(len(self.risks), 2)
        self.assertEqual(len(self.actions), 2)

    def test_keep_open_snoozes_for_thirty_minutes(self):
        wire = self.make_wire(("no", "no"))
        self.assertEqual(self.warn()["snooze_seconds"], 1800)
        self.now = 29 * 60
        wire.on_page(SCAM)
        self.assertEqual(len(self.risks), 1)
        self.now = 30 * 60
        self.warn()
        self.assertEqual(len(self.risks), 2)
        wire.call.assert_not_called()

    def test_second_yes_closes_and_forgets(self):
        self.make_wire(("no", "yes", "yes"))
        self.assertEqual(self.warn()["snooze_seconds"], 0)
        self.assertEqual(len(self.actions), 1)
        self.assertTrue(self.actions[0]["ok"])
        self.warn()
        self.assertEqual(len(self.risks), 2)

    def test_unanswered_first_question_snoozes_for_two_minutes(self):
        wire = self.make_wire(ask=lambda offer: answer)
        for answer in ("timeout", "dismissed", None, "unknown"):
            with self.subTest(answer=answer):
                self.now = 0
                wire.reset()
                self.risks.clear()
                self.offers.clear()
                self.assertEqual(self.warn()["snooze_seconds"], 120)
                self.assertEqual(len(self.offers), 1)
                self.now = 60
                wire.on_page(SCAM)
                self.assertEqual(len(self.risks), 1)
                self.now = 120
                self.warn()
                self.assertEqual(len(self.risks), 2)
                wire.call.assert_not_called()

    def test_second_timeout_snoozes_from_the_time_of_the_answer(self):
        def ask(offer):
            if len(self.offers) == 1:
                self.now = 20
                return "no"
            self.now = 40
            return "timeout"

        wire = self.make_wire(ask=ask)
        self.assertEqual(self.warn()["snooze_seconds"], 120)
        self.now = 159
        wire.on_page(SCAM)
        self.assertEqual(len(self.risks), 1)
        self.now = 160
        self.warn()
        self.assertEqual(len(self.risks), 2)

    def test_confirmation_has_fresh_id_labels_and_no_pause_or_contact(self):
        wire = self.make_wire(("no", "no"), contact=("Priya", lambda text: True))
        risk = Risk("remote_access", "high", url="https://chase.com", app="AnyDesk", source="risk.joined")
        wire.on_risk_warning({"risk": risk.to_dict(), "text": "Be careful."})
        self.decisions.get(timeout=2)
        first, second = self.offers
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(first.pause_seconds, PAUSE_SECONDS)
        self.assertIsNotNone(first.extra_action)
        self.assertEqual(second.prompt, "Are you sure? Scammers often say it's safe to continue.")
        self.assertEqual(button_labels(second.prompt, second.options), ("Close it", "Keep it open"))
        self.assertEqual((second.context, second.reason, second.timeout), ("risk", risk, 20.0))
        self.assertFalse(getattr(second, "pause_seconds", 0))
        self.assertIsNone(getattr(second, "extra_action", None))

    def test_pending_questions_suppress_duplicates_even_after_the_hold_expires(self):
        entered, release = queue.Queue(), queue.Queue()

        def ask(offer):
            entered.put(offer)
            return release.get(timeout=3)

        wire = self.make_wire(ask=ask)
        self.addCleanup(release.put, "timeout")
        wire.on_page(SCAM)
        first = entered.get(timeout=2)
        self.now = 121
        wire.on_page(SCAM)
        wire.on_risk_warning({"risk": first.reason.to_dict(), "text": "duplicate"})
        self.assertEqual(len(self.risks), 1)
        self.assertEqual(len(self.offers), 1)
        release.put("no")
        entered.get(timeout=2)
        self.now = 242
        wire.on_page(SCAM)
        self.assertEqual(len(self.risks), 1)
        release.put("no")
        self.assertEqual(self.decisions.get(timeout=2)["snooze_seconds"], 1800)

    def test_risk_decision_reports_the_final_answer(self):
        self.make_wire(("no", "no"))
        self.assertEqual(self.warn(), {"key": ("help.example", "scam_popup"),
                                       "answer": "no", "snooze_seconds": 1800})
        self.assertEqual(self.actions, [])

    def test_reset_clears_snoozes(self):
        wire = self.make_wire(("no", "no"))
        self.warn()
        wire.reset()
        self.assertEqual(wire.deduper._seen, {})
        self.warn()
        self.assertEqual(len(self.risks), 2)

    def test_reset_during_a_question_does_not_restore_its_snooze(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()

        def ask(offer):
            entered.set()
            release.wait(2)
            return "no"

        wire = self.make_wire(ask=ask)
        self.addCleanup(release.set)
        # Join the worker so the assertion checks its final cleanup as well.
        worker = []
        original_ask = wire.ask

        def record_worker(offer):
            worker.append(threading.current_thread())
            try:
                return original_ask(offer)
            finally:
                finished.set()

        wire.ask = record_worker
        wire.on_page(SCAM)
        self.assertTrue(entered.wait(2))
        wire.reset()
        release.set()
        self.assertTrue(finished.wait(2))
        worker[0].join(timeout=2)
        self.assertEqual(wire.deduper._seen, {})
        self.assertEqual(wire._inflight, set())
        self.assertEqual(len(self.offers), 1)
        self.assertTrue(self.decisions.empty())

    def test_second_yes_closes_the_original_remote_tool(self):
        killed = []
        wire = self.make_wire(("no", "yes"), kill=killed.append)
        wire.snapshot_fn = lambda: [{"name": "AnyDesk.exe"}, {"name": "chrome.exe"}]
        risk = Risk("remote_access", "high", app="AnyDesk")
        wire.on_risk_warning({"risk": risk.to_dict(), "text": "Be careful."})
        self.assertEqual(self.decisions.get(timeout=2), {
            "key": ("AnyDesk", "remote_access"), "answer": "yes", "snooze_seconds": 0})
        self.assertEqual(killed, ["AnyDesk.exe"])
        self.assertTrue(wire.deduper.fresh(risk))

    def test_failed_tool_close_holds_repeated_page_warnings_for_two_minutes(self):
        wire = self.make_wire(("yes", "yes"), kill=lambda name: False)
        wire.snapshot_fn = lambda: [{"name": "QuickAssist.exe"}]
        wire.poll_once()
        page = {"url": "https://chase.com/bank", "tab_id": 42}
        wire.on_page(page)
        self.assertEqual(self.decisions.get(timeout=2), {
            "key": ("chase.com", "remote_access"), "answer": "yes", "snooze_seconds": 120})
        for self.now in (10, 60, 119):
            wire.poll_once()
            wire.on_page(page)
        self.assertEqual(len(self.offers), 1)
        self.now = 120
        wire.on_page(page)
        self.assertEqual(self.decisions.get(timeout=2)["answer"], "yes")
        self.assertEqual(len(self.offers), 2)

    def test_deduper_snooze_uses_the_injected_clock_and_app_key(self):
        deduper = Deduper(clock=lambda: self.now)
        risk = Risk("remote_access", app="AnyDesk")
        key = ("AnyDesk", "remote_access")
        deduper.snooze(key, 5)
        self.now = 4
        self.assertFalse(deduper.fresh(risk))
        self.now = 5
        self.assertTrue(deduper.fresh(risk))
        deduper.forget(key)
        self.assertTrue(deduper.fresh(risk))


class ConfirmationOverlayTests(unittest.TestCase):
    def test_result_card_replaces_a_still_visible_risk_warning(self):
        overlay = Overlay()
        overlay.show_card("Warning. Be careful.", kind="risk")
        agent = SimpleNamespace(bus=EventBus(), proactive=SimpleNamespace(ui=overlay))
        with patch("ally.risk.wire._make_guardian", return_value=(None, None)), \
                patch.object(RiskWire, "start"):
            wire = attach(agent)
        wire.notify("Closed the page.")
        self.assertEqual(overlay._q.get_nowait()[1], "Warning. Be careful.")
        self.assertEqual(overlay._q.get_nowait(), ("card", "Closed the page.", ("OK",), "risk"))

    def test_confirmation_replaces_answered_offer_and_renders_custom_labels(self):
        bus, offers, done = EventBus(), [], threading.Event()
        bus.subscribe("risk_decision", lambda msg: done.set())
        wire = RiskWire(bus, ask=lambda offer: offers.append(offer) or "no")
        self.addCleanup(wire.stop)
        wire.on_risk_warning({"risk": Risk("scam_popup").to_dict(), "text": "Be careful."})
        self.assertTrue(done.wait(2))
        first, second = offers
        overlay = Overlay()
        overlay._tk, overlay._root = Mock(), Mock()
        overlay._screen_size = Mock(return_value=(1200, 900))
        overlay._hl_wins = []
        overlay._card_win = overlay._offer_win = None
        overlay._card_bottom = overlay._offer_x = 0
        win = Mock()
        win.winfo_children.return_value = []
        win.winfo_reqheight.return_value = 420
        win.focus_get.return_value = None
        overlay._tk.Toplevel.return_value = win
        overlay.show_card("Warning. Be careful.", kind="risk")
        overlay.offer(first)
        while not overlay._q.empty():
            overlay._handle(overlay._q.get_nowait())
        overlay._pick(first.id, "no")
        overlay.clear()
        overlay.offer(second)
        while not overlay._q.empty():
            overlay._handle(overlay._q.get_nowait())
        self.assertEqual(overlay._panel.current_offer.id, second.id)
        buttons = overlay._tk.Button.call_args_list[-2:]
        self.assertEqual([c.kwargs["text"] for c in buttons], ["Close it", "Keep it open"])
        self.assertEqual(buttons[0].kwargs["bg"], "#7A4100")
        buttons[0].kwargs["command"]()
        self.assertEqual(second.choice, "yes")


if __name__ == "__main__":
    unittest.main()
