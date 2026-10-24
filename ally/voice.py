"""Voice input: record until you stop talking, transcribe, and handle it as a request.

Recording: sounddevice (pip install sounddevice numpy), or `arecord` on Linux / `rec` (SoX).
Transcription: Cartesia Ink (POST https://api.cartesia.ai/stt, model "ink-whisper") when
CARTESIA_API_KEY is set, or faster-whisper offline (pip install faster-whisper).
Tip: the dashboard's microphone button works in Chrome with nothing extra installed.
"""

from __future__ import annotations

import importlib
import importlib.util
import io
import json
import logging
import os
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.request
import wave

from .claude import ssl_context
from .platforms.base import NO_WINDOW, tempfile_path, which
from .registry import ToolError
from .speech import CARTESIA_URL, CARTESIA_VERSION

log = logging.getLogger("ally.voice")
RATE = 16000


def wav_bytes(pcm: bytes, rate: int = RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def multipart(fields: dict, file_field: str, filename: str, data: bytes, content_type: str) -> tuple[bytes, str]:
    boundary = "----ally" + secrets.token_hex(12)
    out = bytearray()
    for k, v in fields.items():
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode()
    out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; filename=\"{filename}\"\r\n"
            f"Content-Type: {content_type}\r\n\r\n").encode()
    out += data + b"\r\n" + f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def cartesia_stt(wav: bytes, language: str = "en", timeout: float = 30) -> str:
    key = os.environ.get("CARTESIA_API_KEY", "")
    if not key:
        raise ToolError("Set CARTESIA_API_KEY in the .env file for voice input.")
    body, ctype = multipart({"model": "ink-whisper", "language": language}, "file", "speech.wav", wav, "audio/wav")
    req = urllib.request.Request(CARTESIA_URL + "/stt", data=body, method="POST", headers={
        "Authorization": f"Bearer {key}", "Cartesia-Version": CARTESIA_VERSION, "Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as resp:
            return (json.loads(resp.read().decode("utf-8")).get("text") or "").strip()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300] if e.fp else ""
        raise ToolError(f"Cartesia speech-to-text error {e.code}: {detail}")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise ToolError(f"Can't reach Cartesia: {getattr(e, 'reason', e)}")


_whisper_models: dict[str, object] = {}
_whisper_lock = threading.Lock()  # one transcription at a time (push-to-talk and always-listening share it)


def whisper_name(model_name: str, language: str) -> str:
    """English-only models ("base.en") can't do other languages; use the multilingual one."""
    if language and language != "en" and model_name.endswith(".en"):
        return model_name[:-3]
    return model_name


def has_whisper() -> bool:
    try:
        return importlib.util.find_spec("faster_whisper") is not None  # cheap check, no heavy import
    except (ImportError, ValueError):
        return False


def whisper_pcm(pcm: bytes, language: str = "en", model_name: str = "base.en", prompt: str = "") -> str:
    """Transcribe 16 kHz mono 16-bit PCM on this computer (the model downloads once, ~75-500 MB)."""
    try:
        import numpy as np  # type: ignore
        from faster_whisper import WhisperModel  # type: ignore
    except Exception:
        raise ToolError("Offline voice needs faster-whisper: pip install faster-whisper")
    name = whisper_name(model_name, language)
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    with _whisper_lock:
        model = _whisper_models.get(name)
        if model is None:
            log.info("loading speech-to-text model %s (the first time downloads it)", name)
            model = _whisper_models[name] = WhisperModel(name, device="cpu", compute_type="int8")
        segments, _info = model.transcribe(audio, language=language or None, beam_size=1, vad_filter=True,
                                           condition_on_previous_text=False, initial_prompt=prompt or None)
        # Whisper invents words for noise ("Thank you."): drop pieces it thinks aren't speech.
        kept = [s.text.strip() for s in segments if not (s.no_speech_prob > 0.6 and s.avg_logprob < -0.7)]
    return " ".join(kept).strip()


def whisper_stt(wav: bytes, language: str = "en", model_name: str = "base.en", prompt: str = "") -> str:
    with wave.open(io.BytesIO(wav), "rb") as w:
        pcm = w.readframes(w.getnframes())
    return whisper_pcm(pcm, language, model_name, prompt)


class VoiceInput:
    def __init__(self, agent):
        self.agent = agent
        self.listening = False
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._rec_cache: tuple[float, str] | None = None

    @property
    def cfg(self) -> dict:
        return self.agent.config.data["voice"]

    def engine(self) -> str:
        e = self.cfg.get("engine", "auto")
        if e != "auto":
            return e
        if os.environ.get("CARTESIA_API_KEY"):
            return "cartesia"
        return "whisper" if has_whisper() else "none"

    def recorder(self) -> str:
        now = time.monotonic()
        if self._rec_cache and now - self._rec_cache[0] < 60:
            return self._rec_cache[1]
        found = "none"
        try:
            importlib.import_module("numpy")
            importlib.import_module("sounddevice")  # raises OSError if PortAudio is missing
            found = "sounddevice"
        except Exception:  # not installed, or PortAudio missing (OSError)
            if which("arecord"):
                found = "arecord"
            elif which("rec"):
                found = "rec"
        self._rec_cache = (now, found)
        return found

    def available(self) -> tuple[bool, str]:
        if self.cfg.get("engine") == "off":
            return False, "voice input is off in config"
        if self.engine() == "none":
            return False, "no speech-to-text: set CARTESIA_API_KEY or pip install faster-whisper"
        if self.recorder() == "none":
            return False, "no microphone recorder: pip install sounddevice numpy"
        return True, f"{self.recorder()} + {self.engine()}"

    def toggle(self) -> str:
        listener = getattr(self.agent, "listener", None)
        if listener is not None and listener.running:
            listener.prompt()  # the microphone is already open: take the next sentence, no wake word needed
            return "Listening..."
        with self._lock:
            if self.listening:
                self._stop.set()
                proc = self._proc
                if proc and proc.poll() is None:
                    proc.terminate()
                return "Stopped listening."
            ok, why = self.available()
            if not ok:
                raise ToolError("Voice input isn't set up: " + why + ". You can also use the mic button "
                                "in the dashboard.")
            self.listening = True
            self._stop.clear()
        threading.Thread(target=self._session, name="voice", daemon=True).start()
        return "Listening..."

    def _session(self) -> None:
        a = self.agent
        try:
            a.speaker.stop()
            if self.cfg.get("beep", True):
                a.speaker.beep("start", wait=True)
            a.events.add("voice", "Listening...")
            t0 = time.time()
            pcm = self._record()
            a.last_utterance = (t0, time.time())  # where the user looked while talking (eye tracker)
            if self.cfg.get("beep", True):
                a.speaker.beep("stop", wait=False)
            if len(pcm) < RATE * 2 * 0.3:
                a.events.add("voice", "Didn't hear anything.")
                return
            wav = wav_bytes(pcm)
            text = self.transcribe(wav)
            if not text:
                a.events.add("voice", "Didn't catch that.")
                a.speaker.say("Sorry, I didn't catch that.")
                return
            a.handle_text(text, source="voice")
        except ToolError as e:
            a.events.add("error", str(e))
            a.speaker.say(str(e))
        except Exception as e:
            log.exception("voice input failed")
            a.events.add("error", f"Voice input failed: {e}")
        finally:
            with self._lock:
                self.listening = False
                self._proc = None

    def transcribe(self, wav: bytes) -> str:
        engine = self.engine()
        lang = self.cfg.get("language", "en")
        if engine == "cartesia":
            return cartesia_stt(wav, lang)
        if engine == "whisper":
            return whisper_stt(wav, lang, self.cfg.get("whisper_model", "base.en"), prompt="Ally,")
        raise ToolError("No speech-to-text engine is set up.")

    def _record(self) -> bytes:
        rec = self.recorder()
        max_s = float(self.cfg.get("max_seconds", 8))
        if rec == "sounddevice":
            return self._record_sounddevice(max_s, float(self.cfg.get("silence_seconds", 1.2)))
        if rec in ("arecord", "rec"):
            path = tempfile_path(".wav")
            argv = (["arecord", "-q", "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-d", str(int(max_s)), path]
                    if rec == "arecord" else
                    ["rec", "-q", "-c", "1", "-r", str(RATE), "-b", "16", path, "trim", "0", str(int(max_s))])
            try:
                with self._lock:
                    self._proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                  creationflags=NO_WINDOW)
                self._proc.wait(timeout=max_s + 5)
                with wave.open(path, "rb") as w:
                    return w.readframes(w.getnframes())
            except (OSError, wave.Error, subprocess.TimeoutExpired) as e:
                raise ToolError(f"Recording failed: {e}")
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass
        raise ToolError("No microphone recorder available. pip install sounddevice numpy")

    def _record_sounddevice(self, max_s: float, silence_s: float) -> bytes:
        import numpy as np  # type: ignore
        import sounddevice as sd  # type: ignore

        block = RATE // 10  # 100 ms
        chunks, heard, quiet, noise = [], False, 0.0, None
        start = time.monotonic()
        try:
            with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=block) as stream:
                while not self._stop.is_set():
                    data, _overflow = stream.read(block)
                    chunks.append(data.copy())
                    level = float(np.sqrt(np.mean(data.astype(np.float32) ** 2)))
                    elapsed = time.monotonic() - start
                    if noise is None or (elapsed < 0.3 and not heard):
                        noise = level if noise is None else min(noise, level) * 0.5 + level * 0.5
                    threshold = max(350.0, (noise or 0) * 2.5)
                    if level > threshold:
                        heard, quiet = True, 0.0
                    else:
                        quiet += 0.1
                    if elapsed >= max_s or (heard and quiet >= silence_s) or (not heard and elapsed > 5):
                        break
        except Exception as e:
            raise ToolError(f"Microphone error: {e}. Check microphone permission for the app running Ally.")
        return b"".join(c.tobytes() for c in chunks) if heard else b""
