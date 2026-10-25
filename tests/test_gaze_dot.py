"""The gaze dot: where it is drawn, and when it hides. No camera and no real window."""

from __future__ import annotations

import ast
import io
import types
import unittest
from pathlib import Path
from unittest import mock

from helpers import ROOT  # noqa: F401

from ally.gaze import GazeManager
from ally.gaze_dot import (DOT_EX_STYLE, DOT_MAX_R, DOT_MIN_R, FADE_OUT, SHOWN_ALPHA, WS_EX_LAYERED,
                           WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_TOPMOST, WS_EX_TRANSPARENT,
                           DotState, GazeDot, dot_point, dot_radius, log_once)
from ally.gaze_helper import classify_command
from ally.rules import match
from ally.tools.gaze_tools import show_gaze
from test_gaze_agent import _Stdin, manager


class PositionTests(unittest.TestCase):
    def test_radius_comes_from_uncertainty_and_is_clamped(self):
        self.assertEqual(dot_radius(40), 40)
        self.assertEqual(dot_radius(1), DOT_MIN_R)
        self.assertEqual(dot_radius(400), DOT_MAX_R)
        self.assertEqual(dot_radius(None), DOT_MIN_R)
        self.assertEqual(dot_radius("nope"), DOT_MIN_R)

    def test_plain_screen_and_zoomed_view(self):
        self.assertEqual(dot_point(400, 300, 1, 0, 0), (400.0, 300.0))
        # At 1x a stale offset is ignored: the dot sits on the glass point.
        self.assertEqual(dot_point(400, 300, 1, 500, 500), (400.0, 300.0))
        # Zoomed: the magnifier shows the view that starts at (left, top).
        self.assertEqual(dot_point(400, 300, 2, 100, 40), (100 + 400 / 2, 40 + 300 / 2))
        self.assertEqual(dot_point(1920, 1080, 4, 10, 20), (10 + 1920 / 4, 20 + 1080 / 4))

    def test_state_uses_the_view(self):
        state = DotState()
        state.enabled = True
        state.set_view(2, 100, 40)
        spec = state.step(True, True, 400, 300, 200, 0)
        self.assertEqual((spec["x"], spec["y"]), (300.0, 190.0))
        self.assertEqual(spec["radius"], DOT_MAX_R)
        self.assertEqual(spec["alpha"], SHOWN_ALPHA)


class HideTests(unittest.TestCase):
    def _dot(self):
        now = {"t": 0.0}
        drawn = []
        dot = GazeDot(painter=drawn.append, clock=lambda: now["t"])
        return dot, drawn, now

    def test_hidden_until_asked_then_fades_without_a_face_or_a_calibration(self):
        dot, drawn, now = self._dot()
        self.assertIsNone(dot.update(10, 10, 40, face=True, calibrated=True))
        self.assertIsNone(drawn[-1])

        dot.set_enabled(True)
        shown = dot.update(400, 300, 40, face=True, calibrated=True)
        self.assertEqual((shown["x"], shown["y"], shown["radius"]), (400.0, 300.0, 40))
        self.assertEqual(shown["alpha"], SHOWN_ALPHA)

        dot.set_view(2, 80, 20)
        now["t"] = 0.05
        zoomed = dot.update(400, 300, 40, face=True, calibrated=True)
        self.assertEqual((zoomed["x"], zoomed["y"]), (80 + 200.0, 20 + 150.0))

        now["t"] = 0.05 + FADE_OUT / 2
        fading = dot.update(face=False, calibrated=True)
        self.assertIsNotNone(fading)
        self.assertLess(fading["alpha"], SHOWN_ALPHA)
        self.assertGreater(fading["alpha"], 0.05)
        self.assertEqual((fading["x"], fading["y"]), (zoomed["x"], zoomed["y"]))

        now["t"] = 0.05 + FADE_OUT + 0.1
        self.assertIsNone(dot.update(face=False, calibrated=True))

        now["t"] += 0.05
        back = dot.update(10, 12, 30, face=True, calibrated=True)
        self.assertEqual(back["alpha"], SHOWN_ALPHA)
        now["t"] += FADE_OUT
        self.assertIsNone(dot.update(10, 12, 30, face=True, calibrated=False))

        dot.set_enabled(False)
        now["t"] += 0.01
        self.assertIsNone(dot.update(10, 12, 30, face=True, calibrated=True))

    def test_styles_never_take_focus_or_clicks(self):
        self.assertEqual(DOT_EX_STYLE & WS_EX_LAYERED, WS_EX_LAYERED)
        self.assertEqual(DOT_EX_STYLE & WS_EX_TRANSPARENT, WS_EX_TRANSPARENT)
        self.assertEqual(DOT_EX_STYLE & WS_EX_TOPMOST, WS_EX_TOPMOST)
        self.assertEqual(DOT_EX_STYLE & WS_EX_NOACTIVATE, WS_EX_NOACTIVATE)
        self.assertEqual(DOT_EX_STYLE & WS_EX_TOOLWINDOW, WS_EX_TOOLWINDOW)

    def test_a_missing_window_is_logged_once(self):
        import ally.gaze_dot as gaze_dot

        gaze_dot._logged = False
        warnings = []
        dot = GazeDot(clock=lambda: 0.0)
        dot.set_enabled(True)
        with mock.patch("ally.gaze_dot.LayeredDot.open", return_value=None), \
                mock.patch.object(gaze_dot.log, "warning", side_effect=lambda msg, *a: warnings.append(msg)):
            dot.update(1, 2, 20, face=True, calibrated=True)
            dot.update(3, 4, 20, face=True, calibrated=True)
        self.assertEqual(len(warnings), 1)
        log_once("again")
        self.assertEqual(len(warnings), 1)


class ViewCommandTests(unittest.TestCase):
    def test_helper_commands(self):
        self.assertEqual(classify_command("dot on"), ("dot", True))
        self.assertEqual(classify_command("dot off"), ("dot", False))
        self.assertEqual(classify_command("view 2 100 40"), ("view", (2.0, 100, 40)))
        self.assertEqual(classify_command("view 1.5 0 0"), ("view", (1.5, 0, 0)))
        self.assertEqual(classify_command("view no"), ("ignore", None))

    def test_agent_sends_the_view_when_it_changes(self):
        gaze, agent = manager()
        stdin = _Stdin()
        gaze.proc = types.SimpleNamespace(poll=lambda: None, stdin=stdin)
        view = {"v": (2.0, 100, 40)}
        agent.platform = types.SimpleNamespace(view=lambda: view["v"])
        gaze.cfg["show_dot"] = True
        line = '{"t": 1.0, "x": 400, "y": 300, "r": 40}\n'
        gaze._read_stdout(types.SimpleNamespace(stdout=io.StringIO(line)))
        self.assertIn("view 2 100 40\n", stdin.buf)
        stdin.buf = ""
        gaze.ready = True
        gaze._read_stdout(types.SimpleNamespace(stdout=io.StringIO(line)))
        self.assertEqual(stdin.buf, "")
        view["v"] = (3.0, 10, 20)
        gaze._read_stdout(types.SimpleNamespace(stdout=io.StringIO(line)))
        self.assertIn("view 3 10 20\n", stdin.buf)

        # The magnifier thread's cached offset is used, so view() is not queued.
        called = []
        mag = types.SimpleNamespace(ok=True, level=4.0, offset=(8, 9))
        agent.platform = types.SimpleNamespace(view=lambda: called.append("view") or (1, 0, 0), _mag=mag)
        gaze._view_sent = None
        gaze._read_stdout(types.SimpleNamespace(stdout=io.StringIO(line)))
        self.assertIn("view 4 8 9\n", stdin.buf)
        self.assertEqual(called, [])

    def test_show_and_hide_phrases_save_and_tell_the_helper(self):
        self.assertEqual(match("show where I am looking").actions, [("show_gaze", {"on": True})])
        self.assertEqual(match("hide the eye dot").actions, [("show_gaze", {"on": False})])
        gaze, agent = manager()
        stdin = _Stdin()
        gaze.proc = types.SimpleNamespace(poll=lambda: None, stdin=stdin)
        text = "[gaze]\nshow_dot = false           # the circle\npan_speed = 0.35\n"
        saved = {}

        def save_config(body):
            saved["text"] = body
            agent.config.data["gaze"]["show_dot"] = True
            return True, [], []

        agent.config.read_text = lambda: text
        agent.save_config = save_config
        agent.platform = types.SimpleNamespace(view=lambda: (2.0, 5, 6))
        reply = show_gaze(types.SimpleNamespace(agent=agent), True)
        self.assertIn("on", reply.lower())
        self.assertIn("show_dot = true           # the circle", saved["text"])
        self.assertIn("pan_speed = 0.35", saved["text"])
        self.assertIn("dot on\n", stdin.buf)
        self.assertIn("view 2 5 6\n", stdin.buf)
        show_gaze(types.SimpleNamespace(agent=agent), False)
        self.assertIn("dot off\n", stdin.buf)


class PrivacyTests(unittest.TestCase):
    def test_the_dot_does_not_import_the_network_or_write_pictures(self):
        path = Path(__file__).resolve().parent.parent / "ally" / "gaze_dot.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        banned = {"socket", "http", "ssl", "urllib", "requests", "httpx", "websocket"}
        names = {"imwrite", "VideoWriter", "imencode"}
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                mods = [node.module]
            for mod in mods:
                self.assertNotIn(mod.split(".")[0], banned, mod)
            if isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, names)


class ManagerImportTests(unittest.TestCase):
    def test_manager_is_the_one_the_agent_uses(self):
        self.assertIs(GazeManager, type(manager()[0]))
