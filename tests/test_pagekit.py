"""Page steps in Python: order, composition, verify/back-off, Simplified mode policy, proactive rules.

A FakeExtension models what each step adds to a page (CSS layers, marks, overlays, zoom), following the
contract in docs/page_steps.md, so "each step alone and any combination" can be checked without Chrome.
"""

import itertools
import tempfile
import time
import unittest
from pathlib import Path

import helpers  # noqa: F401  (adds the repo root to sys.path)

from ally.contracts import EventBus, Struggle
from ally.habits import HabitMemory
from ally.pagekit import core
from ally.proactive import (SIMPLE_OFFER, Coordinator, NullUI, NullVoice, fused_kinds, known_keys,
                            pick_accommodation)
from ally.registry import Registry, ToolError
from ally.tools import page_tools, ui_text_tools

LEVELS = (1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5)


def audit_entry(zoom, text_px=14, vw=1400, overflow=0, sticky=0.1):
    return {"zoom": zoom, "textPx": text_px, "textSizes": [[text_px, 1.0]], "vw": int(vw / zoom), "vh": 900,
            "overflowX": overflow > 0, "overflowPx": overflow, "cutOffText": 0, "clipped": [], "stickyCover": sticky,
            "covered": [], "smallTargets": [], "targets": 10}


class FakeExtension:
    """The extension's page-step actions on one page, as plain state."""

    def __init__(self, page_type="general", breaks_above=None, buttons_break=False, popup=None):
        self.connected = True
        self.focused = True
        self.zoom = 1.0
        self.text_scale = 1.0
        self.layers: set[str] = set()
        self.marks: dict[str, set[str]] = {}
        self.hosts: set[str] = set()
        self.page_type = page_type
        self.breaks_above = breaks_above  # text scale above which the page overflows
        self.buttons_break = buttons_break
        self.calls: list[tuple] = []
        self.simple = {}
        self.popup = popup  # "cookie": a consent pop-up the clutter step hides; "stuck": one it can't

    # ---- the bridge
    def call(self, action, timeout=8.0, **args):
        self.calls.append((action, args))
        return getattr(self, "a_" + action)(**{k: v for k, v in args.items() if k != "tabId"})

    def state(self):
        return (round(self.zoom, 3), round(self.text_scale, 3), frozenset(self.layers), frozenset((k, frozenset(v)) for k, v in self.marks.items()),
                frozenset(self.hosts))

    # ---- actions
    def a_pageInfo(self):
        info = {"tabId": 7, "url": "https://example.test/", "zoom": self.zoom, "defaultZoom": 1.0,
                "textScale": self.text_scale,
                "type": self.page_type, "reasons": ["this page handles payments"] if self.page_type == "excluded" else []}
        if self.popup == "stuck" or (self.popup == "cookie" and "cookie" not in self.marks.get("clutter", set())):
            info.update(type="excluded", reasons=["a pop-up is open"])
        return info

    def a_pageBaseline(self, force=False):
        return {"tabId": 7, "zoom": self.zoom, "metrics": audit_entry(self.zoom)}

    def a_auditAtZooms(self, levels=(), maxZoom=2.5):
        out = []
        for z in levels:
            over = 300 if self.breaks_above and z > self.breaks_above + 1e-6 else 0
            out.append(audit_entry(z, overflow=over))
        return {"tabId": 7, "original": self.zoom, "defaultZoom": 1.0, "audits": out}

    def a_setZoomSettled(self, zoom):
        self.zoom = zoom
        return zoom

    def a_stepOn(self, step, opts=None):
        opts = opts or {}
        if step == "clutter":
            kinds = opts.get("kinds") or list(core.CLUTTER_KINDS)
            self.marks.setdefault("clutter", set()).update(k for k in kinds if k in ("cookie", "chat"))
            self.layers.add("ally_clutter")
            return {"said": "hid a cookie banner and a chat button"}
        if step == "menus":
            self.hosts.add("ally-menu-strip" if opts.get("mode") == "strip" else "ally-menu-host")
            self.layers.add("ally_menus")
            return {"said": "put the menu in one big list"}
        if step == "text":
            self.text_scale = opts["scale"]
            self.layers |= {"ally_text", "ally_fit"}
            return {"said": ""}
        if step == "buttons":
            self.layers.add("ally_buttons")
            return {"said": "made the buttons bigger"}
        if step == "reader":
            self.hosts.add("ally-reader-host")
            return {"said": ""}
        if step == "simple":
            if "steps" in opts:
                self.hosts.add("ally-simple-chip")
                self.simple = dict(opts)
            return {"said": ""}
        raise ToolError(f"Unknown page step: {step}")

    def a_stepOff(self, step, opts=None):
        if step == "clutter":
            self.marks.pop("clutter", None)
            self.layers.discard("ally_clutter")
        elif step == "menus":
            self.hosts -= {"ally-menu-host", "ally-menu-strip"}
            self.layers.discard("ally_menus")
        elif step == "text":
            self.layers -= {"ally_text", "ally_fit"}
            self.text_scale = 1.0
        elif step == "buttons":
            self.layers.discard("ally_buttons")
        elif step == "simple":
            self.layers.clear()
            self.marks.clear()
            self.hosts.clear()
            self.text_scale = 1.0
        return {"said": ""}

    def a_pageVerify(self, stickyBudget=0.25):
        overflow = bool(self.breaks_above and self.text_scale > self.breaks_above + 1e-6)
        buttons = self.buttons_break and "ally_buttons" in self.layers
        return {"ok": not (overflow or buttons),
                "checks": {"overflow": {"ok": not overflow}, "covered": {"ok": not buttons},
                           "clipped": {"ok": True}, "sticky": {"ok": True}}}

    def a_simpleForgetAll(self):
        return True


class Ctx:
    def __init__(self, ext):
        self.browser = ext
        self.config = {}
        self.said = []
        self.state = _State()

    def say(self, text, wait=False):
        self.said.append(text)
        return True


class _State(dict):
    def set(self, k, v):
        self[k] = v


class OrderTests(unittest.TestCase):
    def test_canonical_order_and_unknown_steps(self):
        self.assertEqual(core.ordered(["buttons", "clutter", "text"]), ["clutter", "text", "buttons"])
        with self.assertRaises(ToolError):
            core.ordered(["clutter", "sparkles"])

    def test_audit_levels_start_at_current_zoom_and_stop_at_max(self):
        self.assertEqual(core.audit_levels(1.0, 2.0), [1.0, 1.1, 1.25, 1.5, 1.75, 2.0])
        self.assertEqual(core.audit_levels(1.5, 2.5), [1.5, 1.75, 2.0, 2.5])

    def test_simplified_policy_by_page_type(self):
        self.assertEqual(core.steps_for("excluded")["steps"], [])
        art = core.steps_for("article")
        self.assertTrue(art["reader"])
        self.assertNotIn("text", art["steps"])
        form = core.steps_for("form")
        self.assertEqual(form["steps"], ["clutter", "text", "buttons"])
        self.assertEqual(form["opts"]["clutter"]["kinds"], ["cookie", "promo", "chat"])
        self.assertEqual(core.steps_for("general")["steps"], list(core.STEP_ORDER))


class CompositionTests(unittest.TestCase):
    """Each step alone and any combination, in any order, ends in the same state."""

    def run_one_by_one(self, order):
        ext = FakeExtension()
        ctx = Ctx(ext)
        for step in order:
            core.run_steps(ctx, [step])
        return ext

    def test_every_subset_in_every_order_ends_the_same(self):
        for n in range(1, 5):
            for subset in itertools.combinations(core.STEP_ORDER, n):
                ends = {self.run_one_by_one(order).state() for order in itertools.permutations(subset)}
                together = FakeExtension()
                core.run_steps(Ctx(together), list(subset))
                ends.add(together.state())
                self.assertEqual(len(ends), 1, f"order changed the end state for {subset}")

    def test_running_a_step_twice_equals_once(self):
        for step in ("clutter", "menus", "buttons"):
            once, twice = FakeExtension(), FakeExtension()
            core.run_steps(Ctx(once), [step])
            core.run_steps(Ctx(twice), [step])
            core.run_steps(Ctx(twice), [step])
            self.assertEqual(once.state(), twice.state(), step)

    def test_turning_one_step_off_leaves_the_others(self):
        for step in core.STEP_ORDER:
            ext = FakeExtension()
            ctx = Ctx(ext)
            core.run_steps(ctx, list(core.STEP_ORDER))
            others = [s for s in core.STEP_ORDER if s != step]
            expected = FakeExtension()
            core.run_steps(Ctx(expected), others)
            core.run_steps(ctx, [step], on=False)
            self.assertEqual(ext.layers, expected.layers, step)
            self.assertEqual(ext.marks, expected.marks, step)
            self.assertEqual(ext.hosts, expected.hosts, step)

    def test_text_makes_the_fonts_bigger_and_never_zooms(self):
        ext = FakeExtension()
        report = core.run_steps(Ctx(ext), ["text"])
        self.assertGreater(ext.text_scale, 1.0)
        self.assertEqual(ext.zoom, 1.0)
        self.assertIn("ally_text", ext.layers)
        self.assertNotIn("setZoomSettled", [c[0] for c in ext.calls])
        self.assertTrue(any("bigger" in s for s in report["said"]))

    def test_steps_after_a_failure_still_run(self):
        ext = FakeExtension()
        real = ext.a_stepOn

        def flaky(step, opts=None):
            if step == "menus":
                raise ToolError("menus broke")
            return real(step, opts)
        ext.a_stepOn = flaky
        report = core.run_steps(Ctx(ext), ["clutter", "menus", "buttons"])
        self.assertEqual(report["applied"], ["clutter", "buttons"])
        self.assertEqual(report["problems"], ["menus broke"])
        self.assertIn("didn't work", core.summary(report))


class VerifyBackOffTests(unittest.TestCase):
    def test_buttons_that_break_the_page_are_undone(self):
        ext = FakeExtension(buttons_break=True)
        report = core.run_steps(Ctx(ext), ["clutter", "buttons"])
        self.assertNotIn("ally_buttons", ext.layers)
        self.assertIn("ally_clutter", ext.layers)  # not part of what broke it
        self.assertIn("buttons", report["backed_off"])
        self.assertTrue(report["verify"]["ok"])

    def test_text_backs_off_when_verify_fails(self):
        ext = FakeExtension(breaks_above=1.3)  # 14px text predicts 150%, but the page breaks above 130%
        report = core.run_steps(Ctx(ext), ["text"])
        self.assertAlmostEqual(ext.text_scale, 1.3)
        self.assertTrue(report["backed_off"])
        self.assertTrue(report["verify"]["ok"])
        self.assertEqual(ext.zoom, 1.0)

    def test_text_scale_is_predicted(self):
        ext = FakeExtension()
        core.run_steps(Ctx(ext), ["text"])
        self.assertAlmostEqual(ext.text_scale, 1.5)  # 14px x 1.5 = 21px >= 20px goal

    def test_bigger_is_at_least_30_percent_even_for_big_text(self):
        ext = FakeExtension()
        ext.a_pageBaseline = lambda force=False: {"tabId": 7, "metrics": audit_entry(1.0, text_px=22)}
        core.run_steps(Ctx(ext), ["text"])
        self.assertAlmostEqual(ext.text_scale, 1.3)

    def test_bigger_again_and_smaller(self):
        ext = FakeExtension()
        core.run_steps(Ctx(ext), ["text"], opts={"text": {"direction": "up"}})
        self.assertAlmostEqual(ext.text_scale, 1.5)
        core.run_steps(Ctx(ext), ["text"], opts={"text": {"direction": "up"}})
        self.assertAlmostEqual(ext.text_scale, 1.75)
        core.run_steps(Ctx(ext), ["text"], opts={"text": {"direction": "down"}})
        self.assertAlmostEqual(ext.text_scale, 1.5)
        for _ in range(4):
            core.run_steps(Ctx(ext), ["text"], opts={"text": {"direction": "down"}})
        self.assertAlmostEqual(ext.text_scale, 1.0)
        self.assertNotIn("ally_text", ext.layers)

    def test_verify_reads_only(self):
        ext = FakeExtension()
        core.run_steps(Ctx(ext), ["clutter", "buttons"])
        before = ext.state()
        out = Registry()
        out.add(page_tools.check_page_layout.__ally_tool__)
        res = out.call("check_page_layout", {}, ctx=Ctx(ext))
        self.assertTrue(res.ok, res.error)
        self.assertEqual(ext.state(), before)


def registry():
    r = Registry()
    for t in (page_tools.hide_clutter, page_tools.simplify_menus, page_tools.enlarge_buttons,
              page_tools.check_page_layout, page_tools.make_page_easier, page_tools.simplified_mode,
              page_tools.open_reader, ui_text_tools.enlarge_text):
        r.add(t.__ally_tool__)
    return r


class ToolTests(unittest.TestCase):
    def test_hide_clutter_refuses_unknown_kinds(self):
        out = registry().call("hide_clutter", {"kinds": ["cookie", "spam"]}, ctx=Ctx(FakeExtension()))
        self.assertFalse(out.ok)

    def test_hide_clutter_says_what_it_hid(self):
        out = registry().call("hide_clutter", {}, ctx=Ctx(FakeExtension()))
        self.assertTrue(out.ok, out.error)
        self.assertIn("cookie banner", out.value)

    def test_enlarge_text_uses_page_steps_in_chrome(self):
        ext = FakeExtension()
        out = registry().call("enlarge_text", {"goal": "bigger", "buttons": True}, ctx=Ctx(ext))
        self.assertTrue(out.ok, out.error)
        self.assertEqual(out.value["tier"], "chrome-text")
        self.assertIn("ally_buttons", ext.layers)
        self.assertGreater(ext.text_scale, 1.0)
        self.assertEqual(ext.zoom, 1.0)

    def test_enlarge_text_in_browser_flag_works_without_focus(self):
        ext = FakeExtension()
        ext.focused = False  # e.g. the user answered by clicking the dashboard
        out = registry().call("enlarge_text", {"goal": "bigger", "in_browser": True}, ctx=Ctx(ext))
        self.assertTrue(out.ok, out.error)
        self.assertEqual(out.value["tier"], "chrome-text")

    def test_simplified_mode_leaves_excluded_pages_alone(self):
        ext = FakeExtension(page_type="excluded")
        out = registry().call("simplified_mode", {}, ctx=Ctx(ext))
        self.assertTrue(out.ok, out.error)
        self.assertIn("won't simplify", out.value)
        self.assertEqual(ext.state(), FakeExtension().state())

    def test_simplified_mode_hides_a_consent_popup_first(self):
        ext = FakeExtension(popup="cookie")
        out = registry().call("simplified_mode", {}, ctx=Ctx(ext))
        self.assertTrue(out.ok, out.error)
        self.assertIn("Simple mode is on", out.text())
        self.assertIn("cookie", ext.marks.get("clutter", set()))

    def test_simplified_mode_still_refuses_a_popup_it_cannot_hide(self):
        ext = FakeExtension(popup="stuck")
        out = registry().call("simplified_mode", {}, ctx=Ctx(ext))
        self.assertIn("because a pop-up is open", out.text())
        self.assertNotIn("clutter", ext.marks)  # what it tried is put back
        self.assertNotIn("ally_clutter", ext.layers)

    def test_simplified_mode_general_page(self):
        ext = FakeExtension()
        ctx = Ctx(ext)
        out = registry().call("simplified_mode", {}, ctx=ctx)
        self.assertTrue(out.ok, out.error)
        self.assertEqual(ctx.said, ["One moment, I'm making this page simpler."])
        self.assertIn("ally-menu-strip", ext.hosts)
        self.assertIn("ally-simple-chip", ext.hosts)
        self.assertEqual(ext.simple["steps"], ["clutter", "menus", "text", "buttons"])
        self.assertFalse(ext.simple["remember"])
        working = [a for a in ext.calls if a[0] == "stepOn" and a[1]["step"] == "simple" and "working" in a[1]["opts"]]
        self.assertEqual([w[1]["opts"]["working"] for w in working], [True, False])

    def test_simplified_mode_article_opens_reader(self):
        ext = FakeExtension(page_type="article")
        out = registry().call("simplified_mode", {}, ctx=Ctx(ext))
        self.assertTrue(out.ok, out.error)
        self.assertIn("ally-reader-host", ext.hosts)
        self.assertEqual(ext.text_scale, 1.0)

    def test_simplified_mode_form_page_skips_menus(self):
        ext = FakeExtension(page_type="form")
        registry().call("simplified_mode", {}, ctx=Ctx(ext))
        self.assertNotIn("ally-menu-strip", ext.hosts)
        clutter = [a for a in ext.calls if a[0] == "stepOn" and a[1]["step"] == "clutter"]
        self.assertEqual(clutter[0][1]["opts"]["kinds"], ["cookie", "promo", "chat"])

    def test_simplified_mode_off(self):
        ext = FakeExtension()
        registry().call("simplified_mode", {}, ctx=Ctx(ext))
        out = registry().call("simplified_mode", {"on": False}, ctx=Ctx(ext))
        self.assertTrue(out.ok, out.error)
        self.assertEqual(ext.state(), FakeExtension().state())

    def test_chrome_not_connected_is_a_clear_error(self):
        ext = FakeExtension()
        ext.connected = False
        out = registry().call("enlarge_buttons", {}, ctx=Ctx(ext))
        self.assertFalse(out.ok)
        self.assertIn("Chrome", out.error)


class ProactiveTests(unittest.TestCase):
    def test_browser_struggles_map_to_page_steps(self):
        self.assertEqual(pick_accommodation("squint", True).tool, "enlarge_text")
        self.assertEqual(pick_accommodation("rage_click", True).tool, "enlarge_buttons")
        both = pick_accommodation("fused", True, {"squint", "rage_click"})
        self.assertEqual((both.tool, both.args.get("buttons")), ("enlarge_text", True))
        self.assertEqual(pick_accommodation("fused", True, {"rage_click", "hesitation"}).key, "easier_clicking")
        self.assertTrue(pick_accommodation("squint", True).args["in_browser"])
        self.assertEqual(pick_accommodation("squint", False).tool, "magnify_screen")
        self.assertEqual(pick_accommodation("retype", True).tool, "enlarge_text")  # no browser-wide font size

    def test_fused_kinds_reads_the_signals(self):
        s = Struggle("fused", 0.7, detail={"signals": [{"kind": "squint"}, {"kind": "lean"}]})
        self.assertEqual(fused_kinds(s), {"squint", "lean"})
        self.assertEqual(fused_kinds(Struggle("lean", 0.7)), {"lean"})

    def make_coordinator(self, in_browser=True, zoom=None):
        tmp = Path(tempfile.mkdtemp())
        mem = HabitMemory(tmp / "habits.json", auto_after=2)
        calls = []
        ctx = {"app": "Google Chrome", "site": "example.test" if in_browser else "", "in_browser": in_browser,
               "zoom": zoom}
        coord = Coordinator(EventBus(), lambda n, a, src: calls.append((n, a)), mem, lambda: dict(ctx),
                            lambda: {"cooldown_seconds": 0, "min_confidence": 0.1, "offer_timeout_seconds": 0.05},
                            ui=NullUI(), voice=NullVoice())
        return coord, mem, calls, ctx

    def test_old_habit_keys_are_not_replayed(self):
        coord, mem, calls, _ = self.make_coordinator()
        from ally.contracts import Accommodation
        old = Accommodation("page_zoom_150", "make this page bigger", "set_page_zoom", {"zoom": 1.5})
        mem.record("example.test", old, True)
        mem.record("example.test", old, True)
        coord.check_context()
        self.assertEqual(calls, [])
        self.assertNotIn("page_zoom_150", known_keys())

    def test_page_habit_replays_once_not_every_alt_tab(self):
        coord, mem, calls, ctx = self.make_coordinator()
        acc = pick_accommodation("squint", True)
        mem.record("example.test", acc, True)
        mem.record("example.test", acc, True)
        coord.check_context()
        self.assertEqual([c[0] for c in calls], ["enlarge_text"])
        ctx["site"], ctx["in_browser"] = "", False  # Alt+Tab away...
        coord.check_context()
        ctx["site"], ctx["in_browser"] = "example.test", True  # ...and back
        coord.check_context()
        self.assertEqual(len(calls), 1)

    def test_page_habit_skipped_when_chrome_already_zoomed(self):
        coord, mem, calls, _ = self.make_coordinator(zoom=1.75)
        acc = pick_accommodation("squint", True)
        mem.record("example.test", acc, True)
        mem.record("example.test", acc, True)
        coord.check_context()
        self.assertEqual(calls, [])

    def test_reset_snoozes_habits_and_offers(self):
        coord, mem, calls, _ = self.make_coordinator()
        acc = pick_accommodation("squint", True)
        mem.record("example.test", acc, True)
        mem.record("example.test", acc, True)
        coord.snooze()
        coord.check_context()
        self.assertEqual(calls, [])
        self.assertTrue(coord.snoozed("example.test"))

    def test_two_kinds_on_one_site_offer_simplified_mode_once_a_day(self):
        coord, mem, calls, _ = self.make_coordinator()
        offers = []
        coord.bus.subscribe("offer", offers.append)
        coord.handle_struggle(Struggle("squint", 0.9))
        coord.handle_struggle(Struggle("rage_click", 0.9))
        self.assertEqual(offers[-1].accommodation.key, SIMPLE_OFFER.key)
        coord.handle_struggle(Struggle("lean", 0.9))
        coord.handle_struggle(Struggle("rage_click", 0.9))
        self.assertNotEqual(offers[-1].accommodation.key, SIMPLE_OFFER.key)

    def test_senses_struggles_pass_their_own_gate_others_are_checked_again(self):
        tmp = Path(tempfile.mkdtemp())
        offers = []
        coord = Coordinator(EventBus(), lambda n, a, src: None, HabitMemory(tmp / "h.json"),
                            lambda: {"app": "Notepad", "site": "", "in_browser": False},
                            lambda: {"cooldown_seconds": 0, "min_confidence": 0.6, "offer_timeout_seconds": 0.05},
                            ui=NullUI(), voice=NullVoice())
        coord.bus.subscribe("offer", offers.append)
        coord.handle_struggle(Struggle("fused", 0.55, source="senses"))  # weak but agreed by two signals
        self.assertEqual(len(offers), 1)
        coord._recent.clear()
        coord.handle_struggle(Struggle("squint", 0.55, source="simulate"))  # run.py simulate: checked here
        self.assertEqual(len(offers), 1)

    def test_simplified_mode_never_becomes_an_automatic_habit(self):
        coord, mem, calls, _ = self.make_coordinator()
        mem.record("example.test", SIMPLE_OFFER, True)
        mem.record("example.test", SIMPLE_OFFER, True)
        coord.check_context()
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
