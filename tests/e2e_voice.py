"""End-to-end test of spoken offers and yes/no answers, with the real agent process.

    python tests/e2e_voice.py            (about a minute; prints a metrics table, exits 1 on a failure)
    python tests/e2e_voice.py --json out.json

What is real: the agent (run.py agent), its HTTP API, config, VoiceConfirm, Speaker, the Cartesia
client code, the microphone segmenter, the fallback chain, the prompt cache and the latency log.
What is faked (so it runs anywhere, with no key, speakers or microphone):
- Cartesia: tests/fake_cartesia.py on localhost (CARTESIA_BASE_URL). Its audio lasts as long as
  real speech would, and it can be made slow, broken or picky about models.
- The microphone: tests/fakes/sounddevice.py plays a scripted tape of "speech" bursts; the fake
  speech-to-text maps each burst's length to a transcript.
- Speakers and the OS voice: tests/fakes/bin/aplay and espeak-ng wait as long as the sound lasts.
So the timings measure Ally's own overhead plus the simulated network latency set below, not
Cartesia's real latency (run python run.py voice-test with a real key for that).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from fake_cartesia import FakeCartesia  # noqa: E402

TTS_LATENCY = 0.20  # simulated Cartesia time to return audio (s)
STT_LATENCY = 0.15

CONFIG = """
[agent]
port = {port}
announce_start = false
hotkeys_enabled = false
speak_replies = false
[speech]
engine = "auto"
cartesia_voice_id = "Tessa"
cartesia_timeout_seconds = 3
cartesia_retry_seconds = 12
[voice]
engine = "cartesia"
always_listen = false
beep = false
[gaze]
enabled = false
[confirm]
listen_seconds = 3
"""


def pct(values: list, p: float):
    if not values:
        return None
    v = sorted(values)
    k = max(0, min(len(v) - 1, round(p / 100 * (len(v) - 1))))
    return v[k]


def _set_eager(config: str, eager: bool) -> str:
    return config.replace("listen_seconds = 3\n", f"listen_seconds = 3\neager_answers = {str(eager).lower()}\n")


def free_port() -> int:
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Harness:
    def __init__(self):
        self.fake = FakeCartesia()
        self.fake.set(tts_latency=TTS_LATENCY, stt_latency=STT_LATENCY)
        self.home = Path(tempfile.mkdtemp(prefix="ally-e2e-voice-"))
        self.port = free_port()
        (self.home / "config.toml").write_text(CONFIG.format(port=self.port), encoding="utf-8")
        (self.home / "plugins").mkdir()
        (self.home / ".env").write_text("CARTESIA_API_KEY=sk_car_test_not_real\n", encoding="utf-8")
        self.tape = self.home / "mic_tape.json"
        self.audio_log = self.home / "audio_log.jsonl"
        self.tape.write_text('{"sessions": []}', encoding="utf-8")
        env = dict(os.environ)
        env.update(CARTESIA_BASE_URL=self.fake.url, FAKE_MIC_TAPE=str(self.tape), FAKE_AUDIO_LOG=str(self.audio_log),
                   PYTHONPATH=str(HERE / "fakes") + os.pathsep + env.get("PYTHONPATH", ""),
                   PATH=str(HERE / "fakes" / "bin") + os.pathsep + env.get("PATH", ""))
        env.pop("CARTESIA_API_KEY", None)  # comes from the test home's .env
        self.proc = subprocess.Popen([sys.executable, str(ROOT / "run.py"), "agent", "--home", str(self.home),
                                      "--no-hotkeys"], env=env, cwd=str(ROOT),
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.token = ""
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                self.token = (self.home / "state" / "token").read_text().strip()
                if self.get("/health", auth=False).get("ok"):
                    return
            except Exception:
                pass
            time.sleep(0.2)
        raise SystemExit("the agent didn't start; see " + str(self.home / "logs"))

    # ---- http
    def _req(self, method: str, path: str, body=None, auth=True, timeout=60):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["X-Ally-Token"] = self.token
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method, headers=headers)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode())

    def get(self, path, auth=True):
        return self._req("GET", path, auth=auth)

    def post(self, path, body, timeout=60):
        return self._req("POST", path, body, timeout=timeout)

    # ---- scenario helpers
    def script(self, sessions: list, stt: list) -> None:
        self.tape.write_text(json.dumps({"sessions": sessions}), encoding="utf-8")
        time.sleep(0.02)  # a fresh mtime
        os.utime(self.tape)
        self.fake.set(stt_script=stt)

    def audio_events(self, since: float) -> list[dict]:
        try:
            lines = self.audio_log.read_text().splitlines()
        except OSError:
            return []
        return [e for e in map(json.loads, lines) if e["t"] >= since]

    def close(self):
        try:
            self.post("/api/shutdown", {}, timeout=5)
        except Exception:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.fake.close()


def run(json_out: str | None = None) -> int:
    h = Harness()
    results: list[dict] = []
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        if not ok:
            failures.append(f"{name}: {detail}")

    def ask(name, prompt, sessions, stt, expect, kind="offer", setup=None, during=None, path="/api/voice/ask",
            body=None):
        if setup:
            setup()
        h.script(sessions, stt)
        h.fake.reset()
        t0 = time.time()
        if during:
            threading.Thread(target=during, daemon=True).start()
        out = h.post(path, body or {"prompt": prompt, "kind": kind}, timeout=90)
        wall = round((time.time() - t0) * 1000)
        stats = h.get("/api/voice")
        aud = h.audio_events(t0)
        row = {"scenario": name, "answer": out.get("value"), "by": out.get("by"), "intent": out.get("intent"),
               "attempts": out.get("attempts"), "wall_ms": wall, **(out.get("latency") or {}),
               "tts_requests": len(h.fake.tts_requests), "stt_requests": len(h.fake.stt_requests),
               "os_voice": sum(1 for e in aud if e["what"] == "espeak"),
               "played": sum(1 for e in aud if e["what"] == "aplay"), "transcript": out.get("transcript", ""),
               "speech_engine": stats["speech"]["last_engine"]}
        results.append(row)
        check(name, out.get("value") == expect[0] and (expect[1] is None or out.get("by") == expect[1]),
              f"expected {expect}, got {out.get('value')} by {out.get('by')} ({out.get('intent')})")
        return row

    # Wait for the prompt set to be pre-rendered at start (cache warm).
    deadline = time.time() + 20
    while time.time() < deadline and not h.get("/api/voice").get("prerendered"):
        time.sleep(0.2)
    pre = h.get("/api/voice")
    check("prerender", pre.get("prerendered", 0) >= 10, f"only {pre.get('prerendered')} prompts pre-rendered")
    check("voice name resolved", h.fake.voice_requests >= 1, "Tessa was never looked up")

    YES = [{"seconds": 0.5, "text": "Yeah, sure."}]
    say_at = lambda secs: [{"at": 0.5, "seconds": secs}]  # noqa: E731

    ask("yes (cached prompt)", "Want me to make this bigger?", [say_at(0.5)], YES, ("yes", "voice"))
    ask("no thanks", "Want me to make this bigger?", [say_at(0.9)], [{"seconds": 0.9, "text": "No thanks."}],
        ("no", "voice"))
    ask("uh-huh (backchannel)", "Want me to make this bigger?", [say_at(0.5)], [{"seconds": 0.5, "text": "Mm-hmm."}],
        ("yes", "voice"))
    ask("silence -> timeout", "Want me to make this bigger?", [[]], [], ("timeout", None))
    ask("noise only -> timeout", "Want me to make this bigger?", [say_at(0.3)], [{"seconds": 0.3, "text": "Thank you."}],
        ("timeout", None))
    ask("unclear -> re-ask -> yes", "Want me to make this bigger?", [say_at(1.3), say_at(0.5)],
        [{"seconds": 1.3, "text": "Hmm, maybe?"}, {"seconds": 0.5, "text": "Yes please."}], ("yes", "voice"))
    ask("unclear twice -> buttons", "Want me to make this bigger?", [say_at(1.3), say_at(1.3)],
        [{"seconds": 1.3, "text": "Yeah, no."}], ("timeout", None))
    ask("'slower' -> replay -> yes", "Want me to turn on high contrast?", [say_at(1.7), say_at(0.5)],
        [{"seconds": 1.7, "text": "Slower, please."}, {"seconds": 0.5, "text": "Yes."}], ("yes", "voice"))
    ask("uncached prompt", "Want me to read this page to you?", [say_at(0.5)], YES, ("yes", "voice"))
    ask("answer starts late (2.6 s of 3)", "Want me to make this bigger?", [[{"at": 2.6, "seconds": 1.1}]],
        [{"seconds": 1.1, "text": "Yes, go ahead."}], ("yes", "voice"))
    ask("pause inside the answer", "Want me to make this bigger?",
        [[{"at": 0.5, "seconds": 0.3}, {"at": 1.3, "seconds": 0.5}]],
        [{"seconds": 0.3, "text": "Um."}, {"seconds": 0.8, "text": "Um. Yes."}, {"seconds": 0.5, "text": "Yes."}],
        ("yes", "voice"))
    ask("button wins mid-prompt", "Want me to make the text much bigger for you today?", [say_at(0.5)], YES,
        ("yes", "cli"), during=lambda: (time.sleep(0.4), h.post("/api/voice/answer", {"answer": "yes", "by": "cli"})))
    ask("scam warning -> 'close it'", "", [say_at(0.7)], [{"seconds": 0.7, "text": "Close it."}], ("yes", "voice"),
        path="/api/voice/warn", body={"kind": "scam_popup", "severity": "high"})
    ask("remote access -> 'no'", "", [say_at(0.9)], [{"seconds": 0.9, "text": "No."}], ("no", "voice"),
        path="/api/voice/warn", body={"kind": "remote_access", "severity": "high"})

    # Fallbacks.
    ask("Cartesia TTS down, cached prompt", "Want me to make this bigger?", [say_at(0.5)], YES, ("yes", "voice"),
        setup=lambda: h.fake.set(tts="down"))
    r = ask("Cartesia TTS down, new prompt", "Want me to zoom in on this?", [say_at(0.5)], YES, ("yes", "voice"))
    check("OS voice fallback used", r["os_voice"] >= 1, "the built-in voice never spoke")
    time.sleep(12.5)  # let the breaker from the 503s above clear, so the hang below is really tried
    r = ask("Cartesia TTS hangs, new prompt", "Want me to make the mouse bigger?", [say_at(0.5)], YES,
            ("yes", "voice"), setup=lambda: h.fake.set(tts="hang"))
    r2 = ask("Cartesia TTS hangs, 2nd new prompt", "Want me to turn the volume up?", [say_at(0.5)], YES,
             ("yes", "voice"))
    check("circuit breaker", (r2.get("prompt_to_audio_ms") or 9999) < 500,
          f"2nd prompt still waited {r2.get('prompt_to_audio_ms')} ms for Cartesia")
    h.fake.set(tts="ok")
    time.sleep(12.5)  # past cartesia_retry_seconds
    r3 = ask("Cartesia back after retry window", "Want me to show the cursor?", [say_at(0.5)], YES, ("yes", "voice"))
    check("Cartesia recovers", r3["speech_engine"] == "cartesia", f"still using {r3['speech_engine']}")
    ask("sonic-3.6 rejected -> sonic-3", "Want me to open reader mode?", [say_at(0.5)], YES, ("yes", "voice"),
        setup=lambda: h.fake.set(reject_models=["sonic-3.6"]))
    models = sorted({b.get("model_id") for b in h.fake.tts_requests})
    check("model fallback", "sonic-3" in models, f"models tried: {models}")
    h.fake.set(reject_models=[])
    ask("Cartesia STT down (no Whisper)", "Want me to make this bigger?", [say_at(0.5)], YES, ("timeout", None),
        setup=lambda: h.fake.set(stt="down"))
    h.fake.set(stt="ok")

    # Latency benchmark: the same yes/no exchange, with and without eager answers.
    bench = {}
    for eager in (False, True):
        (h.home / "config.toml").write_text(_set_eager(CONFIG.format(port=h.port), eager))
        time.sleep(2.0)  # config hot-reload
        replies, firsts = [], []
        for i in range(10):
            h.script([say_at(0.5)], [{"seconds": 0.5, "text": "Yes please."}])
            out = h.post("/api/voice/ask", {"prompt": "Want me to make this bigger?"}, timeout=60)
            lat = out.get("latency") or {}
            check(f"bench eager={eager} #{i}", out.get("value") == "yes", str(out.get("intent")))
            if lat.get("speech_end_to_answer_ms") is not None:
                replies.append(lat["speech_end_to_answer_ms"])
            if lat.get("prompt_to_audio_ms") is not None:
                firsts.append(lat["prompt_to_audio_ms"])
        bench["eager" if eager else "full_pause"] = {"reply_p50": pct(replies, 50), "reply_p95": pct(replies, 95),
                                                     "first_audio_p50": pct(firsts, 50),
                                                     "first_audio_p95": pct(firsts, 95), "n": len(replies)}

    # Latency log on disk.
    log_rows = [json.loads(x) for x in (h.home / "logs" / "voice_latency.jsonl").read_text().splitlines()]
    check("latency log", len(log_rows) >= len(results) - 1, f"{len(log_rows)} rows for {len(results)} questions")
    status = h.get("/api/status")
    check("status has confirm", "confirm" in status and "speech" in status, "missing keys")
    h.close()

    cols = ["scenario", "answer", "by", "intent", "attempts", "prompt_to_audio_ms", "speech_end_to_answer_ms",
            "stt_ms", "wall_ms", "tts_requests", "os_voice", "speech_engine"]
    widths = [34, 7, 6, 10, 3, 8, 8, 6, 7, 4, 3, 15]
    print("  ".join(c[:w].ljust(w) for c, w in zip(["scenario", "answer", "by", "intent", "att", "1st aud",
                                                       "reply", "stt", "total", "tts", "os", "engine"], widths)))
    for r in results:
        print("  ".join(str(r.get(c, "") if r.get(c) is not None else "")[:w].ljust(w) for c, w in zip(cols, widths)))
    print()
    print("Latency benchmark (10 trials each, cached prompt, 'Yes please.'; ms):")
    for k, v in bench.items():
        print(f"  {k:11} reply p50 {v['reply_p50']}  p95 {v['reply_p95']}   first audio p50 {v['first_audio_p50']}"
              f"  p95 {v['first_audio_p95']}")
    print()
    if failures:
        print("FAILURES:")
        for f in failures:
            print("  -", f)
    else:
        print(f"All {len(results)} scenarios passed.")
    if json_out:
        Path(json_out).write_text(json.dumps({"results": results, "failures": failures, "bench": bench,
                                              "simulated": {"tts_latency_s": TTS_LATENCY,
                                                            "stt_latency_s": STT_LATENCY}}, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    sys.exit(run(ap.parse_args().json))
