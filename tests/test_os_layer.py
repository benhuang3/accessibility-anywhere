"""OS-layer logic that runs on any OS: the UIA worker thread, box checks, and the reading card process."""

import sys
import threading
import time
import unittest

import helpers  # noqa: F401  (adds the repo root to sys.path)

from ally import card_helper
from ally.platforms import win_uia
from ally.platforms.cards import ReadingCards
from ally.registry import ToolError


class UsableBoundsTests(unittest.TestCase):
    def test_real_box_kept(self):
        self.assertEqual(win_uia.usable_bounds((10, 20, 300, 40)), (10, 20, 300, 40))

    def test_empty_or_broken_boxes_dropped(self):
        for b in ((0, 0, 0, 0), (5, 5, 0, 10), (5, 5, 10, -1), None, (1, 2, 3), "abc"):
            self.assertIsNone(win_uia.usable_bounds(b), b)


class WorkerTests(unittest.TestCase):
    def test_calls_run_on_the_worker_thread_after_setup(self):
        seen = {}
        w = win_uia._Worker(setup=lambda: seen.setdefault("setup", threading.current_thread().name))
        w.start()
        self.assertEqual(w.call(lambda: threading.current_thread().name), "uia")
        self.assertEqual(seen["setup"], "uia")
        self.assertEqual(w.call(lambda a, b: a + b, 2, 3), 5)
        w.stop()

    def test_errors_reach_the_caller(self):
        w = win_uia._Worker()
        w.start()
        with self.assertRaises(ZeroDivisionError):
            w.call(lambda: 1 / 0)
        self.assertEqual(w.call(lambda: "still works"), "still works")
        w.stop()

    def test_timeout_marks_the_worker_broken(self):
        w = win_uia._Worker()
        w.start()
        with self.assertRaises(TimeoutError):
            w.call(time.sleep, 0.5, timeout=0.05)
        self.assertTrue(w.broken)
        with self.assertRaises(RuntimeError):
            w.call(lambda: 1, timeout=0.05)

    def test_failed_setup_is_reported(self):
        def boom():
            raise OSError("no COM here")
        w = win_uia._Worker(setup=boom)
        w.start()
        with self.assertRaises(RuntimeError) as cm:
            w.call(lambda: 1)
        self.assertIn("no COM here", str(cm.exception))

    def test_call_replaces_a_broken_worker(self):
        saved = win_uia._com_setup, win_uia._com_teardown
        win_uia._com_setup = win_uia._com_teardown = lambda: None
        try:
            win_uia.shutdown()
            with self.assertRaises(TimeoutError):
                win_uia._call(time.sleep, 0.5, timeout=0.05)
            first = win_uia._worker
            self.assertEqual(win_uia._call(lambda: "fresh"), "fresh")
            self.assertIsNot(win_uia._worker, first)
        finally:
            win_uia.shutdown()
            win_uia._com_setup, win_uia._com_teardown = saved


class CardPositionTests(unittest.TestCase):
    def test_centered_without_a_box(self):
        self.assertEqual(card_helper.card_position(None, 1920, 1080, 800, 300), (560, 390))

    def test_below_the_box_when_there_is_room(self):
        x, y = card_helper.card_position((500, 100, 400, 40), 1920, 1080, 800, 300)
        self.assertEqual(y, 100 + 40 + card_helper.MARGIN)
        self.assertEqual(x, 500 + 200 - 400)

    def test_above_the_box_near_the_bottom(self):
        _x, y = card_helper.card_position((100, 900, 400, 40), 1920, 1080, 800, 300)
        self.assertEqual(y, 900 - 300 - card_helper.MARGIN)

    def test_kept_on_screen(self):
        x, y = card_helper.card_position((1880, 10, 30, 20), 1920, 1080, 800, 300)
        self.assertLessEqual(x + 800, 1920)
        self.assertGreaterEqual(y, 0)


# A stand-in for card_helper: says READY and writes every command it gets to stdout.
FAKE_HELPER = [sys.executable, "-c",
               "import sys\nprint('READY', flush=True)\nfor line in sys.stdin:\n    print('GOT ' + line.strip(), flush=True)"]


class ReadingCardsTests(unittest.TestCase):
    def test_show_and_clear_send_commands(self):
        cards = ReadingCards(argv=FAKE_HELPER)
        try:
            msg = cards.show((1, 2, 3, 4), "Big words")
            self.assertIn("Big words", msg)
            self.assertTrue(cards.visible)
            cards.clear()
            self.assertFalse(cards.visible)
        finally:
            cards.close()
        self.assertIsNone(cards.proc)

    def test_empty_text_is_refused(self):
        cards = ReadingCards(argv=FAKE_HELPER)
        with self.assertRaises(ToolError):
            cards.show(None, "   ")
        self.assertIsNone(cards.proc)

    def test_helper_error_becomes_a_tool_error(self):
        cards = ReadingCards(argv=[sys.executable, "-c", "print('ERROR tkinter unavailable', flush=True)"])
        with self.assertRaises(ToolError) as cm:
            cards.show(None, "hello")
        self.assertIn("tkinter unavailable", str(cm.exception))

    def test_a_dead_helper_is_restarted(self):
        cards = ReadingCards(argv=FAKE_HELPER)
        try:
            cards.show(None, "one")
            first = cards.proc
            first.kill()
            first.wait(5)
            cards.show(None, "two")
            self.assertIsNot(cards.proc, first)
            self.assertIsNone(cards.proc.poll())
        finally:
            cards.close()

    def test_clear_without_a_helper_starts_nothing(self):
        cards = ReadingCards(argv=FAKE_HELPER)
        cards.clear()
        self.assertIsNone(cards.proc)


if __name__ == "__main__":
    unittest.main()
