"""Tab switching and "show me where to click": phrases, choosing, and the tools against a fake extension."""

from __future__ import annotations

import unittest

import helpers  # noqa: F401  (adds the repo root to sys.path)

from ally import rules
from ally.pagekit import tabs as tabkit
from ally.registry import Registry
from ally.tools import browser_tools, click_tools


def tab(tid, index, title, url, active=False, current=True):
    return {"id": tid, "index": index, "title": title, "url": url, "active": active, "windowId": 1 if current else 2,
            "current": current}


LISTED = {"tabs": [tab(10, 0, "Inbox (3) - Gmail", "https://mail.google.com/mail/u/0/"),
                   tab(11, 1, "Golden Gate Bridge - Wikipedia", "https://en.wikipedia.org/wiki/Golden_Gate_Bridge", True),
                   tab(12, 2, "Welcome to USAGov", "https://www.usa.gov/"),
                   tab(20, 0, "YouTube", "https://www.youtube.com/", current=False)],
          "recent": [11, 12, 10]}


class PhraseTests(unittest.TestCase):
    def tool(self, text):
        m = rules.match(text)
        return m.actions[0] if m else None

    def test_tabs(self):
        self.assertEqual(self.tool("go to the other tab"), ("switch_tab", {"which": "other"}))
        self.assertEqual(self.tool("switch tabs"), ("switch_tab", {"which": "other"}))
        self.assertEqual(self.tool("next tab"), ("switch_tab", {"which": "next"}))
        self.assertEqual(self.tool("previous tab"), ("switch_tab", {"which": "previous"}))
        self.assertEqual(self.tool("go to tab 3"), ("switch_tab", {"number": 3}))
        self.assertEqual(self.tool("switch to the second tab"), ("switch_tab", {"number": 2}))
        self.assertEqual(self.tool("go to the gmail tab"), ("switch_tab", {"name": "gmail"}))
        self.assertEqual(self.tool("what tabs are open"), ("list_tabs", {}))
        self.assertIsNone(self.tool("open a new tab"))
        for text, name in (("switch back to facebook", "facebook"), ("switch to facebook", "facebook"),
                           ("go back to facebook", "facebook"), ("go back to my email", "email"),
                           ("switch back to the gmail tab", "gmail"), ("take me back to the news", "news")):
            self.assertEqual(self.tool(text), ("switch_tab", {"name": name}), text)
        # Phrases that mean something else keep their meaning (or go to Claude).
        self.assertEqual(self.tool("go back")[0], "browser_navigate")
        for text in ("switch to dark mode", "switch to reader view", "go back to the top", "go back to normal",
                     "go back to the previous page", "switch back to bigger text", "go to facebook"):
            self.assertNotEqual((self.tool(text) or ("",))[0], "switch_tab", text)

    def test_pointing(self):
        self.assertEqual(self.tool("where do I click to sign in"), ("show_where", {"target": "sign in"}))
        self.assertEqual(self.tool("where is the search box"), ("show_where", {"target": "search"}))
        self.assertEqual(self.tool("show me the sign in button"), ("show_where", {"target": "sign in"}))
        self.assertEqual(self.tool("show me where to click"), ("show_numbers", {"on": True}))
        self.assertEqual(self.tool("highlight the menu"), ("highlight_area", {"target": "menu"}))
        self.assertEqual(self.tool("highlight the links"), ("restyle_page", {"style": "highlight_links"}))
        self.assertEqual(self.tool("stop highlighting"), ("clear_highlight", {}))

    def test_unchanged(self):
        for text, tool in (("what am i looking at", "where_am_i_looking"), ("go back", "browser_navigate"),
                           ("reload", "browser_navigate"), ("click sign in", "click_text"),
                           ("show me the picture", "zoom_picture"), ("zoom in", "zoom_in")):
            self.assertEqual(self.tool(text)[0], tool, text)
        self.assertIsNone(self.tool("where am i looking"))  # Claude answers this one


class ChooseTests(unittest.TestCase):
    def test_other_is_the_last_used(self):
        self.assertEqual(tabkit.choose(LISTED, "other")["tab"]["id"], 12)
        no_history = dict(LISTED, recent=[])
        self.assertEqual(tabkit.choose(no_history, "other")["tab"]["id"], 10)  # falls back to the one on the left

    def test_next_previous_first_last_wrap(self):
        self.assertEqual(tabkit.choose(LISTED, "next")["tab"]["id"], 12)
        self.assertEqual(tabkit.choose(LISTED, "previous")["tab"]["id"], 10)
        self.assertEqual(tabkit.choose(LISTED, "first")["tab"]["id"], 10)
        self.assertEqual(tabkit.choose(LISTED, "last")["tab"]["id"], 12)
        at_end = {"tabs": [dict(t, active=t["id"] == 12) for t in LISTED["tabs"]], "recent": []}
        self.assertEqual(tabkit.choose(at_end, "next")["tab"]["id"], 10)

    def test_number_and_name(self):
        self.assertEqual(tabkit.choose(LISTED, number=1)["tab"]["id"], 10)
        self.assertEqual(tabkit.choose(LISTED, number=9)["why"], "no_number")
        self.assertEqual(tabkit.choose(LISTED, name="gmail")["tab"]["id"], 10)
        self.assertEqual(tabkit.choose(LISTED, name="my email")["tab"]["id"], 10)  # synonym
        self.assertEqual(tabkit.choose(LISTED, name="wikipedia")["tab"]["id"], 11)
        self.assertEqual(tabkit.choose(LISTED, name="youtube")["tab"]["id"], 20)  # other windows count for names
        self.assertEqual(tabkit.choose(LISTED, name="the news")["why"], "no_name")

    def test_one_tab(self):
        one = {"tabs": [tab(1, 0, "Only", "https://a.test/", True)], "recent": [1]}
        self.assertEqual(tabkit.choose(one, "other")["why"], "only_one")

    def test_where(self):
        view = {"w": 1000, "h": 800}
        self.assertEqual(tabkit.where({"x": 900, "y": 10, "w": 80, "h": 30}, view), "near the top right")
        self.assertEqual(tabkit.where({"x": 450, "y": 380, "w": 100, "h": 40}, view), "in the middle of the screen")
        self.assertEqual(tabkit.where({"x": 10, "y": 380, "w": 100, "h": 40}, view), "on the left")
        self.assertEqual(tabkit.where({"x": 450, "y": 700, "w": 100, "h": 40}, view), "near the bottom")


class FakeExt:
    connected = focused = True

    def __init__(self, items=None, listed=None):
        self.items = items or []
        self.listed = listed or LISTED
        self.calls = []
        self.shown = None

    def call(self, action, /, timeout=8.0, **args):
        self.calls.append((action, args))
        if action != "tabSwitch":
            args.pop("tabId", None)
        return getattr(self, "a_" + action)(**args)

    def a_pageInfo(self):
        return {"tabId": 11, "url": "https://example.test/"}

    def a_tabList(self):
        return self.listed

    def a_tabSwitch(self, tabId):
        self.switched = tabId
        return {"id": tabId}

    def a_highlightFind(self, max=400):
        return {"gen": 2, "view": {"w": 1000, "h": 800}, "items": self.items}

    def a_highlightShow(self, ids, gen, mode, note=""):
        assert gen == 2
        self.shown = (ids, mode, note)
        boxes = [next(i for i in self.items if i["id"] == x) for x in ids]
        return {"shown": len(boxes), "boxes": boxes, "view": {"w": 1000, "h": 800}}

    def a_highlightClear(self):
        had, self.shown = self.shown, None
        return {"said": "cleared the highlight" if had else ""}

    def a_clickShow(self, only=None):
        return {"items": [{"n": 1, "label": "Sign in"}]}


class Ctx:
    def __init__(self, ext):
        self.browser = ext
        self.config = {}
        self.agent = None


def run(ext, name, args=None):
    r = Registry()
    for fn in (browser_tools.switch_tab, browser_tools.list_tabs, click_tools.show_where, click_tools.highlight_area,
               click_tools.clear_highlight, click_tools.show_numbers):
        r.add(fn.__ally_tool__)
    out = r.call(name, args or {}, ctx=Ctx(ext))
    assert out.ok, out.error
    return out.text()


ITEMS = [{"id": "h1", "label": "main content text", "kind": "area", "x": 0, "y": 100, "w": 700, "h": 600},
         {"id": "h2", "label": "Main menu navigation", "kind": "area", "x": 200, "y": 10, "w": 300, "h": 30},
         {"id": "h3", "label": "History", "kind": "section", "x": 20, "y": 120, "w": 600, "h": 80},
         {"id": "h4", "label": "Sign in", "kind": "control", "x": 900, "y": 10, "w": 80, "h": 30},
         {"id": "h5", "label": "Search the catalogue", "kind": "control", "x": 600, "y": 10, "w": 200, "h": 30}]


class ToolTests(unittest.TestCase):
    def test_switch_and_list(self):
        ext = FakeExt()
        self.assertEqual(run(ext, "switch_tab"), "Switched to Welcome to USAGov.")
        self.assertEqual(ext.switched, 12)
        self.assertIn("You're already on", run(ext, "switch_tab", {"name": "wikipedia"}))
        self.assertIn("I don't see a tab called news", run(ext, "switch_tab", {"name": "news"}))
        self.assertIn("There's no tab 7", run(ext, "switch_tab", {"number": 7}))
        said = run(ext, "list_tabs")
        self.assertIn("You have 3 tabs: 1, Inbox (3) - Gmail; 2, Golden Gate Bridge - Wikipedia; 3, Welcome to USAGov.", said)
        self.assertIn("You're on 2.", said)

    def test_show_where(self):
        ext = FakeExt(ITEMS)
        said = run(ext, "show_where", {"target": "sign in"})
        self.assertEqual(said, "It's near the top right. I put a yellow box around it. Say click sign in to click it.")
        self.assertEqual(ext.shown, (["h4"], "point", "Click here: Sign in"))
        self.assertIn("search", run(ext, "show_where", {"target": "the search box"}).lower())
        self.assertIn("I don't see shopping cart", run(ext, "show_where", {"target": "shopping cart"}))
        self.assertIn("Showing 1 numbers", run(ext, "show_where", {"target": ""}))

    def test_highlight_area(self):
        ext = FakeExt(ITEMS)
        self.assertIn("Highlighted main content text", run(ext, "highlight_area", {"target": "main text"}))
        self.assertEqual(ext.shown[:2], (["h1"], "spotlight"))
        run(ext, "highlight_area", {"target": "the menu"})
        self.assertEqual(ext.shown[0], ["h2"])
        ext.items = ITEMS + [{"id": "h9", "label": "Breadcrumb menu navigation", "kind": "area", "x": 0, "y": 60,
                              "w": 400, "h": 20}]
        run(ext, "highlight_area", {"target": "menu"})
        self.assertEqual(ext.shown[0], ["h2"])  # the main menu, not the breadcrumb trail
        run(ext, "highlight_area", {"target": "the section about history"})
        self.assertEqual(ext.shown[0], ["h3"])
        self.assertEqual(run(ext, "clear_highlight"), "Cleared the highlight.")
        self.assertEqual(run(ext, "clear_highlight"), "Nothing is highlighted.")


if __name__ == "__main__":
    unittest.main()
