import threading
import unittest
from unittest.mock import patch

from helpers import ROOT  # noqa: F401  (puts the repo on sys.path)

from ally.contracts import EventBus, Risk
from ally.risk import actions, contact
from ally.risk.wire import PAUSE_SECONDS, RiskWire

JOINED = Risk("remote_access", "high", "A remote tool is running while a bank page is open.", "https://www.chase.com/",
              "AnyDesk", "risk.joined")
MEDIUM = Risk("untrusted_site", "medium", "Looks different.", "https://paypa1.example/")


def asked_offer(risk, **kwargs):
    """Publish a risk_warning and return the Offer the wire asked with."""
    bus, offers, done = EventBus(), [], threading.Event()

    def ask(offer):
        offers.append(offer)
        done.set()
        return "no"

    wire = RiskWire(bus, snapshot_fn=lambda: [], ask=ask, **kwargs)
    wire.start()
    try:
        bus.publish("risk_warning", {"risk": risk.to_dict(), "text": "x"})
        assert done.wait(2)
        return offers[0], wire, bus
    finally:
        wire.stop()


class PauseTests(unittest.TestCase):
    def test_remote_tool_plus_bank_gets_a_pause_and_a_longer_timeout(self):
        offer, _, _ = asked_offer(JOINED)
        self.assertEqual(offer.pause_seconds, PAUSE_SECONDS)
        self.assertGreaterEqual(offer.timeout, PAUSE_SECONDS + 20)

    def test_other_warnings_do_not_pause(self):
        offer, _, _ = asked_offer(MEDIUM)
        self.assertFalse(getattr(offer, "pause_seconds", 0))


class TrustedContactTests(unittest.TestCase):
    def test_high_warnings_offer_to_tell_the_contact_and_report_the_result(self):
        sent = []
        offer, wire, bus = asked_offer(JOINED, contact=("Priya", lambda text: sent.append(text) or True))
        label, run = offer.extra_action
        self.assertEqual(label, "Tell Priya")
        got = []
        bus.subscribe("risk_contact", got.append)
        self.assertEqual(run(), "I've sent Priya a message.")
        self.assertEqual(got, [{"name": "Priya", "kind": "remote_access", "ok": True, "call_opened": False}])
        self.assertNotIn("chase", sent[0].lower())          # never the address
        self.assertNotRegex(sent[0], r"https?://|\d{3}")

    def test_a_failed_send_says_so(self):
        offer, _, _ = asked_offer(JOINED, contact=("Priya", lambda text: False))
        self.assertIn("couldn't reach Priya", offer.extra_action[1]())
        offer, _, _ = asked_offer(JOINED, contact=("Priya", lambda text: 1 / 0))
        self.assertIn("couldn't reach Priya", offer.extra_action[1]())

    def test_no_contact_or_medium_warning_means_no_button(self):
        self.assertIsNone(getattr(asked_offer(JOINED)[0], "extra_action", None))
        self.assertIsNone(getattr(asked_offer(MEDIUM, contact=("Priya", lambda t: True))[0], "extra_action", None))

    def test_topics_must_be_long_and_plain(self):
        for bad in ("", "short", "has space here", "../../etc", "a" * 70, None, 5):
            self.assertIsNone(contact.ntfy_sender(bad), bad)
        self.assertIsNotNone(contact.ntfy_sender("ally-priya-7f3k29qx"))

    def test_sender_posts_to_the_topic_and_never_raises(self):
        class Resp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        seen = []
        with patch("urllib.request.urlopen", lambda req, timeout: seen.append(req) or Resp()):
            self.assertTrue(contact.ntfy_sender("ally-priya-7f3k29qx")("hello"))
        self.assertEqual(seen[0].full_url, "https://ntfy.sh/ally-priya-7f3k29qx")
        with patch("urllib.request.urlopen", side_effect=OSError("offline")):
            self.assertFalse(contact.ntfy_sender("ally-priya-7f3k29qx")("hello"))

    def test_sender_never_raises_when_building_the_request_fails(self):
        with patch("urllib.request.Request", side_effect=ValueError("bad request")):
            self.assertFalse(contact.ntfy_sender("ally-priya-7f3k29qx")("hello"))

    def test_message_for_every_kind_is_plain(self):
        for kind in ("remote_access", "scam_popup", "untrusted_site", "payment_field", "unknown"):
            text = contact.message_for(kind)
            self.assertTrue(text.startswith("Ally safety alert:"))
            self.assertNotRegex(text, r"\d")
            self.assertNotIn("paid", text.lower())
            self.assertTrue(text.endswith("Ally asked them to stop and wait. Please contact them now."))


class CatalogueCloseTests(unittest.TestCase):
    def test_closing_works_for_a_catalogue_tool_and_only_its_processes(self):
        killed = []
        snap = lambda: [{"name": "AA_v3.exe"}, {"name": "chrome.exe"}, {"name": "nvda.exe"}]  # noqa: E731
        self.assertEqual(actions.close_tool("Ammyy Admin", snap, killed.append), 1)
        self.assertEqual(killed, ["AA_v3.exe"])
        self.assertEqual(actions.close_tool("NVDA (Non-Visual Desktop Access)", snap, killed.append), 0)


class VideoCallTests(unittest.TestCase):
    MEET = "https://meet.google.com/abc-defg-hij"

    def test_call_button_opens_the_call_here_and_sends_the_join_link(self):
        sent, opened = [], []
        offer, _, _ = asked_offer(JOINED, contact=("Priya", lambda text, url="": sent.append((text, url)) or True, self.MEET),
                                  call=lambda action, args: opened.append((action, args)) or args["url"])
        label, run = offer.extra_action
        self.assertEqual(label, "Call Priya")
        self.assertEqual(run(), "Opening a call with Priya. Hang up on the other caller first.")
        self.assertEqual(opened, [("openUrl", {"url": self.MEET, "newTab": True})])
        self.assertEqual(sent[0][1], self.MEET)
        self.assertIn("join the video call", sent[0][0])

    def test_other_call_notes_ask_the_contact_to_join(self):
        risk = Risk("scam_popup", "high", "A fake warning page.")
        offer, _, _ = asked_offer(risk, contact=("Priya", lambda text, url: True, self.MEET),
                                  call=lambda *args: None)
        self.assertEqual(offer.extra_action[1](), "Opening a call with Priya. I've asked them to join.")

    def test_failed_call_alert_keeps_the_couldnt_reach_wording(self):
        offer, _, _ = asked_offer(JOINED, contact=("Priya", lambda text, url: False, self.MEET),
                                  call=lambda *args: None)
        self.assertEqual(offer.extra_action[1](), "I couldn't reach Priya. Please give them a call.")

    def test_only_known_video_services_over_https_count(self):
        for good in (self.MEET, "https://us02web.zoom.us/j/123", "https://whereby.com/priya"):
            self.assertTrue(contact.valid_call_url(good), good)
        for bad in ("http://meet.google.com/x", "https://meet.google.com.evil.example/x", "javascript:alert(1)",
                    "https://evil.example/meet.google.com", "", None, "https://" + "a" * 400,
                    "https://user:pass@meet.google.com/x", "https://@meet.google.com/x",
                    "https://meet.google.com:443/x", "https://meet.google.com:8443/x",
                    "https://meet.google.com:/x", "https://meet.google.com:bad/x"):
            self.assertFalse(contact.valid_call_url(bad), bad)

    def test_call_links_reject_whitespace_and_control_characters_anywhere(self):
        for char in (" ", "\t", "\n", "\r", "\x00", "\x1f", "\x7f", "\x85", "\u00a0"):
            for url in (char + self.MEET, self.MEET + char, "https://meet.google.com/a" + char + "b"):
                with self.subTest(url=url):
                    self.assertFalse(contact.valid_call_url(url))

    def test_a_bad_call_link_falls_back_to_tell(self):
        offer, _, _ = asked_offer(JOINED, contact=("Priya", lambda text, url="": True, "https://evil.example/x"))
        self.assertEqual(offer.extra_action[0], "Tell Priya")

    def test_the_push_carries_a_join_button_only_for_a_valid_link(self):
        class Resp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        seen = []
        with patch("urllib.request.urlopen", lambda req, timeout: seen.append(req) or Resp()):
            send = contact.ntfy_sender("ally-priya-7f3k29qx")
            send("x", self.MEET)
            send("x", "https://evil.example/")
        self.assertEqual(seen[0].get_header("Click"), self.MEET)
        self.assertIn("Join the call", seen[0].get_header("Actions"))
        self.assertIsNone(seen[1].get_header("Click"))


if __name__ == "__main__":
    unittest.main()
