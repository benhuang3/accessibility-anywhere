import unittest

from helpers import ROOT  # noqa: F401  (puts the repo on sys.path)

from ally.risk.soft import FLAG_MEANINGS, soft_flags, soft_gate


class SoftSignalTests(unittest.TestCase):
    def test_scams_that_slip_under_the_main_rule_trip_the_gate(self):
        remote = "Download and run the remote tool. Keep this page open and do not hang up the phone."
        refund = "We refunded you by mistake. Call right away so we can reverse it."
        self.assertTrue(soft_gate(soft_flags(remote)))
        self.assertTrue(soft_gate(soft_flags(refund, has_phone=True)))

    def test_ordinary_pages_do_not(self):
        for text in ("Lemon bars: bake 25 minutes.", "Call us at our office to book.", "Gift cards make great presents.",
                     "Our refund policy lasts 30 days.", "Keep this page open while it loads."):
            with self.subTest(text=text):
                self.assertFalse(soft_gate(soft_flags(text)), text)

    def test_a_phone_number_alone_is_not_a_gate(self):
        self.assertFalse(soft_gate(soft_flags("Contact us", has_phone=True)))

    def test_every_flag_has_a_meaning_and_junk_is_safe(self):
        every = soft_flags("remote access do not tell gift cards arrest warrant", has_phone=True)
        self.assertEqual(set(every), set(FLAG_MEANINGS))
        self.assertEqual(soft_flags(None), ())
        self.assertEqual(soft_flags(""), ())


if __name__ == "__main__":
    unittest.main()
