"""Text-to-speech: the OS voice by default, Cartesia (natural voice) when configured.

New speech interrupts old speech. stop() silences everything immediately.
Cartesia: POST https://api.cartesia.ai/tts/bytes, headers Authorization: Bearer <key> and
Cartesia-Version, returns WAV bytes that the OS audio player plays.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import struct
import subprocess
import threading
import urllib.error
import urllib.request
import wave

from .claude import ssl_context
from .platforms.base import Platform, tempfile_path
from .registry import ToolError

log = logging.getLogger("ally.speech")

CARTESIA_URL = "https://api.cartesia.ai"
CARTESIA_VERSION = "2026-08-14"


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


def cartesia_tts(text: str, voice_id: str, model: str = "sonic-3.6", speed: float = 1.0,
                 language: str = "en", timeout: float = 20) -> bytes:
    key = os.environ.get("CARTESIA_API_KEY", "")
    if not key:
        raise ToolError("Set CARTESIA_API_KEY in the .env file to use the Cartesia voice.")
    if not voice_id:
        raise ToolError("Set speech.cartesia_voice_id in config.toml (copy one from https://play.cartesia.ai).")
    body = {
        "model_id": model,
        "transcript": text,
        "voice": {"mode": "id", "id": voice_id},
        "language": language,
        "output_format": {"container": "wav", "encoding": "pcm_s16le", "sample_rate": 24000},
        "generation_config": {"speed": max(0.6, min(1.5, speed))},
    }
    req = urllib.request.Request(
        CARTESIA_URL + "/tts/bytes", data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {key}", "Cartesia-Version": CARTESIA_VERSION,
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300] if e.fp else ""
        raise ToolError(f"Cartesia TTS error {e.code}: {detail}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ToolError(f"Can't reach Cartesia: {getattr(e, 'reason', e)}")


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
    def __init__(self, platform: Platform, settings):
        """settings: a callable returning the current [speech] config dict."""
        self.platform = platform
        self.settings = settings
        self.rate_override: float | None = None
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._gen = 0
        self._thread: threading.Thread | None = None
        self.last_error = ""
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

    def say(self, text: str, interrupt: bool = True, wait: bool = False) -> bool:
        cfg = self.settings()
        engine = cfg.get("engine", "system")
        text = (text or "").strip()
        if engine == "off" or not text:
            return False
        limit = int(cfg.get("max_chars", 4000))
        if len(text) > limit:
            text = text[:limit].rsplit(" ", 1)[0] + "."
        if interrupt:
            self.stop()
        with self._lock:
            self._gen += 1
            gen = self._gen
        t = threading.Thread(target=self._run, args=(gen, text, engine, cfg), name="speech", daemon=True)
        self._thread = t
        t.start()
        if wait:
            t.join()
        return True

    def _active(self, gen: int) -> bool:
        return gen == self._gen

    def _play(self, gen: int, start) -> None:
        """start() begins the sound and returns a Popen-like handle; this waits for it to end."""
        with self._lock:
            if not self._active(gen):
                return
            proc = self._proc = start()
        proc.wait()  # a local: stop() may clear self._proc meanwhile

    def _run(self, gen: int, text: str, engine: str, cfg: dict) -> None:
        try:
            if engine == "cartesia":
                try:
                    self._run_cartesia(gen, text, cfg)
                    return
                except ToolError as e:
                    self.last_error = str(e)
                    log.warning("Cartesia speech failed, using the system voice: %s", e)
            rate, voice = self.rate(), cfg.get("voice", "")
            self._play(gen, lambda: self.platform.tts_start(text, rate, voice))
        except ToolError as e:
            self.last_error = str(e)
            log.warning("speech failed: %s", e)
        except OSError as e:
            self.last_error = str(e)
            log.warning("speech program failed: %s", e)

    def _run_cartesia(self, gen: int, text: str, cfg: dict) -> None:
        for piece in chunks(text):
            if not self._active(gen):
                return
            audio = cartesia_tts(piece, cfg.get("cartesia_voice_id", ""), cfg.get("cartesia_model", "sonic-3.6"),
                                 speed=self.rate())
            if not self._active(gen):
                return
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
