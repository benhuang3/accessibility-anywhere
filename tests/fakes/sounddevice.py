"""A fake sounddevice for tests/e2e_voice.py: a microphone that plays a scripted tape.

FAKE_MIC_TAPE is a JSON file: {"sessions": [[{"at": 0.8, "seconds": 0.6}, ...], ...]}. Each time a
stream opens, the next session plays: speech-loud noise at each "at" (seconds after the stream
opened) for "seconds", quiet room noise otherwise. Blocks are paced in real time.
"""

import json
import os
import random
import time

import numpy as np

_state = {"mtime": None, "next": 0}


def _session():
    path = os.environ.get("FAKE_MIC_TAPE", "")
    try:
        mt = os.path.getmtime(path)
        data = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if mt != _state["mtime"]:
        _state["mtime"], _state["next"] = mt, 0
    sessions = data.get("sessions") or []
    i = _state["next"]
    _state["next"] += 1
    log = os.environ.get("FAKE_AUDIO_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "what": "mic-open", "session": i}) + "\n")
    return sessions[i] if i < len(sessions) else []


class InputStream:
    def __init__(self, samplerate=16000, channels=1, dtype="int16", blocksize=1600, **_):
        self.rate, self.block = samplerate, blocksize
        self.segments = _session()
        self.t0 = None
        self.n = 0
        self.rng = random.Random(7)

    def __enter__(self):
        self.t0 = time.monotonic()
        return self

    def __exit__(self, *a):
        return False

    def read(self, frames):
        due = self.t0 + (self.n + 1) * frames / self.rate
        delay = due - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        mid = (self.n + 0.5) * frames / self.rate
        self.n += 1
        loud = any(s["at"] <= mid < s["at"] + s["seconds"] for s in self.segments)
        amp = 4000 if loud else 60
        data = np.array([int(self.rng.gauss(0, amp)) for _ in range(frames)], dtype=np.int16).reshape(-1, 1)
        return data, False
