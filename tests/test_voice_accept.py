"""Spoken requests as Whisper writes them; no microphone, network, or real tools."""

import unittest

from helpers import ROOT  # noqa: F401

from ally import listener, rules


# True in the third column means the always-on listener must hear a wake word.
CORPUS = [
    ("Ally, make it easier to read", "reader", True),
    ("Allie can you make this easier to read", "reader", True),
    ("zoom in", "zoom_in", False),
    ("can you zoom in a bit", "zoom_in", False),
    ("make it bigger please", "text_bigger", False),
    ("I can't see this", "text_bigger", False),
    ("scroll down", "scroll_down", False),
    ("go down", "scroll_down", False),
    ("keep going", "scroll_down", False),
    ("next part", "scroll_down", False),
    ("go up a bit", "scroll_up", False),
    ("go back", "back", False),
    ("take me back", "back", False),
    ("back to the last page", "back", False),
    ("show me the picture", "zoom_picture", False),
    ("open the image", "zoom_picture", False),
    ("let me see the photo", "zoom_picture", False),
    ("read this to me", "read_page", False),
    ("read it out loud", "read_page", False),
    ("stop", "stop", False),
    ("Ally stop", "stop", True),
    ("be quiet", "stop", False),
    ("Hello Ollie, zoom in a little more", "zoom_in", True),
    ("Okay so Ally make it bigger", "text_bigger", True),
    ("um Olly could you make the words bigger please", "text_bigger", True),
    ("Hi Al E, go down", "scroll_down", True),
    ("A Lee, next part", "scroll_down", True),
    ("Ally's, take me back", "back", True),
    ("Ali, read the highlighted words to me", "read_selection", True),
    ("Aly, go up a bit please", "scroll_up", True),
    ("Alli, let me see the photo", "zoom_picture", True),
    ("Alley! Can you zoom out a bit?", "zoom_out", True),
    ("Read this page, Allie", "read_page", True),
    ("hello Ally, make this page easier to read for me", "reader", True),
    ("could you give me a clean reading view please", "reader", False),
    ("please show just the article for me", "reader", False),
    ("can you put this in reader mode", "reader", False),
    ("could you close the reading view please", "reader_off", False),
    ("make these words a little larger", "text_bigger", False),
    ("the writing here is too tiny", "text_bigger", False),
    ("please enlarge this text a little more", "text_bigger", False),
    ("i need the letters bigger", "text_bigger", False),
    ("make the words a little smaller", "text_smaller", False),
    ("could you shrink this text a little", "text_smaller", False),
    ("please zoom in on this page for me", "zoom_in", False),
    ("zoom out a little more please", "zoom_out", False),
    ("please move down this page", "scroll_down", False),
    ("can you show me the next part please", "scroll_down", False),
    ("go a little further down", "scroll_down", False),
    ("please move up this page", "scroll_up", False),
    ("show me the part above please", "scroll_up", False),
    ("return to the previous page please", "back", False),
    ("take me to the page before this please", "back", False),
    ("can you take me forward", "forward", False),
    ("please try loading this page again", "reload", False),
    ("could you refresh the current tab", "reload", False),
    ("open up the picture please", "zoom_picture", False),
    ("show this photo a little closer please", "zoom_picture", False),
    ("please put the picture away", "close_picture", False),
    ("move to the next image please", "next_picture", False),
    ("show the image before this please", "previous_picture", False),
    ("could you read me this article", "read_page", False),
    ("please read the whole page aloud", "read_page", False),
    ("say the words i highlighted please", "read_selection", False),
    ("read just my selection please", "read_selection", False),
    ("that's enough talking please", "stop", False),
    ("no more talking please", "stop", False),
    ("please make the buttons a little bigger", "buttons", False),
    ("these buttons are much too tiny", "buttons", False),
    ("please remove these pop ups for me", "clutter", False),
    ("get these ads out of the way please", "clutter", False),
    ("could you show the ads again", "clutter_off", False),
    ("make this menu easier to use please", "menus", False),
    ("could you put the simple menu away", "menus_off", False),
    ("could you make this website simpler", "simple_on", False),
    ("please leave simplified mode", "simple_off", False),
    ("please increase the contrast", "contrast", False),
    ("can you switch to a dark theme", "dark", False),
    ("please spread these words out", "spacing", False),
    ("please use the dyslexia reading font", "dyslexia", False),
    ("can you reverse the screen colors", "invert", False),
    ("please show everything in black and white", "grayscale", False),
    ("use regular colors again please", "colors_normal", False),
    ("open the screen magnifier please", "magnifier", False),
    ("close the screen magnifier please", "magnifier_off", False),
    ("please magnify this spot", "look_magnify", False),
    ("can you move the magnifier with my eyes", "follow_eyes", False),
    ("could you move the magnifier with the cursor", "follow_mouse", False),
    ("please follow my eyes more slowly", "follow_slower", False),
    ("could you move the view faster", "follow_faster", False),
    ("what's the weather", None, False),
    ("Ally call Priya", None, True),
    ("call Priya", None, False),
    ("Ally send this photo to Priya", None, True),
    ("buy the bigger picture", None, False),
    ("transfer money to my daughter", None, False),
    ("i think we should get lunch soon honestly", None, False),
    ("we had a lovely day at the park", None, False),
    ("what does this article mean", None, False),
    ("why is the text so small on this site", None, False),
    ("summarize the second section", None, False),
    ("tell me a story", None, False),
    ("don't make the words bigger", None, False),
    ("don't scroll down", None, False),
    ("never open up the picture", None, False),
    ("make the words bigger and read them to me", None, False),
    ("should i go back or go forward", None, False),
    ("i like the bigger picture on the wall", None, False),
    ("we read the whole page aloud yesterday", None, False),
    ("we read the whole page aloud", None, False),
    ("i read the whole page aloud", None, False),
    ("why read the whole page aloud", None, False),
    ("how do i read the whole page aloud", None, False),
    ("make it easier to read but keep the ads", None, False),
    ("put the colors back to normal on all websites", None, False),
    ("go back to my bank page", "switch_tab", False),  # the team's tab switching, not "back"
    ("my friend Ali said the alley was dark", None, True),
    ("my friend Ali scroll down", None, True),
    ("i told Allie to read this to me", None, True),
    ("we met Ollie at the park", None, True),
    ("okay so um Ally zoom in", None, True),
    ("Alligator go back", None, True),
    ("Totally fine", None, True),
    ("Ally, what do you think of this photo", None, True),
    ("Ally, how do i make a phone call", None, True),
]


def outcome(text, require_wake, fuzzy):
    woke, rest = listener.split_wake(text, ["ally"])
    if require_wake and not woke:
        return None
    request = rest if woke else text
    result = rules.match(request, strict=True)
    if fuzzy and not result:
        result = rules.closest(request)
    return result.rule if result else None


class VoiceAcceptTests(unittest.TestCase):
    def test_corpus(self):
        self.assertGreaterEqual(len(CORPUS), 60)
        positives = [(text, expected, wake) for text, expected, wake in CORPUS if expected is not None]
        before = after = 0
        misses = []
        for text, expected, wake in CORPUS:
            actual = outcome(text, wake, fuzzy=True)
            with self.subTest(text=text):
                if expected is None:
                    self.assertIsNone(actual)
                else:
                    self.assertIn(actual, (None, expected))  # a miss is safer than a wrong command
                    before += outcome(text, wake, fuzzy=False) == expected
                    after += actual == expected
                    if actual is None:
                        misses.append(text)
        print(f"\nVoice corpus: strict {before}/{len(positives)} ({before / len(positives):.1%}); "
              f"with closest {after}/{len(positives)} ({after / len(positives):.1%})")
        self.assertGreaterEqual(after / len(positives), 0.9, misses)

    def test_demo_script(self):
        for text, expected, wake in CORPUS[:22]:
            with self.subTest(text=text):
                self.assertEqual(outcome(text, wake, fuzzy=True), expected)

    def test_all_rules_have_examples(self):
        names = {r.name for r in rules.RULES} | {r[0] for r in rules.PICTURE_RULES}
        names |= {"zoom_picture", "describe_picture", "click_text", "click_number"}
        names |= {r[0] for r in rules.CLICK_RULES}
        self.assertTrue(names <= rules.EXAMPLES.keys())
        for name, examples in rules.EXAMPLES.items():
            self.assertTrue(5 <= len(examples) <= 15, name)

    def test_never_infers_sensitive_commands(self):
        for text in ("reset everything", "put it back to normal", "set up my face", "quick calibration",
                     "help set up my eyes", "test the eye tracking", "describe my screen", "explain this photograph",
                     "click number three", "press seven", "click continue", "show the numbers", "hide the numbers",
                     "zoom to 200 percent", "double the size", "send the page", "call Priya", "pay the bill",
                     "make a payment", "turn simple mode off everywhere"):
            with self.subTest(text=text):
                self.assertIsNone(rules.closest(text))

    def test_ambiguous_and_empty_requests(self):
        for text in ("", "please", "up or down", "zoom in or out", "make this bigger or smaller",
                     "open or close the picture", "reader mode on or off"):
            with self.subTest(text=text):
                self.assertIsNone(rules.closest(text))

    def test_copies_actions(self):
        result = rules.closest("go down")
        self.assertEqual(result.actions, [("scroll_page", {"direction": "down"})])
        result.actions[0][1]["direction"] = "up"
        self.assertEqual(rules.closest("go down").actions, [("scroll_page", {"direction": "down"})])


if __name__ == "__main__":
    unittest.main()
