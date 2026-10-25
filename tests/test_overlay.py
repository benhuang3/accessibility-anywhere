"""Unit tests for ally.overlay (no real windows)."""

from __future__ import annotations

import threading
import unittest
from unittest.mock import Mock, patch

from helpers import ROOT  # noqa: F401

from ally.contracts import Offer
from ally.overlay.layout import (
    CommandView,
    DisplayState,
    PanelState,
    answer_offer,
    button_labels,
    fit_card,
    highlight_bars,
    key_to_choice,
    make_card_command,
    make_clear_command,
    make_highlight_command,
    make_offer_command,
    sanitize_text,
    split_title,
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


class PanelStateTests(unittest.TestCase):
    def setUp(self):
        self.clock = Mock(return_value=10.0)
        self.state = PanelState(clock=self.clock)

    def test_card_then_risk_offer_is_one_panel(self):
        self.state.card("Be careful. This page looks risky.", kind="risk")
        self.clock.return_value = 10.02
        self.assertEqual(self.state.offer(1, "risk", "Close it?")[0], "panel")

    def test_risk_offer_then_card_is_one_panel(self):
        self.assertEqual(self.state.offer(1, "risk", "Close it?")[0], "bubble")
        self.clock.return_value = 10.02
        self.assertEqual(self.state.card("Be careful.", kind="risk")[0], "panel")
        self.clock.return_value = 99.0
        self.assertEqual(self.state.view[0], "panel")

    def test_old_offer_does_not_merge(self):
        self.state.offer(1, "risk", "Close it?", t=10)
        self.assertEqual(self.state.card("Be careful.", kind="risk", t=13)[0], "card+bubble")

    def test_merge_boundary(self):
        for delay, expected in ((1.5, "panel"), (1.501, "card+bubble")):
            with self.subTest(delay=delay):
                state = PanelState()
                state.offer(1, "risk", "Close it?", t=10)
                self.assertEqual(state.card("Warning.", kind="risk", t=10 + delay)[0], expected)

    def test_visible_risk_card_accepts_later_offer(self):
        self.state.card("Warning.", kind="risk", t=10)
        self.assertEqual(self.state.offer(1, "risk", "Close it?", t=20)[0], "panel")

    def test_non_risk_offer_never_merges_in_either_order(self):
        for card_first in (True, False):
            with self.subTest(card_first=card_first):
                state = PanelState()
                if card_first:
                    state.card("Warning.", kind="risk")
                state.offer(1, "browser:example.com", "Make this bigger?")
                if not card_first:
                    state.card("Warning.", kind="risk")
                self.assertEqual(state.view[0], "card+bubble")
        state.ok()
        self.assertEqual(state.view[0], "bubble")

    def test_info_card_does_not_merge_or_replace_risk(self):
        self.state.card("Info.")
        self.assertEqual(self.state.offer(1, "risk", "Close it?")[0], "card+bubble")
        self.state.card("Warning.", kind="risk")
        before = self.state.view
        self.assertEqual(self.state.card("Ignore this.", kind="info"), before)

    def test_offer_done_and_clear_retain_card(self):
        self.state.card("Warning.", kind="risk")
        self.state.offer(1, "risk", "Close it?")
        self.assertEqual(self.state.offer_done(1)[0], "card")
        self.assertEqual(self.state.clear()[0], "card")
        self.state.offer(2, "risk", "Close it?")
        self.assertEqual(self.state.clear()[0], "card")

    def test_stale_offer_done_does_not_remove_new_offer(self):
        self.state.card("Warning.", kind="risk")
        self.state.offer(1, "risk", "First?")
        self.state.offer(2, "risk", "Second?")
        self.assertEqual(self.state.offer_done(1)[0], "panel")
        self.assertEqual(self.state.current_offer.id, 2)

    def test_ok_closes_card_and_clear_does_not_resurrect_it(self):
        self.state.card("Warning.", kind="risk")
        self.assertEqual(self.state.ok()[0], "none")
        self.assertEqual(self.state.clear()[0], "none")

    def test_state_text_is_sanitized_and_capped(self):
        raw = "Danger\x00\r" + "x" * 1000
        self.state.card(raw, kind="risk")
        self.state.offer(1, "risk", raw)
        for text in (self.state.current_card.text, self.state.current_offer.prompt):
            self.assertEqual(len(text), 400)
            self.assertNotIn("\x00", text)
            self.assertNotIn("\r", text)


class PanelTextTests(unittest.TestCase):
    def test_split_title(self):
        for text, expected in (
            ("One sentence.", ("One sentence.", "")),
            ("First. Second. Third.", ("First.", "Second. Third.")),
            ("Are you sure? Take your time.", ("Are you sure?", "Take your time.")),
            ("", ("", "")),
        ):
            with self.subTest(text=text):
                self.assertEqual(split_title(text), expected)

    def test_button_labels(self):
        self.assertEqual(button_labels("Want me to close this page?"), ("Yes, close it", "No, keep it"))
        self.assertEqual(button_labels("Want me to make this page bigger?"), ("Yes", "No"))
        self.assertEqual(button_labels(""), ("Yes", "No"))


class PanelInteractionTests(unittest.TestCase):
    def overlay(self):
        ov = Overlay()
        ov._render = Mock()
        ov._clear_highlight = Mock()
        return ov

    def test_yes_and_no_resolve_once_and_close_panel(self):
        for choice in ("yes", "no"):
            with self.subTest(choice=choice):
                ov = self.overlay()
                offer = Offer("Want me to close this page?", context="risk")
                offer.resolve = Mock(wraps=offer.resolve)
                ov._panel.card("Warning.", kind="risk")
                ov.offer(offer)
                ov._handle(ov._q.get_nowait())
                ov._pick(offer.id, choice)
                ov._pick(offer.id, choice)
                offer.resolve.assert_called_once_with(choice, "overlay")
                self.assertEqual(ov._panel.view[0], "none")
                ov.clear()
                ov._handle(ov._q.get_nowait())
                self.assertEqual(ov._panel.view[0], "none")

    def test_voice_answer_then_clear_returns_to_card(self):
        ov = self.overlay()
        offer = Offer("Close it?", context="risk")
        ov._panel.card("Warning.", kind="risk")
        ov.offer(offer)
        ov._handle(ov._q.get_nowait())
        offer.resolve("yes", "voice")
        ov.clear()
        ov._handle(ov._q.get_nowait())
        self.assertEqual(ov._panel.view[0], "card")
        self.assertEqual(offer.answered_by, "voice")
        ov._clear_highlight.assert_called_once()

    def test_voice_wins_race_with_click(self):
        ov = self.overlay()
        offer = Offer("Close it?", context="risk")
        ov._panel.card("Warning.", kind="risk")
        ov.offer(offer)
        ov._handle(ov._q.get_nowait())
        offer.resolve("no", "voice")
        ov._pick(offer.id, "yes")
        self.assertEqual(ov._panel.view[0], "card")
        self.assertEqual((offer.choice, offer.answered_by), ("no", "voice"))

    def test_card_ok_calls_action_and_closes(self):
        ov = self.overlay()
        ov.on_action = Mock()
        ov._panel.card("Warning.", kind="risk")
        ov._card_action("OK")
        self.assertEqual(ov._panel.view[0], "none")
        ov.on_action.assert_called_once_with("OK")

    def test_bubble_answer_keeps_unrelated_card(self):
        ov = self.overlay()
        offer = Offer("Make this bigger?", context="browser:example.com")
        ov._panel.card("Warning.", kind="risk")
        ov.offer(offer)
        ov._handle(ov._q.get_nowait())
        ov._pick(offer.id, "no")
        self.assertEqual(ov._panel.view[0], "card")

    def test_force_render_clears_both_cached_keys_before_rendering(self):
        ov = self.overlay()
        ov._card_key = ov._bubble_key = object()
        keys = []
        ov._render.side_effect = lambda: keys.append((ov._card_key, ov._bubble_key))
        ov._force_render()
        self.assertEqual(keys, [(None, None)])

    def test_extra_result_is_ignored_after_offer_is_answered_cleared_or_replaced(self):
        for action in ("answer", "clear", "replace", "voice"):
            with self.subTest(action=action):
                ov = self.overlay()
                offer = Offer("Close it?", context="risk")
                ov.offer(offer)
                ov._handle(ov._q.get_nowait())
                release = threading.Event()

                def callback():
                    release.wait(2)
                    return "I've sent Priya a message."

                try:
                    ov._start_extra(offer.id, "Tell Priya", callback)
                    if action == "answer":
                        ov._pick(offer.id, "yes")
                    elif action == "clear":
                        ov._handle(make_clear_command())
                    elif action == "voice":
                        offer.resolve("yes", "voice")
                    else:
                        replacement = Offer("Another question?", context="risk")
                        ov.offer(replacement)
                        ov._handle(ov._q.get_nowait())
                    before = ov._panel.view
                    ov._render.reset_mock()
                    release.set()
                    ov._handle(ov._q.get(timeout=2))
                    self.assertEqual(ov._panel.view, before)
                    self.assertNotIn(offer.id, ov._extra_done)
                    ov._render.assert_not_called()
                finally:
                    release.set()

    def test_extra_callback_failure_is_queued_as_a_plain_note(self):
        ov = self.overlay()
        offer = Offer("Close it?", context="risk")
        ov.offer(offer)
        ov._handle(ov._q.get_nowait())
        with self.assertLogs("ally", level="ERROR"):
            ov._start_extra(offer.id, "Tell Priya", Mock(side_effect=OSError("offline")))
            ov._handle(ov._q.get(timeout=2))
        self.assertEqual(ov._extra_done[offer.id], "That didn't work. Please try again.")


class PanelRenderingTests(unittest.TestCase):
    def overlay(self):
        ov = Overlay()
        ov._tk = Mock()
        ov._root = Mock()
        ov._screen_size = Mock(return_value=(1200, 900))
        ov._hl_wins = []
        ov._card_win = ov._offer_win = None
        ov._card_bottom = ov._offer_x = 0

        def window(*args, **kwargs):
            win = Mock()
            win.winfo_children.return_value = []
            win.winfo_reqheight.return_value = 420
            win.focus_get.return_value = None
            return win

        ov._tk.Toplevel.side_effect = window
        ov._tk.Button.side_effect = lambda *args, **kwargs: Mock()
        return ov

    def test_merge_reuses_one_window_in_either_order_and_clear_keeps_it(self):
        for card_first in (True, False):
            with self.subTest(card_first=card_first):
                ov = self.overlay()
                offer = Offer("Close it?", context="risk")
                if card_first:
                    ov.show_card("Warning. Be careful.", kind="risk")
                ov.offer(offer)
                if not card_first:
                    ov.show_card("Warning. Be careful.", kind="risk")
                while not ov._q.empty():
                    ov._handle(ov._q.get_nowait())
                ov._tk.Toplevel.assert_called_once_with(ov._root)
                win = ov._card_win
                self.assertIsNotNone(win)
                self.assertIsNone(ov._offer_win)
                ov._render()
                ov.clear()
                ov._handle(ov._q.get_nowait())
                self.assertIs(ov._card_win, win)
                win.destroy.assert_not_called()
                self.assertEqual(ov._tk.Toplevel.call_count, 1)
                self.assertEqual(ov._panel.view[0], "card")

    def test_panel_styles_and_keyboard_actions_without_tk(self):
        for key, choice in (("<Return>", "yes"), ("<KP_Enter>", "yes"), ("<Escape>", "no")):
            with self.subTest(key=key):
                ov = self.overlay()
                offer = Offer("Want me to close this page?", context="risk")
                ov.show_card("Warning. Be careful.", kind="risk")
                ov.offer(offer)
                while not ov._q.empty():
                    ov._handle(ov._q.get_nowait())
                win = ov._card_win
                bindings = {call.args[0]: call.args[1] for call in win.bind.call_args_list}
                labels = {call.kwargs["text"]: call.kwargs for call in ov._tk.Label.call_args_list}
                self.assertEqual(labels["Ally safety check"]["fg"], "#9A4B00")  # Clarity risk amber
                self.assertEqual(labels["Warning."]["font"], ("Segoe UI", -32, "bold"))
                self.assertEqual(labels["Be careful."]["font"], ("Segoe UI", -26, "normal"))
                self.assertGreater(labels[offer.prompt]["wraplength"], 0)
                buttons = ov._tk.Button.call_args_list[-2:]
                self.assertEqual([c.kwargs["text"] for c in buttons], ["Yes, close it", "No, keep it"])
                self.assertEqual(buttons[0].kwargs["bg"], "#0B57D0")  # Clarity accent (primary)
                self.assertEqual(buttons[1].kwargs["bg"], "#FFFFFF")
                edges = [c for c in ov._tk.Frame.call_args_list if c.kwargs.get("width") == 260]
                self.assertTrue(edges)
                self.assertTrue(all(c.kwargs["height"] >= 64 for c in edges))
                win.focus_force.reset_mock()
                ov._render()
                win.focus_force.assert_not_called()
                self.assertEqual(bindings[key](None), "break")
                self.assertEqual((offer.choice, offer.answered_by), (choice, "overlay"))
                self.assertEqual(ov._panel.view[0], "none")
                win.destroy.assert_called_once()

    def test_non_risk_bubble_stays_below_card(self):
        ov = self.overlay()
        ov.show_card("Warning.", kind="risk")
        ov.offer(Offer("Make this bigger?", context="browser:example.com"))
        while not ov._q.empty():
            ov._handle(ov._q.get_nowait())
        self.assertEqual(ov._tk.Toplevel.call_count, 2)
        self.assertIsNotNone(ov._card_win)
        self.assertIsNotNone(ov._offer_win)
        self.assertEqual(ov._offer_win.geometry.call_args.args[0], f"+150+{ov._card_bottom + 12}")

    def test_extra_action_rebuilds_pending_and_final_notes_without_blocking_or_repeating(self):
        for merged in (True, False):
            with self.subTest(merged=merged):
                ov = self.overlay()
                release, started = threading.Event(), threading.Event()
                workers = []

                def callback():
                    workers.append(threading.current_thread())
                    started.set()
                    release.wait(2)
                    return "I've sent Priya a message."

                offer = Offer("Close it?", context="risk")
                offer.extra_action = ("Tell Priya", callback)
                if merged:
                    ov.show_card("Warning.", kind="risk")
                ov.offer(offer)
                while not ov._q.empty():
                    ov._handle(ov._q.get_nowait())
                run = next(c.kwargs["command"] for c in ov._tk.Button.call_args_list
                           if c.kwargs["text"] == "Tell Priya")
                ov._force_render = Mock(wraps=ov._force_render)
                ov._tk.Button.reset_mock()
                try:
                    run()
                    self.assertTrue(started.wait(2))
                    self.assertFalse(release.is_set())
                    self.assertTrue(workers[0].daemon)
                    self.assertIsNot(workers[0], threading.current_thread())
                    self.assertEqual(ov._extra_done[offer.id], "Contacting Priya\N{HORIZONTAL ELLIPSIS}")
                    self.assertEqual(ov._tk.Label.call_args_list[-1].kwargs["text"],
                                     "Contacting Priya\N{HORIZONTAL ELLIPSIS}")
                    self.assertNotIn("Tell Priya", [c.kwargs["text"] for c in ov._tk.Button.call_args_list])
                    run()  # even an already queued click cannot send twice
                    self.assertEqual(len(workers), 1)
                    release.set()
                    result = ov._q.get(timeout=2)
                    ov._force_render.assert_called_once_with()
                    self.assertEqual(ov._extra_done[offer.id], "Contacting Priya\N{HORIZONTAL ELLIPSIS}")
                    ov._handle(result)
                    self.assertEqual(ov._force_render.call_count, 2)
                    self.assertEqual(ov._tk.Label.call_args_list[-1].kwargs["text"], "I've sent Priya a message.")
                finally:
                    release.set()


if __name__ == "__main__":
    unittest.main()
