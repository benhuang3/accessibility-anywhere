import unittest
from unittest.mock import patch

from helpers import EnvPatch, FakeClaude, fake_screen, final, temp_home, tool_use

from ally import rules
from ally.agent import Agent
from ally.brain import thinking_param
from ally.registry import build_tool


def make_agent(config: str = "", key: str | None = "test-key", base_url: str | None = None) -> Agent:
    with EnvPatch(ANTHROPIC_API_KEY=key, ANTHROPIC_BASE_URL=base_url):
        a = Agent(temp_home(config), start_hotkeys=False)
    a.screen_calls = fake_screen(a.platform)  # never zoom the real screen
    calls = []

    def fake_zoom(zoom: float):
        "Zoom the page."
        calls.append(zoom)
        return f"Page zoom is {round(zoom * 100)}%."

    a.registry.add(build_tool(fake_zoom, name="set_page_zoom"))  # replace the browser tool
    a.zoom_calls = calls
    return a


class RulesTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(rules.normalize("Hey Ally, could you please zoom in?"), "zoom in")
        self.assertEqual(rules.normalize("Make it 1.5x  please!"), "make it 1.5x")

    def test_strict_matches(self):
        cases = {
            "zoom in": "zoom_in", "zoom out": "zoom_out", "bigger please": "text_bigger",
            "I can't read this": "text_bigger", "smaller": "text_smaller", "stop": "stop", "be quiet": "stop", "reset everything": "reset",
            "back to normal": "reset", "read this page": "read_page", "read it to me": "read_page",
            "read the selection": "read_selection", "what's on my screen?": "describe",
            "high contrast": "contrast", "dark mode": "dark", "reader mode": "reader", "invert colors": "invert",
            "grayscale": "grayscale", "turn off the magnifier": "magnifier_off", "magnifier": "magnifier",
            "scroll down": "scroll_down", "go back": "back", "help": "help",
        }
        for text, rule in cases.items():
            m = rules.match(text)
            self.assertIsNotNone(m, text)
            self.assertEqual(m.rule, rule, text)

    def test_strict_leaves_nuance_to_claude(self):
        for text in ("this blog's gray text on white is awful", "summarize the second section",
                     "why is the text so small on this site"):
            self.assertIsNone(rules.match(text, strict=True), text)

    def test_loose_offline(self):
        self.assertEqual(rules.match("ugh this website is way too tiny", strict=False).rule, "text_bigger")
        self.assertEqual(rules.match("could you read me the article", strict=False).rule, "read_page")

    def test_levels(self):
        m = rules.match("zoom to 200%")
        self.assertEqual(m.actions, [("zoom_to", {"level": 2.0})])
        self.assertEqual(rules.match("magnify 3x").actions, [("zoom_to", {"level": 3.0})])
        self.assertEqual(rules.match("make it 150 percent").actions, [("zoom_to", {"level": 1.5})])
        self.assertEqual(rules.match("double").actions, [("zoom_to", {"level": 2.0})])


class ThinkingParamTests(unittest.TestCase):
    def test_per_model(self):
        self.assertEqual(thinking_param("claude-haiku-5-5", "off", "low"), {"type": "disabled"})
        self.assertIsNone(thinking_param("claude-haiku-5-5", "off", "max"))
        self.assertEqual(thinking_param("claude-sonnet-5-5", "off", "low"), {"type": "between_tools"})
        self.assertIsNone(thinking_param("claude-opus-5-5", "off", "low"))
        self.assertIsNone(thinking_param("claude-haiku-5-5", "adaptive", "low"))


class BrainTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeClaude()
        self.env = EnvPatch(ANTHROPIC_API_KEY="test-key", ANTHROPIC_BASE_URL=self.api.url)
        self.env.__enter__()
        self.agent = make_agent()

    def tearDown(self):
        self.env.__exit__()
        self.api.close()

    def test_tool_loop(self):
        self.api.queue(200, tool_use("set_page_zoom", {"zoom": 1.5}, "toolu_A", text="Let me fix that."))
        self.api.queue(200, final("I zoomed the page to 150%."))
        reply = self.agent.brain.handle("this blog is hard to read, the font is tiny")
        self.assertEqual(reply.mode, "claude")
        self.assertEqual(reply.text, "I zoomed the page to 150%.")
        self.assertEqual(self.agent.zoom_calls, [1.5])
        first, second = self.api.requests[0], self.api.requests[1]
        h = {k.lower(): v for k, v in first["headers"].items()}
        self.assertEqual(h["x-api-key"], "test-key")
        self.assertEqual(h["anthropic-version"], "2023-06-01")
        body = first["body"]
        self.assertEqual(body["model"], "claude-haiku-5-5")
        self.assertEqual(body["output_config"], {"effort": "low"})
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertNotIn("temperature", body)
        self.assertIn("<context>", body["messages"][-1]["content"])
        names = [t["name"] for t in body["tools"]]
        self.assertIn("set_page_zoom", names)
        self.assertIn("reading_mode", names)  # config commands are tools too
        self.assertNotIn("zoom_in", names)  # ai=False tools are hidden
        msgs = second["body"]["messages"]
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant", "user"])
        result = msgs[-1]["content"][0]
        self.assertEqual(result["type"], "tool_result")
        self.assertEqual(result["tool_use_id"], "toolu_A")
        self.assertEqual(result["content"], "Page zoom is 150%.")
        self.assertNotIn("is_error", result)

    def test_tool_error_goes_back_to_claude(self):
        self.api.queue(200, tool_use("no_such_tool", {}, "toolu_B"))
        self.api.queue(200, final("Sorry, I couldn't do that."))
        reply = self.agent.brain.handle("do the impossible thing now")
        res = self.api.requests[1]["body"]["messages"][-1]["content"][0]
        self.assertTrue(res["is_error"])
        self.assertEqual(reply.text, "Sorry, I couldn't do that.")

    def test_history_and_followup(self):
        self.api.queue(200, tool_use("set_page_zoom", {"zoom": 1.25}))
        self.api.queue(200, final("Zoomed to 125%."))
        self.agent.brain.handle("the article text is a bit small")
        self.api.queue(200, final("OK."))
        self.agent.brain.handle("a little more than that")
        msgs = self.api.requests[2]["body"]["messages"]
        self.assertEqual(msgs[0]["content"], "the article text is a bit small")
        self.assertIn("set_page_zoom(zoom=1.25)", msgs[1]["content"])
        self.assertTrue(msgs[2]["content"].startswith("a little more than that"))

    def test_fast_path_skips_claude(self):
        reply = self.agent.brain.handle("stop")
        self.assertEqual(reply.mode, "fast")
        self.assertEqual(self.api.requests, [])

    def test_closest_runs_as_fast_rule(self):
        calls = []

        def fake_reader(on: bool = True):
            calls.append(on)
            return "Reader view is on."

        self.agent.registry.add(build_tool(fake_reader, name="reader_view"))
        reply = self.agent.brain.handle("can you make this easier to read")
        self.assertEqual(reply.mode, "fast")
        self.assertEqual(reply.actions[0]["tool"], "reader_view")
        self.assertEqual(calls, [True])
        self.assertEqual(self.api.requests, [])

    def test_strict_rule_precedes_closest(self):
        with patch("ally.brain.rules.closest") as closest:
            self.assertEqual(self.agent.brain.handle("stop").mode, "fast")
        closest.assert_not_called()

    def test_closest_respects_fast_path_setting(self):
        self.agent.config.data["brain"]["fast_path"] = False
        self.api.queue(200, final("I can help with reading."))
        with patch("ally.brain.rules.closest") as closest:
            reply = self.agent.brain.handle("make this easier to read")
        self.assertEqual(reply.mode, "claude")
        closest.assert_not_called()

    def test_closest_leaves_other_requests_to_claude(self):
        for text in ("what's the weather", "call Priya", "don't make the words bigger"):
            with self.subTest(text=text):
                self.api.queue(200, final("Let me help."))
                reply = self.agent.brain.handle(text)
                self.assertEqual(reply.mode, "claude")
                self.assertEqual(reply.actions, [])

    def test_self_heals_unsupported_speed_settings(self):
        self.api.queue(400, {"type": "error", "error": {"type": "invalid_request_error",
                                                         "message": "output_config.effort: not supported"}})
        self.api.queue(200, final("Done."))
        reply = self.agent.brain.handle("please tidy up this page for reading")
        self.assertEqual(reply.text, "Done.")
        self.assertNotIn("output_config", self.api.requests[1]["body"])
        self.api.queue(200, final("Again."))
        self.agent.brain.handle("and once more for the other page")
        self.assertNotIn("output_config", self.api.requests[2]["body"])  # remembered per model

    def test_overload_retry(self):
        self.api.queue(529, {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}})
        self.api.queue(200, final("Worked on retry."))
        self.assertEqual(self.agent.brain.handle("explain this chart to me").text, "Worked on retry.")

    def test_offline_fallback(self):
        self.api.close()  # API unreachable
        reply = self.agent.brain.handle("ugh, everything is way too tiny on here")
        self.assertEqual(reply.mode, "offline")
        self.assertTrue(reply.error)
        self.assertEqual(reply.actions[0]["tool"], "enlarge_text")
        self.assertFalse(self.agent.brain.online)


class OfflineAgentTests(unittest.TestCase):
    def test_no_key_means_offline_and_commands_still_work(self):
        with EnvPatch(ANTHROPIC_API_KEY=None):
            a = make_agent(key=None)
            self.assertEqual(a.brain.mode(), "offline")
            reply = a.brain.handle("reading mode")
        self.assertEqual(reply.actions[0]["tool"], "reading_mode")
        self.assertEqual(a.zoom_calls, [1.5])  # first step of the command ran

    def test_tool_names_work_as_phrases_offline(self):
        with EnvPatch(ANTHROPIC_API_KEY=None):
            a = make_agent(key=None)

            def what_time_is_it():
                "Say the time."
                return "It's noon."

            a.registry.add(build_tool(what_time_is_it))
            self.assertEqual(a.brain.handle("What time is it?").text, "It's noon.")
            self.assertEqual(a.brain.handle("what tine is it").text, "It's noon.")  # typo: fuzzy match offline

    def test_run_action_kinds(self):
        with EnvPatch(ANTHROPIC_API_KEY=None):
            a = make_agent(key=None)
            a.run_action({"tool": "set_page_zoom", "args": {"zoom": 2}})
            a.run_action("set_page_zoom")  # missing arg: reported, not raised
            a.run_action("zoom to 300%")  # phrase -> brain -> rules
        self.assertEqual(a.zoom_calls[0], 2.0)
        self.assertIn(("magnify", 3.0), a.screen_calls)  # "zoom to 300%" hit the fake screen
        kinds = [e["kind"] for e in a.events.since(0)]
        self.assertIn("error", kinds)


if __name__ == "__main__":
    unittest.main()
