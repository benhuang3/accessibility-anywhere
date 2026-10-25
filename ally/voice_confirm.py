"""Spoken offers and yes/no confirmation: Ally speaks first, then listens for the answer.

The Brain talks to voice through two calls (Team Roles & Interfaces):

    say(text)                -> speak, don't wait for a reply
    ask_yes_no(prompt)       -> "yes" | "no" | "timeout"

Both run on Cartesia (Sonic for the voice, Ink for the answer) and fall back on their own:
Cartesia model -> fallback model -> the computer's built-in voice for speaking, and Cartesia
speech-to-text -> offline faster-whisper for listening. If no microphone or speech-to-text works,
the question is still spoken and the on-screen buttons decide.

Safety rules (never broken):
- Silence or an unclear answer is never "yes". Unclear gets one re-ask in different words, then
  "You can tap Yes or No".
- The first answer wins. A button press (answer(), or the Offer being resolved elsewhere) stops
  the question at once, even mid-sentence.
- Half-duplex: the microphone answer window opens only after Ally stops talking, and audio heard
  while Ally was talking is thrown away, so Ally never answers its own question.
- One microphone owner: when the always-on listener is running, the question borrows its
  microphone (listener.claim) instead of opening a second stream.

Integration with the foundation contracts (ally/contracts.py, when merged): pass an Offer to
confirm_offer(offer). It reads offer.prompt, asks, and calls offer.resolve("yes" | "no", by="voice")
unless something else resolved it first.

Try it without the agent:  python -m ally.voice_confirm say "Want me to make this bigger?"
                            python -m ally.voice_confirm ask "Want me to make this bigger?"
                            python -m ally.voice_confirm classify "uh, yeah sure"
                            python -m ally.voice_confirm audition Tessa Leo Marian Dana
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import statistics
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from .registry import ToolError
from .speech import CartesiaError, cartesia_tts, pause, resolve_voice, strip_ssml, wav_seconds, words_per_minute

log = logging.getLogger("ally.voice_confirm")

YES, NO, TIMEOUT = "yes", "no", "timeout"
ANSWERS = (YES, NO, TIMEOUT)

# ---------------------------------------------------------------------------- what Ally says
# Short (10 words or fewer), one idea, plain words. See "Research: Voice" > Prompt set.
PROMPTS = {
    "offer": "Want me to make this bigger?",
    "done": "Done. Say undo to put it back.",
    "no_reply": "No problem. I'll leave it as is.",
    "missed": "Sorry, I missed that.",
    "missed_final": "Sorry, I missed that. You can tap Yes or No.",
    "buttons": "You can tap Yes or No.",  # after a re-ask that already said sorry
    "read_start": "Sure. Here it is.",
    "undone": "Okay, I put it back.",
}

# Risk kinds from the Risk contract -> (what is happening, the protective question or None).
# "Yes" always means "take the protective action". Never reads page text out loud: a scam page's
# words are exactly what the user shouldn't be pushed by.
RISK_TEXT = {
    "scam_popup": ("This pop-up may be a scam.", "Close it?"),
    "untrusted_site": ("This website may not be safe.", "Close this page?"),
    "remote_access": ("Someone may be trying to control your computer.", "Disconnect them?"),
    "payment_field": ("This page is asking for card details.", "Want me to check it first?"),
}
RISK_DEFAULT = ("Something here may not be safe.", "Want me to help?")

_LEADS = re.compile(r"^(?:do you want me to|want me to|would you like me to|should i|shall i|can i)\s+", re.I)


def _lower_first(t: str) -> str:
    return t[:1].lower() + t[1:]


_KNOWN_REPHRASES = {
    "want me to make this bigger?": "Should I make the text larger for you?",
    "want me to read this to you?": "Should I read it out loud for you?",
    "close it?": "Should I close this pop-up for you?",
    "close this page?": "Should I close this page for you?",
    "disconnect them?": "Should I end their connection for you?",
    "want me to check it first?": "Should I look at this page first?",
}


def _last_sentence(text: str) -> str:
    """The question is the last sentence: "Having trouble clicking? Want me to ...?" -> "Want me to ...?"."""
    plain = strip_ssml(text or "").strip()
    return re.split(r"(?<=[.!?])\s+", plain)[-1] if plain else ""


def rephrase(prompt: str) -> str:
    """A different wording of a yes/no question, for the one re-ask (research: rephrase, don't repeat)."""
    last = _last_sentence(prompt)
    known = _KNOWN_REPHRASES.get(last.lower())
    if known:
        return known
    body = last.rstrip("?").strip()
    m = _LEADS.match(body)
    if m:
        action = _lower_first(body[m.end():])
        lead = m.group(0).lower().strip()
        out = (f"Would you like me to {action}?" if lead.startswith(("should", "shall", "can"))
               else f"Should I {action}" + ("" if action.endswith("for you") else " for you") + "?")
        return out
    if last.endswith("?") and body:
        return f"Should I {_lower_first(body)} for you?"
    return f"{last} Just say yes or no." if last else "Just say yes or no."


def action_word(question: str) -> str:
    """The verb a user may echo to say yes: 'Want me to close this page?' -> 'close'."""
    body = _LEADS.sub("", _last_sentence(question).rstrip("?").strip())
    words = normalize(body).split()
    return words[0] if words and words[0] not in _FILLERS and len(words[0]) > 2 else ""


# ---------------------------------------------------------------------------- understanding the answer
@dataclass(frozen=True)
class Intent:
    kind: str  # YES NO UNDO REPEAT SLOWER AMBIGUOUS EMPTY
    confidence: float
    text: str = ""
    matched: tuple = ()

    @property
    def decisive(self) -> bool:
        return self.kind in ("YES", "NO", "UNDO")


_PHRASES = {
    "YES": ["yes", "yeah", "yea", "yep", "yup", "ya", "yah", "yesh", "yess", "yeh", "yeap", "yas", "ye", "yes sir", "yes ma'am", "sure", "sure thing",
            "absolutely", "definitely", "certainly", "of course", "go ahead", "go for it", "do it", "please do",
            "yes please", "sounds good", "that sounds good", "sounds great", "that would be great",
            "that'd be great", "that would be nice", "i would like that", "i'd like that", "i'd love that",
            "i would love that", "why not", "no problem", "no worries", "no problem at all", "i don't mind",
            "don't mind", "i do", "i think so", "that's right", "correct", "affirmative", "let's do it",
            "let's do that", "do that", "make it bigger", "please yes", "you bet", "by all means", "fine by me",
            "works for me", "go on", "aye"],
    "WEAK_YES": ["please", "okay", "ok", "okey", "k", "alright", "all right", "good", "great", "cool", "fine",
                 "right", "uh yes", "mhm yes"],
    "BACKCHANNEL": ["uh huh", "uhhuh", "uh hu", "mm hmm", "mmhmm", "mm hm", "mhm", "mhmm", "hmm hmm", "m hm"],
    "BACKCHANNEL_NEG": ["uh uh", "uh oh", "mm mm", "nuh uh", "unh unh"],
    "NO": ["no", "nope", "nah", "naw", "noo", "nooo", "nop", "nay", "no thanks", "no thank you", "not now", "not right now", "not today",
           "not yet", "leave it", "leave it alone", "leave it as is", "leave it be", "don't", "do not", "dont",
           "don't do it", "don't do that", "please don't", "stop", "cancel", "never mind", "nevermind",
           "not really", "i don't think so", "i'm fine", "i'm good", "i'm okay", "i'm ok", "i am fine",
           "rather not", "i'd rather not", "i would rather not", "no need", "skip", "skip it", "negative",
           "not necessary", "no way", "absolutely not", "of course not", "definitely not", "certainly not",
           "i don't want", "i don't want that", "don't want", "not interested", "no no"],
    "UNDO": ["undo", "undo that", "undo it", "put it back", "go back", "change it back", "revert", "too big",
             "too much", "turn it off", "back to normal", "make it smaller"],
    # "Make it stop": no to an offer, but yes to a risk question, where yes is the protective action.
    "STOP_IT": ["stop it", "stop them", "stop that", "stop this", "make it stop", "get rid of it",
                "get me out", "get me out of here", "block it", "block them", "kick them out", "shut it down"],
    "SLOWER": ["slower", "slow down", "too fast", "speak slower", "talk slower", "more slowly", "slowly",
               "say it slower", "slower please"],
    "REPEAT": ["say that again", "say again", "say it again", "repeat", "repeat that", "what", "what was that",
               "what did you say", "pardon", "pardon me", "sorry", "excuse me", "come again", "huh",
               "i didn't catch that", "one more time", "again", "i can't hear you", "what's that"],
    "HEDGE": ["maybe", "i guess", "i suppose", "not sure", "i'm not sure", "i am not sure", "i don't know",
              "don't know", "dunno", "perhaps", "possibly", "kind of", "sort of", "kinda", "sorta", "either way",
              "whatever", "let me think", "wait", "hold on", "hang on", "just a second", "one second",
              "i'm not certain", "it depends"],
    "SOFT": ["it's fine", "that's fine", "it's okay", "that's okay", "it's ok", "that's ok", "it's alright"],
}
_FILLERS = {"um", "umm", "uh", "uhh", "er", "erm", "hmm", "hm", "ah", "oh", "well", "like", "so", "ally", "allie",
            "alley", "hey", "thanks", "thank", "you", "the", "a", "just", "and", "then", "actually", "it",
            "this", "that", "them", "me", "for", "to"}
# What speech-to-text writes for noise or a cough.
_NOISE = {"", "you", "thank you", "thanks", "thank you very much", "bye", "bye bye", "hmm", "um", "uh", "oh"}


def _phrase_regex() -> tuple[re.Pattern, list[str]]:
    alts = []
    for cat, words in _PHRASES.items():
        for w in words:
            alts.append((len(w), w, cat))
    alts.sort(key=lambda t: -t[0])  # longest first: "no problem" before "no", "i'm okay" before "okay"
    # Escaped outside the f-string: a backslash inside an f-string expression needs Python 3.12.
    escaped = [re.escape(w).replace("\\ ", " ") for _n, w, _c in alts]
    parts = [rf"(?P<g{i}>{e})" for i, e in enumerate(escaped)]
    rx = re.compile(r"(?<![a-z'])(?:" + "|".join(parts) + r")(?![a-z'])")
    return rx, [c for _n, _w, c in alts]


_RX, _RX_CATS = _phrase_regex()


def normalize(text: str) -> str:
    t = (text or "").lower().replace("’", "'").replace("‘", "'")
    t = re.sub(r"[-_/]", " ", t)
    t = re.sub(r"[^a-z' ]+", " ", t)
    t = re.sub(r"\b(?:ally|allie|alley|hey ally)\b", " ", t)
    return " ".join(t.split())


def classify(text: str, question: str = "", protective: bool = False) -> Intent:
    """What a spoken answer to a yes/no question means.

    Rules: phrases are matched longest first, so "no problem" is YES and "I'm okay" is NO. Yes and
    no together ("yeah, no"), or a hedge ("maybe", "I guess"), is AMBIGUOUS. "uh-huh" is a
    low-confidence YES. Echoing the question's action ("close it" for "Close this page?") is YES.
    Anything not understood is AMBIGUOUS; nothing at all is EMPTY.

    protective: the question is a risk warning, where yes means "take the protective action". There
    "stop them" is YES, and a bare "stop", "cancel" or "go back" is AMBIGUOUS (it may mean either).
    """
    norm = normalize(text)
    if norm in _NOISE:
        return Intent("EMPTY", 0.0, text)
    cats: list[str] = []
    matched: list[str] = []
    leftover = norm
    for m in _RX.finditer(norm):
        cats.append(_RX_CATS[int(m.lastgroup[1:])])  # type: ignore[index]
        matched.append(m.group(0))
    leftover = _RX.sub(" ", norm)
    rest = [w for w in leftover.split() if w not in _FILLERS]
    has = set(cats)
    m = tuple(matched)
    if "STOP_IT" in has:
        has.discard("STOP_IT")
        has.add("YES" if protective else "NO")
    if protective and ("UNDO" in has or any(w in ("stop", "cancel") for w in matched)):
        return Intent("AMBIGUOUS", 0.3, text, m)
    if "UNDO" in has:
        return Intent("AMBIGUOUS", 0.4, text, m) if "YES" in has else Intent("UNDO", 0.9, text, m)
    if "HEDGE" in has:
        return Intent("AMBIGUOUS", 0.3, text, m)
    if "YES" in has and "NO" in has:
        return Intent("AMBIGUOUS", 0.3, text, m)
    if "NO" in has:
        return Intent("NO", 0.75 if ("WEAK_YES" in has or "SOFT" in has) else 0.9, text, m)
    if "YES" in has:
        return Intent("YES", 0.9, text, m)
    if "BACKCHANNEL_NEG" in has:
        return Intent("AMBIGUOUS", 0.3, text, m)  # "uh-uh" vs "uh-huh": speech-to-text mixes them up
    if "BACKCHANNEL" in has:
        return Intent("YES", 0.6, text, m)
    if "SLOWER" in has and len(rest) <= 4:
        return Intent("SLOWER", 0.8, text, m)
    if "REPEAT" in has and len(rest) <= 4:
        return Intent("REPEAT", 0.8, text, m)  # "what?" yes; "what about the email..." is something else
    if "SOFT" in has:
        return Intent("AMBIGUOUS", 0.4, text, m)  # "that's fine" can mean "leave it" or "go ahead"
    verb = action_word(question)
    if verb and verb in rest and len(rest) <= 4:
        return Intent("YES", 0.8, text, m + (verb,))  # "close it" / "yes, disconnect them"
    if "WEAK_YES" in has and len(rest) <= 3:
        return Intent("YES", 0.7, text, m)
    if not rest and not cats:
        return Intent("EMPTY", 0.0, text)
    return Intent("AMBIGUOUS", 0.2, text, m)


# A bare opener often starts a reversal ("Yeah... no", "Sure... actually, no"), so it never ends an
# answer early. A clear "no" may: acting on "no" too soon only means doing nothing.
_OPENERS = {"yeah", "yea", "ya", "yah", "yeh", "sure", "okay", "ok", "alright", "all right", "well", "fine"}


def eager_ok(text: str, intent: Intent) -> bool:
    """Can a piece heard after a short pause be taken as the whole answer?"""
    words = normalize(text).split()
    if intent.kind == "NO":
        return intent.confidence >= 0.75 and len(words) <= 4
    if intent.kind == "YES":
        return intent.confidence >= 0.8 and len(words) <= 3 and " ".join(words) not in _OPENERS
    return False


# ---------------------------------------------------------------------------- results
@dataclass
class Answer:
    value: str = TIMEOUT  # yes | no | timeout
    by: str = "none"  # voice | button | dashboard | api | cli | offer | none
    intent: str = ""  # YES NO UNDO AMBIGUOUS SILENCE NOISE NO_MIC NO_STT CANCELLED SUPERSEDED DISABLED BUTTON
    transcript: str = ""
    confidence: float = 0.0
    attempts: int = 0
    prompt: str = ""
    latency: dict = field(default_factory=dict)
    error: str = ""

    def __str__(self) -> str:
        return self.value

    def __eq__(self, other) -> bool:  # Answer == "yes" works, like the contract's plain string
        if isinstance(other, str):
            return self.value == other
        return isinstance(other, Answer) and asdict(self) == asdict(other)

    __hash__ = None  # type: ignore[assignment]

    def to_dict(self) -> dict:
        return asdict(self)


class _Pending:
    """The question being asked right now. The first answer wins."""

    def __init__(self, prompt: str, kind: str, cancel: Callable[[], bool] | None):
        self.prompt, self.kind, self._cancel = prompt, kind, cancel
        self.event = threading.Event()
        self.result: Answer | None = None
        self._lock = threading.Lock()
        self.started = time.time()

    def set(self, answer: Answer) -> bool:
        with self._lock:
            if self.result is not None:
                return False
            self.result = answer
        self.event.set()
        return True

    def done(self) -> bool:
        if self.event.is_set():
            return True
        try:
            if self._cancel is not None and self._cancel():
                self.set(Answer(TIMEOUT, by="offer", intent="CANCELLED", prompt=self.prompt))
                return True
        except Exception:
            log.debug("cancel check failed", exc_info=True)
        return False


def _get(obj, name: str, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


# ---------------------------------------------------------------------------- the voice
class VoiceConfirm:
    """Spoken say / ask_yes_no for the Brain, with all the fallbacks.

    speaker: ally.speech.Speaker. settings(): the full config dict ([confirm], [voice], [speech]).
    listener: optional WakeListener whose microphone is borrowed. ears/transcriber: test hooks.
    """

    def __init__(self, speaker, settings: Callable[[], dict], listener=None, events=None, state=None,
                 log_dir: Path | str | None = None, ears=None, transcriber=None):
        self.speaker = speaker
        self.settings = settings
        self.listener = listener
        self.events = events
        self.state = state
        self.log_path = Path(log_dir) / "voice_latency.jsonl" if log_dir else None
        self._ears = ears
        self._transcriber = transcriber
        self._lock = threading.Lock()
        self._ask_lock = threading.Lock()
        self._pending: _Pending | None = None
        self.mode = "idle"  # idle | speaking | listening (shown in the dashboard as the mic indicator)
        self.history: list[dict] = []
        self.last_error = ""
        self.stt_engine = ""
        self.extra_prompts: list[tuple[str, str]] = []
        self.prerendered = 0

    # ------------------------------------------------------------ config
    @property
    def cfg(self) -> dict:
        return self.settings().get("confirm", {})

    def style(self, style: str | dict | None) -> dict:
        """Named styles -> Cartesia generation settings. Unknown names fall back to calm."""
        if isinstance(style, dict):
            return dict(style)
        c = self.cfg
        emo = c.get("emotion", "calm") or "calm"
        table = {
            "calm": {"emotion": "calm"},
            "offer": {"emotion": emo, "speed": float(c.get("speed", 0.9)), "cache": True},
            "question": {"emotion": emo, "speed": float(c.get("speed", 0.9))},
            "done": {"emotion": "content", "cache": True},
            "sorry": {"emotion": "sympathetic", "speed": float(c.get("speed", 0.9)), "cache": True},
            "warning": {"emotion": "calm", "speed": float(c.get("warning_speed", 0.85)), "cache": True},
            "read": {"emotion": "content"},
            "plain": {},
        }
        return dict(table.get(style or "calm", table["calm"]))

    def _event(self, text: str, **extra) -> None:
        if self.events is not None:
            try:
                self.events.add("voice", text, **extra)
            except Exception:
                log.debug("event failed", exc_info=True)

    @property
    def busy(self) -> bool:
        return self._pending is not None

    # ------------------------------------------------------------ say
    def say(self, text: str, style: str | dict | None = "calm", wait: bool = False, interrupt: bool = True) -> bool:
        """Speak, without waiting for an answer. Returns False if speech is off or text is empty."""
        if not strip_ssml(text or ""):
            return False
        prev = self.mode
        self.mode = "speaking"
        ok = self.speaker.say(text, interrupt=interrupt, wait=wait, style=self.style(style))
        if not ok or wait:
            self.mode = prev if prev == "listening" else "idle"
        else:
            threading.Thread(target=self._idle_after_speech, daemon=True, name="voice-idle").start()
        return ok

    def _idle_after_speech(self) -> None:
        self.speaker.wait(60)
        if self.mode == "speaking":
            self.mode = "idle"

    def done(self) -> bool:
        """After a fix was applied: "Done. Say undo to put it back." """
        return self.say(PROMPTS["done"], "done")

    def undone(self) -> bool:
        return self.say(PROMPTS["undone"], "done")

    def read_aloud(self, text: str) -> bool:
        """'Read this to me': a short cached lead-in, then the text (never cached)."""
        self.say(PROMPTS["read_start"], "done", wait=True)
        return self.say(text, "read", interrupt=False)

    def _speak_question(self, text: str, style: dict, pending: _Pending) -> float | None:
        """Speak and wait until done (or until the question is answered some other way).
        Returns perf_counter() when the sound started, if it did."""
        if self.cfg.get("chime"):
            self.speaker.beep("start", wait=True)
        self.mode = "speaking"
        if not self.speaker.say(text, interrupt=True, style=style):
            return None
        gen = self.speaker.generation
        stops = getattr(self.speaker, "stops", 0)
        while self.speaker.speaking:
            if pending.done():
                if self.speaker.generation == gen:
                    self.speaker.stop()
                break
            time.sleep(0.02)
        if getattr(self.speaker, "stops", 0) != stops and not pending.done():
            # Silenced on purpose ("Ally, stop" through the listener): stop asking, don't listen.
            # Other speech that merely replaced the question doesn't count.
            pending.set(Answer(TIMEOUT, by="voice", intent="STOPPED", prompt=pending.prompt))
        return self.speaker.audio_started.get(gen)

    # ------------------------------------------------------------ hearing
    def _eager(self) -> float | None:
        c = self.cfg
        return float(c.get("eager_silence_ms", 450)) / 1000.0 if c.get("eager_answers", True) else None

    def _open_ears(self):
        """(queue of Utterance, close(), source, in_speech()) or None when there is no microphone."""
        if self._ears is not None:
            ears = self._ears()
            if ears is not None and len(ears) == 3:
                ears = (*ears, lambda: False)
            return ears
        listener = self.listener
        if listener is not None:
            listener.claim_eager = self._eager()
            q = listener.claim()
            if q is not None:
                return q, lambda: listener.release(q), "listener", lambda: listener.in_speech
        try:
            import numpy  # noqa: F401  # type: ignore
            import sounddevice  # noqa: F401  # type: ignore
        except Exception as e:  # not installed, or PortAudio missing
            self.last_error = f"no microphone: {e}"
            return None
        q: queue.Queue = queue.Queue(maxsize=8)
        stop = threading.Event()
        ready = threading.Event()
        flags = {"in_speech": False}
        t = threading.Thread(target=self._mic, args=(q, stop, ready, flags), name="confirm-mic", daemon=True)
        t.start()
        ready.wait(2.0)

        def close():
            stop.set()
            t.join(timeout=1.5)
        return q, close, "mic", lambda: flags["in_speech"]

    def _mic(self, q: queue.Queue, stop: threading.Event, ready: threading.Event, flags: dict) -> None:
        import numpy as np  # type: ignore
        import sounddevice as sd  # type: ignore

        from .listener import BLOCK, Segmenter
        from .voice import RATE

        voice = self.settings().get("voice", {})
        seg = Segmenter(end_silence=max(0.6, float(voice.get("silence_seconds", 1.2)) * 0.7),
                        max_seconds=float(self.cfg.get("max_answer_seconds", 5)), eager_silence=self._eager())
        tail = 0.0
        try:
            with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=BLOCK) as stream:
                ready.set()
                while not stop.is_set():
                    data, _overflow = stream.read(BLOCK)
                    if self.speaker.speaking:
                        tail = time.monotonic() + 0.4  # the room still echoes Ally for a moment (as the listener)
                    level = float(np.sqrt(np.mean(data.astype(np.float32) ** 2)))
                    utt = seg.feed(data.tobytes(), level, time.time(), time.monotonic() < tail)
                    flags["in_speech"] = seg.in_speech
                    if utt is not None:
                        try:
                            q.put_nowait(utt)
                        except queue.Full:
                            pass
        except Exception as e:
            self.last_error = f"microphone problem: {e}"
            log.warning("confirm microphone: %s", e)
        finally:
            flags["in_speech"] = False
            ready.set()

    def stt_order(self) -> list[str]:
        if self._transcriber is not None:
            return ["test"]
        from .voice import has_whisper

        voice = self.settings().get("voice", {})
        engine = voice.get("engine", "auto")
        key = bool(os.environ.get("CARTESIA_API_KEY", "").strip())
        whisper = has_whisper()
        if engine == "off":
            return []
        if engine == "whisper":
            return ["whisper"] if whisper else []
        order = ["cartesia"] if (key or engine == "cartesia") else []
        if whisper:
            order.append("whisper")
        return order

    def transcribe(self, pcm: bytes) -> tuple[str, str]:
        """(text, engine). Cartesia Ink first, then offline Whisper. Raises ToolError if neither works."""
        if self._transcriber is not None:
            return self._transcriber(pcm), "test"
        from .voice import cartesia_stt, wav_bytes, whisper_pcm

        voice = self.settings().get("voice", {})
        lang = voice.get("language", "en")
        err: Exception | None = None
        for engine in self.stt_order():
            try:
                if engine == "cartesia":
                    text = cartesia_stt(wav_bytes(pcm), lang, timeout=6)
                else:
                    text = whisper_pcm(pcm, lang, voice.get("whisper_model", "base.en"))
                self.stt_engine = engine
                return text, engine
            except ToolError as e:
                err = e
                log.warning("speech-to-text (%s) failed for a yes/no answer: %s", engine, e)
        raise ToolError(str(err) if err else "No speech-to-text is set up "
                        "(set CARTESIA_API_KEY or pip install faster-whisper).")

    def _listen(self, pending: _Pending, window: float):
        """Wait for one answer. Returns (Intent, timing dict); the Intent is None for silence.

        window is how long the answer has to *start*; an answer already under way is heard out
        (up to [confirm] max_answer_seconds more). A short pause after a clear one-to-three-word
        yes or no is enough (eager answers); anything less clear waits for the full pause."""
        ears = self._open_ears()
        if ears is None:
            return None, {"no_mic": True}
        q, close, source, in_speech = ears
        opened = time.time()
        deadline = time.monotonic() + window
        hard_stop = deadline + float(self.cfg.get("max_answer_seconds", 5)) + 1.5
        best: Intent | None = None
        noise = False
        timing: dict = {"source": source}
        self.mode = "listening"
        self._event("Listening for yes or no...")
        try:
            while True:
                if pending.done():
                    return None, timing
                now = time.monotonic()
                if now >= deadline:
                    talking = False
                    try:
                        talking = bool(in_speech())
                    except Exception:
                        pass
                    if not talking or now >= hard_stop:
                        if best is None and noise:
                            timing["noise"] = True
                        return best, timing
                try:
                    utt = q.get(timeout=0.05 if now >= deadline else min(0.1, deadline - now))
                except queue.Empty:
                    continue
                if utt.over_speech or utt.t1 < opened - 0.2:
                    continue  # Ally's own voice, or something said before the question was asked
                t_stt = time.perf_counter()
                try:
                    text, engine = self.transcribe(utt.pcm)
                except ToolError as e:
                    if getattr(utt, "eager", False):
                        continue  # the full utterance will be tried again
                    self.last_error = str(e)
                    timing.update(no_stt=True, error=str(e))
                    return Intent("AMBIGUOUS", 0.0, ""), timing
                intent = classify(text, pending.prompt, protective=pending.kind == "risk")
                eager = bool(getattr(utt, "eager", False))
                if eager and not eager_ok(text, intent):
                    continue  # not clear enough to cut the answer short: wait for the end
                timing.update(speech_end=utt.t1, stt_ms=round((time.perf_counter() - t_stt) * 1000),
                              stt_engine=engine, heard_at=time.time(), eager=eager)
                log.info("yes/no answer heard%s: %r -> %s", " (early)" if eager else "", text, intent.kind)
                if text:
                    self._event(f"heard: {text}", intent=intent.kind)
                if intent.decisive or intent.kind in ("REPEAT", "SLOWER"):
                    return intent, timing
                if intent.kind == "EMPTY":
                    noise = True
                else:
                    best = intent  # keep listening a little: "uh..." then "yes"
                    deadline = max(deadline, time.monotonic() + 2.0)
                    hard_stop = max(hard_stop, deadline + 1.5)
        finally:
            close()
            if self.listener is not None:
                self.listener.claim_eager = None
            if self.mode == "listening":
                self.mode = "idle"

    # ------------------------------------------------------------ ask
    def ask_yes_no(self, prompt: str, timeout: float | None = None, **kw) -> str:
        """The Brain's contract: speak prompt, return "yes", "no" or "timeout"."""
        return self.ask(prompt, timeout=timeout, **kw).value

    def ask(self, prompt: str, *, timeout: float | None = None, rephrased: str | None = None,
            style: str | dict = "offer", kind: str = "offer", cancel: Callable[[], bool] | None = None) -> Answer:
        """Speak a yes/no question and wait for the answer (voice, or answer() from a button).

        timeout: seconds to wait for an answer to start, after each time the question is spoken
        (default [confirm] listen_seconds). cancel(): return True to stop early (e.g. the Offer was
        resolved by a button). rephrased: the wording for the re-ask (default: rephrase(prompt)).
        A newer question replaces an older one (the older one returns timeout, intent SUPERSEDED).
        """
        prompt = (prompt or "").strip()
        pending = _Pending(prompt, kind, cancel)
        with self._lock:
            old, self._pending = self._pending, pending
        if old is not None:
            old.set(Answer(TIMEOUT, intent="SUPERSEDED"))
        t_start = time.perf_counter()
        latency: dict = {}
        try:
            if not prompt:
                pending.set(Answer(TIMEOUT, intent="EMPTY_PROMPT"))
            elif not self.cfg.get("enabled", True):
                pending.set(Answer(TIMEOUT, intent="DISABLED"))
            else:
                with self._ask_lock:  # the previous question (if any) has been told to stop
                    if not pending.done():
                        self._ask_loop(pending, prompt, rephrased, style, timeout, latency)
        except Exception as e:  # never let a voice problem break the Brain's flow
            log.exception("yes/no question failed")
            pending.set(Answer(TIMEOUT, intent="ERROR", error=str(e)))
        finally:
            with self._lock:
                if self._pending is pending:
                    self._pending = None
            if self.mode == "listening":
                self.mode = "idle"
        ans = pending.result or Answer(TIMEOUT, intent="ERROR")
        ans.prompt = strip_ssml(prompt)
        attempts = latency.pop("attempts", 0)
        ans.attempts = ans.attempts or attempts
        latency["total_ms"] = round((time.perf_counter() - t_start) * 1000)
        ans.latency = {**latency, **ans.latency}
        self._finish(ans, kind)
        return ans

    @staticmethod
    def _settle(pending: _Pending, ans: Answer) -> None:
        pending.set(ans)  # loses to an answer that arrived first (a button)

    def _ask_loop(self, pending: _Pending, prompt: str, rephrased: str | None, style, timeout,
                  latency: dict) -> None:
        c = self.cfg
        window = float(timeout if timeout is not None else c.get("listen_seconds", 8))
        delay = float(c.get("listen_delay_ms", 250)) / 1000.0
        reasks = 1 if c.get("reask", True) else 0  # unclear -> one re-ask in other words
        replays = 1  # "what?" / "slower" -> one replay, slower
        base = self.style(style)
        text, st, slow = prompt, dict(base), 1.0
        last: Intent | None = None
        attempts = 0
        while not pending.done():
            attempts += 1
            latency["attempts"] = attempts
            if slow != 1.0:
                st["speed"] = round(float(base.get("speed", 1.0)) * slow, 3)
                st.pop("cache", None)  # slowed-down re-asks are one-offs
            t_say = time.perf_counter()
            self._event(f"asked: {strip_ssml(text)}", kind=pending.kind)
            started = self._speak_question(text, st, pending)
            if started is not None and "prompt_to_audio_ms" not in latency:
                latency["prompt_to_audio_ms"] = round((started - t_say) * 1000)
                latency["tts_engine"] = self.speaker.last_engine
            if pending.done() or pending.event.wait(delay):
                return
            intent, timing = self._listen(pending, window)
            if pending.done():
                return
            latency["mic"] = timing.get("source", "")
            if timing.get("speech_end"):
                latency["speech_end_to_answer_ms"] = round((time.time() - timing["speech_end"]) * 1000)
                latency["stt_ms"] = timing.get("stt_ms")
                latency["stt_engine"] = timing.get("stt_engine")
                latency["eager"] = timing.get("eager", False)
            if timing.get("no_mic"):
                self._event("No microphone: answer with the Yes or No button.")
                pending.event.wait(window)  # the buttons still get the listen window
                return self._settle(pending, Answer(TIMEOUT, intent="NO_MIC", attempts=attempts,
                                                    error=self.last_error))
            if timing.get("no_stt"):
                self.say(PROMPTS["missed_final"], "sorry", wait=True)
                pending.event.wait(window)
                return self._settle(pending, Answer(TIMEOUT, intent="NO_STT", attempts=attempts,
                                                    error=timing.get("error", "")))
            if intent is None:
                # Silence (or only noise): the question was asked once; never apply anything.
                return self._settle(pending, Answer(TIMEOUT, intent="NOISE" if timing.get("noise") else "SILENCE",
                                                    attempts=attempts))
            last = intent
            if intent.decisive:
                return self._settle(pending, Answer(YES if intent.kind == "YES" else NO, by="voice",
                                                    intent=intent.kind, transcript=intent.text,
                                                    confidence=intent.confidence, attempts=attempts))
            if intent.kind in ("REPEAT", "SLOWER") and replays:
                replays -= 1
                if intent.kind == "SLOWER":
                    self.slow_down()
                slow *= 0.85
                text = rephrased or rephrase(prompt)
                continue
            if reasks:
                reasks -= 1
                st = dict(self.style("sorry"), speed=base.get("speed", 1.0))
                st.pop("cache", None)
                text = PROMPTS["missed"] + pause(250) + (rephrased or rephrase(prompt))
                continue
            break
        if pending.done():
            return
        already_sorry = c.get("reask", True) and reasks == 0
        self.say(PROMPTS["buttons" if already_sorry else "missed_final"], "sorry", wait=True)
        self._settle(pending, Answer(TIMEOUT, intent="AMBIGUOUS", transcript=last.text if last else "",
                                     confidence=last.confidence if last else 0.0, attempts=attempts))

    def answer(self, value: str, by: str = "button") -> bool:
        """An answer from somewhere else (on-screen button, dashboard, CLI). The first answer wins.
        Returns True if it answered the open question."""
        v = str(value).strip().lower()
        v = {"y": YES, "yes": YES, "true": YES, "n": NO, "no": NO, "false": NO}.get(v, v)
        if v not in (YES, NO):
            raise ToolError('The answer must be "yes" or "no".')
        with self._lock:
            p = self._pending
        if p is None:
            return False
        ok = p.set(Answer(v, by=by, intent="BUTTON", confidence=1.0))
        if ok and self.speaker.speaking:
            self.speaker.stop()
        return ok

    def cancel(self) -> bool:
        """Stop the open question without an answer."""
        with self._lock:
            p = self._pending
        return bool(p and p.set(Answer(TIMEOUT, by="none", intent="CANCELLED")))

    def _finish(self, ans: Answer, kind: str) -> None:
        c = self.cfg
        if ans.value == NO and ans.by == "voice" and ans.intent == "NO" and kind == "offer" and c.get(
                "say_no_reply", True):
            self.say(PROMPTS["no_reply"], "done")
        detail = f" ('{ans.transcript}')" if ans.transcript else ""
        ms = ans.latency.get("speech_end_to_answer_ms")
        self._event(f"answer: {ans.value} by {ans.by}{detail}" + (f", {ms} ms after speech" if ms else ""),
                    intent=ans.intent)
        row = {"ts": round(time.time(), 3), "kind": kind, "answer": ans.value, "by": ans.by, "intent": ans.intent,
               "attempts": ans.attempts, **{k: v for k, v in ans.latency.items() if v is not None}}
        self.history = (self.history + [row])[-20:]
        if c.get("log_latency", True) and self.log_path is not None:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(row) + "\n")
            except OSError as e:
                log.debug("latency log failed: %s", e)

    # ------------------------------------------------------------ habits
    def slow_down(self, step: float = 0.1, floor: float = 0.6) -> float:
        """'Slower, please': lower the speech rate and keep it (a habit)."""
        new = round(max(floor, self.speaker.rate() - step), 2)
        self.speaker.rate_override = new
        if self.state is not None:
            self.state.set("speech_rate", new)
        self._event(f"Speaking slower from now on (speed {new:g}).")
        threading.Thread(target=self.prerender, name="voice-prerender", daemon=True).start()
        return new

    # ------------------------------------------------------------ offers and risks
    def confirm_offer(self, offer, background: bool = False):
        """Ask an Offer's question out loud and resolve it with the spoken answer.

        offer: the foundation Offer (or a dict / any object) with .prompt, optional .reprompt and
        .resolve(answer, by). A button that resolves it first wins: we notice and stop. The Offer's
        own timer ends the question the same way; voice keeps no separate offer timeout.
        A yes / no given through answer() (dashboard, CLI, API) resolves the Offer too, with its by.
        Returns the Answer, or the running Thread when background=True."""
        prompt = str(_get(offer, "prompt", "") or "")

        def resolved() -> bool:
            if _get(offer, "resolved") is True or _get(offer, "done") is True:
                return True
            return any(_get(offer, a) for a in ("answer", "resolved_by", "choice", "result", "resolution"))

        def run() -> Answer:
            ans = self.ask(prompt, rephrased=_get(offer, "reprompt"), kind="offer", cancel=resolved)
            if ans.value in (YES, NO) and ans.by not in ("offer", "none") and not resolved():
                fn = _get(offer, "resolve")
                if callable(fn):
                    try:
                        fn(ans.value, by=ans.by)
                    except TypeError:
                        fn(ans.value, ans.by)
            return ans

        if background:
            t = threading.Thread(target=run, name="voice-offer", daemon=True)
            t.start()
            return t
        return run()

    @staticmethod
    def risk_text(risk) -> tuple[str, str | None]:
        """(statement, question) for a Risk (dict or object with kind, severity)."""
        kind = str(_get(risk, "kind", "") or "")
        return RISK_TEXT.get(kind, RISK_DEFAULT)

    def warn(self, risk, ask: bool | None = None, background: bool = False):
        """Spoken risk warning: calm, slower, a pause before the question. Never alarmed.

        High and medium severity ask the protective question ("Close it?") and return the Answer;
        low severity (or ask=False) only says the statement and returns None."""
        statement, question = self.risk_text(risk)
        severity = str(_get(risk, "severity", "medium") or "medium").lower()
        if ask is None:
            ask = question is not None and severity not in ("low", "info")
        if not ask or not question:
            self.say(statement, "warning")
            return None
        text = statement + pause(400) + question

        def run() -> Answer:
            return self.ask(text, rephrased=rephrase(question), style="warning", kind="risk")

        if background:
            t = threading.Thread(target=run, name="voice-risk", daemon=True)
            t.start()
            return t
        return run()

    # ------------------------------------------------------------ getting ready
    def register_prompts(self, prompts, style: str = "offer") -> None:
        """Offer prompts the Brain uses, so they are pre-rendered too."""
        for p in prompts:
            if p and (p, style) not in self.extra_prompts:
                self.extra_prompts.append((p, style))

    def prompt_set(self) -> list[tuple[str, dict]]:
        items = [(PROMPTS["offer"], "offer"), (rephrase(PROMPTS["offer"]), "offer"), (PROMPTS["done"], "done"),
                 (PROMPTS["no_reply"], "done"), (PROMPTS["missed_final"], "sorry"), (PROMPTS["buttons"], "sorry"),
                 (PROMPTS["read_start"], "done"),
                 (PROMPTS["undone"], "done")]
        for statement, question in list(RISK_TEXT.values()):
            items.append((statement + pause(400) + question, "warning"))
            items.append((statement, "warning"))
        items += self.extra_prompts
        return [(t, self.style(s)) for t, s in items]

    def prerender(self) -> int:
        """Render the fixed prompts with Cartesia now (instant and offline later)."""
        if not self.cfg.get("prerender", True):
            return 0
        n, err = self.speaker.prerender(self.prompt_set())
        self.prerendered = n
        if n:
            log.info("pre-rendered %d voice prompts with Cartesia", n)
        if err:
            self.last_error = err
            log.warning("could not pre-render voice prompts: %s", err)
        return n

    def info(self) -> dict:
        lat = [h.get("speech_end_to_answer_ms") for h in self.history if h.get("speech_end_to_answer_ms")]
        aud = [h.get("prompt_to_audio_ms") for h in self.history if h.get("prompt_to_audio_ms") is not None]
        p = self._pending
        return {"enabled": bool(self.cfg.get("enabled", True)), "mode": self.mode, "busy": p is not None,
                "question": p.prompt if p else "", "stt": self.stt_order(), "last_stt": self.stt_engine,
                "prerendered": self.prerendered, "error": self.last_error,
                "latency": {"reply_ms_p50": round(statistics.median(lat)) if lat else None,
                            "first_audio_ms_p50": round(statistics.median(aud)) if aud else None,
                            "last": self.history[-5:]},
                "speech": self.speaker.info()}


# ---------------------------------------------------------------------------- module-level contract
_current: VoiceConfirm | None = None
_current_lock = threading.Lock()


def install(vc: VoiceConfirm | None) -> None:
    """The agent installs its VoiceConfirm here so say() / ask_yes_no() work from anywhere."""
    global _current
    with _current_lock:
        _current = vc


def standalone(home=None) -> VoiceConfirm:
    """A VoiceConfirm without the agent (for trying voice on its own)."""
    from .config import ConfigManager
    from .paths import Paths, load_env_file
    from .platforms import get_platform
    from .speech import Speaker

    paths = Paths(home)
    load_env_file(paths.env_file)
    cm = ConfigManager(paths.config)
    cm.reload()
    speaker = Speaker(get_platform(), lambda: cm.data["speech"], cache_dir=paths.cache_dir / "voice")
    return VoiceConfirm(speaker, lambda: cm.data, log_dir=paths.log_dir)


def current() -> VoiceConfirm:
    global _current
    with _current_lock:
        if _current is None:
            _current = standalone()
        return _current


def say(text: str, style: str | dict | None = "calm", wait: bool = False) -> bool:
    """Speak text (Cartesia, or the OS voice if Cartesia fails)."""
    return current().say(text, style, wait=wait)


def ask_yes_no(prompt: str, timeout: float | None = None, **kw) -> str:
    """Speak a yes/no question and listen. Returns "yes", "no" or "timeout"."""
    return current().ask_yes_no(prompt, timeout=timeout, **kw)


# ---------------------------------------------------------------------------- voice audition
def audition(voices: list[str], texts: list[str] | None = None, speed: float = 0.9, emotion: str = "calm",
             model: str = "sonic-3.6", play: bool = False, platform=None) -> list[dict]:
    """Render the prompt set with each voice and measure it: time to audio and words per minute.
    Pick the lowest-pitched voice that still sounds warm, near 150-160 wpm (Research: Voice)."""
    from .platforms.base import tempfile_path

    texts = texts or [PROMPTS["offer"], PROMPTS["done"], RISK_TEXT["scam_popup"][0] + " " +
                      RISK_TEXT["scam_popup"][1], "Sure. Here it is. The bank's letter says your statement is ready."]
    rows = []
    for v in voices:
        try:
            vid = resolve_voice(v)
        except CartesiaError as e:
            rows.append({"voice": v, "error": str(e)})
            continue
        for t in texts:
            t0 = time.perf_counter()
            try:
                audio = cartesia_tts(t, vid, model, speed=speed, emotion=emotion)
            except CartesiaError as e:
                rows.append({"voice": v, "text": t, "error": str(e)})
                continue
            ms = round((time.perf_counter() - t0) * 1000)
            secs = wav_seconds(audio)
            rows.append({"voice": v, "id": vid, "text": t, "request_ms": ms, "seconds": round(secs, 2),
                         "wpm": words_per_minute(t, secs)})
            if play and platform is not None:
                path = tempfile_path(".wav")
                try:
                    with open(path, "wb") as f:
                        f.write(audio)
                    platform.play_wav_start(path).wait()
                finally:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
    return rows


def _main(argv: list[str]) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="python -m ally.voice_confirm", description="Try Ally's voice on its own.")
    p.add_argument("command", choices=["say", "ask", "classify", "audition", "warn"])
    p.add_argument("text", nargs="*")
    p.add_argument("--style", default="calm")
    p.add_argument("--play", action="store_true", help="audition: play each sample")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    text = " ".join(a.text).strip()
    if a.command == "classify":
        i = classify(text)
        print(f"{i.kind} (confidence {i.confidence:g}) matched={list(i.matched)}")
        return 0
    if a.command == "audition":
        from .paths import Paths, load_env_file
        from .platforms import get_platform

        load_env_file(Paths().env_file)
        for r in audition(a.text or ["Tessa", "Leo", "Marian", "Dana"], play=a.play, platform=get_platform()):
            print(json.dumps(r))
        return 0
    vc = standalone()
    if a.command == "say":
        vc.say(text or PROMPTS["offer"], a.style, wait=True)
        print("spoke with:", vc.speaker.last_engine, vc.speaker.fallback_reason or "")
        return 0
    if a.command == "warn":
        ans = vc.warn({"kind": text or "scam_popup", "severity": "high"})
        print(ans.to_dict() if ans else "said")
        return 0
    ans = vc.ask(text or PROMPTS["offer"])
    print(json.dumps(ans.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
