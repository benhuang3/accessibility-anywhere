import threading
import unittest

from helpers import ROOT  # noqa: F401  (puts the repo on sys.path)

from ally.bridge import Bridge, Client


def _client() -> Client:
    return Client(object(), "chrome-extension://test")


class PageEventTests(unittest.TestCase):
    def test_page_event_reaches_on_page_and_not_the_focus_state(self):
        bridge = Bridge()
        got, done = [], threading.Event()
        bridge.on_page = lambda msg: (got.append(msg), done.set())
        client = _client()
        msg = {"type": "event", "name": "page", "url": "https://shop.example/", "text": "hi", "focused": True}
        bridge._handle(client, msg)
        self.assertTrue(done.wait(2))
        self.assertEqual(got[0]["url"], "https://shop.example/")
        self.assertFalse(client.focused)  # a page report never changes focus bookkeeping

    def test_page_event_without_a_hook_is_ignored(self):
        bridge = Bridge()
        client = _client()
        bridge._handle(client, {"type": "event", "name": "page", "url": "https://a.example/", "focused": True})
        self.assertFalse(client.focused)

    def test_a_crashing_hook_does_not_break_the_bridge(self):
        bridge = Bridge()
        done = threading.Event()

        def boom(_msg):
            done.set()
            raise RuntimeError("boom")

        bridge.on_page = boom
        bridge._handle(_client(), {"type": "event", "name": "page"})
        self.assertTrue(done.wait(2))

    def test_other_events_still_update_focus(self):
        bridge = Bridge()
        client = _client()
        bridge._handle(client, {"type": "event", "name": "focus", "focused": True})
        self.assertTrue(client.focused)


if __name__ == "__main__":
    unittest.main()
