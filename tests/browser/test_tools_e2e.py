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

    def test_enlarge_text_picks_a_zoom_that_keeps_the_page_ok(self):
        out = self.call("enlarge_text", {"goal": "bigger"})
        self.assertEqual(out["tier"], "chrome-fit")
        self.assertGreater(ally.zoom(self.page), 1.0)
        self.assertIn("ally_fit", ally.styles(self.page))
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


if __name__ == "__main__":
    unittest.main()
