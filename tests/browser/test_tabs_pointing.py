"""Tab switching and "show me where to click" / highlighting in a real browser (docs/tabs_and_pointing.md).

Run from tests/:  python timed.py 120 browser.test_tabs_pointing
"""

from __future__ import annotations

import time
import unittest

try:
    import playwright  # noqa: F401
    HAVE_PLAYWRIGHT = True
except ImportError:
    HAVE_PLAYWRIGHT = False

import helpers  # noqa: F401,E402  (adds the repo root to sys.path)

from .harness import ActionError, AllyBrowser, FixtureServer  # noqa: E402

from ally.registry import Registry, ToolError  # noqa: E402
from ally.tools import browser_tools, click_tools  # noqa: E402

server: FixtureServer | None = None
ally: AllyBrowser | None = None
HOST = "ally-highlight-host"


def setUpModule() -> None:
    global server, ally
    if not HAVE_PLAYWRIGHT:
        raise unittest.SkipTest("playwright isn't installed (pip install playwright; python -m playwright install chromium)")
    server = FixtureServer().start()
    try:
        ally = AllyBrowser(server).start()
    except Exception:
        server.stop()
        raise
    if not ally.has_step("highlight"):
        tearDownModule()
        raise unittest.SkipTest("the highlight step isn't registered in the extension")


def tearDownModule() -> None:
    if ally:
        ally.close()
    if server:
        server.stop()


class Bridge:
    connected = focused = True

    def call(self, action: str, /, timeout: float = 8.0, **args):
        try:
            return ally.action(action, args)
        except ActionError as e:
            raise ToolError(str(e).split(" failed: ", 1)[-1]) from None


class Ctx:
    def __init__(self):
        self.browser = Bridge()
        self.config = {}
        self.agent = None


def run(name, args=None):
    """Arguments checked as usual, but the tool runs on this thread (Playwright's sync API needs that)."""
    r = Registry()
    for t in (browser_tools.switch_tab, browser_tools.list_tabs, click_tools.show_where, click_tools.highlight_area,
              click_tools.clear_highlight, click_tools.show_numbers):
        r.add(t.__ally_tool__)
    t = r.get(name)
    try:
        return str(t.func(Ctx(), **r.prepare_args(t, dict(args or {}))))
    except ToolError as e:
        raise AssertionError(f"{name}: {e}") from None


TABS = "async () => (await chrome.tabs.query({windowType: 'normal'})).map((t) => [t.id, t.active, t.url, t.index])"


def active_url() -> str:
    return next(t[2] for t in ally.evaluate(TABS) if t[1])


def wait_active(part: str, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while part not in active_url() and time.time() < deadline:
        time.sleep(0.05)
    return part in active_url()


def overlay(page) -> dict:
    return page.evaluate("""(id) => {
        const host = document.getElementById(id);
        if (!host) return { shown: false };
        const root = host.shadowRoot;
        const vis = (sel) => [...root.querySelectorAll(sel)].map((e) => e.getBoundingClientRect())
            .filter((r) => r.width > 0 && r.height > 0).map((r) => [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]);
        return { shown: true, boxes: vis(".box"), shades: vis(".shade").length, arrows: vis(".arrow").length,
                 note: (root.querySelector(".note") || {}).textContent || "" };
    }""", HOST)


class TabTests(unittest.TestCase):
    def setUp(self):
        self.page = ally.open("clicks_a.html")
        self.extra = [ally.evaluate("async (u) => (await chrome.tabs.create({url: u, active: false})).id", server.url(p))
                      for p in ("clicks_b.html", "picture_basic.html")]
        deadline = time.time() + 10
        while time.time() < deadline and any(
                ally.evaluate("async (id) => (await chrome.tabs.get(id)).status", t) != "complete" for t in self.extra):
            time.sleep(0.05)
        ally.evaluate("async () => { const [t] = await chrome.tabs.query({url: '*://*/clicks_a.html'}); "
                      "await chrome.tabs.update(t.id, {active: true}); }")
        self.assertTrue(wait_active("clicks_a.html"))

    def tearDown(self):
        for t in self.extra:
            ally.evaluate("(id) => chrome.tabs.remove(id).catch(() => {})", t)
        ally.page().bring_to_front()

    def test_next_previous_number_and_name(self):
        self.assertIn("Switched to Clicks page B", run("switch_tab", {"which": "next"}))
        self.assertTrue(wait_active("clicks_b.html"))
        self.assertIn("Switched to Clicks page A", run("switch_tab", {"which": "previous"}))
        self.assertTrue(wait_active("clicks_a.html"))
        self.assertIn("Switched to Picture basic page", run("switch_tab", {"number": 3}))
        self.assertTrue(wait_active("picture_basic.html"))
        self.assertIn("Switched to Clicks page B", run("switch_tab", {"name": "clicks page b"}))
        self.assertTrue(wait_active("clicks_b.html"))
        self.assertIn("I don't see a tab called gmail", run("switch_tab", {"name": "gmail"}))

    def test_other_tab_goes_back_and_forth(self):
        run("switch_tab", {"number": 3})
        self.assertTrue(wait_active("picture_basic.html"))
        self.assertIn("Clicks page A", run("switch_tab", {"which": "other"}))
        self.assertTrue(wait_active("clicks_a.html"))
        self.assertIn("Picture basic page", run("switch_tab", {"which": "other"}))
        self.assertTrue(wait_active("picture_basic.html"))

    def test_list(self):
        said = run("list_tabs")
        self.assertIn("You have 3 tabs: 1, Clicks page A; 2, Clicks page B; 3, Picture basic page.", said)
        self.assertIn("You're on 1.", said)


class PointingTests(unittest.TestCase):
    def fresh(self):
        page = ally.open("highlight.html")
        ally.action("pageTeardown")
        return page

    def test_show_where_points_without_clicking(self):
        page = self.fresh()
        said = run("show_where", {"target": "sign in"})
        self.assertEqual(said, "It's near the top right. I put a yellow box around it. Say click sign in to click it.")
        o = overlay(page)
        self.assertEqual(len(o["boxes"]), 1)
        self.assertEqual(o["arrows"], 1)
        self.assertIn("Click here: Sign in", o["note"])
        self.assertEqual(ally.page_state(page)["marks"], {})
        # The page still works under the overlay; a real click clears the pointer.
        page.click("#q")
        self.assertFalse(overlay(page)["shown"])
        self.assertEqual(page.evaluate("() => document.activeElement.id"), "q")

    def test_off_screen_target_is_scrolled_to(self):
        page = self.fresh()
        self.assertIn("I put a yellow box", run("show_where", {"target": "contact us"}))
        self.assertGreater(page.evaluate("() => scrollY"), 1000)
        box = overlay(page)["boxes"][0]
        self.assertTrue(0 <= box[1] < page.evaluate("() => innerHeight"), box)

    def test_box_follows_scrolling(self):
        page = self.fresh()
        run("show_where", {"target": "history"})
        before = overlay(page)["boxes"][0]
        page.evaluate("() => scrollBy(0, 30)")
        page.wait_for_timeout(150)
        after = overlay(page)["boxes"][0]
        self.assertAlmostEqual(after[1], before[1] - 30, delta=3)

    def test_spotlight_areas_and_sections(self):
        page = self.fresh()
        self.assertIn("Highlighted main content text", run("highlight_area", {"target": "the main text"}))
        o = overlay(page)
        self.assertGreaterEqual(o["shades"], 2)  # main is taller than the screen: only the side strips show
        self.assertEqual(o["arrows"], 0)
        run("highlight_area", {"target": "prices"})
        self.assertEqual(overlay(page)["shades"], 4)  # a small section: dimmed on all four sides
        self.assertIn("Highlighted Main menu navigation", run("highlight_area", {"target": "menu"}))
        self.assertIn("Highlighted History", run("highlight_area", {"target": "the section about history"}))
        page.mouse.click(5, 5)  # a click doesn't clear a spotlight
        self.assertTrue(overlay(page)["shown"])
        self.assertEqual(run("clear_highlight"), "Cleared the highlight.")
        self.assertFalse(overlay(page)["shown"])

    def test_section_without_a_section_box(self):
        page = self.fresh()
        self.assertIn("Highlighted Rules", run("highlight_area", {"target": "the section about rules"}))
        box = overlay(page)["boxes"][0]
        heading = page.evaluate("() => document.getElementById('rules-h').getBoundingClientRect().height")
        self.assertGreater(box[3], heading * 2.5)  # the heading and its two paragraphs, not the heading alone
        volunteers = page.evaluate("() => [...document.querySelectorAll('h2')].find((h) => h.textContent === 'Volunteers')"
                                   ".getBoundingClientRect().top")
        self.assertLessEqual(box[1] + box[3], volunteers + 8)  # and stops before the next heading
        run("clear_highlight")

    def test_escape_reset_and_back(self):
        page = self.fresh()
        run("highlight_area", {"target": "prices"})
        page.keyboard.press("Escape")
        self.assertFalse(overlay(page)["shown"])
        run("show_where", {"target": "sign in"})
        ally.action("resetPage")
        self.assertFalse(overlay(page)["shown"])
        self.assertEqual(ally.page_state(page)["hosts"], [])
        run("highlight_area", {"target": "prices"})
        page.evaluate("() => { location.href = 'clicks_b.html'; }")
        page.wait_for_function("() => location.href.includes('clicks_b')", timeout=5000)
        page.go_back(wait_until="load")
        page.wait_for_timeout(200)
        self.assertFalse(overlay(page)["shown"])

    def test_not_found(self):
        self.fresh()
        self.assertIn("I don't see shopping cart", run("show_where", {"target": "shopping cart"}))
        self.assertIn("I don't see weather", run("highlight_area", {"target": "weather"}))


if __name__ == "__main__":
    unittest.main()
