import os
import plistlib
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from helpers import EnvPatch, fake_screen, temp_home

from ally import autostart, speech, voice
from ally.macros import pick_os
from ally.paths import CODE_ROOT, Paths
from ally.platforms import base, linux, windows
from ally.registry import build_tool
from ally.supervisor import supervise

CHILD = r"""
import os, sys, time
counter = os.path.join(os.environ["ALLY_TEST_DIR"], "count")
n = int(open(counter).read()) + 1 if os.path.exists(counter) else 1
open(counter, "w").write(str(n))
plan = os.environ["ALLY_TEST_PLAN"].split(",")
step = plan[min(n, len(plan)) - 1]
if step == "hang":
    time.sleep(60)
sys.exit(int(step))
"""


def free_port() -> int:
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class SupervisorTests(unittest.TestCase):
    def run_plan(self, plan: str, **kw) -> tuple[int, int, float]:
        d = tempfile.mkdtemp()
        script = Path(d) / "child.py"
        script.write_text(CHILD, encoding="utf-8")
        home = temp_home(f"[agent]\nport = {free_port()}\n")
        t0 = time.monotonic()
        with EnvPatch(ALLY_TEST_DIR=d, ALLY_TEST_PLAN=plan):
            code = supervise(home, command=[sys.executable, str(script)], **kw)
        runs = int((Path(d) / "count").read_text())
        return code, runs, time.monotonic() - t0

    def test_restarts_after_crashes_then_stops_on_clean_exit(self):
        code, runs, secs = self.run_plan("1,1,0")
        self.assertEqual((code, runs), (0, 3))
        self.assertGreaterEqual(secs, 2.5)  # backoff 1 s then 2 s

    def test_restart_request_is_immediate(self):
        code, runs, secs = self.run_plan("75,0")
        self.assertEqual((code, runs), (0, 2))
        self.assertLess(secs, 2.0)

    def test_hung_agent_is_killed_and_restarted(self):
        code, runs, secs = self.run_plan("hang,0", check_every=0.2, grace=0.3)
        self.assertEqual((code, runs), (0, 2))
        self.assertLess(secs, 10)

    def test_closed_port_answers_fast(self):
        from ally.supervisor import health, listening

        port = free_port()
        t0 = time.monotonic()
        self.assertFalse(listening(port))
        self.assertIsNone(health(port))
        self.assertLess(time.monotonic() - t0, 1.5)  # Windows alone takes ~2 s per refused connect


class AutostartTests(unittest.TestCase):
    def test_files(self):
        paths = Paths(temp_home())
        plist = plistlib.loads(autostart.mac_plist(paths))
        self.assertEqual(plist["Label"], "com.ally.agent")
        self.assertEqual(plist["ProgramArguments"][-1], "start")
        self.assertEqual(plist["KeepAlive"], {"SuccessfulExit": False})
        self.assertEqual(plist["EnvironmentVariables"]["ALLY_HOME"], str(paths.home))
        desktop = autostart.linux_desktop(paths)
        self.assertIn("[Desktop Entry]", desktop)
        self.assertIn(autostart._desktop_quote(str(CODE_ROOT / "run.py")) + ' "start"', desktop)
        self.assertIn('env "ALLY_HOME=', desktop)
        self.assertEqual(autostart._desktop_quote('a"b$c'), '"a\\"b\\$c"')
        self.assertTrue(autostart.windows_command(paths).endswith('run.py" start'))

    def test_linux_install_and_uninstall(self):
        cfg = tempfile.mkdtemp()
        with EnvPatch(XDG_CONFIG_HOME=cfg):
            if sys.platform.startswith("linux"):
                msg = autostart.install(temp_home(), start_now=False)
                self.assertIn("ally-agent.desktop", msg)
                self.assertTrue(autostart.installed())
                autostart.uninstall()
                self.assertFalse(autostart.installed())


class GnomeShortcutTests(unittest.TestCase):
    def test_binding_names(self):
        from ally.gnome_shortcuts import gnome_binding

        self.assertEqual(gnome_binding("<mod>+="), "<Control><Alt>equal")
        self.assertEqual(gnome_binding("<ctrl>+<shift>+<f5>"), "<Control><Shift>F5")
        self.assertEqual(gnome_binding("<cmd>+<space>"), "<Super>space")

    def test_command_quoting(self):
        from ally.gnome_shortcuts import command_for

        paths = Paths(CODE_ROOT)
        run_py = shlex.quote(str(CODE_ROOT / "run.py"))  # quoted on Windows (backslashes)
        self.assertTrue(command_for("zoom_in", paths, {"zoom_in"}).endswith(f"{run_py} tool zoom_in"))
        self.assertTrue(command_for("make it bigger", paths, set()).endswith("send 'make it bigger'"))
        cmd = command_for({"tool": "set_page_zoom", "args": {"zoom": 2}}, paths, set())
        self.assertTrue(cmd.endswith("""tool set_page_zoom '{"zoom": 2}'"""), cmd)


class FakePlatform(base.Platform):
    name = "linux"

    def tts_argv(self, text, rate, voice):
        return [sys.executable, "-c", "import time; time.sleep(5)"], None

    def play_wav_argv(self, path):
        return [sys.executable, "-c", "pass"]


class SpeechTests(unittest.TestCase):
    def test_chunks(self):
        text = "First sentence. " * 60
        parts = speech.chunks(text, 200)
        self.assertTrue(all(len(p) <= 200 for p in parts))
        self.assertEqual(" ".join(parts).split(), text.split())
        self.assertEqual(speech.chunks("x" * 950, 400)[0], "x" * 400)

    def test_say_and_stop(self):
        s = speech.Speaker(FakePlatform(), lambda: {"engine": "system", "rate": 1.0, "max_chars": 100})
        gen = s.generation
        self.assertTrue(s.say("hello"))
        time.sleep(0.3)
        self.assertTrue(s.speaking)
        self.assertNotEqual(s.generation, gen)
        t0 = time.monotonic()
        s.stop()
        for _ in range(40):
            if not s.speaking:
                break
            time.sleep(0.05)
        self.assertFalse(s.speaking)
        self.assertLess(time.monotonic() - t0, 2.5)

    def test_off_engine_is_silent(self):
        s = speech.Speaker(FakePlatform(), lambda: {"engine": "off"})
        self.assertFalse(s.say("hello"))

    def test_beep_file(self):
        p = base.tempfile_path(".wav")
        speech.make_beep(p)
        import wave

        with wave.open(p) as w:
            self.assertEqual((w.getnchannels(), w.getframerate()), (1, 16000))
        os.unlink(p)


class PlatformTests(unittest.TestCase):
    def test_windows_follow_offset(self):
        self.assertEqual(windows.follow_offset(960, 1920, 2.0), 480)
        self.assertEqual(windows.follow_offset(0, 1920, 2.0), 0)
        self.assertEqual(windows.follow_offset(1920, 1920, 2.0), 960)
        self.assertEqual(len(windows.INVERT), 25)
        self.assertEqual(windows.GRAYSCALE[:3], [0.3, 0.3, 0.3])
        self.assertEqual([windows.sapi_rate(r) for r in (0.3, 1.0, 1.5, 3.0)], [-7, 0, 5, 10])
        self.assertEqual(windows._b64(""), "-")
        self.assertNotIn(" ", windows._b64("two words, café"))

    @unittest.skipUnless(sys.platform == "win32", "Windows only")
    def test_windows_speech_worker(self):
        worker = windows._SpeechWorker()
        try:
            t0 = time.monotonic()
            u = worker.say("", 1.0, "")  # empty text: finishes as soon as the voice is loaded
            u.wait(timeout=20)
            u = worker.say("This sentence is long enough to be cut off by stop.", 1.0, "")
            time.sleep(0.3)
            t1 = time.monotonic()
            u.terminate()
            u.wait(timeout=2)
            self.assertLess(time.monotonic() - t1, 1.0)
            self.assertLess(time.monotonic() - t0, 20)
        finally:
            worker.close()
        self.assertIsNotNone(worker.proc.poll())

    def test_next_level(self):
        self.assertEqual(base.next_level(1.0, True), 1.5)
        self.assertEqual(base.next_level(8.0, True), 8.0)
        self.assertEqual(base.next_level(1.5, False), 1.0)

    def test_linux_gsettings_calls(self):
        calls = []

        def fake_run(argv, timeout=10, input_bytes=None, check=False):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        old_run, old_which = linux.run, linux.which
        linux.run, linux.which = fake_run, (lambda *n: "/usr/bin/" + n[0])
        try:
            p = linux.LinuxPlatform()
            p.magnify(2)
            self.assertIn(["gsettings", "set", linux.MAG, "mag-factor", "2.00"], calls)
            self.assertIn(["gsettings", "set", linux.APPS, "screen-magnifier-enabled", "true"], calls)
            calls.clear()
            p.magnify(1)
            p.color_filter("invert")
            self.assertIn(["gsettings", "set", linux.MAG, "invert-lightness", "true"], calls)
            self.assertEqual(calls[-1], ["gsettings", "set", linux.APPS, "screen-magnifier-enabled", "true"])
            calls.clear()
            p.color_filter("none")
            self.assertEqual(calls[-1], ["gsettings", "set", linux.APPS, "screen-magnifier-enabled", "false"])
            p.text_scale(1.5)
            self.assertIn(["gsettings", "set", linux.IFACE, "text-scaling-factor", "1.50"], calls)
        finally:
            linux.run, linux.which = old_run, old_which

    def test_pick_os(self):
        v = {"mac": "open -a Calculator", "windows": "calc"}
        self.assertEqual(pick_os(v, "macos"), "open -a Calculator")
        self.assertIsNone(pick_os(v, "linux"))
        self.assertEqual(pick_os("same", "windows"), "same")


class VoiceTests(unittest.TestCase):
    def test_multipart(self):
        body, ctype = voice.multipart({"model": "ink-whisper", "language": "en"}, "file", "speech.wav", b"RIFF", "audio/wav")
        boundary = ctype.split("boundary=")[1]
        self.assertTrue(ctype.startswith("multipart/form-data; boundary="))
        self.assertIn(b'name="model"\r\n\r\nink-whisper\r\n', body)
        self.assertIn(b'name="file"; filename="speech.wav"\r\nContent-Type: audio/wav\r\n\r\nRIFF\r\n', body)
        self.assertTrue(body.endswith(f"--{boundary}--\r\n".encode()))

    def test_wav_bytes(self):
        import io
        import wave

        with wave.open(io.BytesIO(voice.wav_bytes(b"\x00\x00" * 1600))) as w:
            self.assertEqual((w.getframerate(), w.getnframes()), (16000, 1600))


FAKE_PYNPUT = """
import threading, time
class GlobalHotKeys:
    def __init__(self, mapping):
        self.mapping = mapping
    def start(self):
        def fire():
            time.sleep(0.3)
            list(self.mapping.values())[0]()
        threading.Thread(target=fire, daemon=True).start()
    def stop(self):
        pass
"""


NO_NUMPY_START = """
import sys
sys.modules['numpy'] = None  # this machine has no numpy
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[2])
from helpers import temp_home
from ally.agent import Agent
a = Agent(temp_home(), start_hotkeys=False)
try:
    names = a.registry.names()
    assert 'magnify_where_i_look' in names and 'calibrate_eye_tracker' in names, sorted(names)[:10]
    from ally import gaze_model
    assert gaze_model.fixation([(0.0, 10, 10), (0.3, 12, 11)], 0, 1, 50)['samples'] == 2
    print('started')
finally:
    a.close()
"""


class NoNumpyTests(unittest.TestCase):
    def test_ally_starts_without_numpy(self):
        """The core has no required packages; only the eye tracker's math needs numpy."""
        tests = str(Path(__file__).resolve().parent)
        env = dict(os.environ, ANTHROPIC_API_KEY="")
        out = subprocess.run([sys.executable, "-c", NO_NUMPY_START, str(CODE_ROOT), tests],
                             capture_output=True, text=True, timeout=60, env=env)
        self.assertIn("started", out.stdout, out.stderr[-2000:])


class HotkeyProcessTests(unittest.TestCase):
    def make_agent(self, extra_path=None):
        from ally.agent import Agent

        cfg = '[hotkeys]\n"<mod>+z" = { tool = "fake_zoom", args = { zoom = 2.0 } }\n'
        env = {"ANTHROPIC_API_KEY": None}
        if extra_path:
            env["PYTHONPATH"] = extra_path + os.pathsep + str(CODE_ROOT)
        self.env = EnvPatch(**env)
        self.env.__enter__()
        a = Agent(temp_home(cfg), start_hotkeys=True)
        fake_screen(a.platform)
        hits = []

        def fake_zoom(zoom: float):
            "Zoom."
            hits.append(zoom)
            return "ok"

        a.registry.add(build_tool(fake_zoom))
        return a, hits

    def tearDown(self):
        self.env.__exit__()

    def test_hotkey_fires_through_helper_process(self):
        d = Path(tempfile.mkdtemp())
        (d / "pynput").mkdir()
        (d / "pynput" / "__init__.py").write_text("", encoding="utf-8")
        (d / "pynput" / "keyboard.py").write_text(FAKE_PYNPUT, encoding="utf-8")
        a, hits = self.make_agent(str(d))
        try:
            a.hotkeys.apply(force=True)
            for _ in range(60):
                if hits:
                    break
                time.sleep(0.1)
            self.assertEqual(hits, [2.0])
            self.assertEqual(a.hotkeys.status, "on")
        finally:
            a.hotkeys.stop()

    def test_missing_pynput_is_reported_not_retried(self):
        d = Path(tempfile.mkdtemp())
        (d / "pynput").mkdir()
        (d / "pynput" / "__init__.py").write_text("raise ImportError('no display')", encoding="utf-8")
        a, _hits = self.make_agent(str(d))
        try:
            a.hotkeys.apply(force=True)
            for _ in range(50):
                if a.hotkeys.status == "error":
                    break
                time.sleep(0.1)
            self.assertEqual(a.hotkeys.status, "error")
            self.assertIn("pynput", a.hotkeys.error)
            time.sleep(0.3)
            a.hotkeys.ensure_running()
            self.assertEqual(a.hotkeys.status, "error")
        finally:
            a.hotkeys.stop()


if __name__ == "__main__":
    unittest.main()
