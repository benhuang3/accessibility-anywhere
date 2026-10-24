"""Lab tools for senses: the matrix recorder, replay, privacy check, and camera-helper checks.

These tests never open a camera. Helper processes are fakes.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path

from helpers import ROOT  # noqa: F401  (puts the repo on sys.path)

from ally.gaze_helper import (CARD_SCALES, INDICATOR_EX_STYLE, INDICATOR_TEXT, classify_command, note_once,
                              parse_card)
from ally.senses import evaluate, matrix


def _privacy():
    path = ROOT / "scripts" / "privacy_check.py"
    spec = importlib.util.spec_from_file_location("privacy_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


privacy = _privacy()


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


def _frames(t0, seconds, dt=0.1, **over):
    n = int(round(seconds / dt))
    return [(round(t0 + i * dt, 3), _face(**over)) for i in range(n)]


def _dump(path: Path, alias: str, glasses: str, light: str, faces, labels, senses: dict):
    rows = [{"rec": "meta", "alias": alias, "glasses": glasses, "light": light, "camera": 0,
             "screen": [100, 100], "senses": senses, "commit": "test", "started": 0}]
    for label in labels:
        rows.append({"rec": "label", **label})
    for t, sig in faces:
        rows.append({"rec": "helper", "t": t, "f": sig})
    rows.append({"rec": "input", "e": "r", "t": 10.2, "clickable": None})
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


_REPLAY_SENSES = {"cooldown_seconds": 0, "rearm_seconds": 0, "fps": 10, "solo": [], "window_seconds": 4,
                  "min_confidence": 0.5}


def _label(name, start, end, expect):
    return {"name": name, "start": start, "end": end, "expect": expect}


class EvaluateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ally-eval-"))
        self.cfg = ROOT / "config.toml"
        self.cfg_before = self.cfg.read_bytes() if self.cfg.exists() else None
        self.state_before = (ROOT / "state").exists()

    def tearDown(self):
        if self.cfg.exists():
            self.assertEqual(self.cfg.read_bytes(), self.cfg_before)
        self.assertEqual((ROOT / "state").exists(), self.state_before)

    def test_report_counts_false_positives_and_misses(self):
        faces = []
        faces += _frames(0.0, 4.0)
        faces += _frames(6.0, 1.5)
        faces += _frames(10.0, 2.0)
        faces += _frames(20.0, 3.0, squint_l=0.5, squint_r=0.5)
        faces += _frames(24.0, 2.0)
        faces += _frames(30.0, 0.4, squint_l=0.5, squint_r=0.5)
        faces += _frames(40.0, 2.0)
        faces += _frames(50.0, 4.0, squint_l=0.5, squint_r=0.5, width=0.45)
        faces += _frames(56.0, 2.0)
        faces += _frames(70.0, 4.0, squint_l=0.5, squint_r=0.5, width=0.45)
        labels = [
            _label("calibration", 0, 5, "baseline"),
            _label("gap", 5, 9, "gap"),
            _label("read_normal", 10, 18, "none"),
            _label("read_tiny", 20, 28, "natural"),
            _label("act_squint", 30, 38, "squint"),
            _label("gap2", 38, 48, "gap"),
            _label("lean_squint", 50, 60, "struggle"),
            _label("gap3", 60, 68, "gap"),
            _label("free", 70, 80, "none"),
        ]
        path = self.tmp / "ada-no-bright.jsonl"
        _dump(path, "ada", "no", "bright", faces, labels, _REPLAY_SENSES)
        run = evaluate.load_run(path)
        totals = evaluate.score_runs([run])
        self.assertEqual(totals.error, "")
        self.assertGreaterEqual(totals.struggle_fp, 1)
        self.assertGreaterEqual(totals.struggle_fn, 1)
        by_name = {seg["segment"]: seg for seg in totals.segments}
        self.assertEqual(by_name["read_normal"]["struggle_fp"], 0)
        self.assertEqual(by_name["act_squint"]["struggle_fn"], 1)
        self.assertEqual(by_name["lean_squint"]["struggle_fn"], 0)
        self.assertGreater(by_name["lean_squint"]["latency_s"], 0)
        self.assertGreaterEqual(by_name["free"]["struggle_fp"], 1)
        self.assertEqual(by_name["read_tiny"]["struggle_fp"], 0)
        self.assertGreaterEqual(totals.natural_signals, 1)
        self.assertEqual(totals.natural_struggles, 0)
        self.assertGreaterEqual(totals.signal_fp, 1)
        self.assertGreaterEqual(totals.signal_fn_by[("squint", "strong")], 1)
        md = evaluate.write_report([path], self.tmp)
        self.assertIn("per minute", md)
        self.assertIn("no", md)
        csv_text = (self.tmp / "senses-report.csv").read_text(encoding="utf-8")
        self.assertIn("__fp_per_minute__", csv_text)
        rates = evaluate.fp_per_minute([(run, totals)])
        self.assertEqual(rates[0]["glasses"], "no")
        self.assertEqual(rates[0]["light"], "bright")
        self.assertGreater(rates[0]["per_minute"], 0)

    def _tuner_file(self, alias: str) -> Path:
        faces = []
        faces += _frames(0.0, 4.0)
        faces += _frames(6.0, 2.0)
        # Just over the default squint and lean lines: a struggle while the label says none.
        faces += _frames(10.0, 3.0, squint_l=0.31, squint_r=0.31, width=0.36)
        faces += _frames(14.0, 2.0)
        faces += _frames(20.0, 4.0, squint_l=0.7, squint_r=0.7, width=0.55)
        labels = [
            _label("calibration", 0, 5, "baseline"),
            _label("gap", 5, 9, "gap"),
            _label("read_normal", 10, 16, "none"),
            _label("gap2", 16, 19, "gap"),
            _label("lean_squint", 20, 28, "struggle"),
        ]
        path = self.tmp / f"{alias}.jsonl"
        _dump(path, alias, "yes", "dim", faces, labels, _REPLAY_SENSES)
        return path

    def test_tune_needs_two_people_and_clears_false_positives(self):
        one = self._tuner_file("ada")
        code, text = evaluate.recommendation(evaluate.load_runs([one]))
        self.assertEqual(code, 2)
        self.assertIn("2 people", text)
        same = self._tuner_file("ada")
        code, text = evaluate.recommendation(evaluate.load_runs([one, same]))
        self.assertEqual(code, 2, text)

        other = self._tuner_file("bea")
        runs = evaluate.load_runs([one, other])
        before = evaluate.score_runs(runs)
        self.assertGreaterEqual(before.struggle_fp, 1)
        self.assertEqual(before.struggle_fn, 0)
        tune = evaluate.search(runs)
        after = evaluate.score_runs(runs, tune)
        self.assertEqual(after.struggle_fp, 0, evaluate.toml_snippet(tune))
        self.assertEqual(after.struggle_fn, 0, evaluate.toml_snippet(tune))
        code, text = evaluate.recommendation(runs)
        self.assertEqual(code, 0, text)
        self.assertIn("[senses]", text)
        self.assertIn("Held-out struggle false positives: 0", text)
        self.assertIn("Held-out false negatives: 0", text)
        self.assertIn("Margin to the nearest false positive", text)


_HOLD = """
import socket, subprocess, sys, time
mode = sys.argv[1]
kind = sys.argv[2] if len(sys.argv) > 2 else ""
if mode == "listen":
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    print("up", flush=True)
    time.sleep(30)
elif mode == "quiet":
    print("up", flush=True)
    time.sleep(30)
else:
    child = subprocess.Popen([sys.executable, __file__, kind], stdout=subprocess.PIPE, text=True)
    child.stdout.readline()
    print("up", flush=True)
    child.wait()
"""


class PrivacyCheckTests(unittest.TestCase):
    def test_netstat_lines_match_the_pid(self):
        text = (
            "  TCP    127.0.0.1:5555         0.0.0.0:0              LISTENING       42\n"
            "  UDP    0.0.0.0:5353           *:*                                    42\n"
            "  TCP    10.0.0.2:5555          1.2.3.4:443            ESTABLISHED     99\n"
        )
        own = privacy.parse_netstat(text, {42})
        self.assertEqual(len(own), 2)
        self.assertEqual(privacy.parse_netstat(text, {7}), [])

    def test_new_image_file_is_reported(self):
        shot = Path("shot.png")
        self.assertEqual(privacy.new_media(set(), {shot}), [shot])
        self.assertEqual(privacy.new_media({shot}, {shot}), [])

    def test_report_includes_the_manual_steps(self):
        text = privacy.format_report(True, 60, 60, [], [4])
        self.assertIn("PASS", text)
        self.assertIn("Wi-Fi", text)
        self.assertIn("Resource Monitor", text)
        failed = privacy.format_report(False, 1, 1, ["network endpoint pid 4"], [4])
        self.assertIn("FAIL", failed)

    def test_socket_child_is_flagged_and_a_quiet_child_passes(self):
        folder = Path(tempfile.mkdtemp(prefix="ally-privacy-"))
        script = folder / "hold.py"
        script.write_text(_HOLD, encoding="utf-8")
        loud = subprocess.Popen([sys.executable, str(script), "spawn", "listen"], stdout=subprocess.PIPE, text=True)
        quiet = subprocess.Popen([sys.executable, str(script), "spawn", "quiet"], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(_read_line(loud), "up")
            self.assertEqual(_read_line(quiet), "up")
            loud_hits = privacy.network_hits([loud.pid])
            quiet_hits = privacy.network_hits([quiet.pid])
            self.assertIsNotNone(loud_hits)
            self.assertTrue(loud_hits, loud_hits)
            self.assertIsNotNone(quiet_hits)
            self.assertEqual(quiet_hits, [], quiet_hits)
        finally:
            for proc in (loud, quiet):
                privacy.kill_tree(proc.pid)
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=3)
                except Exception:
                    pass
                try:
                    proc.stdout.close()
                except Exception:
                    pass


def _read_line(proc, timeout: float = 10) -> str:
    box: list[str] = []

    def read():
        box.append(proc.stdout.readline())

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        privacy.kill_tree(proc.pid)
        raise AssertionError("process did not start")
    return (box[0] if box else "").strip()


class CardCommandTests(unittest.TestCase):
    def test_card_json_and_junk_never_crash(self):
        kind, spec = classify_command('card {"text": "Read this", "title": "Small", "size": "tiny", "seconds": 8}')
        self.assertEqual(kind, "card")
        self.assertEqual(spec["size"], "tiny")
        self.assertEqual(spec["title"], "Small")
        self.assertEqual(spec["seconds"], 8)
        self.assertEqual(classify_command("card off"), ("card_off", None))
        self.assertEqual(classify_command("card {"), ("ignore", None))
        self.assertEqual(classify_command("card"), ("ignore", None))
        self.assertEqual(classify_command("nope"), ("ignore", None))
        self.assertEqual(classify_command(""), ("ignore", None))
        self.assertEqual(classify_command("hud ["), ("hud_on", None))
        kind, rows = classify_command('hud [["squint", "hot"]]')
        self.assertEqual(kind, "hud_rows")
        self.assertEqual(rows, [["squint", "hot"]])
        self.assertEqual(classify_command("hud off"), ("hud_off", None))
        self.assertEqual(classify_command("calibrate"), ("calibrate", None))
        fixed = parse_card('{"text": "Read", "size": "huge", "seconds": "nope"}')
        self.assertEqual(fixed["size"], "normal")
        self.assertEqual(fixed["seconds"], 0)
        self.assertIsNone(parse_card('{"title": "only a title"}'))
        line = matrix.card_line({"text": "Hello", "title": "Setup", "size": "large", "seconds": 15})
        kind, spec = classify_command(line)
        self.assertEqual(kind, "card")
        self.assertEqual((spec["size"], spec["seconds"], spec["text"]), ("large", 15, "Hello"))

    def test_tiny_text_is_smaller_than_normal(self):
        self.assertLess(CARD_SCALES["tiny"], CARD_SCALES["normal"])
        self.assertLess(CARD_SCALES["normal"], CARD_SCALES["large"])


class SelfCheckTests(unittest.TestCase):
    def test_parts_last_about_a_minute(self):
        parts = matrix.selfcheck_parts()
        self.assertAlmostEqual(sum(p["seconds"] for p in parts), 60)
        self.assertEqual([p["name"] for p in parts],
                         ["settle", "left", "right", "smile", "blink", "turn", "lean"])

    def test_score_passes_a_clear_run_and_fails_a_swap(self):
        from ally.senses.detectors import Tuning
        tune = Tuning.from_config({})
        good = {
            "settle": [_face(width=0.30, light=120)],
            "left": [_face(squint_l=0.60, squint_r=0.10)],
            "right": [_face(squint_l=0.10, squint_r=0.60)],
            "smile": [_face(smile=0.50)],
            "blink": [_face(blink=0.80)],
            "turn": [_face(yaw=30)],
            "lean": [_face(width=0.40)],
        }
        rows = matrix.score_selfcheck(good, tune)
        self.assertTrue(all(row["ok"] for row in rows), rows)
        swapped = dict(good)
        swapped["left"] = [_face(squint_l=0.10, squint_r=0.60)]
        bad = {row["name"]: row for row in matrix.score_selfcheck(swapped, tune)}
        self.assertFalse(bad["left"]["ok"])
        self.assertIn("squint_l 0.10", bad["left"]["measured"])
        self.assertIn("swapped", bad["left"]["hint"])
        equal_turn = dict(good)
        equal_turn["turn"] = [_face(yaw=tune.pose_gate)]
        turn = {row["name"]: row for row in matrix.score_selfcheck(equal_turn, tune)}
        self.assertFalse(turn["turn"]["ok"])
        dark = dict(good)
        dark["settle"] = [_face(width=0.30, light=10)]
        light = {row["name"]: row for row in matrix.score_selfcheck(dark, tune)}
        self.assertFalse(light["light"]["ok"])
        self.assertIn("more light", light["light"]["hint"])

    def test_selfcheck_sends_cards_and_prints_pass(self):
        tmp = Path(tempfile.mkdtemp(prefix="ally-selfcheck-"))
        model = tmp / "face_landmarker.task"
        model.write_bytes(b"not a model")
        clock = _Clock()
        base = clock.t
        spans = [("settle", 6, _face(width=0.30, light=120)),
                 ("left", 16, _face(squint_l=0.60, squint_r=0.10)),
                 ("right", 24, _face(squint_l=0.10, squint_r=0.60)),
                 ("smile", 32, _face(smile=0.50)),
                 ("blink", 40, _face(blink=0.80)),
                 ("turn", 48, _face(yaw=30)),
                 ("lean", 56, _face(width=0.40))]
        lines = [json.dumps({"ready": True, "calibrated": False, "screen": [1920, 1080]})]
        lines += [json.dumps({"t": base + offset, "f": sig}) for _name, offset, sig in spans]
        calls = []
        procs = []

        def spawn(kind, command):
            calls.append((kind, command))
            proc = _Proc(lines)
            procs.append(proc)
            return proc

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = matrix.selfcheck(camera=0, model=model, spawn=spawn, sleep=clock.sleep,
                                    ally_running=lambda: False, now=clock.now, senses={"fps": 10})
        text = buf.getvalue()
        self.assertEqual(code, 0, text)
        self.assertIn("All checks passed.", text)
        self.assertIn("PASS  left eye", text)
        self.assertIn("LEFT", text)
        self.assertIn("RIGHT", text)
        self.assertEqual(len(calls), 1)
        payload = json.loads(calls[0][1][3])
        self.assertFalse(payload["gaze"])
        self.assertTrue(payload["signals"])
        sent = procs[0].stdin.getvalue()
        self.assertIn("card ", sent)
        self.assertIn("LEFT", sent)
        self.assertIn("card off", sent)

    def test_selfcheck_refuses_while_ally_is_running(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = matrix.selfcheck(camera=0, ally_running=lambda: True, senses={})
        self.assertEqual(code, 2)
        self.assertIn("python run.py stop", buf.getvalue())


class CameraOffTests(unittest.TestCase):
    def test_indicator_style_skips_focus_and_the_taskbar(self):
        self.assertIn("Camera on", INDICATOR_TEXT)
        self.assertIn("click to turn off", INDICATOR_TEXT)
        self.assertTrue(INDICATOR_EX_STYLE & 0x08000000)  # WS_EX_NOACTIVATE
        self.assertTrue(INDICATOR_EX_STYLE & 0x00000080)  # WS_EX_TOOLWINDOW
        self.assertTrue(INDICATOR_EX_STYLE & 0x00000008)  # WS_EX_TOPMOST

    def test_skip_message_is_logged_once(self):
        import ally.gaze_helper as helper
        helper._noted = False
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            note_once("Camera-on indicator skipped.")
            note_once("Camera-on indicator skipped.")
        self.assertEqual(err.getvalue().count("Camera-on indicator skipped."), 1)

    def test_click_turns_off_eye_tracking_and_face_sensing(self):
        from ally.gaze import GazeManager
        from ally.senses.manager import Senses
        from test_senses import fake_agent, wait_for

        agent, _got = fake_agent()
        agent.senses = Senses(agent)
        gaze = GazeManager(agent)
        gaze.override = True
        gaze._read_stdout(types.SimpleNamespace(stdout=io.StringIO('{"camera_off": true}\n')))
        self.assertTrue(wait_for(lambda: any(event["kind"] == "gaze" for event in agent.events.since(0))))
        self.assertFalse(gaze.wanted())
        self.assertFalse(agent.senses.vision_wanted())
        self.assertTrue(any("Camera turned off" in event["text"] for event in agent.events.since(0)))


if __name__ == "__main__":
    unittest.main()
