"""Remote touch tests never inject real input or move the mouse."""

import ctypes
import unittest
from unittest.mock import Mock, patch

from helpers import ROOT  # noqa: F401
from ally.risk import remote_touch as touch


class RemoteTouchTests(unittest.TestCase):
    def controller(self):
        batches = []

        def send(rows):
            batches.append([(row.mi.dx, row.mi.dy, ctypes.c_int32(row.mi.mouseData).value, row.mi.dwFlags)
                            for row in rows])
            return True

        return touch.RemoteTouch(send, lambda: (3200, 2000, 2)), batches

    def test_tap_uses_absolute_primary_coordinates_and_one_complete_left_click(self):
        controller, batches = self.controller()
        self.assertFalse(controller.handle("start", .75, .25))
        self.assertEqual(batches, [])
        self.assertTrue(controller.handle("end", .75, .25))
        self.assertEqual(batches, [[(49151, 16384, 0, touch.MOVE | touch.ABSOLUTE),
                                    (0, 0, 0, touch.LEFTDOWN), (0, 0, 0, touch.LEFTUP)]])
        self.assertIsNone(controller.start)
        self.assertFalse(controller.handle("end", .75, .25))
        self.assertEqual(len(batches), 1)

    def test_small_jitter_clicks_but_drag_scrolls_on_both_axes(self):
        controller, batches = self.controller()
        controller.handle("start", .5, .5)
        controller.handle("move", .505, .505)  # under 12 DIP at 200% DPI
        self.assertTrue(controller.handle("end", .505, .505))
        batches.clear()
        controller.handle("start", .5, .5)
        controller.handle("move", .47, .452)  # 48 DIP left and up
        self.assertFalse(controller.handle("end", .47, .452))
        self.assertEqual(batches, [[(32768, 32768, 0, touch.MOVE | touch.ABSOLUTE)],
                                   [(0, 0, 120, touch.HWHEEL)], [(0, 0, -120, touch.WHEEL)]])
        self.assertIsNone(controller.start)

    def test_drag_moves_pointer_to_start_once_per_gesture_before_any_wheel(self):
        controller, batches = self.controller()
        for x, y in ((.75, .25), (.25, .75)):
            with self.subTest(start=(x, y)):
                batches.clear()
                controller.handle("start", x, y)
                self.assertEqual(batches, [])
                controller.handle("move", x - .01, y)  # scrolling, below one wheel step
                move = [(round(x * 65535), round(y * 65535), 0, touch.MOVE | touch.ABSOLUTE)]
                self.assertEqual(batches, [move])
                controller.handle("move", x - .03, y - .048)
                controller.handle("move", x - .06, y - .096)
                self.assertFalse(controller.handle("end", x - .09, y - .144))
                self.assertEqual(batches, [move] + [[(0, 0, 120, touch.HWHEEL)],
                                                    [(0, 0, -120, touch.WHEEL)]] * 3)
                self.assertIsNone(controller.start)

    def test_small_deltas_accumulate_and_reverse_like_a_phone(self):
        controller, batches = self.controller()
        controller.handle("start", .5, .5)
        for i in range(1, 14):
            controller.handle("move", .5, .5 + i * .01)
        self.assertEqual(sum(row[2] for batch in batches for row in batch), 240)
        controller.handle("move", .5, .4)
        self.assertEqual(sum(row[2] for batch in batches for row in batch), -240)
        self.assertEqual(batches[0], [(32768, 32768, 0, touch.MOVE | touch.ABSOLUTE)])
        self.assertTrue(all(row[3] == touch.WHEEL for batch in batches[1:] for row in batch))

    def test_end_checks_final_distance_even_when_all_moves_were_dropped(self):
        controller, batches = self.controller()
        controller.handle("start", .5, .5)
        self.assertFalse(controller.handle("end", .5, .3))
        self.assertTrue(batches)
        self.assertEqual(batches[0], [(32768, 32768, 0, touch.MOVE | touch.ABSOLUTE)])
        self.assertTrue(all(row[3] == touch.WHEEL for batch in batches[1:] for row in batch))

    def test_reset_or_missing_start_cannot_click_and_invalid_messages_are_inert(self):
        controller, batches = self.controller()
        for phase in ("move", "end"):
            self.assertFalse(controller.handle(phase, .5, .5))
        for value in (None, True, "0.5", -.1, 1.1, float("nan"), float("inf"), 10 ** 1000):
            self.assertFalse(controller.handle("start", value, .5))
        controller.handle("start", .5, .5)
        controller.reset()
        self.assertFalse(controller.handle("end", .5, .5))
        self.assertEqual(batches, [])

    def test_injection_error_and_partial_click_release_cannot_leave_state_stuck(self):
        controller = touch.RemoteTouch(Mock(side_effect=OSError("denied")), lambda: (3200, 2000, 2))
        controller.handle("start", .5, .5)
        self.assertFalse(controller.handle("end", .5, .5))
        self.assertIsNone(controller.start)
        send = Mock(side_effect=[2, 1])
        with patch.object(touch.sys, "platform", "win32"), \
                patch.object(touch.ctypes, "windll", Mock(user32=Mock(SendInput=send)), create=True):
            self.assertFalse(touch._send_input([touch._mouse(touch.MOVE | touch.ABSOLUTE),
                                               touch._mouse(touch.LEFTDOWN), touch._mouse(touch.LEFTUP)]))
        self.assertEqual(send.call_args_list[0].args[0], 3)
        self.assertEqual(send.call_args_list[0].args[2], ctypes.sizeof(touch.INPUT))
        release = ctypes.cast(send.call_args_list[1].args[1], ctypes.POINTER(touch.INPUT)).contents
        self.assertEqual(release.mi.dwFlags, touch.LEFTUP)

    def test_non_windows_default_is_a_noop(self):
        with patch.object(touch.sys, "platform", "linux"), patch.object(touch.ctypes, "windll", Mock(), create=True) as dll:
            controller = touch.RemoteTouch()
            controller.handle("start", .5, .5)
            self.assertFalse(controller.handle("end", .5, .5))
            dll.user32.SendInput.assert_not_called()


if __name__ == "__main__":
    unittest.main()
