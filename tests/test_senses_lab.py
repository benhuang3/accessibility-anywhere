"""Lab tools for senses: the matrix recorder, replay, privacy check, and camera-helper checks.

These tests never open a camera. Helper processes are fakes.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from helpers import ROOT  # noqa: F401  (puts the repo on sys.path)

from ally.senses import matrix


def _face(**over):
    sig = {"squint_l": 0.1, "squint_r": 0.1, "smile": 0.0, "blink": 0.0, "yaw": 0.0, "pitch": 0.0,
           "width": 0.3, "light": 120.0}
    sig.update(over)
    return sig


class _Stdin:
    """Remembers writes after close, which is what the recorder does when it stops the helper."""

    def __init__(self):
        self.buf: list[str] = []

    def write(self, text):
        self.buf.append(text)
        return len(text)

    def flush(self):
        return None

    def close(self):
        return None

    def getvalue(self):
        return "".join(self.buf)


class _Proc:
    def __init__(self, lines: list[str]):
        self.stdout = io.StringIO("\n".join(lines) + "\n")
        self.stderr = io.StringIO("")
        self.stdin = _Stdin()
        self.pid = 4242
        self._rc = None

    def poll(self):
        return self._rc

    def wait(self, timeout=None):
        self._rc = 0
        return 0

    def kill(self):
        self._rc = -9


class _Clock:
    def __init__(self, boom_at: int | None = None):
        self.t = 1_000_000.0
        self.n = 0
        self.boom_at = boom_at

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.n += 1
        if self.boom_at is not None and self.n >= self.boom_at:
            raise KeyboardInterrupt
        self.t += float(seconds)


def _lines():
    junk = [0] * 50
    face = _face()
    return [
        json.dumps({"ready": True, "calibrated": False, "screen": [1920, 1080]}),
        json.dumps({"t": 1.0, "f": face, "frame": junk, "jpeg": "data:image/jpeg;base64," + ("A" * 800)}),
        json.dumps({"t": 2.0, "face": False}),
    ]


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ally-matrix-"))
        self.model = self.tmp / "face_landmarker.task"
        self.model.write_bytes(b"not a model")
        self.calls = []

    def _spawn(self, kind, command):
        self.calls.append((kind, command))
        if kind == "camera":
            return _Proc(_lines())
        return _Proc([json.dumps({"ready": True}),
                      json.dumps({"e": "r", "t": 1.5, "clickable": None})])

    def _record(self, **kw):
        clock = kw.pop("clock", None) or _Clock()
        buf = io.StringIO()
        import contextlib
        with contextlib.redirect_stdout(buf):
            code = matrix.record(
                "ada", "no", "bright", camera=0, run="test", root=self.tmp, model=self.model,
                spawn=self._spawn, sleep=clock.sleep, ally_running=lambda: False, now=clock.now,
                commit="abc123", senses={"fps": 10, "squint_delta": 0.2}, screen=(1920, 1080), **kw)
        return code, buf.getvalue(), clock

    def test_protocol_matches_the_script(self):
        parts = matrix.protocol(0)
        self.assertEqual(parts[0]["name"], "calibration")
        self.assertEqual(parts[0]["seconds"], 15)
        self.assertEqual(parts[0]["expect"], "baseline")
        names = [p["name"] for p in parts]
        for expected in ("read_normal", "read_tiny", "act_squint", "smile", "laugh", "look_down",
                         "close_left", "close_right", "turn_head", "lean", "lean_squint"):
            self.assertIn(expected, names)
        gaps = [p for p in parts if p["name"] == "gap"]
        self.assertTrue(gaps)
        self.assertTrue(all(p["seconds"] == 3 and p["expect"] == "gap" for p in gaps))
        by_name = {p["name"]: p for p in parts}
        self.assertEqual(by_name["read_normal"]["expect"], "none")
        self.assertEqual(by_name["read_tiny"]["expect"], "natural")
        self.assertEqual(by_name["read_tiny"]["size"], "tiny")
        self.assertEqual(by_name["act_squint"]["expect"], "squint")
        self.assertEqual(by_name["close_left"]["expect"], "none")
        self.assertEqual(by_name["close_right"]["expect"], "none")
        self.assertEqual(by_name["lean"]["expect"], "lean")
        self.assertEqual(by_name["lean_squint"]["expect"], "struggle")
        self.assertEqual(by_name["smile"]["expect"], "none")
        free = matrix.protocol(2)
        self.assertEqual(free[-1]["name"], "free")
        self.assertEqual(free[-1]["expect"], "none")
        self.assertEqual(free[-1]["seconds"], 120)

    def test_refuses_while_ally_is_running(self):
        buf = io.StringIO()
        import contextlib
        with contextlib.redirect_stdout(buf):
            code = matrix.record("ada", "yes", "dim", camera=0, run="nope", root=self.tmp, model=self.model,
                                 spawn=self._spawn, ally_running=lambda: True, senses={})
        self.assertEqual(code, 2)
        self.assertIn("python run.py stop", buf.getvalue())
        self.assertEqual(self.calls, [])

    def test_recording_is_numbers_only(self):
        code, text, clock = self._record()
        self.assertEqual(code, 0, text)
        path = self.tmp / "senses_runs" / "test" / "ada-no-bright.jsonl"
        self.assertTrue(path.exists())
        raw = path.read_text(encoding="utf-8")
        self.assertNotIn("jpeg", raw)
        self.assertNotIn("frame", raw)
        self.assertNotIn("base64", raw)
        rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
        self.assertEqual(rows[0]["rec"], "meta")
        self.assertEqual(rows[0]["alias"], "ada")
        self.assertEqual(rows[0]["glasses"], "no")
        self.assertEqual(rows[0]["light"], "bright")
        self.assertEqual(rows[0]["camera"], 0)
        self.assertEqual(rows[0]["screen"], [1920, 1080])
        self.assertEqual(rows[0]["commit"], "abc123")
        self.assertEqual(rows[0]["senses"]["squint_delta"], 0.2)
        labels = [r for r in rows if r["rec"] == "label"]
        self.assertEqual(labels[0]["name"], "calibration")
        self.assertEqual(labels[0]["expect"], "baseline")
        self.assertAlmostEqual(labels[0]["end"] - labels[0]["start"], 15)
        self.assertTrue(any(r["rec"] == "label" and r["expect"] == "gap" for r in labels))
        face = next(r for r in rows if r["rec"] == "helper" and "f" in r)
        self.assertEqual(set(face["f"]), {"squint_l", "squint_r", "smile", "blink", "yaw", "pitch", "width", "light"})
        self.assertTrue(all(isinstance(v, (int, float)) for v in face["f"].values()))
        unknown = next(r for r in rows if r["rec"] == "input" and r.get("e") == "r")
        self.assertIsNone(unknown["clickable"])
        kind, command = self.calls[0]
        self.assertEqual(kind, "camera")
        args = json.loads(command[-1])
        self.assertFalse(args["gaze"])
        self.assertTrue(args["signals"])
        self.assertIn("ally.gaze_helper", " ".join(command))

    def test_cards_are_sent_and_interrupt_keeps_the_file(self):
        seen = {}
        orig = self._spawn

        def spawn(kind, command):
            proc = orig(kind, command)
            seen[kind] = proc
            return proc

        clock = _Clock(boom_at=2)
        buf = io.StringIO()
        import contextlib
        with contextlib.redirect_stdout(buf):
            code = matrix.record(
                "ada", "no", "bright", camera=0, run="partial", root=self.tmp, model=self.model,
                spawn=spawn, sleep=clock.sleep, ally_running=lambda: False, now=clock.now,
                commit="abc123", senses={"fps": 10}, screen=(800, 600))
        self.assertEqual(code, 0)
        self.assertIn("Kept what was recorded", buf.getvalue())
        path = self.tmp / "senses_runs" / "partial" / "ada-no-bright.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(rows[0]["rec"], "meta")
        self.assertTrue(any(r["rec"] == "label" and r["name"] == "calibration" for r in rows))
        partial = [r for r in rows if r["rec"] == "label" and r.get("partial")]
        self.assertEqual(len(partial), 1)
        stdin = seen["camera"].stdin.getvalue()
        self.assertIn("card ", stdin)
        self.assertIn("Read this sentence", stdin)
        self.assertNotIn("frame", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
