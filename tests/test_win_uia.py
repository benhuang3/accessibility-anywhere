"""Pure-logic tests for ally.platforms.win_uia. These run on any OS (no comtypes, no Windows)."""

import sys
import unittest

import helpers  # noqa: F401  (adds the repo root to sys.path)

from ally.platforms import win_uia


class WinUiaPureTests(unittest.TestCase):
    def test_import_is_safe_off_windows(self):
        # The module imported above; re-confirm the comtypes-free surface is usable.
        self.assertTrue(hasattr(win_uia, "element_from_point"))
        self.assertTrue(hasattr(win_uia, "focused_element"))
        self.assertTrue(hasattr(win_uia, "set_value"))

    def test_rect_to_bounds(self):
        self.assertEqual(win_uia.rect_to_bounds(10, 20, 110, 70), (10, 20, 100, 50))
        self.assertEqual(win_uia.rect_to_bounds(0, 0, 0, 0), (0, 0, 0, 0))
        self.assertEqual(win_uia.rect_to_bounds(-5, -5, 5, 5), (-5, -5, 10, 10))

    def test_role_for(self):
        self.assertEqual(win_uia.role_for(50000), "button")
        self.assertEqual(win_uia.role_for(50004), "edit")
        self.assertEqual(win_uia.role_for(50020), "text")
        self.assertEqual(win_uia.role_for(50018), "tab")
        self.assertEqual(win_uia.role_for(99999), "other")

    def test_available_reports_false_off_windows(self):
        ok, why = win_uia.available()
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(why, str)
        self.assertTrue(why)
        if sys.platform != "win32":
            self.assertFalse(ok)

    def test_window_title_helpers_degrade_off_windows(self):
        if sys.platform != "win32":
            self.assertEqual(win_uia.get_window_title(0), "")
            self.assertFalse(win_uia.set_window_title(0, "x"))

    def test_checks_shape(self):
        result = win_uia.checks()
        self.assertEqual(len(result), 1)
        status, name, detail = result[0]
        self.assertIn(status, ("ok", "warn", "fail"))
        self.assertEqual(name, "UI Automation")
        self.assertIsInstance(detail, str)


if __name__ == "__main__":
    unittest.main()
