"""Click by voice / by number and Claude picture descriptions in a real browser (docs/click_by_voice.md).

Focus: what happens across browser back (back/forward cache), reload and navigation.
Run from tests/:  python timed.py 120 browser.test_clicks
"""

from __future__ import annotations

import json
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
from ally.tools import browser_tools, click_tools, page_tools  # noqa: E402

server: FixtureServer | None = None
ally: AllyBrowser | None = None

HOST = "ally-click-host"


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
    if not ally.has_step("clicks"):
        tearDownModule()
        raise unittest.SkipTest("the clicks step isn't registered in the extension")


def tearDownModule() -> None:
    if ally:
        ally.close()
    if server:
        server.stop()


# ---------------------------------------------------------------- a ctx for the Python tools

class Bridge:
    connected = True
    focused = True

    def call(self, action: str, /, timeout: float = 8.0, **args):
        try:
            return ally.action(action, args)
        except ActionError as e:
            raise ToolError(str(e).split(" failed: ", 1)[-1]) from None


class Answer:
    def __init__(self, value):
        self.value = value


class Confirm:
    def __init__(self, answer):
        self.answer, self.asked = answer, []

    def ask(self, question, kind="question", **kw):
        self.asked.append(question)
        return Answer(self.answer)


class Ctx:
    def __init__(self, confirm=None, claude=None):
        self.browser = Bridge()
        self.config = {"picture": {"describe_with_claude": True}}
        self.agent = type("Agent", (), {"confirm": confirm, "gaze": None})()
        self.claude = claude  # function(prompt) -> answer text, or None for "offline"
        self.asked = 0
        self.said = []

    def ask(self, prompt, image=None, media_type="image/png", max_tokens=600, images=None):
        self.asked += 1
        if self.claude is None:
            raise ToolError("This needs Claude.")
        assert image and len(image) > 1000, "a screenshot is sent"
        return self.claude(prompt)

    def say(self, text, wait=False):
        self.said.append(text)
        return True


def run(name, args=None, ctx=None):
    r = Registry()
    for t in (click_tools.show_numbers, click_tools.click_number, click_tools.click_text,
              page_tools.zoom_picture, page_tools.describe_picture, browser_tools.browser_navigate):
        r.add(t.__ally_tool__)
    # Arguments checked as usual, but the tool runs on this thread: Playwright's sync API only works on the thread
    # that started it (the registry runs tools on their own thread).
    t = r.get(name)
    try:
        return str(t.func(ctx or Ctx(), **r.prepare_args(t, dict(args or {}))))
    except ToolError as e:
        raise AssertionError(f"{name}: {e}") from None


# ---------------------------------------------------------------- page helpers

def fresh(path="clicks_a.html"):
    page = ally.open(path)
    ally.action("pageTeardown")
    ally.reset_zoom(page)
    return page


def badges(page) -> dict:
    """{number: [left, top]} of the badges drawn now (visible ones only)."""
    return page.evaluate("""(id) => {
        const host = document.getElementById(id);
        if (!host || !host.shadowRoot) return {};
        const out = {};
        for (const el of host.shadowRoot.querySelectorAll("*")) {
            const t = (el.textContent || "").trim();
            if (!/^\\d{1,2}$/.test(t) || el.children.length) continue;
            const r = el.getBoundingClientRect();
            if (r.width && r.height && getComputedStyle(el).display !== "none" && getComputedStyle(el).visibility !== "hidden")
                out[t] = [Math.round(r.left), Math.round(r.top)];
        }
        return out;
    }""", HOST)


def number_of(items, label):
    return next(int(i["n"]) for i in items if i["label"] == label)


def href(page) -> str:
    """The document's own address (Playwright's page.url lags behind navigations the extension starts)."""
    try:
        return page.evaluate("() => location.href")
    except Exception:  # mid-navigation
        return ""


def wait_url(page, part, timeout=5.0):
    deadline = time.time() + timeout
    while part not in href(page) and time.time() < deadline:
        page.wait_for_timeout(50)
    return part in href(page)


class NumberTests(unittest.TestCase):
    def test_show_and_hide(self):
        page = fresh()
        out = ally.action("clickShow")
        labels = [i["label"] for i in out["items"]]
        for want in ("Back", "Sign in", "Go to page B", "Add one", "Open help", "Email", "Remember me", "Buy now"):
            self.assertIn(want, labels)
        self.assertNotIn("Hidden link", labels)  # under the cookie bar
        self.assertNotIn("Far button", labels)  # off screen
        self.assertEqual([i["n"] for i in out["items"]], list(range(1, len(out["items"]) + 1)))
        self.assertEqual(len(badges(page)), len(out["items"]))
        self.assertEqual(ally.page_state(page)["marks"], {})  # the page itself isn't marked
        state = ally.action("clickState")
        self.assertTrue(state["shown"])
        self.assertEqual(len(state["items"]), len(out["items"]))
        self.assertIn("Numbers are off", run("show_numbers", {"on": False}))
        self.assertEqual(badges(page), {})
        self.assertFalse(ally.action("clickState")["shown"])

    def test_click_number_then_numbers_go(self):
        page = fresh()
        items = ally.action("clickShow")["items"]
        self.assertIn("Clicked Add one", run("click_number", {"n": number_of(items, "Add one")}))
        self.assertEqual(page.inner_text("#count"), "1")
        self.assertEqual(badges(page), {})
        self.assertIn("show numbers first", run("click_number", {"n": 1}))

    def test_text_field_gets_the_cursor(self):
        page = fresh()
        items = ally.action("clickShow")["items"]
        self.assertIn("put the cursor in Email", run("click_number", {"n": number_of(items, "Email")}))
        self.assertEqual(page.evaluate("() => document.activeElement.id"), "email")

    def test_numbers_follow_scrolling(self):
        page = fresh()
        items = ally.action("clickShow")["items"]
        n = str(number_of(items, "Add one"))
        before = badges(page)[n]
        page.evaluate("() => scrollBy(0, 40)")
        page.wait_for_timeout(150)
        after = badges(page).get(n)
        self.assertIsNotNone(after)
        self.assertAlmostEqual(after[1], before[1] - 40, delta=3)
        run("show_numbers", {"on": False})

    def test_reset_removes_numbers(self):
        page = fresh()
        ally.action("clickShow")
        ally.action("resetPage")
        self.assertEqual(ally.page_state(page)["hosts"], [])


class NavigationTests(unittest.TestCase):
    def test_click_link_then_browser_back(self):
        page = fresh()
        items = ally.action("clickShow")["items"]
        run("click_number", {"n": number_of(items, "Go to page B")})
        self.assertTrue(wait_url(page, "clicks_b.html"))
        page.wait_for_load_state("load")
        self.assertFalse(ally.action("clickState")["shown"])  # a new document: nothing showing
        # "go back": the page comes back (often from the back/forward cache) with no old numbers on it.
        self.assertEqual(run("browser_navigate", {"action": "back"}), "Went back.")
        tabs = ally.evaluate("async () => (await chrome.tabs.query({})).map((t) => [t.active, t.url])")
        self.assertTrue(wait_url(page, "clicks_a.html"), f"{href(page)} history={page.evaluate('() => history.length')} {tabs}")
        page.wait_for_load_state("load")
        page.wait_for_timeout(200)
        self.assertEqual(badges(page), {})
        self.assertFalse(ally.action("clickState")["shown"])
        # Numbers work again, freshly numbered, and clicking still works.
        items = ally.action("clickShow")["items"]
        self.assertEqual(items[0]["n"], 1)
        run("click_number", {"n": number_of(items, "Add one")})
        self.assertEqual(page.inner_text("#count"), "1")

    def test_back_forward_cache_with_numbers_showing(self):
        page = fresh()
        ally.action("clickShow")
        page.evaluate("() => { location.href = 'clicks_b.html'; }")  # leave with the numbers still up
        self.assertTrue(wait_url(page, "clicks_b.html"))
        page.wait_for_load_state("load")
        page.go_back(wait_until="load")
        page.wait_for_timeout(200)
        restored = page.evaluate("() => performance.getEntriesByType('navigation')[0].type")
        self.assertEqual(badges(page), {}, f"stale numbers after back ({restored})")
        self.assertFalse(ally.action("clickState")["shown"])
        self.assertTrue(ally.action("clickShow")["items"])

    def test_reload(self):
        page = fresh()
        ally.action("clickShow")
        run("click_number", {"n": number_of(ally.action("clickState")["items"], "Add one")})
        self.assertEqual(page.inner_text("#count"), "1")
        ally.action("clickShow")
        self.assertEqual(run("browser_navigate", {"action": "reload"}), "Reloaded the page.")
        page.wait_for_load_state("load")
        page.wait_for_timeout(200)
        self.assertEqual(page.inner_text("#count"), "0")
        self.assertEqual(badges(page), {})
        self.assertFalse(ally.action("clickState")["shown"])
        items = ally.action("clickShow")["items"]
        run("click_number", {"n": number_of(items, "Add one")})
        self.assertEqual(page.inner_text("#count"), "1")

    def test_click_back_uses_the_page_link_when_there_is_one(self):
        page = fresh()
        self.assertIn("Clicked Back", run("click_text", {"label": "back"}))
        self.assertTrue(wait_url(page, "from=back"))

    def test_click_back_without_a_back_link_goes_back(self):
        page = fresh()
        page.click("#to-b")
        self.assertTrue(wait_url(page, "clicks_b.html"))
        page.wait_for_load_state("load")
        self.assertIn("so I went back", run("click_text", {"label": "back"}))
        self.assertTrue(wait_url(page, "clicks_a.html"))

    def test_click_reload_without_a_reload_button_reloads(self):
        page = fresh()
        page.click("#add")
        self.assertIn("so I reloaded the page", run("click_text", {"label": "reload"}))
        page.wait_for_load_state("load")
        page.wait_for_timeout(200)
        self.assertEqual(page.inner_text("#count"), "0")

    def test_back_with_nowhere_to_go(self):
        tab = ally.evaluate("async () => (await chrome.tabs.create({url: '%s'})).id" % server.url("clicks_b.html?alone=1"))
        deadline = time.time() + 10
        while ally.evaluate("async (id) => (await chrome.tabs.get(id)).status", tab) != "complete" and time.time() < deadline:
            time.sleep(0.05)
        try:
            self.assertEqual(run("browser_navigate", {"action": "back"}), "There's no page before this one.")
        finally:
            ally.evaluate("(id) => chrome.tabs.remove(id)", tab)
            ally.page().bring_to_front()

    def test_back_in_a_tab_ally_opened_returns_to_its_tab(self):
        page = fresh()
        tabs = "async () => (await chrome.tabs.query({})).map((t) => [t.id, t.active, t.url])"
        run("click_text", {"label": "open help"})
        deadline = time.time() + 5
        while not any(t[1] and "help=1" in t[2] for t in ally.evaluate(tabs)) and time.time() < deadline:
            time.sleep(0.05)
        help_tab = next(t for t in ally.evaluate(tabs) if "help=1" in t[2])
        ally.evaluate("(id) => chrome.tabs.update(id, {active: true})", help_tab[0])
        time.sleep(0.5)
        said = run("browser_navigate", {"action": "back"})
        self.assertIn("went back to the tab it came from", said)
        active = next(t for t in ally.evaluate(tabs) if t[1])
        self.assertIn("clicks_a.html", active[2])
        ally.evaluate("(id) => chrome.tabs.remove(id)", help_tab[0])
        ally.page().bring_to_front()

    def test_new_tab_link(self):
        fresh()
        tabs = "async () => (await chrome.tabs.query({})).map((t) => t.url)"
        before = ally.evaluate(tabs)
        said = run("click_text", {"label": "open help"})
        deadline = time.time() + 5
        while len(ally.evaluate(tabs)) == len(before) and time.time() < deadline:
            time.sleep(0.05)
        after = ally.evaluate(tabs)
        self.assertEqual(len(after), len(before) + 1, f"{said} {after}")
        self.assertTrue(any("help=1" in u for u in after))
        ally.evaluate("async () => { for (const t of await chrome.tabs.query({url: '*://*/*help=1*'})) await chrome.tabs.remove(t.id); }")
        ally.page().bring_to_front()


class TextTests(unittest.TestCase):
    def test_click_by_words(self):
        page = fresh()
        self.assertIn("Clicked Add one", run("click_text", {"label": "add one"}))
        self.assertEqual(page.inner_text("#count"), "1")
        run("click_text", {"label": "the remember me box"})
        self.assertTrue(page.is_checked("#remember"))

    def test_two_match_asks_which(self):
        page = fresh()
        said = run("click_text", {"label": "sign in"})
        self.assertIn("I see 2 called Sign in. Which number?", said)
        state = ally.action("clickState")
        self.assertEqual(sorted(i["label"] for i in state["items"]), ["Sign in", "Sign in"])
        self.assertEqual(len(badges(page)), 2)
        run("click_number", {"n": 2})
        self.assertEqual(badges(page), {})

    def test_hash_link_runs_its_own_script(self):
        page = fresh()
        self.assertIn("Clicked Menu", run("click_text", {"label": "menu"}))
        self.assertEqual(page.inner_text("#menu-state"), "open")
        self.assertTrue(href(page).endswith("clicks_a.html") or href(page).endswith("#"))

    def test_button_inside_a_link_is_the_button(self):
        page = fresh()
        self.assertIn("Clicked Save for later", run("click_text", {"label": "save for later"}))
        self.assertEqual(page.inner_text("#saved"), "saved")
        self.assertIn("clicks_a.html", href(page))  # the card's link didn't open

    def test_icon_button_without_a_name_asks(self):
        page = fresh()
        n = next(i["n"] for i in ally.action("clickShow")["items"] if i["label"] == "")
        self.assertIn("yourself", run("click_number", {"n": n}))
        self.assertEqual(page.inner_text("#trash-state"), "")

    def test_not_there(self):
        fresh()
        self.assertIn("I don't see shopping cart", run("click_text", {"label": "shopping cart"}))

    def test_careful_click_asks(self):
        page = fresh()
        self.assertIn("click Buy now yourself", run("click_text", {"label": "buy now"}))  # can't ask out loud
        self.assertEqual(page.title(), "Clicks page A")
        no = Confirm("no")
        self.assertIn("didn't click", run("click_text", {"label": "buy now"}, Ctx(confirm=no)))
        self.assertIn("pays for something", no.asked[0])
        self.assertEqual(page.title(), "Clicks page A")
        self.assertIn("Clicked Buy now", run("click_text", {"label": "buy now"}, Ctx(confirm=Confirm("yes"))))
        self.assertEqual(page.title(), "Bought")


class DescribeTests(unittest.TestCase):
    def setUp(self):
        if not ally.has_step("picture"):
            self.skipTest("the picture step isn't registered")

    def claude(self, prompt):
        self.prompts.append(prompt)
        return json.dumps({"1": "A brown dog sitting on the grass."}) if "numbered" in prompt else \
            "A brown dog sits on green grass and looks at the camera."

    def test_claude_describes_the_undescribed_picture(self):
        self.prompts = []
        page = ally.open("picture_basic.html")
        ally.action("pageTeardown")
        page.wait_for_function("() => [...document.images].every((i) => i.complete)", timeout=5000)
        ctx = Ctx(claude=self.claude)
        said = run("zoom_picture", {"about": "the dog"}, ctx)
        self.assertIn("It looks like a brown dog sitting on the grass", said)
        self.assertEqual(ctx.asked, 1)
        caption = page.evaluate("() => document.getElementById('ally-picture-host').shadowRoot.textContent")
        self.assertIn("A brown dog sitting on the grass", caption)
        self.assertIsNone(page.evaluate("() => document.getElementById('ally-picture-boxes')"))  # boxes removed
        # With the viewer open, "describe the picture" asks Claude about what it shows.
        said = run("describe_picture", {}, ctx)
        self.assertIn("looks at the camera", said)
        ally.action("stepOff", {"step": "picture"})
        # Asked again on the same page: the description is remembered, no new call.
        ctx2 = Ctx(claude=self.claude)
        self.assertIn("brown dog", run("zoom_picture", {"about": "dog"}, ctx2))
        self.assertEqual(ctx2.asked, 0)
        ally.action("stepOff", {"step": "picture"})

    def test_offline_keeps_the_old_answer(self):
        page = ally.open("picture_basic.html")
        ally.action("pageTeardown")
        page.wait_for_function("() => [...document.images].every((i) => i.complete)", timeout=5000)
        said = run("zoom_picture", {"about": "the dog"}, Ctx(claude=None))
        self.assertIn("I don't see a picture of the dog", said)
        self.assertIsNone(page.evaluate("() => document.getElementById('ally-picture-boxes')"))

    def test_describe_without_viewer(self):
        self.prompts = []
        page = ally.open("picture_basic.html")
        ally.action("pageTeardown")
        page.wait_for_function("() => [...document.images].every((i) => i.complete)", timeout=5000)
        said = run("describe_picture", {"about": "bridge"}, Ctx(claude=self.claude))
        self.assertIn("Golden Gate Bridge at sunset", said)
        self.assertIsNone(page.evaluate("() => document.getElementById('ally-picture-host')"))  # no viewer opened


if __name__ == "__main__":
    unittest.main()
