"""End to end: the real Python tools (ally/tools/page_tools.py, enlarge_text) drive the real extension.

The agent's WebSocket bridge is replaced by a tiny adapter that calls the extension's ACTIONS through the
harness, so this runs without starting Ally or using port 8765. Run:
    cd tests && ../.venv/bin/python -m unittest browser.test_tools_e2e -v
"""

from __future__ import annotations

import unittest

try:
    import playwright  # noqa: F401
    HAVE_PLAYWRIGHT = True
except ImportError:
    HAVE_PLAYWRIGHT = False

import helpers  # noqa: F401,E402  (adds the repo root to sys.path)

from .harness import ActionError, AllyBrowser, FixtureServer  # noqa: E402

from ally.registry import Registry, ToolError  # noqa: E402
from ally.tools import page_tools, system_tools, ui_text_tools  # noqa: E402

GENERAL = "combo_general.html"
server: FixtureServer | None = None
ally: AllyBrowser | None = None


class HarnessBridge:
    """Looks like ctx.browser (BrowserAPI): call(action, timeout, **args) -> the extension's answer."""

    connected = True
    focused = True
    tab: dict = {}

    def __init__(self, browser: AllyBrowser):
        self.browser = browser
        self.calls: list[str] = []

    def call(self, action: str, timeout: float = 8.0, **args):
        self.calls.append(action)
        try:
            return self.browser.action(action, args)
        except ActionError as e:
            raise ToolError(str(e).split(" failed: ", 1)[-1]) from None


class State(dict):
    def set(self, k, v):
        self[k] = v


class Ctx:
    def __init__(self, browser: AllyBrowser):
        self.browser = HarnessBridge(browser)
        self.config = {}
        self.state = State()
        self.said: list[str] = []
        self.os = None

    def say(self, text, wait=False):
        self.said.append(text)
        return True

    def stop_speaking(self):
        pass


def registry() -> Registry:
    r = Registry()
    for t in (page_tools.hide_clutter, page_tools.simplify_menus, page_tools.enlarge_buttons,
              page_tools.check_page_layout, page_tools.make_page_easier, page_tools.simplified_mode,
              page_tools.open_reader, ui_text_tools.enlarge_text, system_tools.reset_all):
        r.add(t.__ally_tool__)
    return r


def setUpModule() -> None:
    global server, ally
    if not HAVE_PLAYWRIGHT:
        raise unittest.SkipTest("playwright isn't installed")
    server = FixtureServer().start()
    ally = AllyBrowser(server).start()


def tearDownModule() -> None:
    if ally:
        ally.close()
    if server:
        server.stop()


class ToolsEndToEnd(unittest.TestCase):
    def setUp(self):
        self.page = ally.open(GENERAL)
        ally.action("pageTeardown")
        ally.reset_zoom(self.page)
        ally.action("simpleForgetAll")
        self.ctx = Ctx(ally)
        self.r = registry()

    def call(self, name, args=None):
        # The registry checks and converts the arguments as usual, but the tool runs on this thread:
        # Playwright's sync API only works on the thread that started it (the real bridge has no such limit).
        t = self.r.get(name)
        try:
            return t.func(self.ctx, **self.r.prepare_args(t, dict(args or {})))
        except ToolError as e:
            self.fail(f"{name}: {e}")

    def test_enlarge_text_makes_fonts_bigger_and_keeps_the_page_ok(self):
        size = "() => parseFloat(getComputedStyle(document.querySelector('main p, p')).fontSize)"
        before, zoom = self.page.evaluate(size), ally.zoom(self.page)
        out = self.call("enlarge_text", {"goal": "bigger"})
        self.assertEqual(out["tier"], "chrome-text")
        self.assertAlmostEqual(ally.zoom(self.page), zoom, places=3)  # never zooms
        self.assertGreaterEqual(self.page.evaluate(size), 20 - 0.5)  # 14px body text reaches the 20px goal
        self.assertGreater(self.page.evaluate(size), before)
        self.assertIn("ally_text", ally.styles(self.page))
        smaller = self.call("enlarge_text", {"goal": "smaller"})
        self.assertEqual(smaller["tier"], "chrome-text")
        self.assertLess(self.page.evaluate(size), 21)
        self.assertIn("looks fine", self.call("check_page_layout"))
        ally.screenshot(self.page, "e2e_enlarge_text")

    def test_hide_clutter_then_buttons_then_menus(self):
        self.assertIn("cookie banner", self.call("hide_clutter"))
        self.call("enlarge_buttons")
        self.call("simplify_menus")
        styles = ally.styles(self.page)
        for layer in ("ally_clutter", "ally_buttons", "ally_menus"):
            self.assertIn(layer, styles)
        self.assertIn("ally-menu-host", ally.page_state(self.page)["hosts"])
        self.call("simplify_menus", {"on": False})
        self.assertNotIn("ally_menus", ally.styles(self.page))
        self.assertIn("ally_clutter", ally.styles(self.page))  # the other steps stay

    def test_simplified_mode_on_and_off(self):
        out = self.call("simplified_mode")
        self.assertIn("Simple mode is on", out)
        self.assertEqual(self.ctx.said, ["One moment, I'm making this page simpler."])
        state = ally.page_state(self.page)
        self.assertIn("ally-simple-chip", state["hosts"])
        self.assertIn("ally-menu-strip", state["hosts"])
        self.assertNotIn("ally-simplifying", [h for h in state["hosts"] if self._visible(h)])
        ally.screenshot(self.page, "e2e_simplified_on")
        self.call("simplified_mode", {"on": False})
        state = ally.page_state(self.page)
        self.assertFalse([h for h in state["hosts"] if h != "ally-simplifying" and self._visible(h)])
        self.assertEqual([s for s in ally.styles(self.page) if s.startswith("ally_")], [])
        self.assertAlmostEqual(ally.zoom(self.page), ally.default_zoom(self.page), places=2)

    def test_simplified_mode_leaves_a_login_page_alone(self):
        self.page = ally.open("combo_login.html")
        out = self.call("simplified_mode")
        self.assertIn("won't simplify", out)
        self.assertEqual([s for s in ally.styles(self.page) if s.startswith("ally_")], [])

    def test_simplified_mode_on_an_article_opens_the_reader(self):
        self.page = ally.open("combo_article.html")
        self.call("simplified_mode")
        self.assertIn("ally-reader-host", ally.page_state(self.page)["hosts"])
        ally.screenshot(self.page, "e2e_simplified_article")

    def test_remember_survives_a_reload_without_changing_zoom(self):
        self.call("simplified_mode", {"remember": True})
        zoom = ally.zoom(self.page)
        self.assertTrue(ally.action("pageInfo")["simple"])
        self.page.reload(wait_until="load")
        self.page.wait_for_timeout(2500)  # hydration delay before the JS-only re-apply
        self.assertIn("ally-simple-chip", ally.page_state(self.page)["hosts"])
        self.assertAlmostEqual(ally.zoom(self.page), zoom, places=2)
        self.call("simplified_mode", {"on": False, "everywhere": True})
        self.assertFalse(ally.action("pageInfo")["simple"])

    def test_off_right_after_following_a_link_stays_off(self):
        self.call("simplified_mode")
        self.page = ally.open("combo_form.html")  # same site: simple mode follows the link after a short wait
        ally._tab_ids.clear()
        self.call("simplified_mode", {"on": False})  # ...but the user turns it off during that wait
        self.page.wait_for_timeout(2000)
        self.assertEqual([s for s in ally.styles(self.page) if s.startswith("ally_")], [])

    def test_a_remembered_site_gets_its_own_text_size_back(self):
        self.call("simplified_mode", {"remember": True})
        size = "() => parseFloat(getComputedStyle(document.querySelector('main p, p')).fontSize)"
        chosen = self.page.evaluate(size)
        self.page.reload(wait_until="load")
        self.page.wait_for_timeout(2500)
        self.assertAlmostEqual(self.page.evaluate(size), chosen, delta=0.5)
        self.call("simplified_mode", {"on": False, "everywhere": True})

    def test_reset_all_removes_everything(self):
        self.call("make_page_easier", {"steps": ["clutter", "menus", "text", "buttons"]})
        self.ctx.browser.call("resetPage")
        self.assertEqual([s for s in ally.styles(self.page) if s.startswith("ally_")], [])
        state = ally.page_state(self.page)
        self.assertFalse(state["marks"])
        self.assertFalse([h for h in state["hosts"] if self._visible(h)])

    def _visible(self, host_id: str) -> bool:
        return bool(self.page.evaluate(
            "(id) => { const h = document.getElementById(id); return !!h && h.isConnected && "
            "getComputedStyle(h).display !== 'none' && h.getBoundingClientRect().width > 0; }", host_id))


class ReviewPiecesEndToEnd(unittest.TestCase):
    """The real extension's pieces for the AI check: screenshot, outline, and the fix layer's own checks."""

    def setUp(self):
        self.page = ally.open(GENERAL)
        ally.action("pageTeardown")
        ally.reset_zoom(self.page)

    def test_screenshot_is_a_real_jpeg(self):
        import base64
        out = ally.action("pageScreenshot", {})
        data = base64.b64decode(out["data"])
        self.assertEqual(data[:2], b"\xff\xd8")  # JPEG start
        self.assertGreater(len(data), 5000)

    def test_outline_lists_selectors_the_page_has(self):
        out = ally.action("pageOutline", {})
        self.assertTrue(out["items"])
        for item in out["items"][:10]:
            self.assertGreaterEqual(self.page.evaluate("(k) => document.querySelectorAll(k).length", item["key"]), 1)

    def test_fix_layer_applies_safe_rules_and_refuses_unsafe_ones(self):
        ok = ally.action("stepOn", {"step": "fix", "opts": {"rules": [
            {"selector": "header", "property": "position", "value": "static"}]}})
        self.assertEqual(ok["rules"], 1)
        self.assertIn("ally_fix", ally.styles(self.page))
        self.assertEqual(self.page.evaluate("() => getComputedStyle(document.querySelector('header')).position"), "static")
        for rule in ({"selector": "nav", "property": "display", "value": "none"},
                     {"selector": "header", "property": "position", "value": "fixed"},
                     {"selector": "img", "property": "background", "value": "red"}):
            with self.assertRaises(ActionError):
                ally.action("stepOn", {"step": "fix", "opts": {"rules": [rule]}})
        ally.action("stepOff", {"step": "fix"})
        self.assertNotIn("ally_fix", ally.styles(self.page))

    def test_scripted_review_loop_on_the_real_extension(self):
        import json
        answers = [json.dumps({"worse": ["header covers text"], "actions": [
                       {"type": "css", "selector": "header", "property": "position", "value": "static"}],
                       "why": "unstick"}),
                   json.dumps({"worse": []})]
        ctx = Ctx(ally)
        ctx.config = {"page_enlarge": {"ai_background": False}}
        ctx.ask = lambda prompt, images=None, **kw: answers.pop(0)
        t = registry().get("enlarge_buttons")
        out = t.func(ctx, **registry().prepare_args(t, {}))
        self.assertIn("fixed it", out)
        self.assertIn("ally_fix", ally.styles(self.page))
        self.assertIn("ally_buttons", ally.styles(self.page))


if __name__ == "__main__":
    unittest.main()
