import re
import tempfile
import unittest
from pathlib import Path

from helpers import ROOT  # noqa: F401  (puts the repo on sys.path)

from ally.risk.copy import _DEFAULT, copy_key, load_copy, render

ALLOWED_ACTIONS = {"close_page", "close_tool", "open_usual", "go_back", "explain", "stay"}
HEDGES = ("may", "might", "looks", "don't recognise", "pause", "a bit like", "slightly different")
PLACEHOLDERS = re.compile(r"\{[a-z_]+\}")


def words_in_caps(text):
    return [w for w in re.findall(r"[A-Za-z]{4,}", PLACEHOLDERS.sub("", text)) if w.isupper()]


class CopyFileTests(unittest.TestCase):
    def setUp(self):
        self.copy = load_copy()

    def test_every_entry_is_complete(self):
        self.assertGreaterEqual(len(self.copy), 6)
        for key, entry in self.copy.items():
            for field in ("title", "body", "spoken"):
                self.assertTrue(entry[field].strip(), f"{key}.{field}")
            self.assertEqual(len(entry["actions"]), 2, key)
            for action in entry["actions"]:
                self.assertIn(action["id"], ALLOWED_ACTIONS, key)
                self.assertTrue(action["label"].strip(), key)

    def test_safe_choice_comes_first(self):
        for key, entry in self.copy.items():
            self.assertNotEqual(entry["actions"][0]["id"], "stay", key)

    def test_calm_wording(self):
        for key, entry in self.copy.items():
            text = " ".join((entry["title"], entry["body"], entry["spoken"]))
            self.assertNotIn("!", text, key)
            self.assertEqual(words_in_caps(text), [], key)
            self.assertTrue(any(h in text.lower() for h in HEDGES), f"{key} is not hedged")

    def test_spoken_is_short(self):
        for key, entry in self.copy.items():
            sentences = [s for s in re.split(r"[.?]\s*", entry["spoken"]) if s.strip()]
            self.assertLessEqual(len(sentences), 4, key)
            self.assertLessEqual(len(entry["spoken"]), 260, key)

    def test_no_phone_numbers_in_our_own_copy(self):
        self.assertIsNone(re.search(r"\d{7,}|\d{3}[-.\s]\d{3,4}[-.\s]?\d{0,4}", _DEFAULT.read_text(encoding="utf-8")))


class RenderTests(unittest.TestCase):
    def test_fills_placeholders(self):
        w = render("remote_access", tool="AnyDesk")
        self.assertIn("AnyDesk just opened", w.body)
        self.assertIn("AnyDesk just opened", w.spoken)
        w = render("untrusted_site", host="paypa1.com", usual_site="paypal.com")
        self.assertIn("paypa1.com", w.body)
        self.assertIn("paypal.com", w.body)

    def test_missing_fields_fall_back_and_nothing_is_left_unfilled(self):
        for key in load_copy():
            w = render(key)
            for text in (w.title, w.body, w.spoken, *(a.label for a in w.actions)):
                self.assertNotIn("{", text, key)
        self.assertIn("a remote-control tool just opened", render("remote_access").body.lower())

    def test_hostile_values_cannot_inject_a_phone_number_or_format_fields(self):
        w = render("remote_access", tool="Call 1-800-555-0100 now {host}")
        text = " ".join((w.title, w.body, w.spoken))
        self.assertIsNone(re.search(r"\d{7,}", re.sub(r"[\s.()-]", "", text)))
        self.assertIn("a phone number", text)
        self.assertIn("{host}", w.body)  # the value is not re-expanded
        long = render("untrusted_site", host="x" * 500, usual_site="y.example")
        self.assertLessEqual(len(long.body), 200)

    def test_first_action_is_the_safe_default(self):
        self.assertEqual(render("scam_popup").actions[0].id, "close_page")
        self.assertEqual(render("remote_access").actions[0].id, "close_tool")

    def test_custom_path(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.toml"
            p.write_text('[k]\ntitle="T {host}"\nbody="b"\nspoken="s"\n[[k.actions]]\nid="close_page"\nlabel="L"\n',
                         encoding="utf-8")
            w = render("k", path=p, host="example.org")
            self.assertEqual((w.title, w.actions[0].id), ("T example.org", "close_page"))


class KeyTests(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(copy_key("scam_popup"), "scam_popup")
        self.assertEqual(copy_key("payment_field"), "payment_field")
        self.assertEqual(copy_key("remote_access"), "remote_access")
        self.assertEqual(copy_key("remote_access", joined=True), "joined_remote_payment")
        self.assertEqual(copy_key("untrusted_site", usual_site="chase.com"), "untrusted_site")
        self.assertEqual(copy_key("untrusted_site"), "untrusted_site_generic")
        self.assertIn(copy_key("untrusted_site"), load_copy())

    def test_unknown_kind(self):
        with self.assertRaises(ValueError):
            copy_key("something_else")


class SentenceStartTests(unittest.TestCase):
    def test_a_missing_tool_name_still_starts_with_a_capital(self):
        wording = render("remote_access")
        self.assertTrue(wording.body.startswith("A remote-control tool"), wording.body)
        self.assertTrue(wording.spoken[0].isupper())


if __name__ == "__main__":
    unittest.main()
