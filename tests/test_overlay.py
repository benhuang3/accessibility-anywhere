"""Unit tests for ally.overlay (no real windows)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from helpers import ROOT  # noqa: F401

from ally.overlay.layout import (
    CommandView,
    DisplayState,
    answer_offer,
    fit_card,
    highlight_bars,
    key_to_choice,
    make_card_command,
    make_clear_command,
    make_highlight_command,
    make_offer_command,
    sanitize_text,
    wrap_lines,
)
from ally.overlay.overlay import Overlay


class FitCardTests(unittest.TestCase):
    def test_top_center_and_width(self):
        x, y, w = fit_card(1000, 800)
        self.assertEqual(y, 40)
        self.assertEqual(w, 800)
        self.assertEqual(x, 100)
        x2, _, w2 = fit_card(2000, 1200)
        self.assertEqual(w2, 900)
        self.assertEqual(x2, (2000 - 900) // 2)


class TextTests(unittest.TestCase):
    def test_sanitize_strips_control_and_caps(self):
        raw = "Hello\x00world" + "x" * 500
        out = sanitize_text(raw)
        self.assertNotIn("\x00", out)
        self.assertEqual(len(out), 400)

    def test_wrap_lines(self):
        text = "one two three four five"
        lines = wrap_lines(text, 10)
        self.assertTrue(all(len(line) <= 10 for line in lines if line))
        self.assertIn("one two", lines[0])


class CommandTests(unittest.TestCase):
    def test_card_command_shape(self):
        cmd = make_card_command("Hi", ("OK", "Later"), "risk")
        self.assertEqual(cmd[0], "card")
        self.assertEqual(cmd[2], ("OK", "Later"))
        self.assertEqual(cmd[3], "risk")

    def test_risk_beats_info_in_view(self):
        view = CommandView()
        view.apply(make_card_command("info", ("OK",), "info"))
        self.assertTrue(view.apply(make_card_command("new info", ("OK",), "info")))
        view.apply(make_card_command("risk", ("OK",), "risk"))
        self.assertFalse(view.apply(make_card_command("ignored", ("OK",), "info")))
        self.assertEqual(view.card_kind, "risk")

    def test_newest_offer_wins(self):
        view = CommandView()
        view.apply(make_offer_command(1, "first"))
        view.apply(make_offer_command(2, "second"))
        self.assertEqual(view.offer, ("offer", 2, "second"))

    def test_clear_resets(self):
        view = CommandView()
        view.apply(make_card_command("x", ("OK",), "info"))
        view.apply(make_highlight_command((1, 2, 3, 4)))
        view.apply(make_clear_command())
        self.assertIsNone(view.card)
        self.assertIsNone(view.offer)
        self.assertIsNone(view.highlight)


class DisplayStateTests(unittest.TestCase):
    def test_coordinator_side_card_rules(self):
        state = DisplayState()
        state.note_card("info")
        self.assertTrue(state.may_show_card("info"))
        self.assertTrue(state.may_show_card("risk"))
        state.note_card("risk")
        self.assertFalse(state.may_show_card("info"))


class OfferAnswerTests(unittest.TestCase):
    def _fake_offer(self):
        class Fake:
            def __init__(self):
                self.calls: list[tuple[str, str]] = []
                self.choice = None

            def resolve(self, choice, by=""):
                if self.choice is not None:
                    return False
                self.choice = choice
                self.calls.append((choice, by))
                return True

        return Fake()

    def test_yes_no_and_first_wins(self):
        offer = self._fake_offer()
        self.assertTrue(answer_offer(offer, "yes"))
        self.assertEqual(offer.calls, [("yes", "overlay")])
        self.assertFalse(answer_offer(offer, "no"))

    def test_keyboard_mapping(self):
        self.assertEqual(key_to_choice("Return"), "yes")
        self.assertEqual(key_to_choice("KP_Enter"), "yes")
        self.assertEqual(key_to_choice("Escape"), "no")
        self.assertIsNone(key_to_choice("a"))


class HighlightGeometryTests(unittest.TestCase):
    def test_four_bars(self):
        bars = highlight_bars((10, 20, 100, 50))
        self.assertEqual(len(bars), 4)


class OverlayStartTests(unittest.TestCase):
    def test_start_false_when_tk_fails(self):
        ov = Overlay()
        with patch("tkinter.Tk", side_effect=Exception("no display")):
            self.assertFalse(ov.start())
        self.assertIn("no display", ov.error)

    def test_stop_idempotent_without_start(self):
        ov = Overlay()
        ov.stop()
        ov.stop()


class OverlayCommandEnqueueTests(unittest.TestCase):
    def test_show_card_respects_risk_over_info(self):
        ov = Overlay()
        ov._state.note_card("risk")
        ov.show_card("should not enqueue", kind="info")
        self.assertTrue(ov._q.empty())

    def test_offer_enqueues_plain_tuple(self):
        ov = Overlay()

        class Offer:
            id = 7
            prompt = "Continue?"

        ov.offer(Offer())
        plain = ov._q.get_nowait()
        self.assertEqual(plain, ("offer", 7, "Continue?"))
        self.assertTrue(ov._q.empty())


if __name__ == "__main__":
    unittest.main()
