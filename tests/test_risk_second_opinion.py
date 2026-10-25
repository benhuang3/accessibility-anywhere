import threading
import unittest

from helpers import ROOT  # noqa: F401  (puts the repo on sys.path)

from ally.contracts import EventBus, Risk
from ally.risk import warning_for
from ally.risk.guardian import Mark, Verdict
from ally.risk.wire import RiskWire

SCAM_PAGE = {"url": "https://virus-alert.example/x", "fullscreen": True,
             "text": "Your computer is infected with a virus. Microsoft Defender security alert. Call now "
                     "1-800-555-0142. Do not close this window."}
# Phone plus alarm words and nothing else: the rules call this medium, not high.
MEDIUM_PAGE = {"url": "https://help.example/account", "text": "Your account is locked. Virus warning. "
                                                                "Call 1-800-555-0100 for help."}


def verdict(kind="scam", confidence=0.9, explanation="This page says your computer is infected and asks you to call.",
            marks=(), actions=("close_page",)):
    return Verdict(kind, "fake tech support", confidence, ("asks you to call",), explanation, tuple(marks),
                   () if kind == "safe" else tuple(actions))


class FakeGuardian:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.seen = result, error, []

    def review(self, evidence):
        self.seen.append(evidence)
        if self.error:
            raise self.error
        return self.result


def wire_with(guardian, **kwargs):
    bus = EventBus()
    got = []
    bus.subscribe("risk", got.append)
    return RiskWire(bus, snapshot_fn=lambda: [], guardian=guardian, **kwargs), got


class SecondOpinionTests(unittest.TestCase):
    def test_a_scam_verdict_adds_the_explanation_and_raises_medium_to_high(self):
        guardian = FakeGuardian(verdict("scam", 0.9))
        wire, got = wire_with(guardian)
        wire.on_page(MEDIUM_PAGE)
        self.assertEqual(len(got), 1)
        risk = got[0]
        self.assertEqual((risk.kind, risk.severity, risk.source), ("scam_popup", "high", "risk.guardian"))
        self.assertIn("infected", risk.evidence)
        text, spoken = warning_for(risk)
        self.assertTrue(text.startswith("This looks like a scam."))
        self.assertEqual(spoken, risk.evidence)
        self.assertNotRegex(text, r"\d{7}")

    def test_the_model_sees_flags_and_a_bounded_excerpt_not_the_whole_page(self):
        guardian = FakeGuardian(verdict("suspicious", 0.5))
        wire, _ = wire_with(guardian)
        wire.on_page(dict(SCAM_PAGE, text=SCAM_PAGE["text"] + " x" * 5000))
        evidence = guardian.seen[0]
        self.assertIn("scam_text", evidence.flags)
        self.assertIn("fullscreen", evidence.flags)
        self.assertLessEqual(len(evidence.text_excerpt), 1500)

    def test_a_confident_safe_verdict_hides_a_medium_warning(self):
        wire, got = wire_with(FakeGuardian(verdict("safe", 0.9)))
        wire.on_page(MEDIUM_PAGE)
        self.assertEqual(got, [])

    def test_a_hesitant_safe_verdict_does_not_hide_it(self):
        wire, got = wire_with(FakeGuardian(verdict("safe", 0.5)))
        wire.on_page(MEDIUM_PAGE)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].source, "risk.text")

    def test_a_safe_verdict_never_hides_a_high_warning(self):
        wire, got = wire_with(FakeGuardian(verdict("safe", 1.0)))
        wire.on_page(SCAM_PAGE)
        self.assertEqual(len(got), 1)
        self.assertEqual((got[0].severity, got[0].source), ("high", "risk.text"))

    def test_no_verdict_or_a_crash_falls_back_to_the_rules(self):
        for guardian in (FakeGuardian(None), FakeGuardian(error=RuntimeError("boom"))):
            wire, got = wire_with(guardian)
            wire.on_page(SCAM_PAGE)
            self.assertEqual([r.source for r in got], ["risk.text"])

    def test_without_a_guardian_nothing_changes(self):
        wire, got = wire_with(None)
        wire.on_page(SCAM_PAGE)
        self.assertEqual([r.source for r in got], ["risk.text"])

    def test_a_clean_page_never_asks_the_model(self):
        guardian = FakeGuardian(verdict("scam"))
        wire, got = wire_with(guardian)
        wire.on_page({"url": "https://recipes.example/pasta", "text": "Pasta with garlic and oil."})
        self.assertEqual((got, guardian.seen), ([], []))

    def test_the_explanation_cannot_carry_a_url_or_number_past_the_wire(self):
        wire, got = wire_with(FakeGuardian(verdict("scam", 0.9, "Call 1-800-555-0142 or visit https://evil.example now.")))
        wire.on_page(MEDIUM_PAGE)
        self.assertNotIn("evil.example", got[0].evidence)
        self.assertNotRegex(got[0].evidence, r"\d{7}")


class BehaviorWireTests(unittest.TestCase):
    def test_page_tricks_alone_raise_a_warning(self):
        wire, got = wire_with(None)
        wire.on_page({"url": "https://harbor.example/", "text": "Welcome", "signals": {"backTrap": 2, "fullscreenRequests": 1}})
        self.assertEqual([(r.kind, r.severity, r.source) for r in got], [("scam_popup", "medium", "risk.behavior")])

    def test_one_odd_behavior_or_hostile_signals_do_not(self):
        for signals in ({"fullscreenRequests": 1}, {"beforeunload": True}, {"evil": 9}, "junk", None, {"backTrap": "9"}):
            wire, got = wire_with(None)
            wire.on_page({"url": "https://video.example/", "text": "Watch", "signals": signals})
            self.assertEqual(got, [], signals)


class HighlightTests(unittest.TestCase):
    def test_the_models_mark_becomes_the_offers_highlight(self):
        marks = (Mark("fake phone number", (0.25, 0.5, 0.5, 0.1)),)
        offers, done = [], threading.Event()

        def ask(offer):
            offers.append(offer)
            done.set()
            return "no"

        wire, got = wire_with(FakeGuardian(verdict("scam", 0.9, marks=marks)), ask=ask, screen_size=lambda: (2000, 1000))
        wire.on_page(MEDIUM_PAGE)
        wire.on_risk_warning({"risk": got[0].to_dict(), "text": "x"})
        self.assertTrue(done.wait(2))
        self.assertEqual(offers[0].highlight, (500, 500, 1000, 100))

    def test_no_screen_size_means_no_highlight_and_no_crash(self):
        marks = (Mark("x", (0.1, 0.1, 0.2, 0.2)),)
        wire, got = wire_with(FakeGuardian(verdict("scam", 0.9, marks=marks)))
        wire.on_page(MEDIUM_PAGE)
        self.assertEqual(wire._marks, {})
        self.assertEqual(len(got), 1)

    def test_info_reports_whether_the_second_opinion_is_on(self):
        self.assertFalse(wire_with(None)[0].info()["second_opinion"])


if __name__ == "__main__":
    unittest.main()
