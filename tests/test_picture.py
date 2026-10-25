"""Picture viewer: phrases, matching and choosing, and the tools against a fake extension (docs/picture_viewer.md)."""

from __future__ import annotations

import unittest

import helpers  # noqa: F401  (adds the repo root to sys.path)

from ally import rules
from ally.pagekit import picture
from ally.registry import Registry, ToolError
from ally.tools import page_tools, system_tools


def item(pid, text="", kind="alt", x=0, y=0, w=300, h=200, described=None):
    descs = [{"text": text, "kind": kind}] if text else []
    return {"id": pid, "kind": "img", "x": x, "y": y, "w": w, "h": h, "area": w * h, "frac": 1.0,
            "descriptions": descs, "described": bool(text) if described is None else described,
            "natural": {"w": w, "h": h}}


VIEW = {"w": 1280, "h": 800}


class PhraseTests(unittest.TestCase):
    def tool(self, text, strict=True):
        m = rules.match(text, strict=strict)
        return m.actions[0] if m else None

    def test_picture_phrases(self):
        cases = {
            "zoom in on the picture": "",
            "zoom in on the picture of the bridge": "the bridge",
            "zoom into the photo of the golden gate bridge": "the golden gate bridge",
            "zoom in on the bridge picture": "bridge",
            "magnify the image": "",
            "enlarge the photo": "",
            "show me the picture bigger": "",
            "make the photo bigger": "",
            "open the picture": "",
            "Ally, zoom in on that photo please": "",
            "zoom in on the pictures of dogs": "dogs",
        }
        for text, about in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.tool(text), ("zoom_picture", {"about": about}))

    def test_viewer_phrases(self):
        self.assertEqual(self.tool("close the picture"), ("close_picture", {}))
        self.assertEqual(self.tool("next picture"), ("next_picture", {}))
        self.assertEqual(self.tool("show me another photo"), ("next_picture", {}))
        self.assertEqual(self.tool("previous photo"), ("next_picture", {"back": True}))
        self.assertEqual(self.tool("zoom out of the picture"), ("zoom_out", {}))
        self.assertEqual(self.tool("zoom out on the photo"), ("zoom_out", {}))
        self.assertEqual(self.tool("please zoom out of the photo now", strict=False), ("zoom_out", {}))

    def test_other_phrases_keep_their_tools(self):
        cases = {"zoom in": "zoom_in", "zoom": "zoom_in", "zoom out": "zoom_out", "make the text bigger": "enlarge_text",
                 "bigger": "enlarge_text", "magnify where I'm looking": "magnify_where_i_look",
                 "show me where I'm looking": "show_gaze", "read the page": "read_page",
                 "simplify the menu": "simplify_menus", "zoom in 200%": "zoom_to", "go back": "browser_navigate",
                 "previous": "browser_navigate"}
        for text, tool in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.tool(text)[0], tool)

    def test_no_picture_word_is_not_a_picture(self):
        self.assertIsNone(self.tool("zoom in on the map"))  # online: Claude decides
        self.assertIsNone(self.tool("take a picture"))
        self.assertEqual(self.tool("zoom in on the map", strict=False)[0], "zoom_in")

    def test_offline_loose_match(self):
        self.assertEqual(self.tool("could you maybe zoom in on the photo of the dog for me", strict=False),
                         ("zoom_picture", {"about": "the dog"}))

    def test_help_mentions_it(self):
        self.assertIn("zoom in on the picture", rules.HELP_TEXT)


class MatchingTests(unittest.TestCase):
    def test_tokens(self):
        self.assertEqual(picture.tokens("the photos of red trams"), ["red", "tram"])
        self.assertEqual(picture.tokens("Bridges"), picture.tokens("bridge"))
        self.assertEqual(picture.tokens("ponies"), ["pony"])
        self.assertEqual(picture.tokens("bus"), ["bus"])
        self.assertEqual(picture.tokens("puppy"), ["dog"])
        self.assertEqual(picture.tokens("picture of a"), [])

    def test_score(self):
        alt = [{"text": "Golden Gate Bridge at sunset", "kind": "alt"}]
        cap = [{"text": "Golden Gate Bridge at sunset", "kind": "caption"}]
        self.assertAlmostEqual(picture.score("the bridge", alt), 1.1)
        self.assertAlmostEqual(picture.score("the bridge", cap), 1.0)
        self.assertAlmostEqual(picture.score("bridge at night", cap), 0.5)
        self.assertEqual(picture.score("dog", alt), 0.0)
        self.assertEqual(picture.score("", alt), 0.0)

    def test_choose_named(self):
        items = [item("p1", "A red tram on a hill"), item("p2", "Golden Gate Bridge at sunset")]
        got = picture.choose(items, "the bridge", view=VIEW)
        self.assertEqual((got["item"]["id"], got["why"]), ("p2", "match"))
        self.assertEqual(picture.choose(items, "a cat", view=VIEW), {"item": None, "why": "no_match"})

    def test_choose_named_tie_goes_to_bigger(self):
        items = [item("p1", "dog in a park", w=100, h=100), item("p2", "dog on a beach", w=400, h=300)]
        self.assertEqual(picture.choose(items, "dog", view=VIEW)["item"]["id"], "p2")

    def test_undescribed_never_chosen(self):
        items = [item("p1"), item("p2", "")]
        self.assertEqual(picture.choose(items, "", view=VIEW), {"item": None, "why": "none_described"})
        self.assertEqual(picture.choose(items, "dog", view=VIEW), {"item": None, "why": "none_described"})
        self.assertEqual(picture.choose([], "", view=VIEW), {"item": None, "why": "none"})
        mixed = [item("p1", w=900, h=600), item("p2", "small boat", w=200, h=150)]
        self.assertEqual(picture.choose(mixed, "", view=VIEW)["item"]["id"], "p2")

    def test_choose_single_and_centre(self):
        self.assertEqual(picture.choose([item("p1", "a boat")], view=VIEW)["item"]["id"], "p1")
        corner = item("p1", "a boat", x=0, y=0, w=300, h=200)
        middle = item("p2", "a car", x=490, y=300, w=300, h=200)
        got = picture.choose([corner, middle], view=VIEW)
        self.assertEqual((got["item"]["id"], got["why"]), ("p2", "centre"))
        big_corner = item("p3", "a lake", x=0, y=0, w=700, h=500)
        self.assertEqual(picture.choose([big_corner, middle], view=VIEW)["item"]["id"], "p3")

    def test_gaze_breaks_the_tie(self):
        left = item("p1", "a boat", x=0, y=300, w=300, h=200)
        middle = item("p2", "a car", x=490, y=300, w=300, h=200)
        got = picture.choose([left, middle], gaze=(150, 400, 20), view=VIEW)
        self.assertEqual((got["item"]["id"], got["why"]), ("p1", "gaze"))
        # A wide gaze circle over both pictures doesn't decide.
        self.assertEqual(picture.choose([left, middle], gaze=(395, 400, 200), view=VIEW)["why"], "centre")
        # A name always wins over the eyes.
        self.assertEqual(picture.choose([left, middle], "car", gaze=(150, 400, 20), view=VIEW)["item"]["id"], "p2")

    def test_reading_order_and_description(self):
        items = [item("p1", "first"), item("p2"), item("p3", "third", kind="caption")]
        self.assertEqual(picture.reading_order(items), ["p1", "p3"])
        self.assertEqual(picture.description(items[2]), "third")
        self.assertEqual(picture.description(items[1]), "")

    def test_spoken(self):
        self.assertEqual(picture.spoken("Golden Gate Bridge at sunset. Taken from the north."), "Golden Gate Bridge at sunset")
        self.assertEqual(picture.spoken("St. Mary's church"), "St. Mary's church")
        long = " ".join(["word"] * 60)
        out = picture.spoken(long)
        self.assertLessEqual(len(out), picture.SPOKEN_MAX + 3)
        self.assertTrue(out.endswith("..."))

    def test_screen_to_viewport(self):
        # 100% Windows scale, 100% page zoom: window at (100, 50), 8 px borders, 80 px of toolbars on top.
        win = {"screenX": 100, "screenY": 50, "outerW": 1296, "outerH": 888, "innerW": 1280, "innerH": 800,
               "dpr": 1.0, "zoom": 1.0}
        self.assertEqual(picture.screen_to_viewport(108 + 10, 50 + 80 + 20, win), (10.0, 20.0))
        # 150% Windows scale: the eye tracker gives physical px, Chrome gives DIPs.
        win150 = dict(win, dpr=1.5)
        x, y = picture.screen_to_viewport((108 + 10) * 1.5, (50 + 80 + 20) * 1.5, win150)
        self.assertAlmostEqual(x, 10.0)
        self.assertAlmostEqual(y, 20.0)
        # 125% page zoom on a 100% screen: innerW/innerH shrink in CSS px, dpr grows.
        win_zoom = {"screenX": 100, "screenY": 50, "outerW": 1296, "outerH": 888, "innerW": 1024, "innerH": 640,
                    "dpr": 1.25, "zoom": 1.25}
        x, y = picture.screen_to_viewport(108 + 125, 50 + 80 + 250, win_zoom)
        self.assertAlmostEqual(x, 100.0)
        self.assertAlmostEqual(y, 200.0)


class FakePictures:
    """The extension's picture actions on one page."""

    def __init__(self, items, old=False):
        self.connected = True
        self.focused = True
        self.items = items
        self.old = old
        self.open = None
        self.scale = 1.0
        self.page_zoom = 1.0
        self.calls = []

    def call(self, action, timeout=8.0, **args):
        self.calls.append((action, {k: v for k, v in args.items() if k != "tabId"}))
        if self.old and action.startswith("picture"):
            raise ToolError(f"Unknown action: {action}")
        return getattr(self, "a_" + action)(**{k: v for k, v in args.items() if k != "tabId"})

    def a_pageInfo(self):
        return {"tabId": 3, "url": "https://example.test/", "zoom": self.page_zoom, "type": "general"}

    def a_pictureList(self):
        return {"view": VIEW, "win": {"screenX": 0, "screenY": 0, "outerW": 1280, "outerH": 880, "innerW": 1280,
                                      "innerH": 800, "dpr": 1.0, "zoom": 1.0}, "items": self.items, "gen": 5}

    def a_stepOn(self, step, opts):
        assert step == "picture"
        assert opts["gen"] == 5
        self.open, self.order, self.scale = opts["id"], opts["order"], 1.0
        return {"said": "opened the picture"}

    def a_stepOff(self, step, opts):
        was, self.open = self.open, None
        return {"said": "closed the picture" if was else ""}

    def a_pictureState(self):
        return {"open": self.open is not None, "id": self.open, "scale": self.scale}

    def a_pictureZoom(self, direction, closeAtFit=False):
        if direction == "out" and self.scale <= 1 and closeAtFit:
            self.open = None
            return {"open": False, "scale": 1.0, "closed": True}
        self.scale = self.scale * 1.5 if direction == "in" else max(1.0, self.scale / 1.5)
        return {"open": True, "scale": self.scale, "closed": False}

    def a_pictureStep(self, back=False):
        if self.open is None:
            return {"open": False}
        i = (self.order.index(self.open) + (-1 if back else 1)) % len(self.order)
        self.open = self.order[i]
        text = next(d["text"] for it in self.items if it["id"] == self.open for d in it["descriptions"])
        return {"open": True, "id": self.open, "description": text}

    def a_zoomStep(self, direction):
        self.page_zoom = round(self.page_zoom * (1.1 if direction == "in" else 1 / 1.1), 3)
        return self.page_zoom


class Ctx:
    def __init__(self, ext, gaze=None):
        self.browser = ext
        self.config = {"agent": {"zoom_target": "auto"}}
        self.agent = type("Agent", (), {"gaze": gaze})()

    def say(self, text, wait=False):
        return True


class FakeGaze:
    def __init__(self, point, on=True):
        self.point, self.on, self.asked = point, on, 0

    def wanted(self):
        return self.on

    def target(self):
        self.asked += 1
        return self.point


def registry():
    r = Registry()
    for fn in (page_tools.zoom_picture, page_tools.close_picture, page_tools.next_picture,
               system_tools.zoom_in, system_tools.zoom_out):
        r.add(fn.__ally_tool__)
    return r


BASIC = [item("p1", "Golden Gate Bridge at sunset. From the north.", x=16, y=60, w=400, h=300),
         item("p2", "A red tram climbing a hill", kind="caption", x=432, y=60),
         item("p3", x=748, y=60)]


class ToolTests(unittest.TestCase):
    def run_tool(self, ext, name, args=None, gaze=None):
        out = registry().call(name, args or {}, ctx=Ctx(ext, gaze))
        self.assertTrue(out.ok, out.error)
        return out.text()

    def test_zoom_picture_named(self):
        ext = FakePictures(BASIC)
        text = self.run_tool(ext, "zoom_picture", {"about": "the tram"})
        self.assertEqual(ext.open, "p2")
        self.assertEqual(ext.order, ["p1", "p2"])
        self.assertIn("A red tram climbing a hill", text)
        self.assertIn("Escape", text)
        self.assertNotIn("go back", text)

    def test_zoom_picture_speaks_the_first_sentence(self):
        ext = FakePictures(BASIC)
        text = self.run_tool(ext, "zoom_picture")
        self.assertEqual(ext.open, "p1")
        self.assertIn("Golden Gate Bridge at sunset.", text)
        self.assertNotIn("north", text)

    def test_nothing_to_show_changes_nothing(self):
        for items, about, said in (([], "", "I don't see a picture"), ([item("p1")], "", "can't tell what"),
                                   (BASIC, "a cat", "I don't see a picture of a cat")):
            with self.subTest(said=said):
                ext = FakePictures(items)
                self.assertIn(said, self.run_tool(ext, "zoom_picture", {"about": about}))
                self.assertIsNone(ext.open)
                self.assertNotIn("stepOn", [c[0] for c in ext.calls])

    def test_no_match_names_the_only_picture(self):
        ext = FakePictures([item("p1", "View from the Presidio of San Francisco, 2017", kind="caption"), item("p2")])
        text = self.run_tool(ext, "zoom_picture", {"about": "the bridge"})
        self.assertIn("I don't see a picture of the bridge", text)
        self.assertIn("View from the Presidio", text)
        self.assertIsNone(ext.open)

    def test_gaze_only_when_tracker_is_on_and_sure(self):
        on = FakeGaze({"x": 500, "y": 200, "radius_px": 10, "source": "eyes"})
        ext = FakePictures(BASIC)
        self.run_tool(ext, "zoom_picture", gaze=on)
        self.assertEqual(ext.open, "p2")
        off = FakeGaze({"x": 500, "y": 200, "radius_px": 10, "source": "eyes"}, on=False)
        ext = FakePictures(BASIC)
        self.run_tool(ext, "zoom_picture", gaze=off)
        self.assertEqual((ext.open, off.asked), ("p1", 0))
        mouse = FakeGaze({"x": 500, "y": 200, "radius_px": 10, "source": "mouse"})
        ext = FakePictures(BASIC)
        self.run_tool(ext, "zoom_picture", gaze=mouse)
        self.assertEqual(ext.open, "p1")

    def test_viewer_takes_zoom_in_and_out(self):
        ext = FakePictures(BASIC)
        self.run_tool(ext, "zoom_picture")
        self.assertIn("Picture zoom 150%", self.run_tool(ext, "zoom_in"))
        self.assertEqual(ext.page_zoom, 1.0)
        self.run_tool(ext, "zoom_out")
        self.assertIn("Closed the picture", self.run_tool(ext, "zoom_out"))
        self.assertIn("Page zoom 110%", self.run_tool(ext, "zoom_in"))  # viewer closed: the page again

    def test_old_extension(self):
        ext = FakePictures(BASIC, old=True)
        self.assertIn("Page zoom 110%", self.run_tool(ext, "zoom_in"))
        out = registry().call("zoom_picture", {}, ctx=Ctx(ext))
        self.assertFalse(out.ok)
        self.assertIn("Reload it at chrome://extensions", out.error)

    def test_zoom_picture_again_zooms_in(self):
        ext = FakePictures(BASIC)
        self.run_tool(ext, "zoom_picture", {"about": "tram"})
        self.assertIn("Picture zoom 150%", self.run_tool(ext, "zoom_picture"))
        self.assertEqual(ext.open, "p2")  # the same picture, not a fresh choice

    def test_next_and_close(self):
        ext = FakePictures(BASIC)
        self.assertIn("No picture is open", self.run_tool(ext, "next_picture"))
        self.run_tool(ext, "zoom_picture")
        self.assertIn("red tram", self.run_tool(ext, "next_picture"))
        self.assertIn("Golden Gate", self.run_tool(ext, "next_picture", {"back": True}))
        self.assertEqual(self.run_tool(ext, "close_picture"), "Closed the picture.")
        self.assertEqual(self.run_tool(ext, "close_picture"), "No picture is open.")


if __name__ == "__main__":
    unittest.main()
