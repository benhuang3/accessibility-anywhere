"""The AI check-and-fix loop (ally/pagekit/review.py) with a scripted Claude and the fake extension."""

import base64
import json
import unittest

import helpers  # noqa: F401  (adds the repo root to sys.path)

from ally.pagekit import core
from ally.pagekit.review import MAX_FIXES, valid_action
from ally.registry import ToolError
from test_pagekit import Ctx, FakeExtension, registry


class ReviewExtension(FakeExtension):
    """The fake extension plus what the AI loop needs: screenshots, an outline and the fix layer."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.fix_rules = []

    def a_pageScreenshot(self, quality=60):
        return {"mediaType": "image/jpeg", "data": base64.b64encode(f"shot@{self.zoom}".encode()).decode()}

    def a_pageOutline(self):
        return {"items": [{"key": "header>nav", "why": "landmark", "tag": "nav", "box": [0, 0, 1280, 60]}]}

    def a_stepOn(self, step, opts=None):
        if step == "fix":
            self.fix_rules += opts["rules"]
            self.layers.add("ally_fix")
            return {"said": "", "rules": len(self.fix_rules)}
        return super().a_stepOn(step, opts)

    def a_stepOff(self, step, opts=None):
        if step == "fix":
            self.fix_rules = []
            self.layers.discard("ally_fix")
            return {"said": ""}
        return super().a_stepOff(step, opts)


class ClaudeScript:
    """Answers ctx.ask from a list; records the prompts and how many pictures each one had."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, prompt, image=None, media_type="image/png", max_tokens=600, images=None):
        self.prompts.append((prompt, len(images or [])))
        a = self.answers.pop(0) if self.answers else {"ok": True}
        if isinstance(a, Exception):
            raise a
        return a if isinstance(a, str) else json.dumps(a)


def ctx_with(ext, script, ai_check=True):
    ctx = Ctx(ext)
    ctx.ask = script
    ctx.config = {"page_enlarge": {"ai_check": ai_check, "ai_background": False}}
    return ctx


FIX_ACTIONS = [{"type": "css", "selector": "header", "property": "position", "value": "static"},
               {"type": "zoom", "level": 1.25},
               {"type": "css", "selector": "body", "property": "background", "value": "url(x)"}]
BAD = {"worse": ["the menu bar covers the heading"], "actions": FIX_ACTIONS, "why": "unstick the header"}
GOOD = {"worse": [], "actions": []}


class ReviewLoopTests(unittest.TestCase):
    def test_ok_on_the_first_look(self):
        ext = ReviewExtension()
        script = ClaudeScript({"ok": True, "problems": []})
        report = core.run_steps(ctx_with(ext, script), ["clutter", "text"])
        self.assertTrue(report["review"]["ok"])
        self.assertEqual(report["review"]["fixes"], 0)
        self.assertEqual(len(script.prompts), 1)
        self.assertEqual(script.prompts[0][1], 2)  # before and after pictures
        self.assertIn("looks right", core.summary(report))

    def test_fix_then_ok(self):
        ext = ReviewExtension()
        script = ClaudeScript(BAD, GOOD)
        report = core.run_steps(ctx_with(ext, script), ["text", "buttons"])
        self.assertTrue(report["review"]["ok"])
        self.assertEqual(report["review"]["fixes"], 1)
        self.assertEqual(ext.fix_rules, [{"selector": "header", "property": "position", "value": "static"}])
        self.assertEqual(ext.zoom, 1.25)  # the background:url() rule was refused, the rest applied
        self.assertIn("ally_fix", ext.layers)
        self.assertIn("fixed it", core.summary(report))

    def test_gives_up_after_three_fixes_and_reverts(self):
        ext = ReviewExtension()
        start = ReviewExtension().state()
        script = ClaudeScript(*([BAD] * (MAX_FIXES + 1)))
        report = core.run_steps(ctx_with(ext, script), ["clutter", "menus", "text", "buttons"])
        self.assertTrue(report["review"]["gave_up"])
        self.assertEqual(report["review"]["fixes"], MAX_FIXES)
        self.assertTrue(report["reverted"])
        self.assertEqual(ext.state(), start)
        self.assertEqual(ext.fix_rules, [])
        self.assertEqual(len(script.prompts), MAX_FIXES + 1)  # one Claude call per round
        self.assertIn("put it back", core.summary(report))

    def test_an_answer_that_isnt_json_keeps_the_deterministic_result(self):
        ext = ReviewExtension()
        report = core.run_steps(ctx_with(ext, ClaudeScript("Looks great to me!")), ["buttons"])
        self.assertIn("skipped", report["review"])
        self.assertIn("ally_buttons", ext.layers)
        self.assertFalse(report["reverted"])

    def test_offline_skips_the_check(self):
        ext = ReviewExtension()
        report = core.run_steps(ctx_with(ext, ClaudeScript(ToolError("This needs Claude."))), ["text"])
        self.assertIn("skipped", report["review"])
        self.assertGreater(ext.zoom, 1.0)

    def test_switched_off_in_config(self):
        ext = ReviewExtension()
        script = ClaudeScript()
        report = core.run_steps(ctx_with(ext, script, ai_check=False), ["text"])
        self.assertIsNone(report["review"])
        self.assertEqual(script.prompts, [])

    def test_no_review_without_claude_or_when_turning_off(self):
        ext = ReviewExtension()
        report = core.run_steps(Ctx(ext), ["text"])  # no ctx.ask
        self.assertIsNone(report["review"])
        script = ClaudeScript()
        core.run_steps(ctx_with(ext, script), ["text"], on=False)
        self.assertEqual(script.prompts, [])

    def test_turning_steps_off_removes_the_ai_fixes(self):
        ext = ReviewExtension()
        core.run_steps(ctx_with(ext, ClaudeScript(BAD, GOOD)), ["text"])
        self.assertIn("ally_fix", ext.layers)
        core.run_steps(Ctx(ext), ["text"], on=False)
        self.assertNotIn("ally_fix", ext.layers)

    def test_simplified_mode_reverted_shows_no_chip(self):
        ext = ReviewExtension()
        ctx = ctx_with(ext, ClaudeScript(*([BAD] * (MAX_FIXES + 1))))
        out = registry().call("simplified_mode", {}, ctx=ctx)
        self.assertTrue(out.ok, out.error)
        self.assertIn("put it back", out.value)
        self.assertNotIn("ally-simple-chip", ext.hosts)
        self.assertEqual(ext.layers, set())


class KeepBestTests(unittest.TestCase):
    def test_worse_format_with_nothing_worse_is_ok(self):
        ext = ReviewExtension()
        report = core.run_steps(ctx_with(ext, ClaudeScript({"worse": [], "better": True})), ["buttons"])
        self.assertTrue(report["review"]["ok"])

    def test_a_round_that_makes_things_worse_is_undone(self):
        ext = ReviewExtension()
        rule_a = {"type": "css", "selector": "header", "property": "position", "value": "static"}
        rule_b = {"type": "css", "selector": "main", "property": "max-width", "value": "100%"}
        script = ClaudeScript({"worse": ["menu overlaps the title"], "actions": [rule_a, {"type": "zoom", "level": 1.1}]},
                              {"worse": ["menu overlaps", "text cut off at the right", "buttons jumbled"],
                               "actions": [{"type": "zoom", "level": 1.1}]},
                              {"worse": ["menu overlaps the title"], "actions": [rule_b]},
                              {"worse": []})
        report = core.run_steps(ctx_with(ext, script), ["text", "buttons"])
        chosen_zoom = report["zoom"]
        self.assertTrue(report["review"]["ok"])
        self.assertIn("undid that round: it made things worse", report["review"]["log"])
        self.assertEqual(ext.fix_rules, [{k: rule_b[k] for k in ("selector", "property", "value")}])
        self.assertEqual(ext.zoom, chosen_zoom)  # the zoom change from the bad round was undone too

    def test_undo_turns_a_step_back_on(self):
        ext = ReviewExtension()
        script = ClaudeScript({"worse": ["a"], "actions": [{"type": "step_off", "step": "buttons"}]},
                              {"worse": ["a", "b"], "actions": []}, {"worse": []})
        report = core.run_steps(ctx_with(ext, script), ["buttons"])
        self.assertTrue(report["review"]["ok"])
        self.assertIn("ally_buttons", ext.layers)


class BackgroundTests(unittest.TestCase):
    def bg_ctx(self, ext, script):
        ctx = Ctx(ext)
        ctx.ask = script
        ctx.config = {"page_enlarge": {"ai_check": True, "ai_background": True}}
        return ctx

    def test_replies_at_once_and_speaks_only_when_it_changes_something(self):
        import threading
        ext = ReviewExtension()
        gate = threading.Event()
        script = ClaudeScript(BAD, GOOD)

        def held_claude(*a, **kw):  # Claude "thinks" until the test has seen the reply come back first
            gate.wait(5)
            return script(*a, **kw)
        ctx = self.bg_ctx(ext, held_claude)
        report = core.run_steps(ctx, ["buttons"])
        self.assertEqual(report["review"], {"pending": True})  # the reply doesn't wait for Claude
        gate.set()
        report["review_thread"].join(5)
        self.assertTrue(report["review"]["ok"])
        self.assertEqual(ctx.said, ["I adjusted the page a little so it fits better."])

    def test_quiet_when_the_page_is_fine(self):
        ext = ReviewExtension()
        ctx = self.bg_ctx(ext, ClaudeScript(GOOD))
        report = core.run_steps(ctx, ["buttons"])
        report["review_thread"].join(5)
        self.assertEqual(ctx.said, [])

    def test_background_revert_says_so_and_runs_the_extra_undo(self):
        ext = ReviewExtension()
        ctx = self.bg_ctx(ext, ClaudeScript(*([BAD] * (MAX_FIXES + 1))))
        undone = []
        report = core.run_steps(ctx, ["text", "buttons"], after_revert=lambda: undone.append(True))
        report["review_thread"].join(5)
        self.assertTrue(report["reverted"])
        self.assertEqual(undone, [True])
        self.assertIn("put it back", ctx.said[-1])

    def test_a_newer_request_on_the_tab_cancels_the_check(self):
        import threading
        ext = ReviewExtension()
        gate, entered = threading.Event(), threading.Event()

        def slow_claude(prompt, images=None, **kw):
            entered.set()
            gate.wait(5)
            return json.dumps(BAD)
        ctx = self.bg_ctx(ext, slow_claude)
        report = core.run_steps(ctx, ["buttons"])
        self.assertTrue(entered.wait(5))
        core.run_steps(Ctx(ext), ["clutter"])  # the user asks for something else on the same tab
        gate.set()
        report["review_thread"].join(5)
        self.assertEqual(report["review"], {"cancelled": True})
        self.assertNotIn("ally_fix", ext.layers)  # the stale fix was never applied


class ActionRulesTests(unittest.TestCase):
    def test_only_safe_actions_pass(self):
        steps = ["text", "buttons"]
        ok = [{"type": "zoom", "level": 1.5}, {"type": "step_off", "step": "buttons"},
              {"type": "css", "selector": "main p", "property": "font-size", "value": "20px"},
              {"type": "css", "selector": ".ad-rail", "property": "display", "value": "none"},
              {"type": "css", "selector": "header", "property": "position", "value": "static"}]
        for a in ok:
            self.assertIsNotNone(valid_action(a, steps, 2.5), a)
        bad = [{"type": "zoom", "level": 4}, {"type": "zoom", "level": 1.33}, {"type": "step_off", "step": "clutter"},
               {"type": "css", "selector": "#ally-menu-strip", "property": "display", "value": "none"},
               {"type": "css", "selector": "body", "property": "background", "value": "red"},
               {"type": "css", "selector": "img", "property": "width", "value": "url(evil)"},
               {"type": "css", "selector": "header", "property": "position", "value": "fixed"},
               {"type": "css", "selector": "a{color:red}", "property": "width", "value": "1px"},
               {"type": "css", "selector": "p", "property": "width", "value": "10px !important"},
               {"type": "script", "code": "alert(1)"}, "zoom please"]
        for a in bad:
            self.assertIsNone(valid_action(a, steps, 2.5), a)


if __name__ == "__main__":
    unittest.main()
