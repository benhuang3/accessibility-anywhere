"""Text-to-speech: Cartesia (natural voice) when configured, the OS voice otherwise.

New speech interrupts old speech. stop() silences everything immediately.
Cartesia: POST https://api.cartesia.ai/tts/bytes, headers Authorization: Bearer <key> and
Cartesia-Version, returns WAV bytes that the OS audio player plays.

Fallback chain for every sentence: the configured Cartesia model (speech.cartesia_model), then
speech.cartesia_fallback_model, then the OS voice. Nothing a user hears depends on the network
being up: short fixed prompts (offers, "Done.") are pre-rendered by Cartesia and cached on disk,
so they keep the Cartesia voice even offline.

A style is a small dict that shapes one utterance: {"emotion": "calm", "speed": 0.9 (times the
user's rate), "volume": 1.0, "cache": True}. The OS voice honours speed only.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import math
import os
import re
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path

from .claude import ssl_context
from .platforms.base import Platform, tempfile_path
from .registry import ToolError

log = logging.getLogger("ally.speech")

# CARTESIA_BASE_URL points Ally at a test server (tests/e2e_voice.py); normally unset.
CARTESIA_URL = os.environ.get("CARTESIA_BASE_URL", "").rstrip("/") or "https://api.cartesia.ai"
CARTESIA_VERSION = "2026-08-14"
FALLBACK_MODEL = "sonic-3"
WAV_RATE = 24000
# Emotions that suit an assistant for older adults. Others are passed through as written, but
# Ally's own prompts never use scared/alarmed-type emotions, even for scam warnings.
EMOTIONS = ("neutral", "calm", "content", "sympathetic", "happy", "curious", "grateful", "apologetic",
            "confident", "serene", "peaceful")
_SSML_RE = re.compile(r"<\s*/?\s*(?:break|speed|volume|emotion|spell)\b[^>]*>", re.I)


class CartesiaError(ToolError):
    """A Cartesia request failed. status is the HTTP status, or 0 when the server couldn't be reached."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status

    @property
    def offline(self) -> bool:
        return self.status == 0

    @property
    def bad_request(self) -> bool:
        """The request itself was rejected (unknown model or field), so a simpler request may work."""
        return self.status in (400, 404, 422)


def strip_ssml(text: str) -> str:
    """Remove the Cartesia SSML tags Ally uses, for the OS voice (which would read them out)."""
    return re.sub(r"\s{2,}", " ", _SSML_RE.sub(" ", text or "")).strip()


def pause(ms: int = 300) -> str:
    """A short pause inside a Cartesia transcript (dropped for the OS voice)."""
    return f' <break time="{int(ms)}ms"/> '


def chunks(text: str, size: int = 400) -> list[str]:
    """Split text at sentence ends so each piece is at most ~size characters."""
    sentences = re.split(r"(?<=[.!?;:])\s+", text.strip())
    out, cur = [], ""
    for s in sentences:
        while len(s) > size:
            cut = s.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            if cur:
                out.append(cur)
                cur = ""
            out.append(s[:cut].strip())
            s = s[cut:].strip()
        if len(cur) + len(s) + 1 > size and cur:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        out.append(cur)
    return [c for c in out if c]


def _cartesia_key() -> str:
    key = os.environ.get("CARTESIA_API_KEY", "").strip()
    if not key:
        raise CartesiaError("Set CARTESIA_API_KEY in the .env file to use the Cartesia voice.", status=401)
    return key


def _cartesia_request(method: str, path: str, body: dict | None = None, timeout: float = 20) -> bytes:
    from . import speech as _self  # read at call time, so tests can point it elsewhere

    req = urllib.request.Request(
        _self.CARTESIA_URL + path, data=json.dumps(body).encode("utf-8") if body is not None else None, method=method,
        headers={"Authorization": f"Bearer {_cartesia_key()}", "Cartesia-Version": CARTESIA_VERSION,
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300] if e.fp else ""
        raise CartesiaError(f"Cartesia error {e.code}: {detail}", status=e.code)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise CartesiaError(f"Can't reach Cartesia: {getattr(e, 'reason', e)}")


def tts_body(text: str, voice_id: str, model: str, speed: float = 1.0, language: str = "en",
             emotion: str = "", volume: float = 1.0) -> dict:
    gen: dict = {"speed": round(max(0.6, min(1.5, float(speed))), 3)}
    if abs(float(volume) - 1.0) > 1e-6:
        gen["volume"] = round(max(0.5, min(2.0, float(volume))), 3)
    if emotion:
        gen["emotion"] = emotion
    return {
        "model_id": model,
        "transcript": text,
        "voice": {"mode": "id", "id": voice_id},
        "language": language,
        "output_format": {"container": "wav", "encoding": "pcm_s16le", "sample_rate": WAV_RATE},
        "generation_config": gen,
    }


def cartesia_tts(text: str, voice_id: str, model: str = "sonic-3.6", speed: float = 1.0,
                 language: str = "en", timeout: float = 20, emotion: str = "", volume: float = 1.0) -> bytes:
    """One sentence (or a few) as WAV bytes. If the request is rejected only because of the
    emotion or SSML extras (older models, beta features), it is retried once without them."""
    _cartesia_key()
    if not voice_id:
        raise CartesiaError("Set speech.cartesia_voice_id in config.toml "
                            "(python run.py voices lists them, or copy one from https://play.cartesia.ai).",
                            status=400)
    try:
        return _cartesia_request("POST", "/tts/bytes",
                                 tts_body(text, voice_id, model, speed, language, emotion, volume), timeout)
    except CartesiaError as e:
        plain = strip_ssml(text)
        if not (e.bad_request and (emotion or plain != text.strip())) or "model" in str(e).lower():
            raise
        log.info("Cartesia rejected the styled request (%s); retrying without emotion/SSML", e)
        return _cartesia_request("POST", "/tts/bytes",
                                 tts_body(plain, voice_id, model, speed, language, "", volume), timeout)


def cartesia_voices(language: str = "en", q: str = "", gender: str = "", limit: int = 100,
                    timeout: float = 15) -> list[dict]:
    """Voices in the Cartesia library: [{id, name, description, language, gender}]."""
    params = {"limit": str(max(1, min(100, int(limit))))}
    if language:
        params["language"] = language
    if q:
        params["q"] = q
    if gender:
        params["gender"] = gender
    raw = _cartesia_request("GET", "/voices?" + urllib.parse.urlencode(params), None, timeout)
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as e:
        raise CartesiaError(f"Cartesia sent an unreadable voice list: {e}", status=500)
    items = data.get("data", data) if isinstance(data, dict) else data
    return [{k: v.get(k) for k in ("id", "name", "description", "language", "gender")}
            for v in items or [] if isinstance(v, dict)]


_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_voice_names: dict[str, str] = {}
_voice_names_lock = threading.Lock()


def resolve_voice(value: str) -> str:
    """speech.cartesia_voice_id may be a voice ID or a voice name ("Tessa"). Names are looked up
    once in the Cartesia voice library (exact name first, then a name that starts with it)."""
    value = (value or "").strip()
    if not value or _UUID_RE.match(value):
        return value
    key = value.lower()
    with _voice_names_lock:
        if key in _voice_names:
            return _voice_names[key]
    found = cartesia_voices(q=value, language="")
    names = [(str(v.get("name") or "").strip().lower(), v.get("id")) for v in found if v.get("id")]
    pick = (next((i for n, i in names if n == key), None)
            or next((i for n, i in names if n.startswith(key)), None)
            or next((i for n, i in names if key in n), None))
    if not pick:
        raise CartesiaError(f"No Cartesia voice is named '{value}'. Run: python run.py voices", status=400)
    with _voice_names_lock:
        _voice_names[key] = pick
    log.info("Cartesia voice '%s' is %s", value, pick)
    return pick


def wav_seconds(data: bytes) -> float:
    """Length of WAV audio in seconds (0 if it isn't a readable WAV)."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except (wave.Error, EOFError, OSError):
        return 0.0


def words_per_minute(text: str, seconds: float) -> float:
    words = len(strip_ssml(text).split())
    return round(words / (seconds / 60.0), 1) if seconds > 0 else 0.0


class PromptCache:
    """Cartesia audio for short fixed prompts, in memory and (optionally) on disk.

    Only text the caller marks cacheable is stored (Ally's own prompts), never page text that is
    read aloud. The key includes voice, model, speed, emotion and volume, so changing any of them
    re-renders.
    """

    def __init__(self, folder: Path | str | None = None, max_items: int = 200):
        self.folder = Path(folder) if folder else None
        self.max_items = max_items
        self._mem: dict[str, bytes] = {}
        self._lock = threading.Lock()

    @staticmethod
    def key(text: str, voice: str, model: str, speed: float, emotion: str, volume: float) -> str:
        raw = json.dumps([text, voice, model, round(speed, 3), emotion, round(volume, 3)], ensure_ascii=False)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def get(self, key: str) -> bytes | None:
        with self._lock:
            hit = self._mem.get(key)
        if hit is not None or self.folder is None:
            return hit
        try:
            data = (self.folder / f"{key}.wav").read_bytes()
        except OSError:
            return None
        with self._lock:
            self._mem[key] = data
        return data

    def put(self, key: str, data: bytes) -> None:
        with self._lock:
            if len(self._mem) >= self.max_items:
                self._mem.pop(next(iter(self._mem)))
            self._mem[key] = data
        if self.folder is not None:
            try:
                self.folder.mkdir(parents=True, exist_ok=True)
                tmp = self.folder / f"{key}.tmp"
                tmp.write_bytes(data)
                os.replace(tmp, self.folder / f"{key}.wav")
            except OSError as e:
                log.debug("prompt cache write failed: %s", e)

    def __len__(self) -> int:
        with self._lock:
            return len(self._mem)


def make_beep(path: str, freq: float = 880.0, seconds: float = 0.12, rate: int = 16000) -> None:
    n = int(seconds * rate)
    frames = bytearray()
    for i in range(n):
        fade = min(1.0, i / 200, (n - i) / 200)
        frames += struct.pack("<h", int(9000 * fade * math.sin(2 * math.pi * freq * i / rate)))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


class Speaker:
    def __init__(self, platform: Platform, settings, cache_dir: Path | str | None = None):
        """settings: a callable returning the current [speech] config dict."""
        self.platform = platform
        self.settings = settings
        self.rate_override: float | None = None
        self.cache = PromptCache(cache_dir)
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._gen = 0
        self._thread: threading.Thread | None = None
        self.last_error = ""
        self.last_engine = ""  # "cartesia", "cartesia-cache" or "system": what the last sentence used
        self.fallback_reason = ""  # why the last sentence didn't use Cartesia
        self.model_in_use = ""  # the Cartesia model that last worked
        self._bad_models: set[str] = set()
        self._down_until = 0.0  # after a network failure, skip Cartesia until then (no waiting per sentence)
        self.down_reason = ""
        self.audio_started: dict[int, float] = {}  # generation -> perf_counter() when sound began
        self._beeps: dict[str, str] = {}

    @property
    def speaking(self) -> bool:
        t = self._thread
        return bool(t and t.is_alive())

    @property
    def generation(self) -> int:
        """Changes whenever speech starts or stops (used to avoid talking over a tool)."""
        return self._gen

    def rate(self) -> float:
        return self.rate_override or float(self.settings().get("rate", 1.0))

    def engine(self, cfg: dict | None = None) -> str:
        """The engine that will be tried first: cartesia, system or off ("auto" resolved)."""
        cfg = cfg or self.settings()
        e = cfg.get("engine", "system")
        if e != "auto":
            return e
        if os.environ.get("CARTESIA_API_KEY", "").strip() and cfg.get("cartesia_voice_id"):
            return "cartesia"
        return "system"

    def models(self, cfg: dict | None = None) -> list[str]:
        cfg = cfg or self.settings()
        out = []
        for m in (cfg.get("cartesia_model", "sonic-3.6"), cfg.get("cartesia_fallback_model", FALLBACK_MODEL)):
            if m and m not in out:
                out.append(m)
        good = [m for m in out if m not in self._bad_models]
        return good or out

    def say(self, text: str, interrupt: bool = True, wait: bool = False, style: dict | None = None) -> bool:
        cfg = self.settings()
        engine = self.engine(cfg)
        text = (text or "").strip()
        if engine == "off" or not strip_ssml(text):
            return False
        limit = int(cfg.get("max_chars", 4000))
        if len(text) > limit:
            text = text[:limit].rsplit(" ", 1)[0] + "."
        if interrupt:
            self.stop()
        with self._lock:
            self._gen += 1
            gen = self._gen
        t = threading.Thread(target=self._run, args=(gen, text, engine, cfg, dict(style or {})),
                             name="speech", daemon=True)
        self._thread = t
        t.start()
        if wait:
            t.join()
        return True

    def wait(self, timeout: float | None = None) -> None:
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout)

    def _active(self, gen: int) -> bool:
        return gen == self._gen

    def _play(self, gen: int, start) -> None:
        """start() begins the sound and returns a Popen-like handle; this waits for it to end."""
        with self._lock:
            if not self._active(gen):
                return
            proc = self._proc = start()
            self.audio_started.setdefault(gen, time.perf_counter())
            if len(self.audio_started) > 50:
                self.audio_started.pop(min(self.audio_started))
        proc.wait()  # a local: stop() may clear self._proc meanwhile

    def _style_values(self, cfg: dict, style: dict) -> tuple[float, str, float]:
        speed = self.rate() * float(style.get("speed", 1.0))
        emotion = style.get("emotion", cfg.get("emotion", ""))
        volume = float(style.get("volume", 1.0))
        return speed, (emotion or ""), volume

    def _run(self, gen: int, text: str, engine: str, cfg: dict, style: dict) -> None:
        try:
            if engine == "cartesia":
                try:
                    self._run_cartesia(gen, text, cfg, style)
                    return
                except ToolError as e:
                    self.last_error = self.fallback_reason = str(e)
                    log.warning("Cartesia speech failed, using the system voice: %s", e)
            else:
                self.fallback_reason = "" if engine == "system" and cfg.get("engine") == "system" else (
                    "Cartesia isn't set up (needs CARTESIA_API_KEY and speech.cartesia_voice_id)")
            speed, _emotion, _volume = self._style_values(cfg, style)
            plain, voice = strip_ssml(text), cfg.get("voice", "")
            self.last_engine = "system"
            self._play(gen, lambda: self.platform.tts_start(plain, speed, voice))
        except ToolError as e:
            self.last_error = str(e)
            log.warning("speech failed: %s", e)
        except OSError as e:
            self.last_error = str(e)
            log.warning("speech program failed: %s", e)

    def render(self, text: str, cfg: dict | None = None, style: dict | None = None) -> tuple[bytes, str]:
        """Cartesia audio for one piece of text: (wav, "cartesia" | "cartesia-cache").

        Tries the prompt cache (when style["cache"]), then each model in order. Raises ToolError
        when no Cartesia audio can be had, so the caller falls back to the OS voice."""
        cfg = cfg or self.settings()
        style = style or {}
        speed, emotion, volume = self._style_values(cfg, style)
        voice = cfg.get("cartesia_voice_id", "")  # the configured value keys the cache (works offline)
        cacheable = bool(style.get("cache")) and bool(cfg.get("cache_prompts", True))
        models = self.models(cfg)
        if cacheable:
            for m in models:
                hit = self.cache.get(PromptCache.key(text, voice, m, speed, emotion, volume))
                if hit:
                    return hit, "cartesia-cache"
        if time.monotonic() < self._down_until:
            raise CartesiaError(f"Cartesia skipped for a moment ({self.down_reason})")
        try:
            voice_id = resolve_voice(voice)
        except CartesiaError as e:
            self._trip(cfg, e)
            raise
        err: ToolError | None = None
        for m in models:
            try:
                audio = cartesia_tts(text, voice_id, m, speed=speed, language=cfg.get("language", "en"),
                                     emotion=emotion, volume=volume,
                                     timeout=float(cfg.get("cartesia_timeout_seconds", 4)))
            except CartesiaError as e:
                err = e
                if e.bad_request and "voice" not in str(e).lower():
                    self._bad_models.add(m)  # e.g. sonic-3.6 not available on this account: skip it from now on
                    log.warning("Cartesia model %s failed (%s); trying the next one", m, e)
                    continue
                self._trip(cfg, e)
                raise
            self._down_until, self.down_reason = 0.0, ""
            self.model_in_use = m
            if cacheable:
                self.cache.put(PromptCache.key(text, voice, m, speed, emotion, volume), audio)
            return audio, "cartesia"
        raise err or CartesiaError("No Cartesia model is configured.", status=400)

    def _trip(self, cfg: dict, e: CartesiaError) -> None:
        """Network trouble (unreachable, timed out, overloaded): use the OS voice for a while instead
        of making every sentence wait for the timeout. Cached prompts keep the Cartesia voice."""
        if e.offline or e.status >= 500 or e.status == 429:
            wait = float(cfg.get("cartesia_retry_seconds", 30))
            self._down_until = time.monotonic() + (wait if e.offline else min(wait, 15.0))
            self.down_reason = str(e)[:200]
            log.warning("Cartesia is unavailable; using the built-in voice for %.0f s: %s",
                        self._down_until - time.monotonic(), e)

    @property
    def cartesia_down(self) -> float:
        """Seconds until Cartesia is tried again (0 = it is used)."""
        return max(0.0, self._down_until - time.monotonic())

    def _run_cartesia(self, gen: int, text: str, cfg: dict, style: dict) -> None:
        pieces = [text] if style.get("cache") else chunks(text)
        for i, piece in enumerate(pieces):
            if not self._active(gen):
                return
            try:
                audio, how = self.render(piece, cfg, style)
            except ToolError:
                if i == 0:
                    raise  # nothing played yet: the whole text goes to the OS voice
                self.last_engine = "system"  # finish the rest with the OS voice
                rest = strip_ssml(" ".join(pieces[i:]))
                self._play(gen, lambda: self.platform.tts_start(rest, self.rate(), cfg.get("voice", "")))
                return
            if not self._active(gen):
                return
            self.last_engine, self.fallback_reason = how, ""
            path = tempfile_path(".wav")
            try:
                with open(path, "wb") as f:
                    f.write(audio)
                self._play(gen, lambda: self.platform.play_wav_start(path))
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    def prerender(self, items: list[tuple[str, dict]]) -> tuple[int, str]:
        """Fetch Cartesia audio for fixed prompts now, so they play instantly (and offline) later.
        Returns (how many are ready, the last error)."""
        cfg = self.settings()
        if self.engine(cfg) != "cartesia" or not cfg.get("cache_prompts", True):
            return 0, ""
        ready, err = 0, ""
        for text, style in items:
            try:
                self.render(text, cfg, dict(style or {}, cache=True))
                ready += 1
            except ToolError as e:
                err = str(e)
                if isinstance(e, CartesiaError) and (e.offline or e.status in (401, 402, 403, 429)):
                    break  # no point trying the rest
        return ready, err

    def info(self) -> dict:
        cfg = self.settings()
        return {"engine": cfg.get("engine", "system"), "active_engine": self.engine(cfg),
                "last_engine": self.last_engine, "fallback_reason": self.fallback_reason,
                "model": self.model_in_use or (self.models(cfg)[0] if self.models(cfg) else ""),
                "voice_id": cfg.get("cartesia_voice_id", ""), "cached_prompts": len(self.cache),
                "cartesia_retry_in": round(self.cartesia_down, 1), "down_reason": self.down_reason,
                "speaking": self.speaking, "rate": self.rate(), "error": self.last_error}

    def stop(self) -> None:
        with self._lock:
            self._gen += 1
            proc, self._proc = self._proc, None
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=1)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        try:
            self.platform.tts_cancel()
        except Exception:
            pass

    def beep(self, kind: str = "start", wait: bool = True) -> None:
        """Short earcon: 'start' (high) or 'stop' (low)."""
        try:
            path = self._beeps.get(kind)
            if not path or not os.path.exists(path):
                path = tempfile_path(f"-{kind}.wav")
                make_beep(path, 880.0 if kind == "start" else 520.0)
                self._beeps[kind] = path
            p = self.platform.play_wav_start(path)
            if wait:
                p.wait(timeout=2)
        except Exception as e:
            log.debug("beep failed: %s", e)
