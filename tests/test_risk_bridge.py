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

    def test_helper_and_call_ended_events_use_daemon_hooks_without_changing_focus(self):
        for name, attr in (("helper_action", "on_helper_action"), ("risk_call_ended", "on_call_ended")):
            with self.subTest(name=name):
                bridge, client = Bridge(), _client()
                got, done = [], threading.Event()

                def hook(msg):
                    got.append((msg, threading.current_thread()))
                    done.set()

                setattr(bridge, attr, hook)
                msg = {"type": "event", "name": name, "focused": True}
                bridge._handle(client, msg)
                self.assertTrue(done.wait(2))
                self.assertEqual(got[0][0], msg)
                self.assertTrue(got[0][1].daemon)
                self.assertIsNot(got[0][1], threading.current_thread())
                self.assertFalse(client.focused)
                setattr(bridge, attr, None)
                bridge._handle(client, msg)
                self.assertFalse(client.focused)

    def test_helper_and_call_ended_hook_errors_are_swallowed(self):
        for attr in ("on_helper_action", "on_call_ended"):
            def boom(msg):
                raise RuntimeError("private content")

            with self.assertLogs("ally.bridge", level="DEBUG") as logs:
                Bridge._safe_hook(boom, {})
            self.assertNotIn("private content", " ".join(logs.output))

    def test_other_events_still_update_focus(self):
        bridge = Bridge()
        client = _client()
        bridge._handle(client, {"type": "event", "name": "focus", "focused": True})
        self.assertTrue(client.focused)


if __name__ == "__main__":
    unittest.main()
