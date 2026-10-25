import ast
from dataclasses import FrozenInstanceError
import inspect
import unittest

from helpers import ROOT

from ally.risk.text import TextScore, is_allowlisted, normalize, redact, score_text


CLASSIC = "Microsoft Windows: viruses detected. Do not close. Call support now at 1-800-555-0100."


class TextScoreTests(unittest.TestCase):
    def check_score(self, text, score, severity, **kwargs):
        result = score_text(text, **kwargs)
        self.assertEqual(result.score, score)
        self.assertEqual(result.severity, severity)
        self.assertFalse(any(c.isdigit() for c in result.evidence))
        return result

    def test_classic_popup_and_tel_link(self):
        self.check_score(CLASSIC, 10, "high")
        self.check_score("Microsoft Windows virus. Do not close. Call support now.", 9, "high",
                         has_tel_link=True)

    def test_medium_without_phone(self):
        self.check_score("Virus. Contact support.", 4, "medium")
        self.check_score("Microsoft virus. Call now.", 6, "medium")

    def test_below_threshold(self):
        for text, expected in (("A recipe for apple pie", 2), ("A recipe for bread", 0),
                               ("Your account is locked", 2), ("212-555-0100", 3),
                               ("Windows bitcoin", 3), ("Windows and Apple release notes", 2)):
            with self.subTest(text=text):
                self.check_score(text, expected, None)

    def test_fullscreen(self):
        result = self.check_score("Virus", 4, "medium", fullscreen=True)
        self.assertEqual(result.signals, ("alarm", "fullscreen"))

    def test_overlay_bonus_and_fixed_order(self):
        text = CLASSIC + " Buy gift cards."
        body = self.check_score(text, 11, "high")
        overlay = self.check_score("", 16, "high", overlay_text=text)
        self.assertEqual(overlay.score - body.score, 5)
        self.assertEqual(body.signals, ("phone", "brand", "alarm", "action", "payment"))
        self.check_score(text, 16, "high", overlay_text=text + " " + text)
        self.check_score("", 3, None, has_tel_link=True)
        for fixture, points in (("212-555-0100", 3), ("Windows", 2), ("Virus", 2),
                                ("Call now", 2), ("bitcoin", 1)):
            with self.subTest(fixture=fixture):
                self.check_score("", points + 1, "medium" if points + 1 >= 4 else None,
                                 overlay_text=fixture)

    def test_high_requires_phone_and_alarm(self):
        self.check_score("Windows virus call now bitcoin", 7, "medium")
        self.check_score("Windows call now 1-800-555-0100", 8, "medium")

    def test_allowlist_preserves_score(self):
        for host in ("https://ftc.gov/advice", "https://support.microsoft.com/help"):
            result = self.check_score(CLASSIC, 10, None, url=host)
            self.assertTrue(result.allowlisted)
        self.check_score(CLASSIC, 10, "high", url="https://unknown.test")
        for host in ("https://ftc.gov@evil.example/", "https://ftc.gov.evil.example",
                     "https://notftc.gov", "https://windows.microsoft.com.evil.example", "",
                     "garbage input", "https://[broken", "https://"):
            with self.subTest(host=host):
                self.assertFalse(is_allowlisted(host))
        for host in ("https://MICROSOFT.COM./x", "consumer.ftc.gov:443", "microsoft.com",
                     "https://sub.cisa.gov", "fbi.gov", "ic3.gov", "aarp.org", "learn.microsoft.com"):
            self.assertTrue(is_allowlisted(host), host)
        for host in ("examplebank.test", "https://help.examplebank.test"):
            self.assertTrue(is_allowlisted(host, ("examplebank.test",)))
        self.assertFalse(is_allowlisted("notexamplebank.test", ("examplebank.test",)))

    def test_words_and_plurals(self):
        self.check_score("viruses gift cards", 3, None)
        self.check_score("pineapple windowsill", 0, None)
        for text in ("infected", "malware", "trojan", "hacked", "blocked", "security alert",
                     "do not close", "do not restart", "do not shut down", "do not shutdown", "do not turn off"):
            self.check_score(text, 2, None)
        for text in ("call immediately", "call toll", "contact technician"):
            self.check_score(text, 2, None)
        for text in ("gift card", "cryptocurrency", "crypto", "wire transfer"):
            self.check_score(text, 1, None)

    def test_phone_bonus_once_and_area_codes(self):
        for area in ("800", "833", "844", "855", "866", "877", "888"):
            self.check_score(f"1-{area}-555-0100 1-{area}-555-0101", 4, "medium")
        self.check_score("212.555.0100", 3, None)
        self.check_score("x2125550100x", 0, None)

    def test_normalization(self):
        self.assertEqual(normalize("  ＷＩＮＤＯＷＳ\n  Vi\u200brUs\u2060  ALERT "), "windows virus alert")
        self.check_score("  ＷＩＮＤＯＷＳ\n  Vi\u200brUs  ", 4, "medium")

    def test_redact(self):
        for phone in ("1-800-555-0100", "+1 (800) 555-0100", "800.555.0100", "8005550100",
                      "555 0100", "(800) 555 0100"):
            with self.subTest(phone=phone):
                self.assertEqual(redact("Call " + phone + " now"), "Call a phone number now")
        self.assertEqual(redact("24 hours in 2026"), "24 hours in 2026")

    def test_purity_and_namespace_import(self):
        result = score_text(CLASSIC)
        self.assertEqual(result, score_text(CLASSIC))
        self.assertIsInstance(result, TextScore)
        with self.assertRaises(FrozenInstanceError):
            result.score = 0
        source = inspect.getsource(inspect.getmodule(score_text))
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                self.assertFalse(any(n.name == "ally" or n.name.startswith("ally.") for n in node.names))
            elif isinstance(node, ast.ImportFrom):
                self.assertFalse((node.module or "").split(".")[0] == "ally")
                self.assertEqual(node.level, 0)

    def test_unknown_bank_help_page_is_a_nudge_not_a_block(self):
        # Decision (Oct 24): "high" needs a brand claim, a full-screen page or an overlay signal as well as
        # phone + alarm. A legitimate help page with a phone number and the word "locked" is only medium.
        text = "If your account is locked, contact support at 1-800-555-0100."
        self.check_score(text, 8, "medium", url="https://examplebank.test/help")
        result = self.check_score(text, 8, None, url="https://examplebank.test/help",
                                  extra_allow=("examplebank.test",))
        self.assertTrue(result.allowlisted)

    def test_high_needs_brand_fullscreen_or_overlay_as_well(self):
        bare = "Your computer is infected. Do not close this window. Call support now at 1-800-555-0100."
        self.assertEqual(score_text(bare).severity, "medium")                       # no brand named
        self.assertEqual(score_text(bare, fullscreen=True).severity, "high")        # full-screen page
        self.assertEqual(score_text("", overlay_text=bare).severity, "high")        # shown in a pop-up overlay
        self.assertEqual(score_text("Windows: " + bare).severity, "high")           # brand claim
        self.assertEqual(score_text(bare, has_tel_link=True).severity, "medium")

    def test_news_article(self):
        text = "Tech-support scams impersonate Microsoft. They claim viruses and demand gift cards."
        result = self.check_score(text, 5, "medium", url="https://news.example/article")
        self.assertIn(result.severity, (None, "medium"))


class OrderNumberTests(unittest.TestCase):
    def test_bare_ten_digit_ids_are_not_phone_numbers(self):
        for text in ("Package 1234567890 is blocked at the depot. Contact support for help.",
                     "Order 2026102412 locked for review. Contact support."):
            r = score_text(text)
            self.assertNotIn("phone", r.signals, text)
            self.assertEqual(r.severity, "medium", text)  # alarm + action only

    def test_formatted_and_toll_free_numbers_still_count(self):
        for number in ("(415) 555-0142", "415.555.0142", "+1 415 555 0142", "8005550142"):
            self.assertIn("phone", score_text("call support now " + number).signals, number)


if __name__ == "__main__":
    unittest.main()
