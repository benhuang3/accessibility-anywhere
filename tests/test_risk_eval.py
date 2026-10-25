import contextlib
import io
import re
import unittest
from urllib.parse import urlsplit

from helpers import ROOT  # noqa: F401  (puts the repo on sys.path)

from ally.risk import eval as risk_eval

SIZES = {"scam_text": 25, "benign_text": 25, "lookalike": 10, "real_domain": 10}
SEVERITIES = {"none", "medium", "high"}
# Phone-style numbers only (a separator is required). A bare 10-digit run is an order or tracking number on
# purpose: case b17 checks that those are not mistaken for phone numbers.
PHONE = re.compile(r"\(?\b(\d{3})\)?[\s.-](\d{3})[\s.-](\d{4})\b")
CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")


class EvalDataTests(unittest.TestCase):
    def setUp(self):
        self.cases = risk_eval.load_cases()

    def test_sizes_and_unique_ids(self):
        self.assertEqual({k: len(v) for k, v in self.cases.items()}, SIZES)
        ids = [c["id"] for group in self.cases.values() for c in group]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_case_has_its_keys_and_valid_expectations(self):
        for case in self.cases["scam_text"]:
            self.assertTrue({"id", "text", "url", "expect_min", "note"} <= set(case), case["id"])
            self.assertIn(case["expect_min"], {"medium", "high"}, case["id"])
        for case in self.cases["benign_text"]:
            self.assertTrue({"id", "text", "url", "expect_max", "note"} <= set(case), case["id"])
            self.assertIn(case["expect_max"], {"none", "medium"}, case["id"])
        for case in self.cases["lookalike"] + self.cases["real_domain"]:
            self.assertTrue({"id", "url", "note"} <= set(case), case["id"])
            self.assertTrue(case["url"].strip(), case["id"])

    def test_the_set_has_both_strong_and_nudge_scams(self):
        wants = [c["expect_min"] for c in self.cases["scam_text"]]
        self.assertGreaterEqual(wants.count("high"), 10)
        self.assertGreaterEqual(wants.count("medium"), 5)

    def test_phone_numbers_are_fictional(self):
        for group in ("scam_text", "benign_text"):
            for case in self.cases[group]:
                for text in (case["text"], case.get("overlay_text", "")):
                    for area, middle, last in PHONE.findall(text):
                        self.assertEqual(middle, "555", case["id"])
                        self.assertTrue(last.startswith("01"), f"{case['id']} {area}-{middle}-{last}")

    def test_no_card_numbers_and_scam_pages_are_not_real_hosts(self):
        for group in ("scam_text", "benign_text"):
            for case in self.cases[group]:
                self.assertIsNone(CARD.search(case["text"]), case["id"])
        for case in self.cases["scam_text"]:
            host = urlsplit(case["url"]).hostname or ""
            self.assertTrue(host.endswith((".example", ".test")) or host == "localhost", f"{case['id']} {host}")


class EvalRunnerTests(unittest.TestCase):
    def setUp(self):
        self.metrics = risk_eval.run(risk_eval.load_cases())

    def test_counts_add_up(self):
        t, d, c = self.metrics["text"], self.metrics["domain"], self.metrics["counts"]
        self.assertEqual(c, SIZES)
        self.assertEqual(sum(t["confusion"]["scam"].values()), SIZES["scam_text"])
        self.assertEqual(sum(t["confusion"]["benign"].values()), SIZES["benign_text"])
        self.assertEqual(d["true_positive"] + d["false_negative"], SIZES["lookalike"])
        self.assertEqual(d["false_positive"] + d["true_negative"], SIZES["real_domain"])
        self.assertEqual(t["flagged_scams"], t["confusion"]["scam"]["medium"] + t["confusion"]["scam"]["high"])

    def test_failures_are_well_formed(self):
        for f in self.metrics["failures"]:
            self.assertTrue({"id", "kind", "note", "severity", "expected"} <= set(f))
            self.assertIn(f["severity"], SEVERITIES)

    def test_report_text_and_exit_codes(self):
        text = risk_eval.report(self.metrics)
        for needle in ("Text rules (R1)", "Domain rules (R3)", "Failures ("):
            self.assertIn(needle, text)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(risk_eval.main([]), 0)  # a report, never a failing exit by default
            self.assertEqual(risk_eval.main(["--json"]), 0)

    def test_runner_flags_a_bad_rule_result(self):
        cases = risk_eval.load_cases()
        cases["real_domain"] = [{"id": "x1", "url": "https://paypa1.com/", "note": "deliberately wrong"}]
        cases["lookalike"] = [{"id": "x2", "url": "https://www.chase.com/", "note": "deliberately wrong"}]
        metrics = risk_eval.run(cases)
        kinds = {f["kind"] for f in metrics["failures"] if f["id"] in ("x1", "x2")}
        self.assertEqual(kinds, {"real_domain flagged", "lookalike missed"})


if __name__ == "__main__":
    unittest.main()
