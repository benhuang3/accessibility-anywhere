"""Click by voice / by number: phrases, matching, careful clicks, and the tools against a fake extension."""

from __future__ import annotations

import unittest

import helpers  # noqa: F401  (adds the repo root to sys.path)

from ally import rules
from ally.pagekit import clicks, picture
from ally.registry import Registry, ToolError
from ally.tools import click_tools

VIEW = {"w": 1280, "h": 800}


def target(cid, label, x=0, y=0, w=100, h=40, href=""):
    return {"id": cid, "label": label, "role": "link" if href else "button", "x": x, "y": y, "w": w, "h": h,
            "href": href, "inView": True}


class PhraseTests(unittest.TestCase):
    def tool(self, text, strict=True):
        m = rules.match(text, strict=strict)
        return m.actions[0] if m else None

    def test_numbers(self):
        for text in ("show numbers", "number the buttons", "what can I click", "show me the links"):
            self.assertEqual(self.tool(text), ("show_numbers", {"on": True}), text)
        for text in ("hide numbers", "numbers off", "hide the numbers"):
            self.assertEqual(self.tool(text), ("show_numbers", {"on": False}), text)
        cases = {"click 7": 7, "click number 12": 12, "press seven": 7, "tap twenty three": 23, "number 5": 5,
                 "click ninety nine": 99}
        for text, n in cases.items():
            self.assertEqual(self.tool(text), ("click_number", {"n": n}), text)
        # A bare number only offline: online it may answer Claude's own question ("how many tablets?").
        self.assertIsNone(self.tool("two"))
        self.assertIsNone(self.tool("7"))
        self.assertEqual(self.tool("seven", strict=False), ("click_number", {"n": 7}))
        self.assertNotEqual(self.tool("click 100")[0], "click_number")

    def test_click_words(self):
        self.assertEqual(self.tool("click sign in"), ("click_text", {"label": "sign in"}))
        self.assertEqual(self.tool("press continue"), ("click_text", {"label": "continue"}))
        self.assertEqual(self.tool("open the about link"), ("click_text", {"label": "about"}))
        self.assertEqual(self.tool("click back"), ("click_text", {"label": "back"}))
        self.assertEqual(self.tool("please tap on the search button for me", strict=False)[0], "click_text")

    def test_apps_and_tabs_are_not_clicks_or_pictures(self):
        for text in ("open a new tab", "open another new window", "open google photos",
                     "show me photos of my grandkids on facebook"):
            self.assertIsNone(self.tool(text), text)

    def test_navigation_keeps_its_words(self):
        for text, action in (("go back", "back"), ("back", "back"), ("previous", "back"), ("go forward", "forward"),
                             ("reload", "reload"), ("refresh", "reload"), ("refresh the page", "reload"),
                             ("reload this page", "reload")):
            self.assertEqual(self.tool(text), ("browser_navigate", {"action": action}), text)

    def test_other_phrases_unchanged(self):
        for text, tool in (("zoom in", "zoom_in"), ("open the picture", "zoom_picture"), ("read the page", "read_page"),
                           ("scroll down", "scroll_page"), ("stop", "stop_speaking"), ("reset", "reset_all"),
                           ("describe my screen", "describe_screen"), ("describe the picture", "describe_picture"),
                           ("what's in this photo", "describe_picture"), ("make the text bigger", "enlarge_text")):
            self.assertEqual(self.tool(text)[0], tool, text)
        self.assertEqual(self.tool("describe the photo of the bridge"), ("describe_picture", {"about": "the bridge"}))


class MatchTests(unittest.TestCase):
    def test_scores(self):
        items = [target("c1", "Sign in"), target("c2", "Sign in to your account"), target("c3", "Help")]
        self.assertEqual([i["id"] for i in clicks.match("sign in", items, VIEW)["items"]], ["c1"])
        self.assertEqual(clicks.match("the sign in button", items, VIEW)["items"][0]["id"], "c1")
        self.assertEqual(clicks.match("account", items, VIEW)["items"][0]["id"], "c2")
        self.assertEqual(clicks.match("shopping cart", items, VIEW)["items"], [])

    def test_tie_returns_all(self):
        items = [target("c1", "Sign in", x=0), target("c2", "Sign in", x=600, y=380)]
        got = clicks.match("sign in", items, VIEW)["items"]
        self.assertEqual([i["id"] for i in got], ["c2", "c1"])  # nearest the middle first

    def test_partial_prefers_the_shortest(self):
        items = [target("c1", "Back"), target("c2", "Back to top of page")]
        self.assertEqual([i["id"] for i in clicks.match("back", items, VIEW)["items"]], ["c1"])

    def test_careful(self):
        self.assertEqual(clicks.careful("Buy now"), "pays for something")
        self.assertEqual(clicks.careful("Place order"), "pays for something")
        self.assertEqual(clicks.careful("Send money"), "sends something")
        self.assertEqual(clicks.careful("Delete account"), "deletes something")
        self.assertEqual(clicks.careful("Sign out"), "signs you out")
        self.assertEqual(clicks.careful("Continue", href="https://shop.test/checkout"), "pays for something")
        self.assertIsNone(clicks.careful("Sign in"))
        self.assertIsNone(clicks.careful("Payment options explained"[:0]))

    def test_numbers(self):
        self.assertEqual(clicks.parse_number("7"), 7)
        self.assertEqual(clicks.parse_number("twenty three"), 23)
        self.assertIsNone(clicks.parse_number("100"))
        self.assertIsNone(clicks.parse_number("zero"))


class FakeClicks:
    def __init__(self, items, old=False):
        self.connected = True
        self.focused = True
        self.items = items
        self.old = old
        self.shown = []
        self.clicked = []
        self.navigated = []

    def call(self, action, /, timeout=8.0, **args):
        args.pop("tabId", None)
        if self.old and action.startswith("click"):
            raise ToolError(f"Unknown action: {action}")
        return getattr(self, "a_" + action)(**args)

    def a_pageInfo(self):
        return {"tabId": 1, "url": "https://example.test/", "zoom": 1.0}

    def a_clickList(self, max=300):
        return {"gen": 3, "view": VIEW, "items": self.items}

    def a_clickShow(self, only=None):
        pool = [i for i in self.items if only is None or i["id"] in only]
        self.shown = [dict(i, n=n + 1) for n, i in enumerate(pool)]
        return {"gen": 4, "items": self.shown}

    def a_clickState(self):
        return {"shown": bool(self.shown), "count": len(self.shown), "items": self.shown}

    def a_clickDo(self, n=None, id=None, gen=None):
        it = next(i for i in self.shown if i["n"] == n) if n is not None else next(i for i in self.items if i["id"] == id)
        if id is not None:
            assert gen == 3
        self.clicked.append(it["label"])
        self.shown = []
        return {"clicked": True, "label": it["label"], "role": it["role"],
                "kind": "focus" if it["role"] == "textbox" else "click"}

    def a_stepOff(self, step, opts):
        had, self.shown = bool(self.shown), []
        return {"said": "hid the numbers" if had else ""}

    def a_navigate(self, action):
        self.navigated.append(action)
        return action


class Confirm:
    def __init__(self, answer):
        self.answer = answer

    def ask(self, question, kind="question", **kw):
        self.question, self.kw = question, kw
        return type("A", (), {"value": self.answer})()


class Ctx:
    def __init__(self, ext, confirm=None):
        self.browser = ext
        self.config = {}
        self.agent = type("Agent", (), {"confirm": confirm})()


def run(ext, name, args=None, confirm=None):
    r = Registry()
    for fn in (click_tools.show_numbers, click_tools.click_number, click_tools.click_text):
        r.add(fn.__ally_tool__)
    return r.call(name, args or {}, ctx=Ctx(ext, confirm))


ITEMS = [target("c1", "Sign in"), target("c2", "Sign in", x=600, y=380), target("c3", "Add one"),
         target("c4", "Buy now"), dict(target("c5", "Email"), role="textbox")]


class ToolTests(unittest.TestCase):
    def test_show_and_click_number(self):
        ext = FakeClicks(ITEMS)
        self.assertIn("Say show numbers first", run(ext, "click_number", {"n": 1}).text())
        self.assertIn("Showing 5 numbers", run(ext, "show_numbers").text())
        self.assertIn("There's no number 9", run(ext, "click_number", {"n": 9}).text())
        self.assertEqual(run(ext, "click_number", {"n": 3}).text(), "Clicked Add one.")
        self.assertEqual(ext.clicked, ["Add one"])

    def test_click_text_and_which_one(self):
        ext = FakeClicks(ITEMS)
        self.assertEqual(run(ext, "click_text", {"label": "add one"}).text(), "Clicked Add one.")
        said = run(ext, "click_text", {"label": "sign in"}).text()
        self.assertEqual(said, "I see 2 called Sign in. Which number?")
        self.assertEqual(sorted(i["id"] for i in ext.shown), ["c1", "c2"])
        self.assertIn("put the cursor in Email", run(ext, "click_text", {"label": "email"}).text())

    def test_careful(self):
        ext = FakeClicks(ITEMS)
        self.assertIn("click Buy now yourself", run(ext, "click_text", {"label": "buy now"}).text())
        self.assertIn("didn't click", run(ext, "click_text", {"label": "buy now"}, Confirm("no")).text())
        self.assertIn("didn't click", run(ext, "click_text", {"label": "buy now"}, Confirm("timeout")).text())
        self.assertEqual(ext.clicked, [])
        self.assertEqual(run(ext, "click_text", {"label": "buy now"}, Confirm("yes")).text(), "Clicked Buy now.")

    def test_back_and_reload_fall_back_to_the_browser(self):
        ext = FakeClicks([target("c1", "Help")])
        self.assertIn("so I went back", run(ext, "click_text", {"label": "back"}).text())
        self.assertIn("so I reloaded the page", run(ext, "click_text", {"label": "refresh"}).text())
        self.assertEqual(ext.navigated, ["back", "reload"])
        self.assertIn("I don't see shopping cart", run(ext, "click_text", {"label": "shopping cart"}).text())

    def test_careful_sees_all_the_words_and_unnamed_buttons(self):
        hidden = dict(target("c1", "Primary action"), text="Primary action | Place order")
        unnamed = target("c2", "")
        ext = FakeClicks([hidden, unnamed])
        self.assertIn("didn't click", run(ext, "click_text", {"label": "primary action"}, Confirm("no")).text())
        ext.a_clickShow()
        c = Confirm("no")
        self.assertIn("didn't click", run(ext, "click_number", {"n": 2}, c).text())
        self.assertIn("can't tell what that button does", c.question)
        self.assertGreater(c.kw["deadline"], 0)  # the question can't outlive the tool
        self.assertEqual(ext.clicked, [])

    def test_back_link_on_the_page_wins(self):
        ext = FakeClicks([target("c1", "Back", href="https://example.test/list")])
        self.assertEqual(run(ext, "click_text", {"label": "back"}).text(), "Clicked Back.")
        self.assertEqual(ext.navigated, [])

    def test_old_extension(self):
        out = run(FakeClicks(ITEMS, old=True), "click_text", {"label": "add one"})
        self.assertFalse(out.ok)
        self.assertIn("Reload it at chrome://extensions", out.error)


class BrowserCallTests(unittest.TestCase):
    def test_navigate_passes_its_action_argument(self):
        """Regression: ctx.browser.call("navigate", action="back") raised "multiple values for 'action'"."""
        from ally.context import BrowserAPI

        class Bridge:
            def call(self, action, args=None, timeout=8.0):
                return action, args, timeout

        self.assertEqual(BrowserAPI(Bridge()).call("navigate", action="back"), ("navigate", {"action": "back"}, 8.0))


class DescriptionParsingTests(unittest.TestCase):
    def test_parse(self):
        numbers = {"p2": 1, "p5": 2}
        self.assertEqual(picture.parse_descriptions('Sure! {"1": "A dog.", "2": "  A  cat. "}', numbers),
                         {"p2": "A dog.", "p5": "A cat."})
        self.assertEqual(picture.parse_descriptions("no json here", numbers), {})
        self.assertEqual(picture.parse_descriptions('{"1": ', numbers), {})
        self.assertEqual(picture.parse_descriptions('{"3": "x"}', numbers), {})

    def test_add(self):
        items = [{"id": "p1", "descriptions": [], "described": False}]
        picture.add_descriptions(items, {"p1": "A dog."})
        self.assertTrue(items[0]["described"])
        self.assertEqual(items[0]["descriptions"], [{"text": "A dog.", "kind": "claude"}])
        self.assertEqual(picture.choose(items, "dog", view=VIEW)["item"]["id"], "p1")

    def test_claude_words_are_spoken_over_a_caption(self):
        item = {"id": "p1", "descriptions": [{"text": "View from the Presidio, 2017", "kind": "caption"}],
                "described": True}
        picture.add_descriptions([item], {"p1": "The Golden Gate Bridge over blue water."})
        self.assertEqual(picture.description(item), "The Golden Gate Bridge over blue water.")
        self.assertEqual(picture.choose([item], "the bridge", view=VIEW)["why"], "match")

    def test_logos_only_when_nothing_else(self):
        logo = {"id": "p1", "x": 590, "y": 350, "w": 100, "h": 100, "area": 10000,
                "descriptions": [{"text": "USAGov Logo", "kind": "alt"}], "described": True}
        photo = {"id": "p2", "x": 0, "y": 600, "w": 200, "h": 150, "area": 30000,
                 "descriptions": [{"text": "Two people at a desk", "kind": "alt"}], "described": True}
        self.assertEqual(picture.choose([logo, photo], view=VIEW)["item"]["id"], "p2")
        self.assertEqual(picture.choose([logo], view=VIEW)["item"]["id"], "p1")
        self.assertEqual(picture.choose([logo, photo], "logo", view=VIEW)["item"]["id"], "p1")


if __name__ == "__main__":
    unittest.main()
