import ast
from dataclasses import FrozenInstanceError
import inspect
from itertools import product
import unittest

from helpers import ROOT

from ally.risk.fields import CardFields, PaymentRisk, detect_card_fields, score_payment


class CardFieldsTests(unittest.TestCase):
    def test_autocomplete(self):
        for value in ("cc-number", "cc-csc", "cc-exp", " CC-EXP-MONTH ",
                      "cc-exp-year", "cc-name"):
            with self.subTest(value=value):
                self.assertEqual(detect_card_fields([{"autocomplete": value}]),
                                 CardFields(True, 1, ("autocomplete",)))
        self.assertEqual(detect_card_fields([{"autocomplete": "off"}]),
                         CardFields(False, 0, ()))

    def test_names(self):
        for value in ("cardnumber", "card-number", "Card Number", "card_no", "ccnum",
                      "cc number", "CVV", "cvc2", "cvc", "cvn", "Security Code",
                      "card expiry", "expiration date", "exp date", "expiry", "expiry date"):
            for key in ("name", "id", "placeholder", "label"):
                with self.subTest(value=value, key=key):
                    self.assertEqual(detect_card_fields([{key: value}]),
                                     CardFields(True, 1, ("name",)))

    def test_unrelated_and_password(self):
        for value in ("discard", "cardigan", "scorecard", "username", "email",
                      "address-line1", "zip", "security question", "expired session"):
            self.assertFalse(detect_card_fields([{"name": value}]).present, value)
        self.assertFalse(detect_card_fields([{
            "type": " PASSWORD ", "name": "cvv_password", "label": "CVV",
            "autocomplete": "cc-number", "maxlength": 16,
            "inputmode": "numeric"}]).present)

    def test_length(self):
        for kind, length, mode, expected in (
            ("tel", 16, "", True), ("text", 16, "", False),
            ("tel", 10, "", False), ("number", "19", "", True),
            ("tel", 20, "", False), ("text", 13, "numeric", True),
            ("text", 19, "decimal", True), ("email", 16, "numeric", False),
            ("tel", 16.0, "", False), ("tel", True, "", False),
            ("tel", "bad", "", False), ("tel", None, "", False)):
            with self.subTest(kind=kind, length=length, mode=mode):
                result = detect_card_fields([{"type": kind, "maxlength": length,
                                             "inputmode": mode}])
                self.assertEqual(result.present, expected)
                self.assertEqual(result.signals, ("length",) if expected else ())

    def test_junk_and_cap(self):
        junk = [None, 1, "cvv", [], {}, {key: [] for key in (
            "type", "name", "id", "autocomplete", "placeholder", "label",
            "maxlength", "inputmode")}]
        self.assertEqual(detect_card_fields(junk), CardFields(False, 0, ()))
        self.assertEqual(detect_card_fields([{"name": "cvv"}] * 500),
                         CardFields(True, 200, ("name",)))
        self.assertFalse(detect_card_fields([{}] * 200 + [{"name": "cvv"}]).present)

    def test_count_and_signal_order(self):
        fields = [{"name": "cvv", "autocomplete": "cc-csc", "type": "tel",
                   "maxlength": 16}, {"name": "cardnumber"}, {}]
        expected = CardFields(True, 2, ("autocomplete", "length", "name"))
        self.assertEqual(detect_card_fields(fields), expected)
        self.assertEqual(detect_card_fields(fields), detect_card_fields(fields))
        with self.assertRaises(FrozenInstanceError):
            expected.count = 0


class PaymentRiskTests(unittest.TestCase):
    def test_all_combinations(self):
        for present, known, insecure, lookalike, remote in product((False, True), repeat=5):
            with self.subTest(present=present, known=known, insecure=insecure,
                              lookalike=lookalike, remote=remote):
                card = CardFields(present, int(present), ())
                args = (card, known, insecure, lookalike, remote)
                result = score_payment(*args)
                expected = ("high" if lookalike or remote else
                            "medium" if not known or insecure else None) if present else None
                reasons = tuple(reason for reason, active in (
                    ("unfamiliar_site", not known), ("insecure_page", insecure),
                    ("lookalike", lookalike), ("remote_session", remote)) if active and present)
                self.assertIsInstance(result, PaymentRisk)
                self.assertEqual(result.severity, expected)
                self.assertEqual(result.reasons, reasons)
                self.assertEqual(result, score_payment(*args))
                self.assertFalse(any(c.isdigit() for c in result.evidence))
                self.assertNotIn("://", result.evidence)
                self.assertEqual(result.evidence.count("."), 1)

    def test_evidence_priority_and_frozen(self):
        card = CardFields(True, 1, ("name",))
        for flags in ((False, True, True, True), (False, True, False, True)):
            result = score_payment(card, *flags)
            top = score_payment(card, True, False, flags[2], flags[3])
            self.assertEqual(result.evidence, top.evidence)
        with self.assertRaises(FrozenInstanceError):
            score_payment(card, True).severity = "high"

    def test_purity_and_namespace_import(self):
        source = inspect.getsource(inspect.getmodule(detect_card_fields))
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                self.assertFalse(any(n.name == "ally" or n.name.startswith("ally.")
                                     for n in node.names))
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual((node.module or "").split(".")[0], "ally")
                self.assertEqual(node.level, 0)
        self.assertFalse((ROOT / "ally" / "risk" / "__init__.py").exists())


class RealWorldNameTests(unittest.TestCase):
    def test_realistic_names_are_detected(self):
        for field in ({"name": "credit_card_number"}, {"id": "payment-card-number"},
                      {"placeholder": "Enter your card number"}, {"label": "Card number *"},
                      {"name": "paymentCardNumber"}, {"label": "Security code (CVV)"},
                      {"label": "Expiration date (MM/YY)"}, {"name": "card_cvc"}, {"name": "cc-exp-month"},
                      {"label": "Expiry"}):
            self.assertTrue(detect_card_fields([field]).present, field)

    def test_lookalike_words_are_not(self):
        for field in ({"name": "discard"}, {"name": "scorecard"}, {"name": "cardnotes"},
                      {"label": "expired session"}, {"label": "Security question"}, {"name": "cardigan"},
                      {"label": "Name on card"}, {"name": "username"}):
            self.assertFalse(detect_card_fields([field]).present, field)

    def test_non_list_input_never_raises(self):
        for junk in (None, 5, "abc", {"a": 1}, object()):
            self.assertFalse(detect_card_fields(junk).present)


if __name__ == "__main__":
    unittest.main()
