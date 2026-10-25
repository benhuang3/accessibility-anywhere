import ast
import inspect
import unittest

from helpers import ROOT  # noqa: F401  (puts the repo on sys.path)

from ally.risk import behavior
from ally.risk.behavior import behavior_flags, clean_signals


class BehaviorTests(unittest.TestCase):
    def test_one_trick_alone_is_not_suspicious(self):
        for raw in ({"fullscreenRequests": 1},                 # a video went full screen
                    {"beforeunload": 1},                       # a document asks before closing
                    {"pushState": 12},                         # a single-page app
                    {"contextMenuBlocked": 1},                 # a map or a game
                    {"dialogs": 1}):
            with self.subTest(raw=raw):
                self.assertFalse(behavior_flags(raw).suspicious)

    def test_scam_combinations_are_suspicious(self):
        for raw in ({"backTrap": 2, "fullscreenRequests": 1},
                    {"beforeunload": 1, "backTrap": 1},
                    {"dialogs": 3, "beforeunload": 1},
                    {"popups": 2, "contextMenuBlocked": 1},
                    {"keyboardLock": 1, "fullscreenRequests": 1},
                    {"fullscreenRequests": 1, "contextMenuBlocked": 1, "backTrap": 1}):
            with self.subTest(raw=raw):
                b = behavior_flags(raw)
                self.assertTrue(b.suspicious, b.flags)

    def test_fullscreen_plus_menu_block_alone_is_not_enough(self):
        self.assertFalse(behavior_flags({"fullscreenRequests": 1, "contextMenuBlocked": 1}).suspicious)

    def test_hostile_or_junk_input_is_ignored(self):
        self.assertEqual(clean_signals(None), {})
        self.assertEqual(clean_signals("x"), {})
        self.assertEqual(clean_signals({"evil": 5, "popups": float("nan"), "dialogs": "9"}), {})
        self.assertEqual(clean_signals({"popups": 10 ** 9})["popups"], 99)
        self.assertEqual(clean_signals({"popups": -4})["popups"], 0)
        self.assertEqual(clean_signals({"beforeunload": True}), {"beforeunload": 1})
        self.assertFalse(behavior_flags({"__proto__": {"backTrap": 9}}).suspicious)

    def test_evidence_is_plain_and_has_no_digits(self):
        for raw in ({}, {"backTrap": 3, "beforeunload": 1}):
            self.assertNotRegex(behavior_flags(raw).evidence, r"\d")

    def test_every_flag_has_a_meaning(self):
        flags = behavior_flags({k: 9 for k in ("pushState", "backTrap", "beforeunload", "fullscreenRequests", "keyboardLock",
                                               "contextMenuBlocked", "dialogs", "popups", "pointerLock")}).flags
        self.assertEqual(set(flags), set(behavior.FLAG_MEANINGS))

    def test_pure_no_ally_imports(self):
        for node in ast.walk(ast.parse(inspect.getsource(behavior))):
            if isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0)
                self.assertNotEqual((node.module or "").split(".")[0], "ally")


if __name__ == "__main__":
    unittest.main()
