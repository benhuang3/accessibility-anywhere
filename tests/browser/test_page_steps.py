"""Page steps in a real browser: the foundation, each step alone, and every combination (docs/page_steps.md).

Run from tests/:  ../.venv/bin/python -m unittest browser.test_page_steps   (Windows: scripts\\browser_tests.ps1)
A step whose STEPS entry isn't registered yet is skipped with the reason, never failed.
"""

from __future__ import annotations

import itertools
import unittest
from pathlib import Path

try:
    import playwright  # noqa: F401
    HAVE_PLAYWRIGHT = True
except ImportError:
    HAVE_PLAYWRIGHT = False

from .harness import EXTENSION, AllyBrowser, FixtureServer

ORDER = ["clutter", "menus", "text", "buttons"]
OPTS = {"clutter": {}, "menus": {}, "text": {"scale": 1.25}, "buttons": {}}
# Pieces each step owns (docs/page_steps.md, "Own pieces only").
OWN_LAYERS = {"clutter": ("ally_clutter", "ally_motion"), "menus": ("ally_menus",), "text": ("ally_fit", "ally_text"),
              "buttons": ("ally_buttons",)}
OWN_HOSTS = {"menus": ("ally-menu",)}
DOC_METRICS = {"href", "vw", "vh", "textPx", "smallTextShare", "overflowX", "overflowPx", "cutOffText", "clipped",
               "stickyCover", "covered", "smallTargets", "targets"}
GENERAL = "combo_general.html"

server: FixtureServer | None = None
ally: AllyBrowser | None = None


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


def tearDownModule() -> None:
    if ally:
        ally.close()
    if server:
        server.stop()


# ---------------------------------------------------------------- helpers

def fresh(path: str = GENERAL):
    """Loads the fixture clean: new document, Ally torn down in the worker, Chrome's default zoom."""
    page = ally.open(path)
    ally.action("pageTeardown")
    ally.reset_zoom(page)
    return page


def owns_layer(step: str, name: str) -> bool:
    return any(name == p or name.startswith(p + "_") for p in OWN_LAYERS[step])


def owns_host(step: str, host: str) -> bool:
    return any(host.startswith(p) for p in OWN_HOSTS.get(step, ()))


def snapshot(page) -> dict:
    s = ally.page_state(page)
    return {"layers": ally.styles(page), "marks": s["marks"], "hosts": s["hosts"], "pause": s["pause"],
            "zoom": round(ally.zoom(page), 3)}


def without(state: dict, step: str) -> dict:
    """The state minus one step's own pieces."""
    return {
        "layers": [n for n in state["layers"] if not owns_layer(step, n)],
        # data-ally-<step> and data-ally-<step>-... (e.g. data-ally-clutter-unlock) belong to that step
        "marks": {k: v for k, v in state["marks"].items()
                  if k != f"data-ally-{step}" and not k.startswith(f"data-ally-{step}-")},
        "hosts": [h for h in state["hosts"] if not owns_host(step, h)],
    }


def run_steps(steps) -> list[dict]:
    return [ally.action("stepOn", {"step": s, "opts": OPTS[s]}) for s in steps]


class PageCase(unittest.TestCase):
    def need(self, *steps: str) -> None:
        have = ally.steps()
        missing = [s for s in steps if s not in have]
        if missing:
            self.skipTest(f"step not registered yet: {', '.join(missing)} (STEPS has {', '.join(have) or 'nothing'})")

    def assertClean(self, page, zoom_default: bool = True) -> None:
        st = snapshot(page)
        self.assertEqual([n for n in st["layers"] if n.startswith("ally_")], [], "ally_* layers left")
        self.assertEqual(st["marks"], {}, "data-ally-* marks left")
        self.assertEqual(st["hosts"], [], "Ally hosts left")
        self.assertEqual(st["pause"], "", "data-ally-pause left on <html>")
        if zoom_default:
            self.assertAlmostEqual(st["zoom"], ally.default_zoom(page), places=3)


# ---------------------------------------------------------------- foundation

class FoundationTests(PageCase):
    def test_extension_loads_cleanly(self):
        self.assertTrue(ally.sw.url.startswith("chrome-extension://"))
        self.assertTrue(ally.evaluate("() => typeof STEPS === 'object' && typeof ACTIONS === 'object'"))
        self.assertEqual(ally.worker_errors(), [], "service worker logged errors")
        # A step file that exists must load; a missing one is skipped quietly by background.js.
        for f in sorted((EXTENSION / "steps").glob("*.js")):
            bad = [w for w in ally.worker_warnings() if f"steps/{f.name}" in w]
            self.assertEqual(bad, [], f"steps/{f.name} exists but didn't load")

    def test_page_info(self):
        page = fresh()
        info = ally.action("pageInfo")
        self.assertIsInstance(info["tabId"], int)
        self.assertTrue(info["url"].endswith(GENERAL), info["url"])
        self.assertAlmostEqual(info["zoom"], ally.default_zoom(page), places=3)
        for k in ("defaultZoom", "type", "reasons"):
            self.assertIn(k, info)

    def test_page_baseline_metrics(self):
        fresh()
        b = ally.action("pageBaseline")
        self.assertTrue(b["href"].endswith(GENERAL))
        missing = DOC_METRICS - set(b["metrics"])
        # smallTextShare is documented; textSizes carries the same information if the runtime has only that.
        if missing == {"smallTextShare"} and "textSizes" in b["metrics"]:
            missing = set()
        self.assertEqual(missing, set(), f"baseline metrics missing documented keys: {sorted(missing)}")

    def test_verify_untouched_page_is_ok(self):
        page = fresh()
        v = ally.action("pageVerify")
        self.assertTrue(v["ok"], v)
        self.assertEqual(set(v["checks"]), {"overflow", "clipped", "sticky", "covered"})
        self.assertAlmostEqual(v["zoom"], ally.default_zoom(page), places=3)

    def test_teardown_is_idempotent(self):
        page = fresh()
        ally.action("pageBaseline")
        self.assertIs(ally.action("pageTeardown"), True)
        self.assertIs(ally.action("pageTeardown"), True)
        self.assertClean(page)

    def test_style_none_keeps_ally_layers(self):
        page = fresh()
        ally.action("applyStyle", {"name": "ally_test",
                                   "css": 'html:not([data-ally-pause~="test"]) body { outline: 3px solid red !important; }'})
        ally.action("applyStyle", {"name": "user_big", "css": "body { font-size: 31px !important; }"})
        self.assertEqual(ally.styles(page), ["ally_test", "user_big"])
        ally.action("clearStyles")
        self.assertEqual(ally.styles(page), ["ally_test"])
        self.assertNotEqual(page.evaluate("getComputedStyle(document.body).fontSize"), "31px")
        self.assertEqual(page.evaluate("getComputedStyle(document.body).outlineStyle"), "solid")
        ally.action("removeStyle", {"name": "ally_test"})

    def test_reset_removes_ally_pieces_but_not_risk_banner(self):
        page = fresh("combo_risk.html")
        ally.action("pageBaseline")  # injects the runtime
        ally.action("applyStyle", {"name": "ally_test", "css": "body { outline: 3px solid red !important; }"})
        ally.action("applyStyle", {"name": "user_big", "css": "body { font-size: 31px !important; }"})
        ally.action("setZoomSettled", {"zoom": 1.25})
        page.evaluate("""() => {
            document.querySelector("h1").setAttribute("data-ally-test", "x");
            const host = document.createElement("div");
            host.id = "ally-menu-host";
            document.body.appendChild(host);
        }""")
        self.assertAlmostEqual(ally.zoom(page), 1.25, places=3)
        ally.action("resetPage")
        self.assertClean(page)
        self.assertEqual(ally.styles(page), [])
        self.assertEqual(page.locator("#ally-risk-banner").count(), 1, "reset removed the risk banner")
        self.assertTrue(page.is_visible("#ally-risk-banner"))
        ally.screenshot(page, "foundation_reset_risk")


# ---------------------------------------------------------------- each step alone

class StepAloneTests(PageCase):
    def test_clutter_hides_cookie_banner_and_off_restores(self):
        self.need("clutter")
        page = fresh()
        self.assertTrue(page.is_visible("#onetrust-banner-sdk"))
        ally.screenshot(page, "general_before")
        r = ally.action("stepOn", {"step": "clutter", "opts": {}})
        self.assertIn("said", r)
        self.assertFalse(page.is_visible("#onetrust-banner-sdk"), "cookie banner still showing")
        self.assertGreater(ally.marks(page, "clutter"), 0)
        self.assertTrue(any(owns_layer("clutter", n) for n in ally.styles(page)))
        with self.subTest("chat iframe"):
            self.assertFalse(page.is_visible("#chat"), "Intercom chat iframe still showing")
        with self.subTest("page content stays"):
            self.assertTrue(page.is_visible("header.site"))
            self.assertTrue(page.is_visible("#story"))
        ally.screenshot(page, "clutter_on")
        ally.action("stepOff", {"step": "clutter", "opts": {}})
        self.assertTrue(page.is_visible("#onetrust-banner-sdk"))
        self.assertEqual(ally.marks(page, "clutter"), 0)
        self.assertFalse(any(owns_layer("clutter", n) for n in ally.styles(page)))

    def test_menus_panel_has_links_and_closes(self):
        self.need("menus")
        page = fresh()
        r = ally.action("stepOn", {"step": "menus", "opts": {}})
        if "links" in r:
            self.assertGreaterEqual(r["links"], 3, r)
        hosts = [h for h in ally.page_state(page)["hosts"] if owns_host("menus", h)]
        self.assertTrue(hosts, "no Ally menu host on the page")
        links = page.evaluate("""(ids) => {
            let n = 0;
            for (const id of ids) {
                const root = document.getElementById(id).shadowRoot;
                if (root) n += root.querySelectorAll("a[href], button, [role=link], [role=menuitem]").length;
            }
            return n;
        }""", hosts)
        closed = page.evaluate("(ids) => ids.every((id) => !document.getElementById(id).shadowRoot)", hosts)
        if not closed:
            self.assertGreaterEqual(links, 3, "menu panel/strip has too few links")
        ally.screenshot(page, "menus_on")
        ally.action("stepOff", {"step": "menus", "opts": {}})
        st = snapshot(page)
        self.assertEqual([h for h in st["hosts"] if owns_host("menus", h)], [])
        self.assertFalse(any(owns_layer("menus", n) for n in st["layers"]))
        self.assertEqual(st["marks"].get("data-ally-menus", 0), 0)

    def test_audit_at_zooms_restores_zoom(self):
        self.need("text")
        page = fresh()
        ally.action("setZoomSettled", {"zoom": 1.1})
        out = ally.action("auditAtZooms", {"levels": [1.25, 1.5, 2]})
        zooms = [round(a["zoom"], 3) for a in out["audits"]]
        for z in (1.25, 1.5, 2):
            self.assertIn(z, zooms)
        self.assertGreaterEqual(len(out["audits"]), 3)
        self.assertTrue(all("textPx" in a for a in out["audits"]))
        self.assertAlmostEqual(out["original"], 1.1, places=3)
        self.assertAlmostEqual(ally.zoom(page), 1.1, places=3, msg="audit didn't restore the zoom")

    def test_text_makes_fonts_bigger_without_zoom(self):
        self.need("text")
        page = fresh()
        size = "() => parseFloat(getComputedStyle(document.querySelector('main p, p')).fontSize)"
        h1 = "() => parseFloat(getComputedStyle(document.querySelector('h1')).fontSize)"
        before, h1_before = page.evaluate(size), page.evaluate(h1)
        zoom = ally.zoom(page)
        r = ally.action("stepOn", {"step": "text", "opts": {"scale": 1.5}})
        self.assertAlmostEqual(r["scale"], 1.5, places=3)
        self.assertAlmostEqual(ally.zoom(page), zoom, places=3)  # never zooms
        self.assertAlmostEqual(page.evaluate(size), round(before * 1.5), delta=1)
        self.assertGreater(page.evaluate(h1), page.evaluate(size))  # headings stay bigger than body text
        self.assertGreater(page.evaluate(h1), h1_before)
        self.assertIn("ally_text", ally.styles(page))
        again = ally.action("stepOn", {"step": "text", "opts": {"scale": 1.25}})  # re-apply starts from the page's sizes
        self.assertAlmostEqual(page.evaluate(size), round(before * 1.25), delta=1)
        self.assertEqual(again["scale"], 1.25)
        ally.screenshot(page, "text_on")
        ally.action("stepOff", {"step": "text", "opts": {}})
        self.assertNotIn("ally_text", ally.styles(page))
        self.assertAlmostEqual(page.evaluate(size), before, delta=0.5)

    def test_buttons_grow_labels_and_link_buttons(self):
        self.need("buttons")
        page = fresh()
        probe = """() => [...['#save', '#donate']].map((s) => { const e = document.querySelector(s);
            const r = e.getBoundingClientRect(); return [parseFloat(getComputedStyle(e).fontSize), r.width, r.height]; })"""
        (save_px, save_w, save_h), (don_px, don_w, don_h) = page.evaluate(probe)
        ally.action("stepOn", {"step": "buttons", "opts": {}})
        (save_px2, save_w2, save_h2), (don_px2, don_w2, don_h2) = page.evaluate(probe)
        self.assertGreaterEqual(save_px2, save_px * 1.3 - 0.5)    # label at least 30% bigger
        self.assertGreaterEqual(save_px2, save_px + 3)
        self.assertGreaterEqual(save_w2 * save_h2, 1.3 * save_w * save_h)  # and the button itself
        self.assertGreaterEqual(don_px2, don_px * 1.3 - 0.5)      # link styled as a button: bigger label
        self.assertGreaterEqual(don_w2 * don_h2, 1.3 * don_w * don_h)      # and a bigger box
        ally.action("stepOff", {"step": "buttons", "opts": {}})
        self.assertAlmostEqual(page.evaluate(probe)[0][0], save_px, delta=0.5)

    def test_bigger_text_wins_over_button_labels_in_any_order(self):
        self.need("buttons")
        self.need("text")
        sizes = []
        for order in (("buttons", "text"), ("text", "buttons")):
            fresh()
            for step in order:
                ally.action("stepOn", {"step": step, "opts": {"scale": 1.5} if step == "text" else {}})
            sizes.append(ally.page().evaluate("() => parseFloat(getComputedStyle(document.querySelector('#save')).fontSize)"))
        self.assertEqual(sizes[0], sizes[1])

    def test_buttons_adds_layer_without_zoom(self):
        self.need("buttons")
        page = fresh()
        before = ally.zoom(page)
        self.assertLess(page.evaluate("document.querySelector('#save').getBoundingClientRect().height"), 44)
        ally.action("stepOn", {"step": "buttons", "opts": {}})
        self.assertIn("ally_buttons", ally.styles(page))
        self.assertAlmostEqual(ally.zoom(page), before, places=3)
        self.assertGreaterEqual(page.evaluate("document.querySelector('#save').getBoundingClientRect().height"), 44)
        ally.screenshot(page, "buttons_on")
        ally.action("stepOff", {"step": "buttons", "opts": {}})
        self.assertNotIn("ally_buttons", ally.styles(page))

    def test_classify_types(self):
        self.need("classify")
        expected = {"combo_general.html": "general", "combo_article.html": "article", "combo_form.html": "form",
                    "combo_login.html": "excluded", "combo_risk.html": "excluded"}
        for path, kind in expected.items():
            with self.subTest(path):
                fresh(path)
                info = ally.action("pageInfo")
                self.assertEqual(info["type"], kind, f"{path}: {info.get('reasons')}")
                if kind == "excluded":
                    self.assertTrue(info["reasons"], "excluded without a reason")


# ---------------------------------------------------------------- combinations

def subsets(have: list[str]):
    for n in range(1, len(ORDER) + 1):
        for combo in itertools.combinations(ORDER, n):
            yield list(combo), [s for s in combo if s not in have]


class CompositionTests(PageCase):
    def need_any(self) -> list[str]:
        have = [s for s in ORDER if s in ally.steps()]
        if not have:
            self.skipTest(f"none of {', '.join(ORDER)} is registered yet")
        return have

    def test_order_does_not_matter(self):
        have = self.need_any()
        for combo, missing in subsets(have):
            with self.subTest("+".join(combo)):
                if missing:
                    self.skipTest(f"step not registered yet: {', '.join(missing)}")
                page = fresh()
                run_steps(combo)
                forward = snapshot(page)
                if len(combo) == len(ORDER):
                    ally.screenshot(page, "all_steps_on")
                page = fresh()
                run_steps(list(reversed(combo)))
                self.assertEqual(snapshot(page), forward, "end state depends on the order")

    def test_twice_equals_once(self):
        have = self.need_any()
        for combo in [[s] for s in have] + ([have] if len(have) > 1 else []):
            with self.subTest("+".join(combo)):
                page = fresh()
                run_steps(combo)
                once = snapshot(page)
                run_steps(combo)
                self.assertEqual(snapshot(page), once)

    def test_one_step_off_leaves_the_others(self):
        have = self.need_any()
        if len(have) < 2:
            self.skipTest("needs at least two steps registered")
        for step in have:
            with self.subTest(step):
                page = fresh()
                run_steps(have)
                before = snapshot(page)
                ally.action("stepOff", {"step": step, "opts": {}})
                after = snapshot(page)
                self.assertEqual(without(after, step), without(before, step), f"turning {step} off changed others")
                self.assertEqual(after["layers"], without(after, step)["layers"], f"{step} left its layers")
                self.assertNotIn(f"data-ally-{step}", after["marks"], f"{step} left its marks")
                self.assertEqual(after["hosts"], without(after, step)["hosts"], f"{step} left its hosts")
                if step != "text":
                    self.assertEqual(after["zoom"], before["zoom"])

    def test_verify_ok_after_combination(self):
        have = self.need_any()
        fresh()
        ally.action("pageBaseline")
        run_steps(have)
        v = ally.action("pageVerify")
        self.assertTrue(v["ok"], f"verify failed after {'+'.join(have)}: {v['checks']}")

    def test_reset_leaves_nothing_and_keeps_risk_banner(self):
        have = self.need_any()
        page = fresh()
        run_steps(have)
        page.evaluate("""() => {
            const b = document.createElement("div");
            b.id = "ally-risk-banner";
            b.textContent = "Test risk banner";
            document.body.appendChild(b);
        }""")
        ally.action("resetPage")
        self.assertClean(page)
        self.assertEqual(ally.styles(page), [])
        self.assertEqual(page.locator("#ally-risk-banner").count(), 1, "reset removed the risk banner")
        if "clutter" in have:
            self.assertTrue(page.is_visible("#onetrust-banner-sdk"))
        ally.screenshot(page, "after_reset")


if __name__ == "__main__":
    unittest.main()
