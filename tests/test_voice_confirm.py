"""Spoken offers and yes/no answers (ally/voice_confirm.py) and the Cartesia speech layer."""

import json
import os
import queue
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from helpers import EnvPatch, temp_home

try:  # on main since 7974a9e: keeps tests from zooming or recoloring the real screen
    from helpers import fake_screen
except ImportError:
    def fake_screen(platform):
        return []

from ally import speech, voice_confirm as vcm
from ally.config import ConfigManager, DEFAULTS
from ally.listener import Segmenter, Utterance, WakeListener
from ally.platforms import base
from ally.voice_confirm import VoiceConfirm, classify, rephrase

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fake_cartesia import FakeCartesia  # noqa: E402
from voice_phrases import PHRASES, evaluate, label  # noqa: E402


# ----------------------------------------------------------------------------- fakes
class FakeSpeaker:
    """Records what would be said; 'speaks' for a short, fixed time."""

    def __init__(self, seconds=0.05, enabled=True):
        self.seconds, self.enabled = seconds, enabled
        self.said: list[tuple[str, dict]] = []
        self._gen = 0
        self._until = 0.0
        self.audio_started: dict = {}
        self.rate_override = None
        self.last_engine = "cartesia"
        self.stops = 0
        self.prerendered: list = []

    @property
    def speaking(self):
        return time.monotonic() < self._until

    @property
    def generation(self):
        return self._gen

    def say(self, text, interrupt=True, wait=False, style=None):
        if not self.enabled:
            return False
        self._gen += 1
        self.said.append((text, dict(style or {})))
        self.audio_started[self._gen] = time.perf_counter()
        self._until = time.monotonic() + self.seconds
        if wait:
            time.sleep(self.seconds)
        return True

    def wait(self, timeout=None):
        while self.speaking:
            time.sleep(0.01)

    def stop(self):
        self.stops += 1
        self._gen += 1
        self._until = 0.0

    def beep(self, kind="start", wait=True):
        pass

    def rate(self):
        return self.rate_override or 1.0

    def prerender(self, items):
        self.prerendered = list(items)
        return len(items), ""

    def info(self):
        return {"last_engine": self.last_engine}

    def texts(self):
        return [speech.strip_ssml(t) for t, _ in self.said]


class FakeEars:
    """Each listen gets the next session: [(delay_s, text), ...]. pcm carries the text."""

    def __init__(self, sessions, in_speech=False):
        self.sessions = list(sessions)
        self.opened = 0
        self.closed = 0
        self.in_speech = in_speech

    def __call__(self):
        session = self.sessions.pop(0) if self.sessions else []
        self.opened += 1
        q: queue.Queue = queue.Queue()
        stop = threading.Event()

        def feed():
            start = time.time()
            for item in session:
                delay, text = item[0], item[1]
                eager = len(item) > 2 and item[2]
                if stop.wait(delay):
                    return
                now = time.time()
                q.put(Utterance(text.encode(), now - 0.4, now, False, eager=eager))
            del start

        threading.Thread(target=feed, daemon=True).start()

        def close():
            stop.set()
            self.closed += 1
        return q, close, "test", lambda: self.in_speech


def settings(**confirm):
    cfg = json.loads(json.dumps(DEFAULTS))
    cfg["confirm"].update(dict(listen_seconds=0.6, listen_delay_ms=10, log_latency=False), **confirm)
    return lambda: cfg


def make(sessions=(), speaker=None, **confirm):
    sp = speaker or FakeSpeaker()
    ears = FakeEars(sessions)
    vc = VoiceConfirm(sp, settings(**confirm), ears=ears, transcriber=lambda pcm: pcm.decode())
    return vc, sp, ears


# ----------------------------------------------------------------------------- understanding answers
class ClassifyTests(unittest.TestCase):
    def test_labeled_phrases(self):
        for text, want in PHRASES:
            with self.subTest(text=text):
                self.assertEqual(label(classify(text, "Want me to make this bigger?").kind), want)

    def test_safety_numbers(self):
        r = evaluate()
        self.assertEqual(r["false_yes"], 0, r["mistakes"])  # never act on something that wasn't a yes
        self.assertEqual(r["yes_heard_as_no"], 0)

    def test_echoing_the_action_is_yes(self):
        q = "This pop-up may be a scam." + speech.pause(400) + "Close it?"
        self.assertEqual(classify("Close it.", q).kind, "YES")
        self.assertEqual(classify("yes disconnect them", "Disconnect them?").kind, "YES")
        self.assertEqual(classify("close it", "").kind, "AMBIGUOUS")  # without the question it means nothing
        self.assertEqual(classify("don't close it", "Close this page?").kind, "NO")

    def test_rephrase_uses_other_words(self):
        for q in ["Want me to make this bigger?", "Want me to turn on high contrast?", "Should I read this page?",
                  "Close it?", "Disconnect them?", "Increase the text size?"]:
            with self.subTest(q=q):
                r = rephrase(q)
                self.assertNotEqual(r.lower(), q.lower())
                self.assertTrue(r.endswith("?"), r)
                self.assertLessEqual(len(r.split()), 10)
        self.assertEqual(rephrase("Want me to turn on high contrast?"), "Should I turn on high contrast for you?")

    def test_rephrase_uses_the_last_question(self):
        # The Coordinator's prompts can lead with a question of their own.
        q = "Having trouble clicking? Want me to make the buttons bigger?"
        self.assertEqual(rephrase(q), "Should I make the buttons bigger for you?")
        self.assertEqual(vcm.action_word(q), "make")

    def test_risk_answers_mean_the_protective_action(self):
        remote = "Someone may be trying to control your computer. Disconnect them?"
        scam = "This pop-up may be a scam. Close it?"
        page = "This website may not be safe. Close this page?"
        self.assertEqual(classify("Stop them!", remote, protective=True).kind, "YES")
        self.assertEqual(classify("stop it", scam, protective=True).kind, "YES")
        self.assertEqual(classify("get me out of here", page, protective=True).kind, "YES")
        for unclear in ("stop", "cancel it", "go back"):  # may mean either: re-ask, never a silent no
            with self.subTest(text=unclear):
                self.assertEqual(classify(unclear, page, protective=True).kind, "AMBIGUOUS")
        self.assertNotEqual(classify("don't stop it", scam, protective=True).kind, "YES")
        self.assertEqual(classify("no", scam, protective=True).kind, "NO")
        self.assertEqual(classify("stop it", "Want me to make this bigger?").kind, "NO")  # offers unchanged


# ----------------------------------------------------------------------------- asking
class AskTests(unittest.TestCase):
    def test_yes(self):
        vc, sp, ears = make([[(0.05, "Yeah, sure.")]])
        a = vc.ask("Want me to make this bigger?")
        self.assertEqual((a.value, a.by, a.intent), ("yes", "voice", "YES"))
        self.assertEqual(a, "yes")  # compares like the contract's plain string
        self.assertEqual(vc.ask_yes_no.__name__, "ask_yes_no")
        self.assertEqual(sp.texts()[0], "Want me to make this bigger?")
        self.assertEqual(sp.said[0][1].get("emotion"), "calm")
        self.assertEqual(ears.closed, ears.opened)
        self.assertIn("total_ms", a.latency)
        self.assertIn("speech_end_to_answer_ms", a.latency)

    def test_no_says_no_problem(self):
        vc, sp, _ = make([[(0.05, "No thanks.")]])
        self.assertEqual(vc.ask_yes_no("Want me to make this bigger?"), "no")
        time.sleep(0.05)
        self.assertIn(vc_prompt("no_reply"), sp.texts())

    def test_silence_is_timeout_and_never_reasked(self):
        vc, sp, ears = make([[]])
        a = vc.ask("Want me to make this bigger?")
        self.assertEqual((a.value, a.intent), ("timeout", "SILENCE"))
        self.assertEqual(len(sp.said), 1)  # said once; silence never repeats the question
        self.assertEqual(ears.opened, 1)

    def test_noise_is_timeout(self):
        vc, _, _ = make([[(0.05, "Thank you.")]])
        self.assertEqual(vc.ask("Want me to make this bigger?").intent, "NOISE")

    def test_unclear_reasks_in_other_words_then_yes(self):
        vc, sp, _ = make([[(0.05, "Hmm, maybe?")], [(0.05, "Yes.")]])
        a = vc.ask("Want me to make this bigger?")
        self.assertEqual((a.value, a.attempts), ("yes", 2))
        self.assertIn("Sorry, I missed that. Should I make the text larger for you?", sp.texts())
        self.assertEqual(sp.said[1][1].get("emotion"), "sympathetic")

    def test_unclear_twice_goes_to_buttons(self):
        vc, sp, _ = make([[(0.05, "Yeah, no.")], [(0.05, "I guess.")]])
        a = vc.ask("Want me to make this bigger?")
        self.assertEqual((a.value, a.intent), ("timeout", "AMBIGUOUS"))
        self.assertEqual(sp.texts()[-1], vc_prompt("buttons"))  # "sorry" was said once, on the re-ask
        self.assertEqual(sum("Sorry" in t for t in sp.texts()), 1)

    def test_no_reask_when_turned_off(self):
        vc, sp, ears = make([[(0.05, "maybe")]], reask=False)
        self.assertEqual(vc.ask("Want me to make this bigger?").value, "timeout")
        self.assertEqual(ears.opened, 1)
        self.assertEqual(sp.texts()[-1], vc_prompt("missed_final"))

    def test_slower_replays_slower_and_keeps_the_habit(self):
        state = {}

        class St:
            def set(self, k, v):
                state[k] = v
        vc, sp, _ = make([[(0.05, "Slower, please.")], [(0.05, "Yes.")]])
        vc.state = St()
        self.assertEqual(vc.ask("Want me to make this bigger?").value, "yes")
        self.assertLess(sp.said[1][1]["speed"], sp.said[0][1]["speed"])
        self.assertNotIn("cache", sp.said[1][1])
        self.assertAlmostEqual(state["speech_rate"], 0.9)

    def test_undo_counts_as_no(self):
        vc, _, _ = make([[(0.05, "Put it back.")]])
        a = vc.ask("Want me to make this bigger?")
        self.assertEqual((a.value, a.intent), ("no", "UNDO"))

    def test_waits_for_more_after_a_filler(self):
        vc, _, _ = make([[(0.05, "Um."), (0.2, "Yes.")]])
        self.assertEqual(vc.ask("Want me to make this bigger?").value, "yes")

    def test_answer_already_under_way_at_the_deadline_is_heard_out(self):
        vc, _, ears = make([[(0.9, "Yes, go ahead.")]], listen_seconds=0.5)
        ears.in_speech = True  # still talking when the window closes
        self.assertEqual(vc.ask("Want me to make this bigger?").value, "yes")

    def test_eager_piece_accepted_only_when_clear(self):
        vc, _, _ = make([[(0.05, "Yes.", True)]])
        a = vc.ask("Want me to make this bigger?")
        self.assertEqual(a.value, "yes")
        self.assertTrue(a.latency.get("eager"))
        vc, _, _ = make([[(0.05, "Yeah", True), (0.1, "Yeah, no.")]], reask=False)  # a bare opener waits
        self.assertEqual(vc.ask("Want me to make this bigger?").intent, "AMBIGUOUS")
        vc, _, _ = make([[(0.05, "No.", True)]])  # a clear no may end early: it changes nothing
        self.assertTrue(vc.ask("Want me to make this bigger?").latency.get("eager"))
        vc, _, _ = make([[(0.05, "Mm-hmm.", True), (0.1, "Mm-hmm.")]])  # low confidence waits for the end
        a = vc.ask("Want me to make this bigger?")
        self.assertEqual(a.value, "yes")
        self.assertFalse(a.latency.get("eager"))

    def test_button_wins_and_stops_speech(self):
        vc, sp, _ = make([[(0.5, "No.")]], speaker=FakeSpeaker(seconds=1.0))
        threading.Timer(0.15, lambda: vc.answer("yes", by="button")).start()
        t0 = time.monotonic()
        a = vc.ask("Want me to make this bigger?")
        self.assertEqual((a.value, a.by), ("yes", "button"))
        self.assertLess(time.monotonic() - t0, 0.8)
        self.assertGreaterEqual(sp.stops, 1)
        self.assertFalse(vc.answer("no"))  # nothing open any more

    def test_stopped_mid_question_does_not_listen(self):
        vc, sp, ears = make([[(0.05, "Yes.")]], speaker=FakeSpeaker(seconds=1.0))
        threading.Timer(0.15, sp.stop).start()  # what the listener does on "Ally, stop"
        t0 = time.monotonic()
        a = vc.ask("Want me to make this bigger?")
        self.assertEqual((a.value, a.intent), ("timeout", "STOPPED"))
        self.assertEqual(ears.opened, 0)
        self.assertLess(time.monotonic() - t0, 0.8)

    def test_other_speech_during_a_question_does_not_stop_it(self):
        vc, sp, ears = make([[(0.05, "Yes.")]], speaker=FakeSpeaker(seconds=0.3))
        threading.Timer(0.1, lambda: sp.say("I'll make this bigger, like last time.")).start()
        self.assertEqual(vc.ask("Want me to make this bigger?").value, "yes")
        self.assertEqual(ears.opened, 1)

    def test_voice_answer_then_button_is_ignored(self):
        vc, _, _ = make([[(0.05, "No.")]])
        a = vc.ask("Want me to make this bigger?")
        self.assertEqual(a.by, "voice")
        self.assertFalse(vc.answer("yes"))

    def test_bad_answer_value(self):
        vc, _, _ = make()
        with self.assertRaises(Exception):
            vc.answer("perhaps")

    def test_new_question_supersedes_old(self):
        vc, _, _ = make([[(2.0, "Yes.")], [(0.05, "No.")]], listen_seconds=3)
        first = {}
        t = threading.Thread(target=lambda: first.update(a=vc.ask("Want me to make this bigger?")))
        t.start()
        time.sleep(0.2)
        second = vc.ask("Want me to read this to you?")
        t.join(3)
        self.assertEqual(first["a"].intent, "SUPERSEDED")
        self.assertEqual(second.value, "no")

    def test_disabled_and_empty(self):
        vc, sp, _ = make(enabled=False)
        self.assertEqual(vc.ask("Want me to make this bigger?").intent, "DISABLED")
        self.assertEqual(sp.said, [])
        vc, _, _ = make()
        self.assertEqual(vc.ask("  ").intent, "EMPTY_PROMPT")

    def test_no_microphone_waits_for_buttons(self):
        sp = FakeSpeaker()
        vc = VoiceConfirm(sp, settings(), ears=lambda: None, transcriber=lambda p: "")
        threading.Timer(0.2, lambda: vc.answer("no", by="button")).start()
        a = vc.ask("Want me to make this bigger?")
        self.assertEqual((a.value, a.by), ("no", "button"))
        vc2 = VoiceConfirm(sp, settings(), ears=lambda: None, transcriber=lambda p: "")
        self.assertEqual(vc2.ask("Want me to make this bigger?").intent, "NO_MIC")

    def test_speech_to_text_failure(self):
        from ally.registry import ToolError

        def broken(pcm):
            raise ToolError("Can't reach Cartesia")
        vc = VoiceConfirm(FakeSpeaker(), settings(), ears=FakeEars([[(0.05, "yes")]]), transcriber=broken)
        a = vc.ask("Want me to make this bigger?")
        self.assertEqual((a.value, a.intent), ("timeout", "NO_STT"))

    def test_speech_off_still_listens(self):
        vc, _, _ = make([[(0.05, "Yes.")]], speaker=FakeSpeaker(enabled=False))
        self.assertEqual(vc.ask("Want me to make this bigger?").value, "yes")

    def test_latency_log(self):
        d = Path(tempfile.mkdtemp())
        vc = VoiceConfirm(FakeSpeaker(), settings(log_latency=True), ears=FakeEars([[(0.05, "yes")]]),
                          transcriber=lambda p: p.decode(), log_dir=d)
        vc.ask("Want me to make this bigger?")
        row = json.loads((d / "voice_latency.jsonl").read_text().splitlines()[0])
        self.assertEqual(row["answer"], "yes")
        self.assertIn("speech_end_to_answer_ms", row)
        self.assertNotIn("transcript", row)  # what people say isn't written to disk
        self.assertEqual(vc.info()["latency"]["last"][0]["answer"], "yes")


def vc_prompt(name):
    return speech.strip_ssml(vcm.PROMPTS[name])


# ----------------------------------------------------------------------------- offers and risks
class FakeOffer:
    def __init__(self, prompt="Want me to make this bigger?"):
        self.prompt, self.timeout, self.answer, self.by = prompt, 20, None, None

    def resolve(self, answer, by):
        if self.answer is None:
            self.answer, self.by = answer, by
            return True
        return False


class OfferAndRiskTests(unittest.TestCase):
    def test_offer_resolved_by_voice(self):
        vc, _, _ = make([[(0.05, "Sure.")]])
        offer = FakeOffer()
        a = vc.confirm_offer(offer)
        self.assertEqual((offer.answer, offer.by, a.value), ("yes", "voice", "yes"))

    def test_offer_resolved_elsewhere_stops_listening(self):
        vc, _, _ = make([[(1.5, "No.")]], listen_seconds=3)
        offer = FakeOffer()
        threading.Timer(0.2, lambda: offer.resolve("yes", "button")).start()
        t0 = time.monotonic()
        t = vc.confirm_offer(offer, background=True)
        t.join(3)
        self.assertEqual((offer.answer, offer.by), ("yes", "button"))
        self.assertLess(time.monotonic() - t0, 1.0)

    def test_button_answer_resolves_the_offer(self):
        vc, _, _ = make([[(1.5, "No.")]], listen_seconds=3)
        offer = FakeOffer()
        threading.Timer(0.2, lambda: vc.answer("yes", by="cli")).start()  # run.py answer yes
        a = vc.confirm_offer(offer)
        self.assertEqual((a.by, offer.answer, offer.by), ("cli", "yes", "cli"))

    def test_spoken_stop_them_answers_a_risk_question(self):
        vc, _, _ = make([[(0.05, "Stop them!")]])
        self.assertEqual(vc.warn({"kind": "remote_access", "severity": "high"}).value, "yes")

    def test_offer_as_dict_and_silence(self):
        vc, _, _ = make([[]])
        got = []
        a = vc.confirm_offer({"prompt": "Want me to read this to you?", "resolve": lambda v, by: got.append(v)})
        self.assertEqual((a.value, got), ("timeout", []))  # silence never resolves an offer

    def test_scam_warning(self):
        vc, sp, _ = make([[(0.05, "Close it.")]])
        a = vc.warn({"kind": "scam_popup", "severity": "high"})
        self.assertEqual(a.value, "yes")
        raw, style = sp.said[0]
        self.assertIn("<break", raw)  # a pause before the question (Cartesia)
        self.assertEqual(speech.strip_ssml(raw), "This pop-up may be a scam. Close it?")
        self.assertEqual(style["emotion"], "calm")
        self.assertLess(style["speed"], 0.9)

    def test_low_risk_only_says(self):
        vc, sp, ears = make()
        self.assertIsNone(vc.warn({"kind": "payment_field", "severity": "low"}))
        time.sleep(0.05)
        self.assertEqual(sp.texts(), ["This page is asking for card details."])
        self.assertEqual(ears.opened, 0)

    def test_risk_questions_dont_invite_no_words(self):
        # "Stop it?" would make "stop it" a no; "Go back?" would make "go back" an undo.
        for statement, question in vcm.RISK_TEXT.values():
            with self.subTest(question=question):
                echo = vcm.normalize(question.rstrip("?"))
                self.assertEqual(classify(echo, question).kind, "YES", echo)
                self.assertLessEqual(len((statement + " " + question).split()), 14)

    def test_every_prompt_is_short(self):
        for name, text in vcm.PROMPTS.items():
            with self.subTest(name=name):
                self.assertLessEqual(len(text.split()), 10)

    def test_prerender_set(self):
        vc, sp, _ = make()
        vc.register_prompts(["Want me to turn on high contrast?"])
        vc.prerender()
        texts = [speech.strip_ssml(t) for t, _ in sp.prerendered]
        self.assertIn("Want me to turn on high contrast?", texts)
        self.assertIn("This pop-up may be a scam. Close it?", texts)
        self.assertTrue(all(st.get("cache") for _, st in sp.prerendered))


# ----------------------------------------------------------------------------- the speech layer
class RecPlatform(base.Platform):
    name = "linux"

    def __init__(self):
        self.tts, self.wavs = [], []

    def tts_start(self, text, rate, voice):
        self.tts.append((text, rate))
        return _Done()

    def play_wav_start(self, path):
        self.wavs.append(Path(path).read_bytes()[:4])
        return _Done()


class _Done:
    def wait(self, timeout=None):
        return 0

    def poll(self):
        return 0

    def terminate(self):
        pass

    def kill(self):
        pass


class CartesiaSpeechTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = FakeCartesia()
        cls.old_url = speech.CARTESIA_URL
        speech.CARTESIA_URL = cls.fake.url
        cls.env = EnvPatch(CARTESIA_API_KEY="sk_car_test")
        cls.env.__enter__()

    @classmethod
    def tearDownClass(cls):
        speech.CARTESIA_URL = cls.old_url
        cls.env.__exit__()
        cls.fake.close()

    def setUp(self):
        self.fake.set(tts="ok", stt="ok", reject_models=[], reject_emotion=False, tts_latency=0)
        self.fake.reset()
        speech._voice_names.clear()
        speech._voice_misses.clear()

    def speaker(self, cache=None, **cfg):
        c = json.loads(json.dumps(DEFAULTS["speech"]))
        c.update({"cartesia_voice_id": "Tessa", "cartesia_timeout_seconds": 2, **cfg})
        plat = RecPlatform()
        return speech.Speaker(plat, lambda: c, cache_dir=cache), plat

    def test_request_shape_and_voice_name(self):
        s, plat = self.speaker()
        s.say("Want me to make this bigger?", wait=True, style={"emotion": "calm", "speed": 0.9})
        body = self.fake.tts_requests[0]
        self.assertEqual(body["model_id"], "sonic-3.6")
        self.assertEqual(body["voice"], {"mode": "id", "id": "11111111-1111-4111-8111-111111111111"})
        self.assertEqual(body["generation_config"], {"speed": 0.9, "emotion": "calm"})
        self.assertEqual(body["output_format"]["container"], "wav")
        self.assertEqual((plat.wavs, plat.tts), ([b"RIFF"], []))
        self.assertEqual(s.last_engine, "cartesia")

    def test_emotion_rejected_retries_plain(self):
        self.fake.set(reject_emotion=True)
        s, plat = self.speaker()
        s.say("Careful." + speech.pause(300) + "Close it?", wait=True, style={"emotion": "calm"})
        self.assertEqual(len(self.fake.tts_requests), 2)
        self.assertNotIn("emotion", self.fake.tts_requests[1]["generation_config"])
        self.assertNotIn("<break", self.fake.tts_requests[1]["transcript"])
        self.assertEqual(plat.wavs, [b"RIFF"])

    def test_model_fallback_and_memory(self):
        self.fake.set(reject_models=["sonic-3.6"])
        s, _ = self.speaker()
        s.say("One.", wait=True)
        s.say("Two.", wait=True)
        models = [b["model_id"] for b in self.fake.tts_requests]
        self.assertEqual(models, ["sonic-3.6", "sonic-3", "sonic-3"])  # the bad model is skipped afterwards
        self.assertEqual(s.model_in_use, "sonic-3")

    def test_down_falls_back_to_os_voice_without_tags(self):
        self.fake.set(tts="down")
        s, plat = self.speaker()
        s.say("This page may be a scam." + speech.pause(400) + "Close it?", wait=True, style={"speed": 0.8})
        self.assertEqual(plat.tts, [("This page may be a scam. Close it?", 0.8)])
        self.assertEqual(s.last_engine, "system")
        self.assertIn("503", s.fallback_reason)

    def test_circuit_breaker_skips_waiting(self):
        self.fake.set(tts="hang")
        s, plat = self.speaker(cartesia_timeout_seconds=1)
        t0 = time.monotonic()
        s.say("First.", wait=True)
        first = time.monotonic() - t0
        t0 = time.monotonic()
        s.say("Second.", wait=True)
        second = time.monotonic() - t0
        self.assertGreater(first, 0.9)
        self.assertLess(second, 0.3)
        self.assertEqual(len(plat.tts), 2)
        self.assertGreater(s.cartesia_down, 0)
        self.fake.set(tts="ok")

    def test_prompt_cache_plays_offline(self):
        d = tempfile.mkdtemp()
        s, plat = self.speaker(cache=d)
        st = {"emotion": "calm", "speed": 0.9}
        n, err = s.prerender([("Want me to make this bigger?", st)])
        self.assertEqual((n, err), (1, ""))
        self.fake.set(tts="down")
        s2, plat2 = self.speaker(cache=d)  # a fresh start: the cache is read from disk
        s2.say("Want me to make this bigger?", wait=True, style=dict(st, cache=True))
        self.assertEqual((s2.last_engine, plat2.tts, plat2.wavs), ("cartesia-cache", [], [b"RIFF"]))
        s2.say("Something new.", wait=True)  # not cached: the OS voice
        self.assertEqual(plat2.tts[0][0], "Something new.")

    def test_page_text_is_never_cached(self):
        d = tempfile.mkdtemp()
        s, _ = self.speaker(cache=d)
        s.say("Your bank balance is 1,234 dollars.", wait=True)
        self.assertEqual(os.listdir(d), [])

    def test_auto_engine(self):
        s, _ = self.speaker(engine="auto")
        self.assertEqual(s.engine(), "cartesia")
        s2, _ = self.speaker(engine="auto", cartesia_voice_id="")
        self.assertEqual(s2.engine(), "system")
        with EnvPatch(CARTESIA_API_KEY=None):
            self.assertEqual(s.engine(), "system")

    def test_unknown_voice_name(self):
        with self.assertRaises(speech.CartesiaError):
            speech.resolve_voice("Nobody")
        self.assertEqual(speech.resolve_voice("leo"), "22222222-2222-4222-8222-222222222222")

    def test_unknown_voice_name_is_not_looked_up_every_sentence(self):
        before = self.fake.voice_requests
        for _ in range(3):
            with self.assertRaises(speech.CartesiaError):
                speech.resolve_voice("Nobody")
        self.assertEqual(self.fake.voice_requests - before, 1)

    def test_partial_fallback_keeps_the_style_speed(self):
        s, plat = self.speaker()
        calls = []
        real = s.render

        def render(piece, cfg=None, style=None):
            calls.append(piece)
            if len(calls) > 1:
                raise speech.CartesiaError("Can't reach Cartesia")
            return real(piece, cfg, style)
        s.render = render
        s.say("First sentence here. " + "word " * 120 + "end.", wait=True, style={"speed": 0.8})
        self.assertEqual(len(plat.wavs), 1)
        self.assertEqual(plat.tts[0][1], 0.8)

    def test_voice_list(self):
        names = [v["name"] for v in speech.cartesia_voices()]
        self.assertEqual(names, ["Tessa", "Leo"])

    def test_wpm(self):
        wav = speech.cartesia_tts("one two three four five six", "11111111-1111-4111-8111-111111111111")
        secs = speech.wav_seconds(wav)
        self.assertGreater(secs, 1.5)
        self.assertGreater(speech.words_per_minute("one two three four five six", secs), 100)

    def test_stt_through_fake(self):
        from ally import voice

        self.fake.set(stt_script=[{"seconds": 0.0, "text": "yes"}])
        self.assertEqual(voice.cartesia_stt(voice.wav_bytes(b"\0\0" * 1600)), "yes")


# ----------------------------------------------------------------------------- microphone plumbing
class SegmenterEagerTests(unittest.TestCase):
    def test_eager_piece_then_full(self):
        seg = Segmenter(end_silence=0.8, eager_silence=0.4)
        out, t = [], 0.0
        levels = [50] * 5 + [4000] * 6 + [50] * 4 + [4000] * 3 + [50] * 9
        for lv in levels:
            t += 0.1
            u = seg.feed(b"\0\0" * 1600, lv, t, False)
            if u:
                out.append(u)
        self.assertEqual([u.eager for u in out], [True, True, False])  # a pause, more speech, the end
        self.assertGreater(len(out[-1].pcm), len(out[0].pcm))

    def test_no_eager_by_default(self):
        seg = Segmenter(end_silence=0.8)
        out = [seg.feed(b"\0\0", lv, i * 0.1, False) for i, lv in enumerate([50] * 3 + [4000] * 5 + [50] * 10)]
        self.assertEqual([u.eager for u in out if u], [False])


class ListenerClaimTests(unittest.TestCase):
    def test_claimed_utterances_skip_wake_handling(self):
        class A:
            config = type("C", (), {"data": {"voice": {"always_listen": False}}})()
        lst = WakeListener(A())
        handled = []
        lst.handle = lambda u: handled.append(u)
        lst.status, lst._thread = "listening", threading.current_thread()
        q = lst.claim()
        self.assertIsNotNone(q)
        lst._stop.clear()
        worker = threading.Thread(target=lst._process, daemon=True)
        worker.start()
        u = Utterance(b"yes", time.time(), time.time(), False)
        lst._queue.put(u)
        self.assertIs(q.get(timeout=2), u)
        lst.release(q)
        lst._queue.put(Utterance(b"x", 0, 0, False, eager=True))  # eager pieces are dropped when unclaimed
        lst._queue.put(Utterance(b"ally zoom in", 0, 0, False))
        time.sleep(0.8)
        lst._stop.set()
        worker.join(2)
        self.assertEqual([h.pcm for h in handled], [b"ally zoom in"])


# ----------------------------------------------------------------------------- config and the agent
class ConfigTests(unittest.TestCase):
    def test_new_keys_validate(self):
        cm = ConfigManager(Path(tempfile.mkdtemp()) / "config.toml")
        cfg, errors, _ = cm.check_text('[confirm]\nlisten_seconds = 8\neager_silence_ms = 450\n'
                                       '[speech]\nengine = "auto"\ncartesia_fallback_model = "sonic-3"\n')
        self.assertEqual(errors, [])
        _, errors, _ = cm.check_text('[confirm]\nlisten_seconds = 99\n')
        self.assertTrue(errors)
        _, errors, _ = cm.check_text('[speech]\nengine = "loud"\n')
        self.assertTrue(errors)

    def test_shipped_config_matches_defaults(self):
        from ally.paths import CODE_ROOT

        cm = ConfigManager(CODE_ROOT / "config.toml")
        cfg, errors, warnings = cm.check_text(cm.read_text())
        self.assertEqual(errors, [])
        self.assertEqual(cfg["confirm"], DEFAULTS["confirm"])


class AgentVoiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import socket

        from ally.agent import Agent

        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        cls.port = s.getsockname()[1]
        s.close()
        cls.env = EnvPatch(ANTHROPIC_API_KEY=None, CARTESIA_API_KEY=None)
        cls.env.__enter__()
        cls.agent = Agent(temp_home(f"[agent]\nport = {cls.port}\nannounce_start = false\n"), start_hotkeys=False)
        fake_screen(cls.agent.platform)
        cls.agent.start()

    @classmethod
    def tearDownClass(cls):
        cls.agent.close()
        cls.env.__exit__()
        vcm.install(None)

    def post(self, path, body):
        import urllib.request

        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=json.dumps(body).encode(),
                                     method="POST", headers={"X-Ally-Token": self.agent.token,
                                                             "Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=20) as r:
            return json.loads(r.read())

    def test_installed_module_functions(self):
        self.assertIs(vcm.current(), self.agent.confirm)
        self.assertFalse(vcm.say("hello"))  # speech is off in tests

    def test_status_and_answer_endpoint(self):
        self.assertIn("confirm", self.agent.status())
        self.assertEqual(self.post("/api/voice/answer", {"answer": "yes"}), {"ok": False, "answered": False})

    def test_ask_endpoint_answered_by_button(self):
        self.agent.confirm._ears = FakeEars([[]])
        self.agent.confirm._transcriber = lambda p: ""
        threading.Timer(0.3, lambda: self.post("/api/voice/answer", {"answer": "no", "by": "dashboard"})).start()
        out = self.post("/api/voice/ask", {"prompt": "Want me to make this bigger?"})
        self.assertEqual((out["value"], out["by"]), ("no", "dashboard"))

    def test_tools_registered(self):
        names = {t["name"] for t in self.agent.tools_info()}
        self.assertTrue({"ask_yes_no", "answer_question", "warn_risk", "list_voices"} <= names)
        ai = {t["name"] for t in self.agent.tools_info() if t.get("ai")}
        self.assertIn("ask_yes_no", ai)
        self.assertNotIn("warn_risk", ai)


if __name__ == "__main__":
    unittest.main()
