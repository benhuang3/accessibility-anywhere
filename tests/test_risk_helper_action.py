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
        kwargs.setdefault("remote_touch", Mock(handle=Mock(return_value=False)))
        wire = RiskWire(EventBus(), **kwargs)
        self.addCleanup(wire.stop)
        return wire

    def test_first_stream_event_per_call_is_named_and_spoken_once(self):
        notes, spoken = [], []
        wire = self.wire(done=notes.append, say=spoken.append, marker=Mock(), screen_size=lambda: (3200, 2000))
        wire._helper_call_opened()
        wire.on_helper_action({"action": "close_tab", "ok": True, "helper_name": "Priya"})
        for action in ("hl_start", "hl_move", "hl_end", "touch_start", "touch_move", "touch_end"):
            wire.on_helper_action({"action": action, "helper_name": " Priya\x00\r\n ", "x": .5, "y": .5})
        self.assertEqual(notes, ["Priya closed that page for you.", "Priya is helping on your screen."])
        self.assertEqual(notes, spoken)
        wire.on_call_ended({"service": "spacecall"})
        wire._helper_call_opened()
        wire.on_helper_action({"action": "hl_start", "helper_name": "a" * 100, "x": .5, "y": .5})
        self.assertEqual(notes[-1], "a" * 30 + " is helping on your screen.")

    def test_marker_maps_primary_capture_pixels_at_200_percent_dpi_and_caches_dimensions(self):
        marks = []
        capture = Mock(return_value=SimpleNamespace(size=(3200, 2000)))
        wire = self.wire(marker=lambda *args: marks.append(args), screen_capture=capture,
                         screen_size=lambda: (1600, 1000))
        wire._helper_call_opened()
        for phase, x, y in (("start", .75, .25), ("move", .5, .5), ("end", 1, 1)):
            wire.on_helper_action({"action": "hl_" + phase, "x": x, "y": y})
        self.assertEqual(marks, [("start", 2400, 500), ("move", 1600, 1000), ("end", 3199, 1999)])
        capture.assert_called_once()
        wire._remote_touch.handle.return_value = True
        wire.on_helper_action({"action": "touch_end", "x": 0, "y": 0})
        self.assertEqual(marks[-1], ("ripple", 0, 0))
        wire._remote_touch.handle.assert_called_once_with("end", 0, 0)

    def test_png_capture_dimensions_and_tuple_capture_use_the_whole_primary_screen(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + struct.pack(">II", 3840, 2160)
        for capture in (png, (png, "ignored metadata")):
            marks = []
            wire = self.wire(marker=lambda *args: marks.append(args), screen_capture=lambda: capture)
            wire._helper_call_opened()
            wire.on_helper_action({"action": "hl_start", "x": 1, "y": 1})
            self.assertEqual(marks[-1], ("start", 3839, 2159))

    def test_bad_messages_and_callback_failures_never_raise(self):
        marks, notes = [], []
        wire = self.wire(marker=lambda *args: marks.append(args), done=notes.append, screen_size=lambda: (3200, 2000))
        wire._helper_call_opened()
        for value in (None, "0.5", True, -0.1, 1.1, float("nan"), float("inf"), 10 ** 1000):
            for axis in ("x", "y"):
                wire.on_helper_action(dict({"action": "hl_start", "x": .5, "y": .5}, **{axis: value}))
        for msg in (None, [], {}, {"action": "click"}, {"action": "point", "ok": True},
                    {"action": "bigger", "ok": True}, {"action": "close_tab", "ok": 1},
                    {"action": "close_tab", "ok": False}, {"action": "hl_start", "x": .5, "y": .5, "helper_name": []}):
            wire.on_helper_action(msg)
        self.assertEqual((marks, notes), ([], []))
        wire.marker = Mock(side_effect=RuntimeError("bad UI"))
        wire.on_helper_action({"action": "hl_start", "x": .5, "y": .5})
        self.assertEqual(notes, ["Your helper is helping on your screen."])
        wire.done = wire.say = Mock(side_effect=RuntimeError("bad UI"))
        wire.on_helper_action({"action": "close_tab", "ok": True})

    def test_streams_are_inert_before_open_and_after_end_or_stop(self):
        marker = Mock()
        wire = self.wire(marker=marker, screen_size=lambda: (3200, 2000))
        gesture = {"action": "touch_start", "x": .2, "y": .8}
        wire.on_helper_action(gesture)
        wire.on_helper_action(dict(gesture, action="hl_start"))
        marker.assert_not_called()
        wire._remote_touch.handle.assert_not_called()
        wire._helper_call_opened()
        wire.on_helper_action(gesture)
        wire._remote_touch.handle.assert_called_once_with("start", .2, .8)
        wire.on_call_ended({"service": "spacecall"})
        marker.assert_called_once_with("clear", 0, 0)
        marker.reset_mock()
        wire._remote_touch.handle.reset_mock()
        for action in ("hl_start", "hl_move", "hl_end", "touch_start", "touch_move", "touch_end"):
            wire.on_helper_action(dict(gesture, action=action))
        marker.assert_not_called()
        wire._remote_touch.handle.assert_not_called()
        wire.stop()
        wire._helper_call_opened()
        wire.on_helper_action(gesture)
        wire._remote_touch.handle.assert_not_called()

    def test_a_late_open_response_cannot_reopen_helper_input_after_hangup(self):
        wire = self.wire()
        generation = wire._helper_call_generation
        wire.on_call_ended({"service": "spacecall"})
        wire._helper_call_opened(generation)
        wire.on_helper_action({"action": "touch_start", "x": .5, "y": .5})
        wire._remote_touch.handle.assert_not_called()
        self.assertFalse(wire._helper_call_open)

    def test_attach_tracks_successful_opens_and_uses_current_ui_with_nullui_guard(self):
        ui, voice = Mock(), Mock()
        agent = SimpleNamespace(bus=EventBus(), bridge=SimpleNamespace(on_page=None),
                                proactive=SimpleNamespace(ui=ui, voice=voice))
        with patch("ally.risk.wire._make_guardian", return_value=(None, lambda: (3200, 2000))), \
                patch.object(RiskWire, "start"), patch.object(RiskWire, "_open_call", return_value=True):
            wire = attach(agent)
        self.addCleanup(wire.stop)
        wire.screen_capture = None
        self.assertTrue(wire._open_call("call"))
        agent.proactive.ui = ui = Mock()
        self.assertEqual(agent.bridge.on_helper_action, wire.on_helper_action)
        agent.bridge.on_helper_action({"action": "hl_start", "x": .5, "y": .25, "helper_name": "Priya"})
        ui.marker.assert_called_once_with("start", 1600, 500)
        ui.show_card.assert_called_once_with("Priya is helping on your screen.", actions=("OK",), kind="done")
        voice.say.assert_called_once_with("Priya is helping on your screen.")
        agent.proactive.ui = SimpleNamespace()  # NullUI is allowed to lack marker().
        agent.bridge.on_helper_action({"action": "hl_end", "x": .5, "y": .25})
        agent.bridge.on_call_ended({"service": "spacecall"})
        self.assertFalse(wire._helper_call_open)

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
