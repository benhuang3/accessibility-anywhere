"""Mini-player regressions, without real OS windows."""

import queue
import threading
import unittest
from unittest.mock import Mock, patch

from helpers import ROOT  # noqa: F401
from ally.contracts import EventBus, Risk
from ally.risk.wire import HOLD_SECONDS, RiskWire


class CallOfferTests(unittest.TestCase):
    def test_opened_call_dismisses_original_question_before_a_late_no(self):
        bus, decisions, offers = EventBus(), queue.Queue(), []
        bus.subscribe("risk_decision", decisions.put)
        call = Mock(return_value={"opened": True})

        def ask(offer):
            offers.append(offer)
            if len(offers) == 1:
                offer.extra_action[1]()
                # The original offer remained live; a late no opened the follow-up.
                offer.resolve("no", "overlay")
                return offer.wait(0)
            return "timeout"

        wire = RiskWire(bus, ask=ask, call=call, clock=lambda: 50,
                        contact=("Priya", lambda *args: True, "https://meet.google.com/abc-defg-hij"))
        self.addCleanup(wire.stop)
        risk = Risk("scam_popup", "high", url="https://help.example")
        wire.on_risk_warning({"risk": risk.to_dict(), "text": "Warning."})
        decision = decisions.get(timeout=2)
        self.assertEqual(len(offers), 1)
        self.assertEqual((offers[0].choice, offers[0].answered_by), ("dismissed", "call"))
        self.assertEqual((decision["answer"], decision["snooze_seconds"]), ("dismissed", HOLD_SECONDS))
        self.assertFalse(wire.deduper.fresh(risk))
        self.assertEqual([c.args[0] for c in call.call_args_list], ["openCall"])

    def test_only_explicit_no_opens_confirmation(self):
        for first in ("no", "timeout", "dismissed"):
            with self.subTest(first=first):
                bus, decisions, offers = EventBus(), queue.Queue(), []
                bus.subscribe("risk_decision", decisions.put)

                def ask(offer):
                    offers.append(offer)
                    offer.resolve(first if len(offers) == 1 else "timeout", "voice")
                    return offer.wait(0)

                wire = RiskWire(bus, ask=ask)
                self.addCleanup(wire.stop)
                wire.on_risk_warning({"risk": Risk("scam_popup", "high").to_dict(), "text": "Warning."})
                decisions.get(timeout=2)
                self.assertEqual(len(offers), 2 if first == "no" else 1)

    def test_failed_open_keeps_offer_and_contact_failure_note(self):
        from ally.contracts import Offer
        offer = Offer("Close it?")
        wire = RiskWire(EventBus(), call=Mock(side_effect=OSError),
                        contact=("Priya", lambda *args: False, "https://meet.google.com/abc-defg-hij"))
        self.addCleanup(wire.stop)
        note = wire._tell(Risk("scam_popup", "high"), offer=offer)
        self.assertIn("couldn't reach Priya", note)
        self.assertFalse(offer.done)

    def test_successful_open_dismisses_even_if_push_delivery_fails(self):
        from ally.contracts import Offer
        offer = Offer("Close it?")
        wire = RiskWire(EventBus(), call=Mock(return_value={"opened": True}),
                        contact=("Priya", lambda *args: False, "https://meet.google.com/abc-defg-hij"))
        self.addCleanup(wire.stop)
        wire._tell(Risk("scam_popup", "high"), offer=offer)
        self.assertEqual((offer.choice, offer.answered_by), ("dismissed", "call"))


class CallWindowTests(unittest.TestCase):
    def test_visible_chrome_call_window_filter(self):
        from ally.risk.callwindow import is_call_window
        self.assertTrue(is_call_window(True, "Chrome_WidgetWin_1", "Priya is here \u00b7 Ally Call - Google Chrome"))
        for visible, kind, title in ((False, "Chrome_WidgetWin_1", "Ally Call"),
                                     (True, "Other", "Ally Call"),
                                     (True, "Chrome_WidgetWin_1", "Other call")):
            self.assertFalse(is_call_window(visible, kind, title))

    def test_native_enumeration_only_pins_matching_windows_without_focus_or_geometry_changes(self):
        import ctypes
        from types import SimpleNamespace
        from ally.risk import callwindow
        windows = {1: (True, "Chrome_WidgetWin_1", "Calling Priya - Ally Call"),
                   2: (False, "Chrome_WidgetWin_1", "Ally Call"),
                   3: (True, "Other", "Ally Call"),
                   4: (True, "Chrome_WidgetWin_1", "Risky site"),
                   5: (True, "Chrome_WidgetWin_1", "Connected - Ally Call - Google Chrome")}
        pinned = []

        def string(hwnd, buf, length, index):
            buf.value = windows[hwnd][index]
            return len(buf.value)

        def rect(hwnd, pointer):
            value = ctypes.cast(pointer, ctypes.POINTER(callwindow.wintypes.RECT)).contents
            value.left, value.top, value.right, value.bottom = (-500, 700, -120, 1060)
            return True

        user32 = SimpleNamespace(
            EnumWindows=lambda callback, data: [callback(hwnd, data) for hwnd in windows],
            IsWindowVisible=lambda hwnd: windows[hwnd][0],
            GetClassNameW=lambda *args: string(*args, 1),
            GetWindowTextW=lambda *args: string(*args, 2),
            SetWindowPos=lambda *args: pinned.append(args) or True,
            GetWindowRect=rect)
        self.assertEqual(callwindow.keep_on_top(user32), (-500, 700, -120, 1060))
        self.assertEqual(pinned, [(hwnd, -1, 0, 0, 0, 0, 0x13) for hwnd in (1, 5)])

    def test_non_windows_is_a_noop(self):
        from ally.risk import callwindow
        with patch.object(callwindow.sys, "platform", "linux"):
            self.assertIsNone(callwindow.keep_on_top())

    def test_spacecall_starts_daemon_and_clears_reserved_area_on_end_or_stop(self):
        rect = (1200, 600, 1580, 960)
        for ending in ("ended", "stop"):
            with self.subTest(ending=ending), patch("ally.risk.wire.callwindow.keep_on_top", return_value=rect) as pin:
                placed = threading.Event()
                reserved = []

                def reserve(value):
                    reserved.append(value)
                    if value is not None:
                        placed.set()

                wire = RiskWire(EventBus(), call=Mock(return_value={"opened": True}), set_reserved=reserve,
                                contact=("Priya", lambda *args: True, "", False, "spacecall", "https://calls.example/"))
                self.addCleanup(wire.stop)
                wire._tell(Risk("scam_popup", "high"))
                self.assertTrue(placed.wait(2))
                thread = wire._call_window_thread
                self.assertTrue(thread.daemon)
                self.assertEqual(wire.call_window_rect, rect)
                if ending == "ended":
                    wire.on_call_ended({"service": "spacecall"})
                else:
                    wire.stop()
                self.assertFalse(thread.is_alive())
                self.assertIsNone(wire.call_window_rect)
                self.assertIsNone(reserved[-1])
                pin.assert_called_once()

    def test_loop_refreshes_reservation_each_second_for_a_replaced_ui(self):
        rect = (1200, 600, 1580, 960)
        refreshed = threading.Event()
        seen = []

        def reserve(value):
            seen.append(value)
            if seen.count(rect) == 2:
                refreshed.set()

        wire = RiskWire(EventBus(), set_reserved=reserve)
        self.addCleanup(wire.stop)
        with patch("ally.risk.wire.callwindow.keep_on_top", return_value=rect) as pin:
            wire._start_call_window()
            self.assertTrue(refreshed.wait(2))
            self.assertEqual(pin.call_count, 2)
            self.assertEqual(wire.call_window_rect, rect)
            wire._stop_call_window()
            self.assertIsNone(seen[-1])

    def test_inflight_native_lookup_cannot_restore_reservation_after_stop(self):
        entered, release = threading.Event(), threading.Event()

        def pin():
            entered.set()
            release.wait(2)
            return (1200, 600, 1580, 960)

        reserve = Mock()
        wire = RiskWire(EventBus(), set_reserved=reserve)
        self.addCleanup(wire.stop)
        with patch("ally.risk.wire.callwindow.keep_on_top", side_effect=pin):
            try:
                wire._start_call_window()
                self.assertTrue(entered.wait(2))
                thread = wire._call_window_thread
                wire.stop()
            finally:
                release.set()
            thread.join(1)
            self.assertFalse(thread.is_alive())
            self.assertIsNone(wire.call_window_rect)
            self.assertTrue(all(call.args == (None,) for call in reserve.call_args_list))

    def test_loop_expires_at_two_hours(self):
        wire = RiskWire(EventBus(), set_reserved=Mock())
        self.addCleanup(wire.stop)
        with patch("ally.risk.wire.time.monotonic", side_effect=[100, 7300]), \
                patch("ally.risk.wire.callwindow.keep_on_top") as pin:
            wire._start_call_window()
            wire._call_window_thread.join(1)
            self.assertFalse(wire._call_window_thread.is_alive())
            self.assertIsNone(wire.call_window_rect)
            pin.assert_not_called()

    def test_attach_looks_up_current_ui_and_tolerates_null_ui(self):
        from types import SimpleNamespace
        from ally.proactive import NullUI
        from ally.risk.wire import attach
        agent = SimpleNamespace(bus=EventBus(), proactive=SimpleNamespace(ui=NullUI()))
        with patch("ally.risk.wire._make_guardian", return_value=(None, None)), patch.object(RiskWire, "start"):
            wire = attach(agent)
        self.addCleanup(wire.stop)
        wire.set_reserved((1, 2, 3, 4))
        agent.proactive.ui = SimpleNamespace(set_reserved=Mock())
        wire.set_reserved((1, 2, 3, 4))
        agent.proactive.ui.set_reserved.assert_called_once_with((1, 2, 3, 4))


if __name__ == "__main__":
    unittest.main()
