"""Always-on voice: say "Ally, ..." and the rest of the sentence is handled as a request.

The microphone is cut into utterances (by loudness, against an adaptive noise floor) and each
one is transcribed on this computer with faster-whisper. The exception is an answer to a spoken
yes/no question: voice_confirm transcribes it with Cartesia first when CARTESIA_API_KEY is set.
Utterances that don't start or end with the wake word are dropped, except right after "Ally" on
its own and during a short follow-up window after Ally answers ("a bit bigger").
While Ally is talking, only "Ally, stop" works, so Ally never answers its own voice.

The microphone has one owner. When Ally asks a spoken yes/no question (voice_confirm), it
claims the next utterances instead of opening a second microphone stream: claimed utterances
go to the question, never to wake-word handling, so "yes" is not taken as a new request.
"""

from __future__ import annotations

import importlib
import logging
import queue
import re
import threading
import time
from dataclasses import dataclass

from . import rules
from .voice import RATE, has_whisper, whisper_pcm

log = logging.getLogger("ally.listener")

BLOCK = RATE // 10  # 100 ms of audio per read
# What speech-to-text writes for "Ally" (it often can't know the spelling of a name).
SOUNDS_LIKE = {"ally": ["ally", "allie", "alley", "ali", "aly", "alli", "allee"]}
PREFIX = r"(?:(?:hey|hi|okay|ok|oh|yo)[\s,.!]+)?"
STOP_WORDS = r"(?:stop|quiet|be quiet|shh+|pause|cancel|enough|shut up|silence)"


@dataclass
class Utterance:
    pcm: bytes
    t0: float  # wall-clock start and end (time.time(), the eye tracker's clock too)
    t1: float
    over_speech: bool  # Ally was talking during it, so the microphone heard Ally too
    eager: bool = False  # a first short pause, not yet the end: speech may go on (see Segmenter.eager_silence)


def wake_pattern(words: list[str]) -> str:
    alts = []
    for w in words:
        w = " ".join(str(w).lower().split())
        if w:
            alts.extend(SOUNDS_LIKE.get(w, [w]))
    alts = sorted({re.escape(a).replace(r"\ ", r"[\s,]+") for a in alts}, key=len, reverse=True)
    return "(?:" + "|".join(alts or ["ally"]) + ")"


def split_wake(text: str, words: list[str]) -> tuple[bool, str]:
    """('Hey Ally, make it bigger.', ['ally']) -> (True, 'make it bigger'). Also 'Make it bigger, Ally.'"""
    w = wake_pattern(words)
    t = text.strip()
    m = re.match(rf"^\W*{PREFIX}{w}\b[\s,.!?:;-]*(.*)$", t, re.I | re.S)
    if m:
        return True, m.group(1).strip(" .,!?")
    m = re.match(rf"^(.*?)[\s,]+{w}\W*$", t, re.I | re.S)
    if m and m.group(1).strip(" .,!?"):
        return True, m.group(1).strip(" .,!?")
    return False, t.strip(" .")


def is_stop(text: str, words: list[str]) -> bool:
    """'... Ally, stop ...' anywhere (used while Ally is talking, when the transcript has Ally's voice too)."""
    return re.search(rf"\b{wake_pattern(words)}\W+{STOP_WORDS}\b", text, re.I) is not None


class Segmenter:
    """Turns 100 ms blocks of audio into utterances, using loudness against an adaptive noise floor."""

    def __init__(self, end_silence: float = 0.7, max_seconds: float = 12.0, min_seconds: float = 0.3,
                 preroll_blocks: int = 3, eager_silence: float | None = None):
        self.end_silence, self.max_seconds, self.min_seconds = end_silence, max_seconds, min_seconds
        self.preroll_blocks = preroll_blocks
        # When set, a pause this long also yields the speech so far, marked eager=True, without
        # ending the utterance: a yes/no question can act on a clear "yes" sooner (like Cartesia
        # ink-2's turn.eager_end). If speech goes on, the full utterance still comes at the end.
        self.eager_silence = eager_silence
        self._eager_sent = False
        self.floor = 150.0
        self._pre: list[tuple[bytes, float]] = []
        self._blocks: list[bytes] = []
        self._loud = 0
        self._quiet = 0.0
        self._voiced = 0  # blocks loud enough to be speech
        self._t0 = 0.0
        self._over = False
        self.in_speech = False

    def feed(self, block: bytes, level: float, t: float, speaking: bool, max_seconds: float | None = None):
        """One block (int16 PCM), its RMS level and wall-clock end time. Returns an Utterance or None."""
        start_at, keep_at = max(300.0, self.floor * 3.0), max(200.0, self.floor * 2.0)
        if not self.in_speech:
            if level < start_at:
                self.floor = 0.95 * self.floor + 0.05 * max(level, 30.0)  # learn the room's background noise
            self._pre = (self._pre + [(block, t)])[-self.preroll_blocks - 2:]
            self._loud = self._loud + 1 if level >= start_at else 0
            if self._loud >= 2:  # 200 ms of speech: start, keeping a little audio from before
                self.in_speech = True
                self._blocks = [b for b, _ in self._pre]
                self._t0 = self._pre[0][1] - 0.1
                self._quiet, self._over, self._voiced = 0.0, speaking, 2
            return None
        self._blocks.append(block)
        self._over = self._over or speaking
        if level < keep_at:
            self._quiet += 0.1
        else:
            self._quiet, self._voiced = 0.0, self._voiced + 1
            self._eager_sent = False
        seconds = len(self._blocks) * 0.1
        eager = self.eager_silence
        if (eager and not self._eager_sent and eager - 1e-6 <= self._quiet < self.end_silence
                and self._voiced * 0.1 >= self.min_seconds):
            self._eager_sent = True
            return Utterance(b"".join(self._blocks), self._t0, t - self._quiet, self._over, eager=True)
        if self._quiet >= self.end_silence or seconds >= (max_seconds or self.max_seconds):
            pcm, over, t0 = b"".join(self._blocks), self._over, self._t0
            self.in_speech, self._blocks, self._pre, self._loud = False, [], [], 0
            self._eager_sent = False
            if self._voiced * 0.1 < self.min_seconds:  # a click or a cough, not words
                return None
            return Utterance(pcm, t0, t - self._quiet, over)
        return None


class WakeListener:
    def __init__(self, agent):
        self.agent = agent
        self.status = "off"  # off | starting | listening | error
        self.error = ""
        self.last_heard = ""
        self.awake_until = 0.0  # until then, no wake word is needed
        self._awake_kind = "prompt"  # "prompt": after "Ally" alone (anything goes); "follow": after an answer
        self._follow = False  # extend the follow-up window while Ally answers
        self._ignore_until = 0.0
        self._thread: threading.Thread | None = None
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._queue: queue.Queue = queue.Queue(maxsize=8)
        self._lock = threading.Lock()
        self._claim: queue.Queue | None = None
        self._seg: Segmenter | None = None
        self.claim_eager: float | None = None  # eager pause while a question owns the microphone

    @property
    def cfg(self) -> dict:
        return self.agent.config.data["voice"]

    @property
    def running(self) -> bool:
        return self.status == "listening" and self._thread is not None and self._thread.is_alive()

    def wanted(self) -> bool:
        return bool(self.cfg.get("always_listen")) and self.cfg.get("engine") != "off"

    def available(self) -> tuple[bool, str]:
        if not has_whisper():
            return False, "always listening needs offline speech-to-text: pip install faster-whisper"
        try:
            importlib.import_module("numpy")
            importlib.import_module("sounddevice")
        except Exception:
            return False, "no microphone recorder: pip install sounddevice numpy"
        return True, ""

    # ---------------------------------------------------------------- control
    def apply(self) -> None:
        """Start or stop to match config (called at start and whenever settings change)."""
        if not self.wanted():
            self.stop()
            self.status, self.error = "off", ""
            return
        ok, why = self.available()
        if not ok:
            self.stop()
            self.status, self.error = "error", why
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self.status, self.error = "starting", ""
            self._thread = threading.Thread(target=self._capture, name="listener-mic", daemon=True)
            self._worker = threading.Thread(target=self._process, name="listener-stt", daemon=True)
            self._thread.start()
            self._worker.start()

    def stop(self) -> None:
        self._stop.set()
        for t in (self._thread, self._worker):
            if t is not None and t is not threading.current_thread():
                t.join(timeout=2)
        self._thread = self._worker = None
        if self.status != "error":
            self.status = "off"

    def prompt(self) -> None:
        """Take the next sentence without the wake word (the listen hotkey, or "Ally" on its own)."""
        self.awake_until = time.monotonic() + max(6.0, float(self.cfg.get("follow_up_seconds", 6)))
        self._awake_kind = "prompt"
        self._follow = False
        if self.cfg.get("beep", True):
            self._ignore_until = time.monotonic() + 0.3  # don't hear our own beep
            self.agent.speaker.beep("start", wait=False)
        self.agent.events.add("voice", "Listening...")

    def claim(self) -> queue.Queue | None:
        """Send the next utterances to the caller (a yes/no question) instead of wake-word handling.
        Returns a queue of Utterance, or None if the listener isn't running. Call release() after."""
        if not self.running:
            return None
        q: queue.Queue = queue.Queue(maxsize=8)
        self._claim = q
        return q

    def release(self, q: queue.Queue | None) -> None:
        if q is not None and self._claim is q:
            self._claim = None

    @property
    def claimed(self) -> bool:
        return self._claim is not None

    @property
    def in_speech(self) -> bool:
        """Someone is talking right now (an utterance has started and not ended)."""
        seg = self._seg
        return bool(seg is not None and seg.in_speech)

    def info(self) -> dict:
        return {"status": self.status, "claimed": self.claimed, "error": self.error, "wake_words": list(self.cfg.get("wake_words") or []),
                "awake": time.monotonic() < self.awake_until, "last_heard": self.last_heard}

    # ---------------------------------------------------------------- threads
    def _capture(self) -> None:
        import numpy as np  # type: ignore
        import sounddevice as sd  # type: ignore

        seg = self._seg = Segmenter(end_silence=float(self.cfg.get("silence_seconds", 1.2)) * 0.6)
        speaker = self.agent.speaker
        while not self._stop.is_set():
            try:
                with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=BLOCK) as stream:
                    self.status, self.error = "listening", ""
                    log.info("always listening (wake word: %s)", ", ".join(self.cfg.get("wake_words") or ["ally"]))
                    tail = 0.0
                    while not self._stop.is_set():
                        data, _overflow = stream.read(BLOCK)
                        now, mono = time.time(), time.monotonic()
                        if speaker.speaking:
                            tail = mono + 0.4  # the room still echoes for a moment after Ally stops
                            if self._follow:
                                self.awake_until = mono + float(self.cfg.get("follow_up_seconds", 6))
                        elif self._follow and mono > self.awake_until:
                            self._follow = False
                        level = float(np.sqrt(np.mean(data.astype(np.float32) ** 2)))
                        if mono < self._ignore_until:
                            level = 0.0
                        talking = mono < tail
                        seg.eager_silence = self.claim_eager if self._claim is not None else None
                        utt = seg.feed(data.tobytes(), level, now, talking, max_seconds=3.0 if talking else None)
                        if utt is not None:
                            try:
                                self._queue.put_nowait(utt)
                            except queue.Full:
                                log.warning("speech-to-text is falling behind; dropped a sentence")
            except Exception as e:  # no microphone, unplugged, or blocked in privacy settings
                if self._stop.is_set():
                    break
                self.status = "error"
                self.error = (f"microphone problem: {e}. Check Settings > Privacy > Microphone "
                              "(let desktop apps use it).")[:300]
                log.warning("listener: %s", self.error)
                self._stop.wait(5)  # try again in a moment (e.g. a headset was plugged in)
        if self.status == "listening":
            self.status = "off"

    def _process(self) -> None:
        while not self._stop.is_set():
            try:
                utt = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            claim = self._claim
            if utt.eager and claim is None:
                continue  # only a yes/no question wants early pieces
            if claim is not None:
                try:
                    claim.put_nowait(utt)  # a spoken yes/no question owns this sentence
                except queue.Full:
                    pass
                continue
            try:
                self.handle(utt)
            except Exception:
                log.exception("listener failed on a sentence")

    def transcribe(self, utt: Utterance) -> str:
        words = self.cfg.get("wake_words") or ["ally"]
        # Teaches the spelling of the name. Not a command: Whisper sometimes repeats its prompt on noise.
        prompt = f"Talking to {str(words[0]).title()}, the accessibility assistant."
        return whisper_pcm(utt.pcm, self.cfg.get("language", "en"), self.cfg.get("whisper_model", "base.en"), prompt)

    def handle(self, utt: Utterance) -> str | None:
        """Decide what one utterance means. Returns the request that was run, if any."""
        text = self.transcribe(utt)
        if not text:
            return None
        words = self.cfg.get("wake_words") or ["ally"]
        a = self.agent
        if utt.over_speech:
            if is_stop(text, words):
                a.speaker.stop()
                a.events.add("voice", f"heard: {text}")
                return "stop"
            return None  # most likely Ally's own voice
        log.debug("heard: %s", text)
        woke, rest = split_wake(text, words)
        mono = time.monotonic()
        if woke and not rest:
            self.last_heard = text
            self.prompt()
            return ""
        if not woke:
            if mono >= self.awake_until:
                return None  # not for Ally
            if rules.normalize(rest) in ("", "you", "thank you", "thanks", "bye", "okay", "ok"):
                return None  # what speech-to-text writes for noise
            if self._awake_kind == "follow" and len(rest.split()) > 5 and not rules.match(rest, strict=False):
                return None  # a follow-up is short ("a bit more") or a known request; this is chatter
        self.last_heard = text
        self.awake_until = 0.0
        self._follow = float(self.cfg.get("follow_up_seconds", 6)) > 0
        a.last_utterance = (utt.t0, utt.t1)
        threading.Thread(target=self._run, args=(rest,), name="voice-request", daemon=True).start()
        return rest

    def _run(self, text: str) -> None:
        try:
            self.agent.handle_text(text, source="voice")
        except Exception:
            log.exception("voice request failed: %s", text)
        finally:
            # The follow-up window starts when Ally stops talking (the capture loop extends it till then).
            follow = float(self.cfg.get("follow_up_seconds", 6))
            if follow > 0 and time.monotonic() >= self.awake_until:
                self._awake_kind = "follow"
                self.awake_until = time.monotonic() + follow
