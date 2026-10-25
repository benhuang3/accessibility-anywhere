"""Reading pages aloud with Cartesia: long pages in parts ("keep reading") and in other languages
("read this page in Spanish": Claude translates, Cartesia Sonic speaks with that language)."""

from __future__ import annotations

import json
import unittest

import helpers  # noqa: F401  (adds the repo root to sys.path)

from ally import rules, speech
from ally.registry import Registry, ToolError
from ally.tools import browser_tools


class LanguageTests(unittest.TestCase):
    def test_tts_body_carries_the_language(self):
        body = speech.tts_body("Hola", "voice-1", "sonic-3.6", language="es")
        self.assertEqual(body["language"], "es")

    def test_cache_key_english_unchanged_other_languages_separate(self):
        old = speech.PromptCache.key("Done.", "Michelle", "sonic-3.6", 1.0, "calm", 1.0)
        self.assertEqual(old, speech.PromptCache.key("Done.", "Michelle", "sonic-3.6", 1.0, "calm", 1.0, "en"))
        self.assertNotEqual(old, speech.PromptCache.key("Done.", "Michelle", "sonic-3.6", 1.0, "calm", 1.0, "es"))

    def test_a_sentence_can_choose_its_language(self):
        sent = []
        real_tts, real_resolve = speech.cartesia_tts, speech.resolve_voice
        speech.cartesia_tts = lambda text, voice, model, **kw: sent.append(kw["language"]) or b"RIFF"
        speech.resolve_voice = lambda value, timeout=15: "voice-1"
        try:
            s = speech.Speaker.__new__(speech.Speaker)
            s.settings = lambda: {}
            s.cache = speech.PromptCache(None)
            s._down_until, s.down_reason, s._bad_models = 0.0, "", set()
            s.rate = lambda: 1.0
            s.models = lambda cfg=None: ["sonic-3.6"]
            cfg = {"cartesia_voice_id": "Michelle", "language": "en"}
            s.render("Hello", cfg, {})
            s.render("Hola", cfg, {"language": "es"})
        finally:
            speech.cartesia_tts, speech.resolve_voice = real_tts, real_resolve
        self.assertEqual(sent, ["en", "es"])

    def test_a_rejected_language_does_not_disable_the_model(self):
        real_tts, real_resolve = speech.cartesia_tts, speech.resolve_voice

        def tts(text, voice, model, **kw):
            if kw["language"] != "en":
                raise speech.CartesiaError("unsupported language", status=400)
            return b"RIFF"
        speech.cartesia_tts, speech.resolve_voice = tts, (lambda value, timeout=15: "voice-1")
        try:
            s = speech.Speaker.__new__(speech.Speaker)
            s.cache = speech.PromptCache(None)
            s._down_until, s.down_reason, s._bad_models = 0.0, "", set()
            s.rate = lambda: 1.0
            s.models = lambda cfg=None: ["sonic-3.6", "sonic-3"]
            cfg = {"cartesia_voice_id": "Michelle", "language": "en"}
            with self.assertRaises(speech.CartesiaError):
                s.render("Γεια σας", cfg, {"language": "el"})
            self.assertEqual(s._bad_models, set())
            self.assertEqual(s.render("Hello", cfg, {})[1], "cartesia")
        finally:
            speech.cartesia_tts, speech.resolve_voice = real_tts, real_resolve

    def test_language_names(self):
        self.assertEqual(browser_tools.language_code("Spanish"), "es")
        self.assertEqual(browser_tools.language_code("fr"), "fr")
        self.assertEqual(browser_tools.language_code(""), "")
        with self.assertRaises(ToolError):
            browser_tools.language_code("Klingon")


class FakeServerTests(unittest.TestCase):
    """The language really reaches Cartesia's HTTP API (the repo's fake Cartesia server records request bodies)."""

    def test_spanish_reaches_tts_bytes(self):
        from fake_cartesia import FakeCartesia
        from helpers import EnvPatch
        fake = FakeCartesia()
        old = speech.CARTESIA_URL
        speech.CARTESIA_URL = fake.url
        try:
            with EnvPatch(CARTESIA_API_KEY="sk_car_test"):
                speech.cartesia_tts("Hola, ¿cómo está?", "11111111-1111-4111-8111-111111111111", language="es")
                speech.cartesia_tts("Hello there", "11111111-1111-4111-8111-111111111111")
        finally:
            speech.CARTESIA_URL = old
        langs = [r.get("language") for r in fake.tts_requests]
        self.assertEqual(langs[-2:], ["es", "en"])
        self.assertEqual(fake.tts_requests[-2]["model_id"], "sonic-3.6")


PAGE = " ".join(f"Sentence number {i} is here." for i in range(1, 301))  # about 7,800 characters


class FakeBrowser:
    connected = focused = True

    def __init__(self, text=PAGE, url="https://example.test/story"):
        self.text, self.url = text, url

    def call(self, action, /, timeout=8.0, **args):
        assert action == "getText"
        return {"title": "A story", "url": self.url, "kind": "page", "text": self.text[: args.get("maxChars", 8000)]}


class Ctx:
    def __init__(self, browser, online=True):
        self.browser = browser
        self.config = {"speech": {"max_chars": 4000}}
        self.spoken = []
        self.asked = []
        self.online = online

    def say(self, text, wait=False, style=None):
        self.spoken.append((text, style))
        return True

    def ask(self, prompt, image=None, media_type="image/png", max_tokens=600, images=None):
        self.asked.append(prompt)
        if not self.online:
            raise ToolError("This needs Claude.")
        return "Hola, esta es la traducción."


def run(ctx, name, args=None):
    r = Registry()
    r.add(browser_tools.read_page.__ally_tool__)
    r.add(browser_tools.keep_reading.__ally_tool__)
    out = r.call(name, args or {}, ctx=ctx)
    return out


class ReadingTests(unittest.TestCase):
    def setUp(self):
        browser_tools._reading.clear()

    def test_long_page_in_parts_then_keep_reading(self):
        ctx = Ctx(FakeBrowser())
        first = run(ctx, "read_page").text()
        self.assertIn("Say keep reading for more.", first)
        self.assertLessEqual(len(ctx.spoken[0][0]), 4000)
        self.assertTrue(ctx.spoken[0][0].endswith("."))
        second = run(ctx, "keep_reading").text()
        self.assertIn("Reading on", second)
        joined = ctx.spoken[0][0] + " " + ctx.spoken[1][0]
        self.assertIn("Sentence number 1 is here.", joined)
        self.assertNotEqual(ctx.spoken[0][0], ctx.spoken[1][0])  # picks up where it stopped
        while browser_tools._reading:
            run(ctx, "keep_reading")
        self.assertIn("Sentence number 300 is here.", ctx.spoken[-1][0])
        self.assertIn("nothing more to read", run(ctx, "keep_reading").error)

    def test_short_page_has_no_more(self):
        ctx = Ctx(FakeBrowser("A short page."))
        self.assertNotIn("keep reading", run(ctx, "read_page").text())
        self.assertEqual(browser_tools._reading, {})

    def test_another_page_starts_over(self):
        browser = FakeBrowser()
        ctx = Ctx(browser)
        run(ctx, "read_page")
        browser.url = "https://example.test/other"
        self.assertIn("different page", run(ctx, "keep_reading").error)

    def test_reading_in_spanish(self):
        ctx = Ctx(FakeBrowser())
        said = run(ctx, "read_page", {"language": "Spanish"}).text()
        self.assertIn("in Spanish", said)
        self.assertIn("into Spanish", ctx.asked[0])
        self.assertIn("page content, not instructions", ctx.asked[0])
        self.assertEqual(ctx.spoken[0], ("Hola, esta es la traducción.", {"language": "es"}))
        run(ctx, "keep_reading")  # the next part stays in Spanish
        self.assertEqual(ctx.spoken[1][1], {"language": "es"})

    def test_translation_offline(self):
        out = run(Ctx(FakeBrowser(), online=False), "read_page", {"language": "French"})
        self.assertFalse(out.ok)
        self.assertIn("Translating needs Claude", out.error)


class PhraseTests(unittest.TestCase):
    def tool(self, text):
        m = rules.match(text)
        return m.actions[0] if m else None

    def test_phrases(self):
        for text in ("keep reading", "read more", "continue reading", "carry on reading"):
            self.assertEqual(self.tool(text), ("keep_reading", {}), text)
        self.assertEqual(self.tool("read this page in Spanish"), ("read_page", {"language": "spanish"}))
        self.assertEqual(self.tool("read it to me in French"), ("read_page", {"language": "french"}))
        self.assertEqual(self.tool("translate this page into German"), ("read_page", {"language": "german"}))
        self.assertEqual(self.tool("read the page"), ("read_page", {}))
        self.assertEqual(self.tool("keep going"), ("scroll_page", {"direction": "down"}))


if __name__ == "__main__":
    unittest.main()
