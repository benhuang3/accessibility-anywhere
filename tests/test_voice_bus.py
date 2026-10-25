"""Voice in the proactive loop (ally/voice_bus.py): offers, risk questions, "Done" and undo."""

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import helpers  # noqa: F401  (puts the repo on sys.path)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from voice_fakes import FakeSpeaker, make, vc_prompt  # noqa: E402

from ally import speech  # noqa: E402
from ally.contracts import EventBus, Offer, Struggle  # noqa: E402
from ally.habits import HabitMemory  # noqa: E402
from ally.proactive import Coordinator, NullUI  # noqa: E402
from ally.voice_bus import BusVoice  # noqa: E402


class Out:
    def __init__(self, ok=True):
        self.ok = ok


def wait_for(cond, seconds=3.0):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


class CoordinatorVoiceTests(unittest.TestCase):
    """The real Coordinator, with BusVoice as its voice."""

    def run_offer(self, sessions):
        bus = EventBus()
        calls = []

        def call_tool(name, args, source):
            calls.append((name, source))
            return Out()
        vc, sp, ears = make(sessions, listen_seconds=1)
        bv = BusVoice(vc, bus, call_tool, undo_seconds=0.8)
        self.addCleanup(bv.close)
        with tempfile.TemporaryDirectory() as d:
            mem = HabitMemory(Path(d) / "habits.json", auto_after=2)
            coord = Coordinator(bus, call_tool, mem, lambda: {"app": "Mail", "site": "", "in_browser": False},
                                lambda: {"enabled": True, "min_confidence": 0.3, "fusion_window_seconds": 4,
                                         "cooldown_seconds": 0, "offer_timeout_seconds": 20,
                                         "auto_apply_after": 2, "voice_confirm": True},
                                ui=NullUI(), voice=bv)
            coord.start(watch_context=False)
            try:
                bus.publish("struggle", Struggle(kind="squint", confidence=0.8))  # one offer (cooldown is 0)
                self.assertTrue(wait_for(lambda: calls), "a spoken yes should apply the fix")
                wait_for(lambda: vc_prompt("done") in sp.texts())
                time.sleep(1.2)  # the undo window
            finally:
                coord.stop()
        return calls, sp, ears

    def test_spoken_yes_applies_the_fix_and_says_done(self):
        calls, sp, ears = self.run_offer([[(0.05, "Yes, please.")], []])
        self.assertEqual(len(calls), 1)
        self.assertNotEqual(calls[0][0], "reset_all")
        self.assertIn(vc_prompt("done"), sp.texts())
        self.assertEqual(ears.opened, 2)  # the question, then the undo window

    def test_undo_after_done_resets(self):
        calls, sp, _ = self.run_offer([[(0.05, "Yes.")], [(0.05, "Undo.")]])
        self.assertEqual(calls[-1], ("reset_all", "voice"))
        self.assertIn(vc_prompt("undone"), sp.texts())


class AfterFixTests(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()
        self.vc, self.sp, self.ears = make([[]])
        self.bv = BusVoice(self.vc, self.bus, lambda *a: Out(), undo_seconds=0.3)
        self.addCleanup(self.bv.close)

    def test_failed_fix_says_sorry(self):
        self.bus.publish("applied", {"why": "accepted", "ok": False})
        self.assertTrue(wait_for(lambda: vc_prompt("failed") in self.sp.texts()))
        self.assertEqual(self.ears.opened, 0)

    def test_habit_fix_adds_nothing(self):
        self.bus.publish("applied", {"why": "habit", "ok": True})
        time.sleep(0.3)
        self.assertEqual(self.sp.said, [])


class RiskQuestionTests(unittest.TestCase):
    def test_question_waits_for_its_warning_and_says_both_as_one(self):
        vc, sp, _ = make([[(0.05, "Close it.")]])
        bv = BusVoice(vc)
        self.addCleanup(bv.close)
        offer = Offer("Close this page?", context="risk", timeout=20)
        t = threading.Thread(target=bv.ask, args=(offer,))
        t.start()  # RiskWire's ask usually arrives before the Coordinator's warning
        time.sleep(0.2)
        bv.say("This website may not be safe.")
        t.join(5)
        self.assertEqual(offer.choice, "yes")
        self.assertEqual(sp.texts()[0], "This website may not be safe. Close this page?")

    def test_warning_already_said_finishes_first(self):
        vc, sp, _ = make([[(0.05, "Keep it open.")]], speaker=FakeSpeaker(seconds=0.3))
        bv = BusVoice(vc)
        self.addCleanup(bv.close)
        bv.say("This page may be a scam.")
        t0 = time.monotonic()
        bv.ask(Offer("Close it?", context="risk", timeout=20))
        self.assertEqual(sp.texts()[:2], ["This page may be a scam.", "Close it?"])
        self.assertGreater(time.monotonic() - t0, 0.25)  # waited for the warning to finish

    def test_stop_asking_on_a_risk_question_is_a_plain_no(self):
        snoozed = []
        vc, _, _ = make([[(0.05, "Stop asking me.")]])
        bv = BusVoice(vc, snooze=snoozed.append)
        self.addCleanup(bv.close)
        offer = Offer("Close it?", context="risk", timeout=20)
        bv.ask(offer)
        self.assertEqual((offer.choice, snoozed), ("no", []))


class OfferVoiceTests(unittest.TestCase):
    def test_line_during_a_question_waits_for_it(self):
        vc, sp, _ = make([[(0.4, "No.")]], listen_seconds=1)
        bv = BusVoice(vc)
        self.addCleanup(bv.close)
        offer = Offer("Want me to make this bigger?", context="mail", timeout=20)
        t = threading.Thread(target=bv.ask, args=(offer,))
        t.start()
        time.sleep(0.15)
        bv.say("I'll make the text bigger, like last time.")
        t.join(5)
        self.assertEqual(offer.choice, "no")  # the question wasn't cut off
        self.assertEqual(sp.texts()[-1], "I'll make the text bigger, like last time.")

    def test_stop_asking_snoozes_that_place(self):
        snoozed = []
        vc, _, _ = make([[(0.05, "Leave me alone.")]])
        bv = BusVoice(vc, snooze=snoozed.append)
        self.addCleanup(bv.close)
        offer = Offer("Want me to make this bigger?", context="app:mail", timeout=20)
        bv.ask(offer)
        self.assertEqual((offer.choice, snoozed), ("no", ["app:mail"]))

    def test_helper_call_quiets_voice(self):
        bus = EventBus()
        vc, sp, ears = make([[(2.0, "Yes.")], [(0.05, "Yes.")]], listen_seconds=3)
        bv = BusVoice(vc, bus)
        self.addCleanup(bv.close)
        first = Offer("Close it?", context="risk", timeout=20)
        t = threading.Thread(target=bv.ask, args=(first,))
        t.start()
        time.sleep(1.4)  # asked and listening
        bus.publish("risk_contact", {"name": "Sam", "ok": True, "call_opened": True})
        t.join(3)
        self.assertIsNone(first.choice)  # the open question stopped; the buttons still decide
        said = len(sp.said)
        bv.ask(Offer("Close it?", context="risk", timeout=20))
        self.assertEqual((len(sp.said), ears.opened), (said, 1))


if __name__ == "__main__":
    unittest.main()
