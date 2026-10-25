"""Eye scrolling against the real extension (ally/gaze_scroll.py): the page's rectangle on screen, scrolling the
page or a web app's inner box, and the scroller's steps driven with a fake gaze point.

Run from tests/:  python timed.py 90 browser.test_eye_scroll
"""

from __future__ import annotations

import unittest

try:
    import playwright  # noqa: F401
    HAVE_PLAYWRIGHT = True
except ImportError:
    HAVE_PLAYWRIGHT = False

import helpers  # noqa: F401,E402  (adds the repo root to sys.path)

from .harness import AllyBrowser, FixtureServer  # noqa: E402

from ally import gaze_scroll as gs  # noqa: E402

server: FixtureServer | None = None
ally: AllyBrowser | None = None
CFG = {"scroll": True, "scroll_speed": 0.35, "scroll_band": 0.15, "scroll_dwell": 0.6}


def setUpModule() -> None:
    global server, ally
    if not HAVE_PLAYWRIGHT:
        raise unittest.SkipTest("playwright isn't installed")
    server = FixtureServer().start()
    try:
        ally = AllyBrowser(server).start()
    except Exception:
        server.stop()
        raise


def tearDownModule() -> None:
    if ally:
        ally.close()
    if server:
        server.stop()


class Bridge:
    connected = True
    chrome_focused = True

    def call(self, action, args=None, timeout=8.0):
        return ally.action(action, args or {})


class Agent:
    def __init__(self):
        self.config = type("C", (), {"data": {"gaze": dict(CFG)}})()
        self.bridge = Bridge()


def look(scroller, gaze, seconds):
    """What the scroller's loop does, on this thread (Playwright's sync API needs that)."""
    t = 1000.0
    while t < 1000.0 + seconds:
        amount = gs.step(scroller.state, t, gaze, scroller._rect(True), CFG, False, (0, 0))
        if amount:
            scroller._scroll(True, amount)
        t += gs.TICK


class EyeScrollTests(unittest.TestCase):
    def test_viewport_rect_matches_the_page(self):
        page = ally.open("scroll_long.html")
        win = ally.action("viewport")
        x, y, w, h = gs.viewport_rect(win)
        dpr = page.evaluate("() => devicePixelRatio")
        self.assertAlmostEqual(w, 1280 * dpr, delta=2)
        self.assertAlmostEqual(h, 800 * dpr, delta=2)
        self.assertGreaterEqual(y, 0)

    def test_scroll_by_page_and_inner_box(self):
        page = ally.open("scroll_long.html")
        out = ally.action("scrollBy", {"screens": 0.5})
        self.assertEqual(out["box"], "page")
        self.assertAlmostEqual(page.evaluate("() => scrollY"), 400, delta=2)
        ally.action("scrollBy", {"screens": -1})
        self.assertEqual(page.evaluate("() => scrollY"), 0)
        self.assertTrue(ally.action("scrollBy", {"screens": -0.2})["atEnd"])
        page = ally.open("scroll_inner.html")
        out = ally.action("scrollBy", {"screens": 0.5})
        self.assertEqual(out["box"], "inner")
        self.assertGreater(page.evaluate("() => document.getElementById('mail').scrollTop"), 100)

    def test_looking_at_the_bottom_scrolls_down_then_up(self):
        page = ally.open("scroll_long.html")
        s = gs.EyeScroller(Agent())
        x, y, w, h = s._rect(True)
        look(s, (x + w / 2, y + h - 10), 2.0)  # near the bottom edge for two seconds
        down = page.evaluate("() => scrollY")
        self.assertGreater(down, 150)
        look(s, (x + w / 2, y + h / 2), 1.0)  # reading in the middle: nothing moves
        self.assertEqual(page.evaluate("() => scrollY"), down)
        look(s, (x + w / 2, y + 10), 2.0)  # near the top
        self.assertLess(page.evaluate("() => scrollY"), down - 150)

    def test_looking_at_the_tabs_does_not_scroll(self):
        page = ally.open("scroll_long.html")
        page.evaluate("() => scrollTo(0, 600)")
        s = gs.EyeScroller(Agent())
        x, y, w, h = s._rect(True)
        look(s, (x + w / 2, y - h * 0.2), 2.0)  # above the page: Chrome's tabs and address bar (off screen in tests)
        self.assertEqual(page.evaluate("() => scrollY"), 600)


if __name__ == "__main__":
    unittest.main()
