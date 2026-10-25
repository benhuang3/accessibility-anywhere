import unittest

from helpers import ROOT  # noqa: F401  (ensures the project root is on sys.path)

from ally import rules
from ally.platforms import base
from ally.registry import Registry, ToolError
from ally.tools import system_tools, ui_text_tools


class FakeBrowser:
    def __init__(self, connected=True, focused=True):
        self.connected = connected
        self.focused = focused
        self.calls = []

    def call(self, action, timeout=8.0, **args):
        self.calls.append((action, args))
        if action == "zoomStep":
            return 1.5
        if action == "setTabTitle":
            return {"title": args.get("title")}
        if action == "rewriteElement":
            return {"ok": True, "matched": 1}
        if action == "restoreText":
            return {"restored": True}
        return None


class FakePlatform(base.Platform):
    name = "linux"
    label = "this test"

    def __init__(self, box=None, overlay_ok=True, editable=None, restore_ok=True):
        super().__init__()
        self._box = box
        self.overlay_ok = overlay_ok
        self.editable = editable  # {"bounds": ..., "old": ...} for a focused box that can be rewritten
        self.restore_ok = restore_ok
        self.replaced = []
        self.restored = []
        self.magnify_calls = []
        self.magnify_at_calls = []
        self.overlay_calls = []
        self.cleared = 0

    def magnify(self, level):
        self.magnify_calls.append(level)
        self.magnify_level = level
        return f"Magnified to {level:g}x."

    def magnify_at(self, level, x, y, follow="eyes"):
        self.magnify_at_calls.append((level, x, y))
        self.magnify_level = level
        return f"Magnified at {x:g},{y:g}."

    def read_focused_box(self):
        return self._box

    def show_text_overlay(self, box, text):
        if not self.overlay_ok:
            return super().show_text_overlay(box, text)  # raises the default ToolError
        self.overlay_calls.append((box, text))
        return f"Showing: {text}"

    def clear_text_overlays(self):
        self.cleared += 1

    def replace_focused_text(self, text):
        if not self.editable:
            return None
        self.replaced.append(text)
        return dict(self.editable)

    def restore_text_at(self, bounds, old):
        self.restored.append((tuple(bounds), old))
        return self.restore_ok


class FakeState:
    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


class FakeCtx:
    def __init__(self, browser, platform):
        self.browser = browser
        self.os = platform
        self.state = FakeState()

    def stop_speaking(self):
        pass


def make_registry():
    r = Registry()
    r.add(ui_text_tools.enlarge_text.__ally_tool__)
    r.add(ui_text_tools.restore_ui_text.__ally_tool__)
    r.add(system_tools.reset_all.__ally_tool__)
    return r


class EnlargeTextTests(unittest.TestCase):
    def test_chrome_bigger_falls_back_to_one_zoom_step_on_an_older_extension(self):
        # FakeBrowser doesn't know pageInfo (returns None), like an extension without the page steps.
        r = make_registry()
        browser = FakeBrowser(connected=True, focused=True)
        ctx = FakeCtx(browser, FakePlatform())
        out = r.call("enlarge_text", {"goal": "bigger"}, ctx=ctx)
        self.assertTrue(out.ok, out.error)
        self.assertEqual(out.value["tier"], "chrome-zoom")
        self.assertEqual(browser.calls, [("pageInfo", {}), ("zoomStep", {"direction": "in"})])
        self.assertEqual(ctx.state.get("ui_text_edits"), [{"tier": "chrome-zoom"}])

    def test_chrome_rewrite_sets_tab_title(self):
        r = make_registry()
        browser = FakeBrowser(connected=True, focused=True)
        ctx = FakeCtx(browser, FakePlatform())
        out = r.call("enlarge_text", {"goal": "Big clear words"}, ctx=ctx)
        self.assertTrue(out.ok, out.error)
        self.assertEqual(out.value["tier"], "chrome-rewrite")
        self.assertIn(("setTabTitle", {"title": "Big clear words"}), browser.calls)
        self.assertEqual(out.value["detail"], "Big clear words")

    def test_native_bigger_with_box_magnifies_at_center(self):
        r = make_registry()
        browser = FakeBrowser(connected=False, focused=False)
        plat = FakePlatform(box=(100, 200, 40, 60))
        ctx = FakeCtx(browser, plat)
        out = r.call("enlarge_text", {"goal": "bigger"}, ctx=ctx)
        self.assertTrue(out.ok, out.error)
        self.assertEqual(out.value["tier"], "screen-magnify")
        self.assertEqual(plat.magnify_at_calls, [(2.0, 120.0, 230.0)])
        self.assertEqual(plat.magnify_calls, [])

    def test_native_bigger_without_box_falls_back_to_magnify(self):
        r = make_registry()
        browser = FakeBrowser(connected=False, focused=False)
        plat = FakePlatform(box=None)
        ctx = FakeCtx(browser, plat)
        out = r.call("enlarge_text", {"goal": "bigger", "level": 3.0}, ctx=ctx)
        self.assertTrue(out.ok, out.error)
        self.assertEqual(out.value["tier"], "screen-magnify")
        self.assertEqual(plat.magnify_calls, [3.0])
        self.assertEqual(plat.magnify_at_calls, [])

    def test_native_replacement_uses_overlay(self):
        r = make_registry()
        browser = FakeBrowser(connected=False, focused=False)
        plat = FakePlatform(box=(0, 0, 10, 10))
        ctx = FakeCtx(browser, plat)
        out = r.call("enlarge_text", {"goal": "Please sign here"}, ctx=ctx)
        self.assertTrue(out.ok, out.error)
        self.assertEqual(out.value["tier"], "overlay")
        self.assertEqual(plat.overlay_calls, [((0, 0, 10, 10), "Please sign here")])

    def test_native_replacement_without_overlay_support_errors(self):
        r = make_registry()
        browser = FakeBrowser(connected=False, focused=False)
        plat = FakePlatform(overlay_ok=False)
        ctx = FakeCtx(browser, plat)
        out = r.call("enlarge_text", {"goal": "hello"}, ctx=ctx)
        self.assertFalse(out.ok)
        self.assertIn("isn't supported", out.error)


class NativeRewriteTests(unittest.TestCase):
    def test_editable_box_is_rewritten_in_place(self):
        r = make_registry()
        plat = FakePlatform(box=(0, 0, 10, 10), editable={"bounds": (5, 6, 70, 20), "old": "Untitled"})
        ctx = FakeCtx(FakeBrowser(connected=False, focused=False), plat)
        out = r.call("enlarge_text", {"goal": "Please sign here"}, ctx=ctx)
        self.assertTrue(out.ok, out.error)
        self.assertEqual(out.value["tier"], "native-rewrite")
        self.assertEqual(plat.replaced, ["Please sign here"])
        self.assertEqual(plat.overlay_calls, [])
        self.assertEqual(ctx.state.get("ui_text_edits"),
                         [{"tier": "native-rewrite", "bounds": [5, 6, 70, 20], "old": "Untitled"}])

    def test_restore_puts_old_text_back_newest_first(self):
        r = make_registry()
        plat = FakePlatform()
        ctx = FakeCtx(FakeBrowser(connected=False, focused=False), plat)
        ctx.state.set("ui_text_edits", [
            {"tier": "native-rewrite", "bounds": [1, 1, 10, 10], "old": "first"},
            {"tier": "overlay"},
            {"tier": "native-rewrite", "bounds": [1, 1, 10, 10], "old": "second"},
        ])
        out = r.call("restore_ui_text", {}, ctx=ctx)
        self.assertTrue(out.ok, out.error)
        self.assertEqual([old for _b, old in plat.restored], ["second", "first"])
        self.assertEqual(ctx.state.get("ui_text_edits"), [])

    def test_failed_restore_is_kept_for_later(self):
        r = make_registry()
        plat = FakePlatform(restore_ok=False)
        ctx = FakeCtx(FakeBrowser(connected=False, focused=False), plat)
        edit = {"tier": "native-rewrite", "bounds": [1, 1, 10, 10], "old": "x"}
        ctx.state.set("ui_text_edits", [edit, {"tier": "overlay"}])
        out = r.call("restore_ui_text", {}, ctx=ctx)
        self.assertTrue(out.ok, out.error)  # the reading cards were still cleared
        self.assertIn("Couldn't put back", out.value)
        self.assertEqual(ctx.state.get("ui_text_edits"), [edit])

    def test_reset_all_undoes_native_rewrites(self):
        r = make_registry()
        plat = FakePlatform()
        ctx = FakeCtx(FakeBrowser(connected=False, focused=False), plat)
        ctx.state.set("ui_text_edits", [{"tier": "native-rewrite", "bounds": [1, 1, 10, 10], "old": "x"}])
        out = r.call("reset_all", {}, ctx=ctx)
        self.assertTrue(out.ok, out.error)
        self.assertEqual(plat.restored, [((1, 1, 10, 10), "x")])


class RestoreTests(unittest.TestCase):
    def test_restore_ui_text_restores_and_clears(self):
        r = make_registry()
        browser = FakeBrowser(connected=True, focused=True)
        plat = FakePlatform()
        ctx = FakeCtx(browser, plat)
        ctx.state.set("ui_text_edits", [{"tier": "chrome-rewrite"}])
        out = r.call("restore_ui_text", {}, ctx=ctx)
        self.assertTrue(out.ok, out.error)
        self.assertIn(("restoreText", {}), browser.calls)
        self.assertEqual(plat.cleared, 1)
        self.assertEqual(ctx.state.get("ui_text_edits"), [])

    def test_reset_all_restores_text_and_overlays(self):
        r = make_registry()
        browser = FakeBrowser(connected=True, focused=True)
        plat = FakePlatform()
        plat.magnify_level = 2.0  # so reset_all also winds the magnifier back
        ctx = FakeCtx(browser, plat)
        out = r.call("reset_all", {}, ctx=ctx)
        self.assertTrue(out.ok, out.error)
        actions = [a for a, _ in browser.calls]
        self.assertIn("resetPage", actions)
        self.assertIn("restoreText", actions)
        self.assertEqual(plat.cleared, 1)
        self.assertIn(1.0, plat.magnify_calls)


class RuleTests(unittest.TestCase):
    def test_enlarge_this_phrases_route_to_enlarge_text(self):
        for phrase in ("enlarge this", "enlarge the text here", "make this text bigger",
                       "make the text bigger here"):
            m = rules.match(phrase, strict=True)
            self.assertIsNotNone(m, phrase)
            self.assertEqual(m.actions, [("enlarge_text", {"goal": "bigger"})], phrase)

    def test_zoom_in_phrases_are_not_clobbered(self):
        for phrase in ("bigger", "zoom in", "enlarge", "make this bigger", "make the text bigger"):
            m = rules.match(phrase, strict=True)
            self.assertIsNotNone(m, phrase)
            self.assertEqual(m.rule, "zoom_in", f"{phrase} -> {m.rule}")


if __name__ == "__main__":
    unittest.main()
