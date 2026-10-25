"""Voice captions and their overlay subscription, without microphone, speech, or Tk."""

from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from helpers import ROOT  # noqa: F401

from ally.agent import Agent
from ally.brain import Reply
from ally.contracts import EventBus
from ally.listener import Utterance, WakeListener
from ally.overlay.overlay import Overlay
from ally.voice import RATE, VoiceInput


class FakeTimer:
    def __init__(self, seconds, callback, args=()):
        self.seconds, self.callback, self.args = seconds, callback, args
        self.daemon = False
        self.started = False

    def start(self):
        self.started = True

    def fire(self):
        self.callback(*self.args)


class FakeSpeaker:
    generation = 0
    speaking = False

    def __init__(self):
        self.say = Mock(return_value=True)
        self.stop = Mock()
        self.beep = Mock()
        self.waiting = threading.Event()
        self.finished = threading.Event()
        self.finished.set()
        self.worker = None

    def wait(self):
        self.worker = threading.current_thread()
        self.waiting.set()
        self.finished.wait(2)


class VoiceBearTests(unittest.TestCase):
    def setUp(self):
        self.timers = []

        def timer(*args, **kwargs):
            result = FakeTimer(*args, **kwargs)
            self.timers.append(result)
            return result

        timer_patch = patch("ally.agent.threading.Timer", side_effect=timer)
        timer_patch.start()
        self.addCleanup(timer_patch.stop)
        self.agent = a = Agent.__new__(Agent)
        a.bus = EventBus()
        a._voice_lock = threading.RLock()
        a._voice_token = None
        a.events = Mock()
        a.config = SimpleNamespace(data={"agent": {}, "voice": {"beep": False, "follow_up_seconds": 9}})
        a.speaker = FakeSpeaker()
        a.brain = Mock()
        a.brain.handle.return_value = Reply("Made it bigger.")
        a.listener = WakeListener(a)
        self.messages = []
        self.idle = threading.Event()

        def received(msg):
            self.messages.append(msg)
            if msg["state"] == "idle":
                self.idle.set()

        a.bus.subscribe("voice", received)
        self.addCleanup(a.speaker.finished.set)

    def states(self):
        return [msg["state"] for msg in self.messages]

    def test_prompt_publishes_contract_and_uses_listeners_window(self):
        self.agent.listener.prompt()
        self.assertEqual(self.states(), ["listening"])
        self.assertEqual(self.messages[0]["text"], "Listening\N{HORIZONTAL ELLIPSIS}")
        self.assertEqual(set(self.messages[0]), {"state", "text", "offer_id", "kind", "ts"})
        self.assertEqual(self.messages[0]["kind"], "request")
        self.assertIsNone(self.messages[0]["offer_id"])
        self.assertIsInstance(self.messages[0]["ts"], float)
        timer = self.timers[0]
        self.assertAlmostEqual(timer.seconds, 9, delta=.1)
        self.assertTrue(timer.daemon and timer.started)
        timer.fire()
        self.assertEqual(self.states(), ["listening", "idle"])

    def test_default_window_and_stale_timeout(self):
        self.agent._voice_state("listening", "Listening...")
        self.assertEqual(self.timers[0].seconds, 8)
        self.agent._voice_state("thinking", "Make this bigger")
        self.timers[0].fire()
        self.assertEqual(self.states(), ["listening", "thinking"])

    def test_wake_word_alone_and_request_publish_before_worker_starts(self):
        listener = self.agent.listener
        listener.transcribe = Mock(return_value="Ally")
        utterance = Utterance(b'', 1, 2, False)
        self.assertEqual(listener.handle(utterance), "")
        listener.transcribe.return_value = "Ally, make this bigger"
        with patch("ally.listener.threading.Thread") as thread:
            thread.return_value.start.side_effect = lambda: self.assertEqual(self.states()[-1], "thinking")
            self.assertEqual(listener.handle(utterance), "make this bigger")
            self.assertEqual(thread.call_args.kwargs["args"], ("make this bigger",))
        self.assertEqual(self.messages[-1]["text"], "make this bigger")
        self.timers[0].fire()
        self.assertEqual(self.states(), ["listening", "thinking"])

    def test_chatter_does_not_publish(self):
        self.agent.listener.transcribe = Mock(return_value="The weather is nice")
        self.assertIsNone(self.agent.listener.handle(Utterance(b'', 1, 2, False)))
        self.assertEqual(self.messages, [])

    def test_reply_waits_on_speakers_completion_on_daemon(self):
        speaker = self.agent.speaker
        speaker.finished.clear()
        self.agent._voice_state("thinking", "Make this bigger")
        reply = self.agent.handle_text("Make this bigger", source="voice")
        self.assertEqual(reply.text, "Made it bigger.")
        self.assertTrue(speaker.waiting.wait(1))
        self.assertTrue(speaker.worker.daemon)
        self.assertIsNot(speaker.worker, threading.current_thread())
        self.assertEqual(self.states(), ["thinking", "speaking"])
        self.assertEqual(self.messages[-1]["text"], reply.text)
        speaker.finished.set()
        self.assertTrue(self.idle.wait(1))
        self.assertEqual(self.states(), ["thinking", "speaking", "idle"])
        speaker.worker.join(1)

    def test_old_speech_completion_does_not_end_new_listening(self):
        speaker = self.agent.speaker
        speaker.finished.clear()
        self.agent.handle_text("Make it bigger", source="voice")
        self.assertTrue(speaker.waiting.wait(1))
        self.agent.listener.prompt()
        speaker.finished.set()
        speaker.worker.join(1)
        self.assertEqual(self.states(), ["speaking", "listening"])

    def test_typed_requests_never_publish(self):
        self.agent.handle_text("Make it bigger")
        self.agent.handle_text("Make it bigger", source="popup")
        self.agent.handle_text("Make it bigger", source="dashboard")
        self.assertEqual(self.messages, [])

    def test_empty_failed_or_silent_reply_goes_idle(self):
        for reply, options in ((Reply(""), {}), (Reply("Failed.", error="offline"), {}),
                               (Reply("Done.", actions=[{"silent": True}]), {}),
                               (Reply("Done."), {"speak": False})):
            with self.subTest(reply=reply, options=options):
                self.messages.clear()
                self.agent.brain.handle.return_value = reply
                self.agent.handle_text("Request", source="voice", **options)
                self.assertEqual(self.states(), ["idle"])
        self.messages.clear()
        self.agent.handle_text("", source="voice")
        self.assertEqual(self.states(), ["idle"])
        self.messages.clear()
        self.agent.brain.handle.side_effect = ValueError("failed")
        with self.assertRaises(ValueError):
            self.agent.handle_text("Request", source="voice")
        self.assertEqual(self.states(), ["idle"])

    def test_disabled_or_unstarted_speech_goes_idle(self):
        self.agent.config.data["agent"]["speak_replies"] = False
        self.agent.handle_text("Request", source="voice")
        self.assertEqual(self.states(), ["idle"])
        self.messages.clear()
        self.agent.config.data["agent"]["speak_replies"] = True
        self.agent.speaker.say.return_value = False
        self.agent.handle_text("Request", source="voice")
        self.assertEqual(self.states(), ["speaking", "idle"])

    def test_speaking_property_without_wait_is_used(self):
        class Speaker:
            def __init__(self):
                self.status = iter((True, True, False))

            @property
            def speaking(self):
                return next(self.status)

        self.agent.speaker = Speaker()
        token = self.agent._voice_state("speaking", "A reply")
        with patch("ally.agent.time.sleep") as sleep:
            self.agent._voice_finished(token, "A reply")
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(self.states(), ["speaking", "idle"])

    def test_missing_or_broken_completion_uses_bounded_word_timer(self):
        for speaker in (SimpleNamespace(), SimpleNamespace(wait=Mock(side_effect=OSError))):
            for words, seconds in ((1, 3), (10, 4), (100, 20)):
                with self.subTest(speaker=speaker, words=words):
                    self.agent.speaker = speaker
                    token = self.agent._voice_state("speaking", "reply")
                    with patch("ally.agent.time.monotonic", return_value=10), \
                            patch("ally.agent.time.sleep") as sleep:
                        self.agent._voice_finished(token, "word " * words)
                    sleep.assert_called_once_with(seconds)
                    self.assertEqual(self.states()[-1], "idle")

    def test_broken_bus_or_caption_worker_cannot_break_reply(self):
        with patch.object(self.agent.bus, "publish", side_effect=RuntimeError("overlay failed")):
            reply = self.agent.handle_text("Request", source="voice")
            self.assertEqual(reply.text, "Made it bigger.")
            self.agent.listener.prompt()
        with patch("ally.agent.threading.Thread", side_effect=RuntimeError("no thread")):
            reply = self.agent.handle_text("Request", source="voice")
            self.assertEqual(reply.text, "Made it bigger.")
            self.assertEqual(self.states()[-1], "idle")

    def voice_session(self, text="Make this bigger", pcm=b'x' * RATE):
        voice = VoiceInput(self.agent)
        voice._record = Mock(return_value=pcm)
        voice.transcribe = Mock(return_value=text)
        return voice

    def test_push_to_talk_listens_thinks_then_waits_for_reply(self):
        self.agent.config.data["voice"]["max_seconds"] = 11
        voice = self.voice_session()
        self.agent.speaker.finished.clear()
        voice._session()
        self.assertTrue(self.agent.speaker.waiting.wait(1))
        self.assertEqual(self.states(), ["listening", "thinking", "speaking"])
        self.assertEqual(self.timers[0].seconds, 11)
        self.timers[0].fire()
        self.assertEqual(self.states()[-1], "speaking")
        self.agent.speaker.finished.set()
        self.assertTrue(self.idle.wait(1))
        self.agent.speaker.worker.join(1)

    def test_push_to_talk_empty_audio_transcript_or_failure_go_idle(self):
        for text, pcm in (("request", b''), ("", b'x' * RATE), ("request", None)):
            with self.subTest(text=text, pcm=pcm):
                self.messages.clear()
                voice = self.voice_session(text, pcm)
                if pcm is None:
                    voice._record.side_effect = OSError("no microphone")
                    with self.assertLogs("ally.voice", level="ERROR"):
                        voice._session()
                else:
                    voice._session()
                self.assertEqual(self.states(), ["listening", "idle"])

    def test_subscription_queues_only_and_hides_voice_for_helper_call(self):
        overlay = Overlay()
        overlay.start = Mock(return_value=True)
        overlay._render = Mock()
        overlay._root = Mock()
        self.agent.proactive = Mock()
        self.agent.proactive_cfg = lambda: {"overlay": True}
        with patch("ally.overlay.Overlay", return_value=overlay), patch("ally.risk.wire.attach"):
            self.agent.start_proactive()
        self.agent._voice_state("listening", "Listening...")
        overlay._render.assert_not_called()
        overlay._handle(overlay._q.get_nowait())
        self.assertEqual(overlay._panel.current_card.kind, "voice")
        self.agent.bus.publish("risk_contact", {"call_opened": False})
        self.assertTrue(overlay._q.empty())
        self.agent.bus.publish("risk_contact", {"call_opened": True})
        overlay._handle(overlay._q.get_nowait())
        self.assertIsNone(overlay._panel.current_card)
        for state in ("listening", "thinking", "speaking", "idle"):
            self.agent._voice_state(state, "caption")
        self.assertTrue(overlay._q.empty())
        self.agent.bus.publish("risk_call_ended", {})
        self.agent._voice_state("thinking", "New request")
        overlay._handle(overlay._q.get_nowait())
        self.assertEqual(overlay._panel.current_card.voice_state, "thinking")


if __name__ == "__main__":
    unittest.main()
