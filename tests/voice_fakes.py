"""Shared fakes for the voice tests: a speaker, scripted microphone sessions and an Offer."""

import json
import queue
import threading
import time

from ally import speech, voice_confirm as vcm
from ally.config import DEFAULTS
from ally.listener import Utterance
from ally.voice_confirm import VoiceConfirm


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
            for item in session:
                delay, text = item[0], item[1]
                eager = len(item) > 2 and item[2]
                if stop.wait(delay):
                    return
                now = time.time()
                q.put(Utterance(text.encode(), now - 0.4, now, False, eager=eager))

        threading.Thread(target=feed, daemon=True).start()

        def close():
            stop.set()
            self.closed += 1
        return q, close, "test", lambda: self.in_speech


class FakeOffer:
    def __init__(self, prompt="Want me to make this bigger?"):
        self.prompt, self.timeout, self.answer, self.by = prompt, 20, None, None

    def resolve(self, answer, by):
        if self.answer is None:
            self.answer, self.by = answer, by
            return True
        return False


def settings(**confirm):
    cfg = json.loads(json.dumps(DEFAULTS))
    cfg["confirm"].update(dict(listen_seconds=0.6, listen_delay_ms=10, log_latency=False), **confirm)
    return lambda: cfg


def make(sessions=(), speaker=None, **confirm):
    sp = speaker or FakeSpeaker()
    ears = FakeEars(sessions)
    vc = VoiceConfirm(sp, settings(**confirm), ears=ears, transcriber=lambda pcm: pcm.decode())
    return vc, sp, ears


def vc_prompt(name):
    return speech.strip_ssml(vcm.PROMPTS[name])
