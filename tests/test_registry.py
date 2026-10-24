import os
import time
import unittest
from typing import Annotated, Literal, Optional

from helpers import temp_home

from ally.registry import ImageResult, PluginLoader, Registry, ToolError, build_tool, tool


@tool
def sample(ctx, zoom: Annotated[float, "zoom factor"], mode: Literal["a", "b"] = "a", tags: list[str] = None,
           note: Optional[str] = None, count: int = 3, flag: bool = False):
    """Do a sample thing.

    Second line of description.

    Args:
        note: a free note
    """
    return {"zoom": zoom, "mode": mode, "count": count, "flag": flag, "ctx": ctx}


class SchemaTests(unittest.TestCase):
    def test_schema_from_hints_and_docstring(self):
        t = sample.__ally_tool__
        d = t.definition()
        props = d["input_schema"]["properties"]
        self.assertEqual(d["name"], "sample")
        self.assertTrue(d["description"].startswith("Do a sample thing."))
        self.assertNotIn("Args", d["description"])
        self.assertEqual(props["zoom"], {"type": "number", "description": "zoom factor"})
        self.assertEqual(props["mode"]["enum"], ["a", "b"])
        self.assertEqual(props["tags"]["type"], "array")
        self.assertEqual(props["note"]["description"], "a free note")
        self.assertEqual(props["count"]["default"], 3)
        self.assertEqual(props["flag"]["type"], "boolean")
        self.assertEqual(d["input_schema"]["required"], ["zoom"])
        self.assertNotIn("ctx", props)
        self.assertTrue(t.takes_ctx)

    def test_string_description_shortcut_and_choices(self):
        @tool("Paint the page.", choices={"color": lambda cfg: cfg["colors"]})
        def paint(color: str):
            return color

        d = paint.__ally_tool__.definition({"colors": ["red", "blue"]})
        self.assertEqual(d["description"], "Paint the page.")
        self.assertEqual(d["input_schema"]["properties"]["color"]["enum"], ["red", "blue"])
        # A broken choices callback must not break the definition.
        self.assertNotIn("enum", paint.__ally_tool__.definition({})["input_schema"]["properties"]["color"])

    def test_bad_name_rejected(self):
        with self.assertRaises(ValueError):
            build_tool(lambda: 1, name="has space")


class CallTests(unittest.TestCase):
    def setUp(self):
        self.r = Registry()
        self.r.add(sample.__ally_tool__)

    def test_coercion_and_ctx(self):
        out = self.r.call("sample", {"zoom": "1.5", "count": "2", "flag": "yes", "extra": 1}, ctx="CTX")
        self.assertTrue(out.ok, out.error)
        self.assertEqual(out.value["zoom"], 1.5)
        self.assertEqual(out.value["count"], 2)
        self.assertIs(out.value["flag"], True)
        self.assertEqual(out.value["ctx"], "CTX")

    def test_errors_are_outcomes_not_exceptions(self):
        self.assertIn("one of", self.r.call("sample", {"zoom": 1, "mode": "z"}).error)
        self.assertIn("needs", self.r.call("sample", {}).error)
        self.assertIn("no tool", self.r.call("missing").error)

        @tool
        def boom():
            "Crash."
            raise RuntimeError("kaboom")

        @tool
        def polite():
            "Fail nicely."
            raise ToolError("Chrome isn't connected.")

        self.r.add(boom.__ally_tool__)
        self.r.add(polite.__ally_tool__)
        self.assertIn("kaboom", self.r.call("boom").error)
        self.assertEqual(self.r.call("polite").error, "Chrome isn't connected.")
        self.r.disabled = {"polite"}
        self.assertIn("turned off", self.r.call("polite").error)
        self.assertNotIn("polite", [d["name"] for d in self.r.ai_definitions()])

    def test_timeout(self):
        @tool(timeout=0.2)
        def slow():
            "Too slow."
            time.sleep(2)

        self.r.add(slow.__ally_tool__)
        t0 = time.monotonic()
        out = self.r.call("slow")
        self.assertLess(time.monotonic() - t0, 1.5)
        self.assertIn("too long", out.error)

    def test_image_result_content(self):
        @tool
        def snap():
            "Picture."
            return ImageResult(b"\x89PNG", "image/png", "here")

        self.r.add(snap.__ally_tool__)
        content = self.r.call("snap").to_content()
        self.assertEqual(content[0], {"type": "text", "text": "here"})
        self.assertEqual(content[1]["type"], "image")
        self.assertEqual(content[1]["source"]["media_type"], "image/png")


class PluginTests(unittest.TestCase):
    def test_load_reload_error_and_remove(self):
        home = temp_home()
        folder = home / "plugins"
        r = Registry()

        @tool
        def builtin_one():
            "Built in."

        r.add(builtin_one.__ally_tool__)
        loader = PluginLoader(r, folder)
        p = folder / "mine.py"
        p.write_text("from ally import tool\n@tool\ndef hello(ctx, name: str = 'you'):\n    'Say hi.'\n"
                     "    return 'hi ' + name\n", encoding="utf-8")
        loader.scan()
        self.assertEqual(r.call("hello", {"name": "Ana"}).value, "hi Ana")

        # Broken edit: old version keeps working, error is recorded.
        p.write_text("from ally import tool\n@tool\ndef hello(:\n", encoding="utf-8")
        os.utime(p, (time.time() + 2, time.time() + 2))
        loader.scan()
        self.assertIn(str(p), loader.errors)
        self.assertEqual(r.call("hello").value, "hi you")

        # Fixed edit replaces the tool.
        p.write_text("from ally import tool\n@tool\ndef hello2():\n    'Two.'\n    return 2\n", encoding="utf-8")
        os.utime(p, (time.time() + 4, time.time() + 4))
        loader.scan()
        self.assertIsNone(r.get("hello"))
        self.assertEqual(r.call("hello2").value, 2)
        self.assertNotIn(str(p), loader.errors)

        # Name clash with a built-in is skipped, not crashing.
        (folder / "clash.py").write_text("from ally import tool\n@tool\ndef builtin_one():\n    'x'\n", encoding="utf-8")
        loader.scan()
        self.assertEqual(r.get("builtin_one").source, "builtin")
        self.assertTrue(any("clashes" in e for e in loader.errors.values()))

        # Deleting the file removes its tools.
        p.unlink()
        loader.scan()
        self.assertIsNone(r.get("hello2"))

    def test_shipped_plugins_load(self):
        from ally.paths import CODE_ROOT

        r = Registry()
        loader = PluginLoader(r, CODE_ROOT / "plugins")
        loader.scan()
        self.assertEqual(loader.errors, {})
        for name in ("what_time_is_it", "night_mode", "spell_out", "plain_language"):
            self.assertIsNotNone(r.get(name), name)
        self.assertIn("It's", r.call("what_time_is_it").value)


if __name__ == "__main__":
    unittest.main()
