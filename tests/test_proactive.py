"""Proactive glue: contracts bus, habit memory, and the Coordinator end to end.

Scoped to the integrated tree (the patch's senses/risk skeletons are replaced by the real
modules, so those are tested elsewhere). Here we drive the Coordinator with simulate-style
events and a fake call_tool, the same path POST /api/event and `run.py simulate` use.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from helpers import temp_home  # noqa: F401  (kept for parity with the suite)

from ally.contracts import ContractError, EventBus, Risk, Struggle, parse_event
from ally.habits import HabitMemory, context_key
from ally.proactive import Coordinator, NullUI, NullVoice, fuse, pick_accommodation


class ContractTests(unittest.TestCase):
    def test_struggle_from_dict_and_target(self):
        s = Struggle.from_dict({"kind": "squint", "confidence": 1.5,
                                "target": {"app": "Mail", "site": "x.com"}, "source": "test"})
        self.assertEqual(s.kind, "squint")
        self.assertEqual(s.confidence, 1.0)  # clamped to 0..1
        self.assertEqual(s.target.app, "Mail")

    def test_parse_event_routes(self):
        self.assertEqual(parse_event({"type": "struggle", "kind": "lean"})[0], "struggle")
        self.assertEqual(parse_event({"type": "risk", "kind": "scam_popup", "severity": "high"})[0], "risk")
        self.assertEqual(parse_event({"type": "answer", "choice": "yes"})[0], "answer")
        with self.assertRaises(ContractError):
            parse_event({"type": "nonsense"})

    def test_bus_publish_subscribe(self):
        bus = EventBus()
        seen = []
        bus.subscribe("struggle", seen.append)
        n = bus.publish("struggle", Struggle(kind="squint", confidence=0.9))
        self.assertEqual(n, 1)
        self.assertEqual(seen[0].kind, "squint")


class HabitTests(unittest.TestCase):
    def test_record_and_auto_apply(self):
        with tempfile.TemporaryDirectory() as d:
            mem = HabitMemory(Path(d) / "habits.json", auto_after=2)
            key = context_key(app="Chrome", site="news.com")
            from ally.contracts import Accommodation
            acc = Accommodation(key="zoom", label="make it bigger", tool="enlarge_text", args={"goal": "bigger"})
            mem.record(key, acc, accepted=True)
            mem.record(key, acc, accepted=True)
            snap = mem.snapshot()
            self.assertIn(key, snap)


class CoordinatorTests(unittest.TestCase):
    def test_fuse_and_pick(self):
        score = fuse([Struggle(kind="squint", confidence=0.7), Struggle(kind="lean", confidence=0.7)])
        self.assertGreater(score, 0.0)
        acc = pick_accommodation("squint", in_browser=True)
        self.assertTrue(acc.tool)

    def test_struggle_to_offer_to_tool(self):
        bus = EventBus()
        calls = []

        def call_tool(name, args, source):
            calls.append((name, args, source))
            class Out:
                ok = True
                def text(self): return "done"
            return Out()

        with tempfile.TemporaryDirectory() as d:
            mem = HabitMemory(Path(d) / "habits.json", auto_after=2)
            coord = Coordinator(bus, call_tool, mem, lambda: {"app": "Mail", "site": "", "in_browser": False},
                                lambda: {"enabled": True, "min_confidence": 0.3, "fusion_window_seconds": 4,
                                         "cooldown_seconds": 0, "offer_timeout_seconds": 5, "auto_apply_after": 2},
                                ui=NullUI(), voice=NullVoice())
            coord.start(watch_context=False)
            try:
                bus.publish("struggle", Struggle(kind="squint", confidence=0.8))
                bus.publish("struggle", Struggle(kind="lean", confidence=0.8))
                # let the worker fuse and offer
                deadline = time.time() + 3
                while coord.pending is None and time.time() < deadline:
                    time.sleep(0.02)
                self.assertIsNotNone(coord.pending, "a fused struggle should produce a pending offer")
                bus.publish("answer", {"choice": "yes"})
                deadline = time.time() + 3
                while not calls and time.time() < deadline:
                    time.sleep(0.02)
                self.assertTrue(calls, "an accepted offer should call a tool")
            finally:
                coord.stop()

    def test_risk_degrades_without_wording(self):
        bus = EventBus()
        with tempfile.TemporaryDirectory() as d:
            mem = HabitMemory(Path(d) / "habits.json")
            coord = Coordinator(bus, lambda *a: None, mem, lambda: {}, lambda: {"enabled": True},
                                ui=NullUI(), voice=NullVoice())
            # handle_risk must not raise even though ally/risk has no warning_for yet
            coord.handle_risk(Risk(kind="scam_popup", severity="high", evidence="call this number"))


if __name__ == "__main__":
    unittest.main()
