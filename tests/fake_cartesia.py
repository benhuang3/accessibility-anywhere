"""A stand-in for the Cartesia API (tests and tests/e2e_voice.py). Standard library + nothing else.

Endpoints: POST /tts/bytes (WAV whose length matches the words and speed, like real speech),
POST /stt (multipart WAV -> the scripted transcript whose voiced length is closest), GET /voices.
Control: POST /__mode {...} changes behaviour, GET /__stats returns what was requested.

Modes: tts = ok | down (503) | hang (never answers in time) | reset (closes the connection);
stt = ok | down; reject_models = ["sonic-3.6"] (400 "model not found"); reject_emotion = true.
"""

from __future__ import annotations

import http.server
import io
import json
import math
import re
import struct
import threading
import time
import wave

VOICES = [
    {"id": "11111111-1111-4111-8111-111111111111", "name": "Tessa", "description": "Warm and kind",
     "language": "en", "gender": "feminine"},
    {"id": "22222222-2222-4222-8222-222222222222", "name": "Leo", "description": "Calm, low",
     "language": "en", "gender": "masculine"},
]


def speech_wav(text: str, speed: float = 1.0, rate: int = 24000, wpm: float = 165.0) -> bytes:
    """Audio as long as a person would take to say text (a quiet tone, not real speech)."""
    words = max(1, len(re.sub(r"<[^>]+>", " ", text).split()))
    seconds = words / (wpm * max(0.5, speed)) * 60.0 + 0.15
    n = int(seconds * rate)
    frames = bytearray()
    for i in range(n):
        frames += struct.pack("<h", int(600 * math.sin(2 * math.pi * 220 * i / rate)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return buf.getvalue()


def voiced_seconds(wav: bytes, threshold: float = 1000.0) -> float:
    with wave.open(io.BytesIO(wav), "rb") as w:
        rate, pcm = w.getframerate(), w.readframes(w.getnframes())
    block = rate // 10
    voiced = 0
    for i in range(0, len(pcm) // 2 - block + 1, block):
        vals = struct.unpack(f"<{block}h", pcm[i * 2:(i + block) * 2])
        if math.sqrt(sum(v * v for v in vals) / block) > threshold:
            voiced += 1
    return voiced / 10.0


class FakeCartesia:
    def __init__(self):
        self.mode = {"tts": "ok", "stt": "ok", "tts_latency": 0.0, "stt_latency": 0.0, "reject_models": [],
                     "reject_emotion": False, "stt_script": []}
        self.tts_requests: list[dict] = []
        self.stt_requests: list[dict] = []
        self.voice_requests = 0
        self.lock = threading.Lock()
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, code: int, body: bytes, ctype: str = "application/json"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _json(self, code: int, obj):
                self._send(code, json.dumps(obj).encode())

            def _auth_ok(self) -> bool:
                if not self.headers.get("Authorization", "").startswith("Bearer ") or not self.headers.get(
                        "Cartesia-Version"):
                    self._json(401, {"error": "missing auth or Cartesia-Version"})
                    return False
                return True

            def do_GET(self):
                if self.path.startswith("/__stats"):
                    with outer.lock:
                        return self._json(200, {"tts": outer.tts_requests, "stt": outer.stt_requests,
                                                "voices": outer.voice_requests})
                if not self._auth_ok():
                    return
                if self.path.startswith("/voices"):
                    outer.voice_requests += 1
                    q = re.search(r"[?&]q=([^&]*)", self.path)
                    term = (q.group(1) if q else "").lower()
                    return self._json(200, {"data": [v for v in VOICES if term in v["name"].lower()],
                                            "has_more": False, "next_page": None})
                self._json(404, {"error": "not found"})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                if self.path == "/__mode":
                    with outer.lock:
                        outer.mode.update(json.loads(raw or b"{}"))
                        if json.loads(raw or b"{}").get("reset_stats"):
                            outer.tts_requests.clear()
                            outer.stt_requests.clear()
                    return self._json(200, outer.mode)
                if not self._auth_ok():
                    return
                m = outer.mode
                if self.path == "/tts/bytes":
                    body = json.loads(raw)
                    with outer.lock:
                        outer.tts_requests.append({"t": time.time(), **body})
                    if m["tts"] == "down":
                        return self._json(503, {"error": "service unavailable"})
                    if m["tts"] == "reset":
                        self.close_connection = True
                        self.connection.close()
                        return
                    if m["tts"] == "hang":
                        time.sleep(30)
                        try:
                            self._json(504, {"error": "too slow"})
                        except OSError:
                            pass  # the client gave up long ago
                        return
                    if body.get("model_id") in m["reject_models"]:
                        return self._json(400, {"error": f"model {body.get('model_id')} not found"})
                    if m["reject_emotion"] and (body.get("generation_config") or {}).get("emotion"):
                        return self._json(400, {"error": "generation_config.emotion is not supported"})
                    for k in ("model_id", "transcript", "voice", "output_format"):
                        if k not in body:
                            return self._json(400, {"error": f"missing {k}"})
                    time.sleep(float(m["tts_latency"]))
                    speed = float((body.get("generation_config") or {}).get("speed", 1.0))
                    return self._send(200, speech_wav(body["transcript"], speed), "audio/wav")
                if self.path == "/stt":
                    if m["stt"] == "down":
                        return self._json(503, {"error": "service unavailable"})
                    start = raw.find(b"RIFF")
                    wav = raw[start:raw.rfind(b"\r\n--")]
                    secs = voiced_seconds(wav)
                    script = m.get("stt_script") or []
                    text = ""
                    if script:
                        best = min(script, key=lambda s: abs(float(s["seconds"]) - secs))
                        text = best["text"] if abs(float(best["seconds"]) - secs) <= 0.35 else ""
                    with outer.lock:
                        outer.stt_requests.append({"t": time.time(), "voiced": secs, "text": text})
                    time.sleep(float(m["stt_latency"]))
                    return self._json(200, {"text": text, "language": "en"})
                self._json(404, {"error": "not found"})

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def set(self, **mode) -> None:
        with self.lock:
            self.mode.update(mode)

    def reset(self) -> None:
        with self.lock:
            self.tts_requests.clear()
            self.stt_requests.clear()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


if __name__ == "__main__":
    f = FakeCartesia()
    print(f.url, flush=True)
    threading.Event().wait()
