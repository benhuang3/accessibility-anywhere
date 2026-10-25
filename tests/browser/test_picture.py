"""Picture viewer in a real browser with the extension (docs/picture_viewer.md).

Run from tests/:  python timed.py 90 browser.test_picture
"""

from __future__ import annotations

import time
import unittest

try:
    import playwright  # noqa: F401
    HAVE_PLAYWRIGHT = True
except ImportError:
    HAVE_PLAYWRIGHT = False

from .harness import AllyBrowser, FixtureServer

server: FixtureServer | None = None
ally: AllyBrowser | None = None

HOST = "ally-picture-host"


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
    if not ally.has_step("picture"):
        tearDownModule()
        raise unittest.SkipTest("the picture step isn't registered in the extension")


def tearDownModule() -> None:
    if ally:
        ally.close()
    if server:
        server.stop()


def fresh(path: str):
    page = ally.open(path)
    ally.action("pageTeardown")
    ally.reset_zoom(page)
    page.wait_for_function("() => [...document.images].every((i) => i.complete)", timeout=5000)
    return page


def by_el(page, items):
    """{element id: item} by asking the page which element sits at each item's centre."""
    out = {}
    for it in items:
        el_id = page.evaluate("""([x, y]) => {
            let el = document.elementsFromPoint(x, y).find((n) => n.matches("img, svg, [role=img]")) || null;
            while (el && !el.id) el = el.parentElement;
            return el ? el.id : "";
        }""", [it["x"] + it["w"] / 2, it["y"] + it["h"] / 2])
        out[el_id] = it
    return out


def viewer(page) -> dict:
    """What the viewer shows: open, caption, picture source and size, buttons (from its open shadow root)."""
    return page.evaluate("""(id) => {
        const host = document.getElementById(id);
        if (!host || !host.shadowRoot) return { open: false };
        const root = host.shadowRoot;
        const img = root.querySelector("img");
        const svg = root.querySelector("svg");
        const bg = root.querySelector(".bg");
        const pic = img || svg || bg;
        const r = pic ? pic.getBoundingClientRect() : { width: 0, height: 0 };
        return {
            open: true,
            text: root.textContent.replace(/\\s+/g, " ").trim(),
            src: img ? img.currentSrc || img.src : bg ? bg.style.backgroundImage : "",
            kind: img ? "img" : svg ? "svg" : bg ? "bg" : "",
            w: r.width, h: r.height,
            buttons: [...root.querySelectorAll("button")].filter((b) => b.offsetParent !== null)
                .map((b) => ({ text: b.textContent.trim(), h: b.getBoundingClientRect().height })),
            z: getComputedStyle(host).zIndex,
        };
    }""", HOST)


class ListTests(unittest.TestCase):
    def test_basic_page(self):
        page = fresh("picture_basic.html")
        listed = ally.action("pictureList")
        self.assertEqual(listed["view"]["w"], 1280)
        self.assertAlmostEqual(listed["win"]["zoom"], 1.0)
        items = by_el(page, listed["items"])
        self.assertEqual(set(items), {"bridge", "tram", "lighthouse", "dog"})  # no decor (alt=""), logo, far
        self.assertEqual(items["bridge"]["descriptions"][0],
                         {"text": "Golden Gate Bridge at sunset. Taken from the north side.", "kind": "alt"})
        self.assertEqual(items["tram"]["descriptions"][0], {"text": "A red tram climbing a hill", "kind": "caption"})
        self.assertEqual(items["lighthouse"]["descriptions"][0]["kind"], "labelledby")
        self.assertFalse(items["dog"]["described"])
        self.assertTrue(all(items[k]["described"] for k in ("bridge", "tram", "lighthouse")))
        ids = [it["id"] for it in listed["items"]]
        self.assertEqual(ids.index(items["bridge"]["id"]), 0)  # reading order: top left first
        self.assertEqual(ally.page_state(page)["marks"], {})  # the page is only read

    def test_edge_cases(self):
        page = fresh("picture_edge.html")
        page.wait_for_timeout(200)
        items = by_el(page, ally.action("pictureList")["items"])
        self.assertNotIn("under", items)  # covered by the fixed header
        self.assertNotIn("frame", items)  # inside an iframe
        self.assertIn("half", items)  # half off screen still counts
        self.assertLess(items["half"]["w"], 200)  # only the visible part
        self.assertEqual(items["flowers"]["kind"], "bg")
        self.assertEqual(items["flowers"]["descriptions"][0]["text"], "Sunflowers in a field")
        self.assertIn("chart", items)
        self.assertEqual(items["chart"]["descriptions"][0]["text"], "Pie chart of monthly costs")
        self.assertIn("lazy", items)
        self.assertEqual(items["infobox"]["descriptions"][0], {"text": "Cable car on Hyde Street", "kind": "caption"})
        self.assertIn("hero", items)  # under a see-through gradient and a stretched link


class ViewerTests(unittest.TestCase):
    def open_named(self, page, el_id):
        listed = ally.action("pictureList")["items"]  # ids are rebuilt by every list: use one list's ids
        items = by_el(page, listed)
        order = [it["id"] for it in listed if it["described"]]
        out = ally.action("stepOn", {"step": "picture", "opts": {"id": items[el_id]["id"], "order": order}})
        return out, items

    def test_open_zoom_close(self):
        page = fresh("picture_basic.html")
        page.evaluate("() => window.scrollTo(0, 40)")
        zoom_before = ally.zoom(page)
        out, _ = self.open_named(page, "bridge")
        self.assertEqual(out["said"], "opened the picture")
        page.wait_for_function("(id) => { const i = document.getElementById(id).shadowRoot.querySelector('img');"
                               " return i && /bridge-1600/.test(i.currentSrc || i.src); }", arg=HOST, timeout=5000)
        v = viewer(page)
        self.assertTrue(v["open"])
        self.assertIn("Golden Gate Bridge at sunset", v["text"])
        self.assertGreater(v["w"], 800)  # fitted to ~90% of the 1280x800 tab
        self.assertGreater(v["h"], 600)
        texts = " ".join(b["text"] for b in v["buttons"])
        for word in ("Zoom in", "Zoom out", "Close"):
            self.assertIn(word, texts)
        self.assertTrue(all(b["h"] >= 56 for b in v["buttons"]))

        z = ally.action("pictureZoom", {"direction": "in"})
        self.assertEqual((z["open"], z["scale"]), (True, 1.5))
        self.assertEqual(ally.action("pictureState")["scale"], 1.5)
        ally.action("pictureZoom", {"direction": "out"})
        z = ally.action("pictureZoom", {"direction": "out"})  # the viewer's own "-": stops at fitted
        self.assertEqual((z["open"], z["scale"]), (True, 1))
        z = ally.action("pictureZoom", {"direction": "out", "closeAtFit": True})  # voice "zoom out"
        self.assertTrue(z["closed"])
        self.assertFalse(viewer(page)["open"])
        self.assertAlmostEqual(ally.zoom(page), zoom_before)
        self.assertEqual(page.evaluate("() => scrollY"), 40)
        self.assertEqual(page.evaluate("() => getComputedStyle(document.documentElement).overflow"), "visible")

    def test_page_zoom_buttons_zoom_the_picture(self):
        page = fresh("picture_basic.html")
        self.open_named(page, "tram")
        before = ally.zoom(page)
        got = ally.action("zoomStep", {"direction": "in"})  # popup button and keyboard shortcut
        self.assertAlmostEqual(got, before)
        self.assertAlmostEqual(ally.zoom(page), before)
        self.assertEqual(ally.action("pictureState")["scale"], 1.5)
        ally.action("stepOff", {"step": "picture"})
        self.assertGreater(ally.action("zoomStep", {"direction": "in"}), before)  # closed: the page again
        ally.reset_zoom(page)

    def test_next_previous_and_escape(self):
        page = fresh("picture_basic.html")
        self.open_named(page, "bridge")
        a = ally.action("pictureStep", {"back": False})
        self.assertIn("tram", a["description"])
        b = ally.action("pictureStep", {"back": False})
        self.assertIn("Lighthouse", b["description"])
        c = ally.action("pictureStep", {"back": False})  # wraps
        self.assertIn("Golden Gate", c["description"])
        self.assertIn("Lighthouse", ally.action("pictureStep", {"back": True})["description"])
        self.assertIn("Next", " ".join(x["text"] for x in viewer(page)["buttons"]))
        page.keyboard.press("Escape")
        self.assertFalse(viewer(page)["open"])
        self.assertFalse(ally.action("pictureState")["open"])

    def test_lazy_svg_and_background(self):
        page = fresh("picture_edge.html")
        for el_id, check in (("lazy", lambda v: "lake.svg" in v["src"]), ("chart", lambda v: v["kind"] == "svg"),
                             ("flowers", lambda v: "flowers.svg" in v["src"])):
            with self.subTest(el_id=el_id):
                self.open_named(page, el_id)
                deadline = time.time() + 3
                v = viewer(page)
                while not check(v) and time.time() < deadline:
                    page.wait_for_timeout(100)
                    v = viewer(page)
                self.assertTrue(check(v), v)
                self.assertGreater(v["w"], 300)
                ally.action("stepOff", {"step": "picture"})

    def test_stale_list_is_refused(self):
        fresh("picture_basic.html")
        first = ally.action("pictureList")
        ally.action("pictureList")  # renumbers
        with self.assertRaises(Exception) as cm:
            ally.action("stepOn", {"step": "picture", "opts": {"id": first["items"][0]["id"], "gen": first["gen"]}})
        self.assertIn("page changed", str(cm.exception))

    def test_ctrl_plus_zooms_the_picture(self):
        page = fresh("picture_basic.html")
        before = ally.zoom(page)
        self.open_named(page, "bridge")
        page.keyboard.press("Control+Equal")
        self.assertEqual(ally.action("pictureState")["scale"], 1.5)
        self.assertAlmostEqual(ally.zoom(page), before)
        ally.action("stepOff", {"step": "picture"})

    def test_reader_and_viewer_scroll_lock(self):
        page = fresh("picture_basic.html")
        overflow = "() => getComputedStyle(document.documentElement).overflow"
        # Viewer, then the reader on top: the reader closes the viewer; closing the reader frees the page.
        self.open_named(page, "bridge")
        ally.action("stepOn", {"step": "reader", "opts": {}})
        self.assertFalse(viewer(page)["open"])
        ally.action("stepOff", {"step": "reader"})
        self.assertEqual(page.evaluate(overflow), "visible")
        # Reader, then a picture from it, closed in the "wrong" order: still scrollable at the end.
        ally.action("stepOn", {"step": "reader", "opts": {}})
        listed = ally.action("pictureList")
        if listed["items"]:
            ally.action("stepOn", {"step": "picture", "opts": {"id": listed["items"][0]["id"], "gen": listed["gen"]}})
            ally.action("stepOff", {"step": "reader"})
            self.assertEqual(page.evaluate(overflow), "hidden")  # the viewer still covers the page
            ally.action("stepOff", {"step": "picture"})
        else:
            ally.action("stepOff", {"step": "reader"})
        self.assertEqual(page.evaluate(overflow), "visible")

    def test_placeholder_srcset_is_not_an_upgrade(self):
        page = fresh("picture_edge.html")
        self.open_named(page, "placeholder")
        page.wait_for_function("(id) => /bridge-1600/.test(document.getElementById(id).shadowRoot.querySelector('img').src)",
                               arg=HOST, timeout=5000)  # data-srcset's biggest, never the 1x1 placeholder
        ally.action("stepOff", {"step": "picture"})

    def test_unknown_id(self):
        fresh("picture_basic.html")
        with self.assertRaises(Exception) as cm:
            ally.action("stepOn", {"step": "picture", "opts": {"id": "p999", "order": []}})
        self.assertIn("isn't on the page any more", str(cm.exception))

    def test_reset_keeps_the_risk_banner(self):
        page = fresh("picture_edge.html")
        self.open_named(page, "flowers")
        self.assertTrue(viewer(page)["open"])
        ally.action("resetPage")
        self.assertFalse(viewer(page)["open"])
        self.assertEqual(page.evaluate("() => !!document.getElementById('ally-risk-banner')"), True)
        self.assertEqual(ally.page_state(page)["hosts"], [])
        self.assertEqual(page.evaluate("() => getComputedStyle(document.documentElement).overflow"), "visible")

    def test_state_probe_on_a_fresh_page(self):
        ally.open("picture_basic.html")  # a new document: nothing injected yet
        self.assertEqual(ally.action("pictureState")["open"], False)


if __name__ == "__main__":
    unittest.main()
