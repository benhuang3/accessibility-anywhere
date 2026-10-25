"""Scrolling with the eyes when not zoomed in (ally/gaze_scroll.py): bands, timing, pauses, and the loop."""

from __future__ import annotations

import threading
import time
import unittest

import helpers  # noqa: F401  (adds the repo root to sys.path)

from ally import gaze_scroll as gs
from ally import rules
from ally.registry import Registry
from ally.tools import gaze_tools

CFG = {"scroll": True, "scroll_speed": 0.35, "scroll_band": 0.15, "scroll_dwell": 0.6}
PAGE = (0.0, 100.0, 1000.0, 800.0)  # left, top, width, height


class BandTests(unittest.TestCase):
    def test_bands(self):
        self.assertEqual(gs.band_of((500, 500), PAGE, 0.15), (0, 0.0))
        zone, depth = gs.band_of((500, 890), PAGE, 0.15)  # 800 * 0.15 = 120 px band: 780..900
        self.assertEqual(zone, 1)
        self.assertAlmostEqual(depth, 110 / 120)
        self.assertEqual(gs.band_of((500, 130), PAGE, 0.15)[0], -1)
        self.assertEqual(gs.band_of((1200, 890), PAGE, 0.15), (0, 0.0))  # beside the page
        self.assertEqual(gs.band_of((500, 20), PAGE, 0.15), (0, 0.0))  # well above it: the tabs, the address bar
        self.assertEqual(gs.band_of((500, 920), PAGE, 0.15)[0], 1)  # a little below the edge still counts

    def test_viewport_rect(self):
        # 100% Windows scale: window at (100, 50), 8 px borders, 80 px of toolbars.
        win = {"screenX": 100, "screenY": 50, "outerW": 1296, "outerH": 888, "innerW": 1280, "innerH": 800,
               "dpr": 1.0, "zoom": 1.0}
        self.assertEqual(gs.viewport_rect(win), (108.0, 130.0, 1280.0, 800.0))
        # 150% Windows scale: physical pixels are 1.5x the DIPs Chrome reports.
        x, y, w, h = gs.viewport_rect(dict(win, dpr=1.5))
        self.assertEqual((x, y, w, h), (162.0, 195.0, 1920.0, 1200.0))
        # 125% page zoom: CSS sizes shrink, the area on screen doesn't.
        x, y, w, h = gs.viewport_rect(dict(win, innerW=1024, innerH=640, dpr=1.25, zoom=1.25))
        self.assertEqual((x, y, w, h), (108.0, 130.0, 1280.0, 800.0))


class StepTests(unittest.TestCase):
    def run_for(self, seconds, gaze, zoomed=False, pointer=(0, 0), start=100.0, state=None):
        state = state or gs.ScrollState()
        total, t = 0.0, start
        while t < start + seconds:
            total += gs.step(state, t, gaze, PAGE, CFG, zoomed, pointer)
            t += gs.TICK
        return total, state

    def test_waits_then_scrolls_faster_at_the_edge(self):
        total, _ = self.run_for(0.5, (500, 890))
        self.assertEqual(total, 0.0)  # still inside the dwell
        deep, _ = self.run_for(2.0, (500, 895))
        shallow, _ = self.run_for(2.0, (500, 790))
        self.assertGreater(deep, 0.3)
        self.assertGreater(deep, shallow * 2)
        self.assertGreater(shallow, 0)
        up, _ = self.run_for(2.0, (500, 110))
        self.assertLess(up, -0.3)

    def test_middle_off_page_zoomed_or_no_eyes(self):
        for gaze, zoomed in (((500, 500), False), ((500, 890), True), (None, False), ((1500, 890), False)):
            with self.subTest(gaze=gaze, zoomed=zoomed):
                self.assertEqual(self.run_for(2.0, gaze, zoomed)[0], 0.0)

    def test_looking_away_stops_at_once_and_dwell_restarts(self):
        state = gs.ScrollState()
        self.run_for(1.5, (500, 890), state=state)
        self.assertEqual(gs.step(state, 102.0, (500, 500), PAGE, CFG, False, (0, 0)), 0.0)
        self.assertEqual(gs.step(state, 102.1, (500, 890), PAGE, CFG, False, (0, 0)), 0.0)  # dwell again

    def test_mouse_movement_pauses(self):
        state = gs.ScrollState()
        self.run_for(1.0, (500, 890), state=state)
        self.assertEqual(gs.step(state, 101.1, (500, 890), PAGE, CFG, False, (300, 300)), 0.0)  # moved the mouse
        total, _ = self.run_for(1.0, (500, 890), pointer=(300, 300), start=101.2, state=state)
        self.assertEqual(total, 0.0)  # paused for 1.5 s
        total, _ = self.run_for(1.5, (500, 890), pointer=(300, 300), start=102.7, state=state)
        self.assertGreater(total, 0.0)


class FakeBridge:
    def __init__(self, chrome=True):
        self.connected = chrome
        self.chrome_focused = chrome
        self.calls = []
        self.lock = threading.Lock()

    def call(self, action, args=None, timeout=8.0):
        with self.lock:
            self.calls.append((action, dict(args or {})))
        if action == "viewport":
            return {"screenX": 0, "screenY": 0, "outerW": 1000, "outerH": 900, "innerW": 1000, "innerH": 800,
                    "dpr": 1.0, "zoom": 1.0}
        return {"moved": 10}


class FakeGaze:
    def __init__(self, point, status="tracking"):
        self.point, self._status = point, status

    def status(self):
        return self._status

    def latest(self, max_age=0.3):
        return self.point

    def problem(self):
        return "Set up the eye tracker first."


class FakePlatform:
    magnify_level = 1.0

    def __init__(self):
        self.wheel = []

    def pointer_pos(self):
        return (5, 5)

    def work_area(self):
        return (0, 0, 1000, 760)

    def scroll_wheel(self, notches):
        self.wheel.append(notches)


class FakeAgent:
    def __init__(self, point, chrome=True, status="tracking", scroll=True):
        self.config = type("C", (), {"data": {"gaze": dict(CFG, scroll=scroll)}})()
        self.bridge = FakeBridge(chrome)
        self.gaze = FakeGaze(point, status)
        self.platform = FakePlatform()


def run_loop(agent, seconds=1.3, override=None):
    s = gs.EyeScroller(agent)
    s.on_override = override
    s.start()
    time.sleep(seconds)
    s.stop()
    time.sleep(0.15)
    return [c for c in agent.bridge.calls if c[0] == "scrollBy"], agent.platform.wheel


class LoopTests(unittest.TestCase):
    def test_chrome_scrolls_the_page(self):
        calls, wheel = run_loop(FakeAgent((500, 790)))  # the page is y 100..900 here: bottom band
        self.assertTrue(calls, "scrollBy sent")
        self.assertTrue(all(c[1]["screens"] > 0 for c in calls))
        self.assertEqual(wheel, [])

    def test_other_apps_turn_the_wheel(self):
        calls, wheel = run_loop(FakeAgent((500, 750), chrome=False))  # work area 0..760: bottom band
        self.assertEqual(calls, [])
        self.assertTrue(wheel and all(n > 0 for n in wheel))

    def test_off_when_not_tracking_zoomed_or_turned_off(self):
        self.assertEqual(run_loop(FakeAgent((500, 790), status="needs calibration"))[0], [])
        zoomed = FakeAgent((500, 790))
        zoomed.platform.magnify_level = 2.0
        self.assertEqual(run_loop(zoomed)[0], [])
        self.assertEqual(run_loop(FakeAgent((500, 790), scroll=False))[0], [])
        self.assertEqual(run_loop(FakeAgent((500, 790)), override=False)[0], [])
        self.assertTrue(run_loop(FakeAgent((500, 790), scroll=False), override=True)[0])


class ToolAndPhraseTests(unittest.TestCase):
    def test_phrases(self):
        self.assertEqual(rules.match("scroll with my eyes").actions, [("eye_scroll", {"on": True})])
        for said in ("turn on hands free scrolling", "turn on hands-free scrolling", "hands free scrolling"):
            self.assertEqual(rules.match(said).actions, [("eye_scroll", {"on": True})], said)
        for said in ("turn off hands free scrolling", "stop hands-free scrolling", "hands free scrolling off"):
            self.assertEqual(rules.match(said).actions, [("eye_scroll", {"on": False})], said)
        self.assertEqual(rules.match("stop scrolling with my eyes").actions, [("eye_scroll", {"on": False})])
        self.assertEqual(rules.match("scroll down").actions[0][0], "scroll_page")

    def test_tool(self):
        agent = FakeAgent((500, 500))
        agent.eye_scroll = gs.EyeScroller(agent)
        r = Registry()
        r.add(gaze_tools.eye_scroll.__ally_tool__)
        ctx = type("Ctx", (), {"agent": agent, "config": {}})()
        self.assertIn("Eye scrolling is on", r.call("eye_scroll", {}, ctx=ctx).text())
        self.assertTrue(agent.eye_scroll.wanted())
        self.assertIn("off", r.call("eye_scroll", {"on": False}, ctx=ctx).text())
        self.assertFalse(agent.eye_scroll.wanted())
        agent.gaze._status = "needs calibration"
        self.assertIn("isn't following you yet", r.call("eye_scroll", {}, ctx=ctx).text())


if __name__ == "__main__":
    unittest.main()
