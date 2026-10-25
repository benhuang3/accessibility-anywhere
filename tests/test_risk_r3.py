import ast
from dataclasses import FrozenInstanceError
import inspect
import unittest

from helpers import ROOT

from ally.risk.domains import BRANDS, TOP_SITES, DomainCheck, check_domain, host_of, registrable_domain


class DomainCheckTests(unittest.TestCase):
    def check_result(self, url, severity, reason=None, **kwargs):
        result = check_domain(url, **kwargs)
        self.assertEqual(result.severity, severity, url)
        if reason:
            self.assertIn(reason, result.reasons, url)
        self.assertFalse(any(c.isdigit() for c in result.evidence))
        self.assertTrue(result.evidence.endswith("."))
        if url:
            self.assertNotIn(url, result.evidence)
        return result

    def test_lookalikes(self):
        for url in ("paypa1.com", "micr0soft.com", "rnicrosoft.com", "\u0430pple.com",
                    "paypa\u04cf.com", "goog1e.com", "vvellsfargo.com"):
            with self.subTest(url=url):
                self.check_result(url, "high", "homoglyph")
        self.check_result("\u0430pple.com", "high", "mixed_script")
        self.check_result("a\u0431c.test", "high", "mixed_script")
        encoded = "\u0430pple".encode("idna").decode("ascii") + ".com"
        self.check_result(encoded, "high", "punycode_lookalike")
        self.assertEqual(host_of(encoded), "\u0430pple.com")
        self.assertNotIn("homoglyph", check_domain("gooogle.com").reasons)

    def test_tokens_keywords_and_distance(self):
        for url, severity, reason in (
            ("paypal.com.secure-login.xyz", "high", "brand_subdomain"),
            ("chase.com-verify.net", "high", "brand_subdomain"),
            ("paypal.com.other.xyz", "medium", "brand_token"),
            ("paypal-help.net", "medium", "brand_token"),
            ("secure.paypal.xyz", "medium", "brand_token"),
            ("paypalsecure.com", "medium", "brand_keyword"),
            ("secure-chase.net", "medium", "brand_keyword"),
            ("paypall.com", "medium", "near_miss"),
            ("payapl.com", "medium", "near_miss"),
            ("wellsfargoo.com", "medium", "near_miss"),
            ("americanexpresss.com", "medium", "near_miss"),
            ("americanexpressss.com", "medium", "near_miss"),
            ("http://192.168.1.10/login", "medium", "raw_ip"),
        ):
            with self.subTest(url=url):
                self.check_result(url, severity, reason)
        self.assertIsNone(check_domain("http://192.168.1.10/login").brand)

    def test_clean(self):
        for url in ("usa.gov", "apply.com", "case.edu", "its.com", "chases.com", "pineapple.com",
                    "applebees.com", "appleton.edu", "chaseoutdoors.com", "www.paypal.com",
                    "smile.amazon.com", "www.amazon.co.uk", "mail.google.com", "accounts.google.com",
                    "en.wikipedia.org", "https://login.microsoftonline.com?", "localhost", "",
                    "https://paypal.com@evil.example/", "paypal.me", "support.amex.com"):
            with self.subTest(url=url):
                self.check_result(url, None)
        for domains in BRANDS.values():
            for domain in domains:
                self.check_result(domain, None)
                self.check_result("secure." + domain, None)
        for domain in TOP_SITES:
            self.check_result("secure." + domain, None)

    def test_custom_brands_and_allow(self):
        options = {"extra_brands": {"examplebank": ("examplebank.test",)},
                   "allow": ("examplebank.test",)}
        self.check_result("examplebank-login.net", "medium", "brand_token", **options)
        for url in ("examplebank.test", "secure.examplebank.test"):
            self.check_result(url, None, **options)
        self.check_result("paypa1.test", None, allow=("paypa1.test",))
        self.check_result("sub.paypa1.test", None, allow=("paypa1.test",))
        self.check_result("paypa1.test.evil.net", "high", "homoglyph", allow=("paypa1.test",))

    def test_parsing_and_suffixes(self):
        self.assertEqual(host_of("https://paypal.com@evil.example/"), "evil.example")
        self.assertEqual(host_of("HTTPS://WWW.PAYPAL.COM.:443/path"), "paypal.com")
        self.assertEqual(host_of("www.paypal.com:443/path"), "paypal.com")
        for host, expected in (("bbc.co.uk", "bbc.co.uk"), ("a.b.evil.com.au", "evil.com.au"),
                               ("example.com", "example.com")):
            self.assertEqual(registrable_domain(host), expected)
        for url in ("", "garbage input", "not-a-url", "https://[broken", "https://", "xn--.com",
                    "https://bad..com", "https://example.com:bad", "file:///paypal.com",
                    "https://exa%mple.com", "mailto:user@example.com"):
            result = self.check_result(url, None)
            # A single valid hostname such as localhost is accepted but never scored.
            if url != "not-a-url":
                self.assertEqual(result.host, "", url)

    def test_purity_and_namespace_import(self):
        result = check_domain("paypa1.com")
        self.assertEqual(result, check_domain("paypa1.com"))
        self.assertIsInstance(result, DomainCheck)
        with self.assertRaises(FrozenInstanceError):
            result.host = "other.test"
        source = inspect.getsource(inspect.getmodule(check_domain))
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                self.assertFalse(any(n.name == "ally" or n.name.startswith("ally.") for n in node.names))
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual((node.module or "").split(".")[0], "ally")
                self.assertEqual(node.level, 0)
        self.assertFalse((ROOT / "ally" / "risk" / "__init__.py").exists())


class RegionalSitesTests(unittest.TestCase):
    def test_brands_own_country_sites_are_clean(self):
        for host in ("google.co.uk", "google.de", "www.google.com.au", "paypal.co.uk", "paypal.de", "apple.co.uk",
                     "amazon.nl", "amazon.com.br", "microsoft.co.uk", "walmart.ca", "mail.google.fr"):
            self.assertIsNone(check_domain("https://" + host).severity, host)

    def test_free_or_unrelated_suffixes_are_not_trusted(self):
        for host in ("paypal.tk", "google.cc", "paypal.ml", "amazon.xyz", "apple.top", "google.co.uk.evil.example",
                     "paypal.de.secure-login.net"):
            self.assertIsNotNone(check_domain("https://" + host).severity, host)


if __name__ == "__main__":
    unittest.main()
