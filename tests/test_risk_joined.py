import ast
import inspect
import re
import unittest

from helpers import ROOT  # noqa: F401  (puts the repo on sys.path)

from ally.risk import joined
from ally.risk.joined import JoinedRisk, joined_risk, running_tools

TOOLS = ("AnyDesk",)


class JoinedRuleTests(unittest.TestCase):
    def test_remote_tool_plus_bank_page_is_high(self):
        for url in ("https://www.chase.com/personal", "https://secure.bankofamerica.com/login",
                    "https://www.paypal.com/signin", "https://online.citi.com"):
            r = joined_risk(TOOLS, url)
            self.assertIsInstance(r, JoinedRisk, url)
            self.assertEqual((r.severity, r.reasons), ("high", ("remote_tool", "bank_page")), url)
            self.assertEqual(r.tools, TOOLS)

    def test_lookalike_bank_page_counts(self):
        r = joined_risk(TOOLS, "https://paypa1.com/login")
        self.assertEqual(r.reasons, ("remote_tool", "lookalike_bank_page"))

    def test_card_fields_on_any_page_count(self):
        r = joined_risk(TOOLS, "https://small-shop.example/checkout", card_present=True)
        self.assertEqual(r.reasons, ("remote_tool", "card_page"))

    def test_needs_both_halves(self):
        self.assertIsNone(joined_risk((), "https://www.chase.com"))
        self.assertIsNone(joined_risk(None, "https://www.chase.com"))
        self.assertIsNone(joined_risk(TOOLS, "https://en.wikipedia.org/wiki/Bank"))
        self.assertIsNone(joined_risk(TOOLS, "https://small-shop.example/", card_present=False))
        self.assertIsNone(joined_risk(TOOLS, ""))
        self.assertIsNone(joined_risk(TOOLS, None))

    def test_own_bank_and_unrelated_media_domains(self):
        banks = {"examplebank": ("examplebank.test",)}
        self.assertIsNotNone(joined_risk(TOOLS, "https://secure.examplebank.test/login", extra_banks=banks))
        self.assertIsNotNone(joined_risk(TOOLS, "https://examplebank-login.net", extra_banks=banks))
        self.assertIsNone(joined_risk(TOOLS, "https://secure.examplebank.test/login"))   # not registered
        # a non-bank brand (Google) is not a bank page
        self.assertIsNone(joined_risk(TOOLS, "https://mail.google.com"))

    def test_running_tools_from_a_snapshot(self):
        snap = [{"name": "chrome.exe"}, {"name": "AnyDesk.exe"}, {"name": "TeamViewer.exe"},
                {"name": "TeamViewer_Service.exe"}, None, 5, {"name": None}, {"nope": 1}]
        self.assertEqual(running_tools(snap), ("AnyDesk", "TeamViewer"))
        self.assertEqual(running_tools(None), ())
        self.assertEqual(running_tools("junk"), ())

    def test_evidence_has_no_digits_or_url(self):
        for kwargs in ({"url": "https://www.chase.com"}, {"url": "https://paypa1.com"},
                       {"url": "https://shop.example", "card_present": True}):
            r = joined_risk(TOOLS, **kwargs)
            self.assertIsNone(re.search(r"\d|https?:", r.evidence), r.evidence)

    def test_deterministic_and_pure(self):
        self.assertEqual(joined_risk(TOOLS, "https://www.chase.com"), joined_risk(TOOLS, "https://www.chase.com"))
        imports = [n for n in ast.walk(ast.parse(inspect.getsource(joined))) if isinstance(n, (ast.Import, ast.ImportFrom))]
        for node in imports:
            self.assertNotIn("contracts", ast.unparse(node))
            self.assertFalse(ast.unparse(node).startswith("from ally"), ast.unparse(node))


if __name__ == "__main__":
    unittest.main()
