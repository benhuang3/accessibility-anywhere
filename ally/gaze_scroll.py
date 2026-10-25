"""Scrolling with the eyes when the screen isn't zoomed in.

Look near the bottom of the page for a moment and it scrolls down, near the top and it scrolls up; the closer
to the edge, the faster. When the magnifier is zoomed in, looking near an edge pans the magnifier instead
(ally/platforms/windows.py), so this stays quiet. In Chrome the edges are the web page's own (not the tabs or the
address bar) and the page is scrolled by the extension; in other apps the edges are the screen's work area
(above the taskbar) and the mouse wheel is turned, which scrolls what is under the pointer.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

log = logging.getLogger("ally.gaze.scroll")

TICK = 0.1  # seconds between steps
VIEWPORT_TTL = 2.0  # seconds a measured Chrome page rectangle is trusted
MOUSE_PAUSE = 1.5  # seconds of no scrolling after the user moves the mouse
MOUSE_MOVE_PX = 30


@dataclass
class ScrollState:
    zone: int = 0  # -1 top band, 1 bottom band, 0 neither
    since: float = 0.0  # when the eyes entered the current band
    paused_until: float = 0.0
    last_pointer: tuple[int, int] | None = None
    total: float = 0.0  # screens scrolled in this look, for the log
    extra: dict = field(default_factory=dict)


def band_of(gaze: tuple[float, float], rect: tuple[float, float, float, float], band: float) -> tuple[int, float]:
    """(-1 top / 1 bottom / 0, depth 0..1 into the band) for a gaze point inside rect (left, top, width, height)."""
    x, y = gaze
    left, top, w, h = rect
    if w <= 0 or h <= 0 or not (left <= x <= left + w) or not (top - h * 0.05 <= y <= top + h * 1.05):
        return 0, 0.0  # off the page (a little slack above and below: eyes overshoot edges)
    size = max(1.0, h * band)
    if y >= top + h - size:
        return 1, min(1.0, (y - (top + h - size)) / size)
    if y <= top + size:
        return -1, min(1.0, (top + size - y) / size)
    return 0, 0.0


def step(state: ScrollState, now: float, gaze, rect, cfg: dict, zoomed: bool, pointer) -> float:
    """Screens to scroll now (positive = down, negative = up), and updates state."""
    if pointer is not None:
        last = state.last_pointer
        state.last_pointer = pointer
        if last is not None and abs(pointer[0] - last[0]) + abs(pointer[1] - last[1]) > MOUSE_MOVE_PX:
            state.paused_until = now + MOUSE_PAUSE  # the user is using the mouse
    if zoomed or gaze is None or rect is None or now < state.paused_until:
        state.zone, state.since = 0, 0.0
        return 0.0
    zone, depth = band_of(gaze, rect, float(cfg.get("scroll_band", 0.15)))
    if zone != state.zone:
        if state.zone and state.total:
            log.info("eye scroll: stopped after %.2f screens %s", abs(state.total), "down" if state.total > 0 else "up")
        state.zone, state.since, state.total = zone, now, 0.0
        if zone:
            log.debug("eye scroll: eyes in the %s band", "bottom" if zone > 0 else "top")
    if not zone or now - state.since < float(cfg.get("scroll_dwell", 0.6)):
        return 0.0
    speed = float(cfg.get("scroll_speed", 0.35)) * (0.3 + 0.7 * depth)  # screens per second
    amount = zone * speed * TICK
    if not state.total:
        log.info("eye scroll: scrolling %s", "down" if zone > 0 else "up")
    state.total += amount
    return amount


def viewport_rect(win: dict) -> tuple[float, float, float, float]:
    """The web page area of a Chrome window on the screen, in physical pixels (like the eye tracker's points),
    from the page's own window metrics (screen positions in DIPs; devicePixelRatio = Windows scale x zoom)."""
    zoom = float(win.get("zoom") or 1.0)
    scale = float(win.get("dpr") or 1.0) / (zoom or 1.0)
    inner_w = float(win.get("innerW") or 0) * zoom
    inner_h = float(win.get("innerH") or 0) * zoom
    border = max(0.0, (float(win.get("outerW") or 0) - inner_w) / 2)
    top = max(0.0, float(win.get("outerH") or 0) - inner_h - border)
    return ((float(win.get("screenX") or 0) + border) * scale, (float(win.get("screenY") or 0) + top) * scale,
            inner_w * scale, inner_h * scale)


class EyeScroller:
    """Runs step() ten times a second while the eye tracker is tracking and [gaze] scroll is on."""

    def __init__(self, agent):
        self.agent = agent
        self.state = ScrollState()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._viewport: tuple[float, tuple] | None = None  # (measured at, rect)
        self._browser = None
        self.on_override: bool | None = None  # "scroll with my eyes" / "stop scrolling with my eyes", until restart

    @property
    def cfg(self) -> dict:
        return self.agent.config.data.get("gaze", {})

    def wanted(self) -> bool:
        if self.on_override is not None:
            return self.on_override
        return bool(self.cfg.get("scroll", True))

    def start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="eye-scroll", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    @property
    def browser(self):
        if self._browser is None:
            from .context import BrowserAPI

            self._browser = BrowserAPI(self.agent.bridge)
        return self._browser

    def _tracking(self) -> bool:
        gaze = getattr(self.agent, "gaze", None)
        try:
            return gaze is not None and gaze.status() == "tracking"
        except Exception:
            return False

    def _rect(self, chrome: bool):
        if chrome:
            now = time.monotonic()
            if self._viewport is None or now - self._viewport[0] > VIEWPORT_TTL:
                try:
                    win = self.browser.call("viewport", timeout=1.5) or {}
                    self._viewport = (now, viewport_rect(win))
                except Exception as e:
                    log.debug("eye scroll: no page rectangle (%s)", e)
                    self._viewport = (now, None)
            return self._viewport[1]
        area = self.agent.platform.work_area()
        return area

    def _scroll(self, chrome: bool, amount: float) -> None:
        if chrome:
            self.browser.call("scrollBy", screens=amount, timeout=1.5)
        else:
            self.agent.platform.scroll_wheel(amount * 10)  # about ten notches to a screen

    def _run(self) -> None:
        while not self._stop.wait(TICK):
            try:
                if not self.wanted() or not self._tracking():
                    self.state.zone = 0
                    continue
                p = self.agent.platform
                chrome = bool(self.browser.connected and self.browser.focused)
                amount = step(self.state, time.monotonic(), self.agent.gaze.latest(), self._rect(chrome), self.cfg,
                              getattr(p, "magnify_level", 1.0) > 1.0, p.pointer_pos())
                if amount:
                    self._scroll(chrome, amount)
            except Exception as e:  # never let a hiccup end eye scrolling
                log.warning("eye scroll: %s", e)
                self.state.paused_until = time.monotonic() + 2.0
