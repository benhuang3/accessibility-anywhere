import queue
import struct
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from helpers import ROOT  # noqa: F401

from ally.contracts import EventBus, Risk
from ally.overlay.overlay import Overlay
from ally.proactive import Coordinator
from ally.risk.wire import RiskWire, attach


SCAM = {"url": "https://help.example/scam", "tab_id": 42,
        "text": "Microsoft Windows virus infected. Call support now at 202-555-0101."}


class HelperActionTests(unittest.TestCase):
    def wire(self, **kwargs):
        wire = RiskWire(EventBus(), **kwargs)
        self.addCleanup(wire.stop)
        return wire

    def test_done_notes_are_short_named_and_spoken_and_failed_actions_are_inert(self):
        notes, spoken = [], []
        wire = self.wire(done=notes.append, say=spoken.append, highlight=Mock(), screen_size=lambda: (3200, 2000))
        for action in ("close_tab", "bigger", "point"):
            wire.on_helper_action({"action": action, "ok": True, "helper_name": " Priya\x00\r\n ", "x": .5, "y": .5})
        self.assertEqual(notes, ["Priya closed that page for you.", "Priya made the page bigger.", "Priya is pointing here."])
        self.assertEqual(notes, spoken)
        wire.on_helper_action({"action": "bigger", "ok": True, "helper_name": "a" * 100})
        self.assertEqual(notes[-1], "a" * 30 + " made the page bigger.")
        for msg in (None, [], {}, {"action": "click", "ok": True}, {"action": "bigger", "ok": 1},
                    {"action": "close_tab", "ok": False}, {"action": "bigger", "ok": True, "helper_name": []}):
            wire.on_helper_action(msg)
        self.assertEqual(len(notes), 4)

    def test_point_maps_primary_capture_pixels_at_200_percent_dpi_and_replaces_timer(self):
        marks, timers = [], []

        class Timer:
            def __init__(self, delay, callback):
                self.delay, self.callback, self.daemon = delay, callback, False
                self.cancelled = False
                timers.append(self)

            def cancel(self): self.cancelled = True
            def start(self): pass

        capture = Mock(return_value=SimpleNamespace(size=(3200, 2000)))
        wire = self.wire(highlight=marks.append, screen_capture=capture, screen_size=lambda: (1600, 1000))
        with patch("ally.risk.wire.threading.Timer", Timer):
            wire.on_helper_action({"action": "point", "ok": True, "x": .75, "y": .25})
            self.assertEqual(marks, [(2330, 430, 140, 140)])
            wire.on_helper_action({"action": "point", "ok": True, "x": .5, "y": .5})
            self.assertEqual(marks[-1], (1530, 930, 140, 140))
            self.assertTrue(timers[0].cancelled)
            self.assertEqual(timers[1].delay, 8)
            self.assertTrue(timers[1].daemon)
            timers[0].callback()
            self.assertIsNotNone(marks[-1], "older timers cannot erase newer points")
            timers[1].callback()
            self.assertIsNone(marks[-1])
        self.assertEqual(capture.call_count, 2)

    def test_png_capture_dimensions_and_screen_edges_remain_on_the_primary_screen(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + struct.pack(">II", 3840, 2160)
        marks = []
        wire = self.wire(highlight=marks.append, screen_capture=lambda: png)
        wire.on_helper_action({"action": "point", "ok": True, "x": 1, "y": 1})
        self.assertEqual(marks[-1], (3700, 2020, 140, 140))

    def test_bad_points_and_callback_failures_never_raise(self):
        marks, notes = [], []
        wire = self.wire(highlight=marks.append, done=notes.append, screen_size=lambda: (3200, 2000))
        for value in (None, "0.5", True, -0.1, 1.1, float("nan"), float("inf")):
            for axis in ("x", "y"):
                wire.on_helper_action(dict({"action": "point", "ok": True, "x": .5, "y": .5}, **{axis: value}))
        self.assertEqual((marks, notes), ([], []))
        wire.highlight = Mock(side_effect=RuntimeError("bad UI"))
        wire.on_helper_action({"action": "point", "ok": True, "x": .5, "y": .5})
        wire.done = wire.say = Mock(side_effect=RuntimeError("bad UI"))
        wire.on_helper_action({"action": "bigger", "ok": True})

    def test_attach_uses_the_current_ui_for_done_and_highlight(self):
        ui, voice = Mock(), Mock()
        agent = SimpleNamespace(bus=EventBus(), bridge=SimpleNamespace(on_page=None),
                                proactive=SimpleNamespace(ui=ui, voice=voice))
        with patch("ally.risk.wire._make_guardian", return_value=(None, None)), patch.object(RiskWire, "start"):
            wire = attach(agent)
        self.addCleanup(wire.stop)
        agent.proactive.ui = ui = Mock()
        self.assertEqual(agent.bridge.on_helper_action, wire.on_helper_action)
        agent.bridge.on_helper_action({"action": "bigger", "ok": True, "helper_name": "Priya"})
        ui.show_card.assert_called_once_with("Priya made the page bigger.", actions=("OK",), kind="done")
        voice.say.assert_called_once_with("Priya made the page bigger.")

    def test_helper_close_cancels_real_coordinator_offer_and_replaces_risk_card(self):
        bus, shown, workers = EventBus(), queue.Queue(), []
        ui = Overlay()
        ui._render, ui._clear_highlight = Mock(), Mock()
        original_offer = ui.offer

        def show(offer):
            original_offer(offer)
            shown.put(offer)

        ui.offer = show
        coord = Coordinator(bus, Mock(), Mock(), lambda: {}, ui=ui)

        def ask(offer):
            workers.append(threading.current_thread())
            return coord.ask(offer)

        wire = RiskWire(bus, ask=ask, done=lambda msg: ui.show_card(msg, kind="done"), call=Mock())
        self.addCleanup(wire.stop)
        bus.subscribe("risk", lambda risk: wire.on_risk_warning({"risk": risk.to_dict(), "text": "Warning."}))
        ui.show_card("Warning.", kind="risk")
        wire.on_page(SCAM)
        offer = shown.get(timeout=2)
        wire.on_helper_action({"action": "close_tab", "ok": True, "tab_id": 42, "helper_name": "Priya"})
        workers[0].join(timeout=2)
        self.assertFalse(workers[0].is_alive())
        while not ui._q.empty(): ui._handle(ui._q.get_nowait())
        self.assertEqual((offer.choice, offer.answered_by), ("dismissed", "helper"))
        self.assertIsNone(coord.pending)
        self.assertIsNone(ui._panel.current_offer)
        self.assertEqual(ui._panel.current_card.text, "Priya closed that page for you.")
        self.assertEqual(ui._panel.current_card.kind, "done")
        self.assertTrue(shown.empty(), "no follow-up question after helper closes the page")
        wire.call.assert_not_called()
        wire.on_page(SCAM)
        self.assertTrue(shown.empty(), "late page reports cannot revive the closed tab")
        self.assertEqual(wire.last_page["url"], "")

    def test_helper_close_between_no_and_confirmation_does_not_ask_followup(self):
        offers, worker, finished = [], [], threading.Event()

        def ask(offer):
            offers.append(offer)
            worker.append(threading.current_thread())
            return "no"

        wire = self.wire(ask=ask)
        wire._tab_ids[("help.example", "scam_popup")] = 42
        from ally.risk.copy import render

        def close_before_confirmation(key):
            wire.on_helper_action({"action": "close_tab", "ok": True, "tab_id": 42})
            finished.set()
            return render(key)

        with patch("ally.risk.wire.render", side_effect=close_before_confirmation):
            wire.on_risk_warning({"risk": Risk("scam_popup", url=SCAM["url"]).to_dict()})
            self.assertTrue(finished.wait(2))
            worker[0].join(timeout=2)
        self.assertEqual(len(offers), 1)

    def test_closing_another_tab_preserves_the_open_question(self):
        offers = queue.Queue()
        wire = self.wire(ask=lambda offer: offers.put(offer) or offer.wait())
        wire._tab_ids[("help.example", "scam_popup")] = 42
        wire.on_risk_warning({"risk": Risk("scam_popup", url=SCAM["url"]).to_dict()})
        offer = offers.get(timeout=2)
        wire.on_helper_action({"action": "close_tab", "ok": True, "tab_id": 99})
        self.assertFalse(offer.done)
        offer.resolve("dismissed", "test")


if __name__ == "__main__":
    unittest.main()
