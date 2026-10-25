"""Browser test harness: Playwright Chromium with Ally's extension loaded and a local fixture server.

Standard library + playwright only. Works on macOS and Windows. Never uses port 8765.

    with FixtureServer() as server, AllyBrowser(server) as ally:
        page = ally.open("combo_general.html")
        ally.action("stepOn", {"step": "clutter"})
        ally.styles(page)   # -> ["ally_clutter", ...]

ALLY_HEADED=1 shows the browser window.
"""

from __future__ import annotations

import functools
import http.server
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
EXTENSION = ROOT / "extension"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SHOTS = ROOT / "logs" / "browser"


class ActionError(RuntimeError):
    """An extension ACTION failed; the message is the extension's own."""


# ---------------------------------------------------------------- fixture server

class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - signature from the base class
        pass

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


class FixtureServer:
    """Serves tests/browser/fixtures on 127.0.0.1 at a free port, in a background thread."""

    def __init__(self, root: Path = FIXTURES) -> None:
        self.root = Path(root)
        self.httpd: http.server.ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> "FixtureServer":
        handler = functools.partial(_QuietHandler, directory=str(self.root))
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    @property
    def port(self) -> int:
        assert self.httpd, "server not started"
        return self.httpd.server_address[1]

    def url(self, path: str = "") -> str:
        return f"http://127.0.0.1:{self.port}/{path.lstrip('/')}"

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    def __enter__(self) -> "FixtureServer":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()


# ---------------------------------------------------------------- browser with the extension

# Runs ACTIONS[name](args) in the service worker; errors come back as data so the message survives.
_CALL_ACTION = """async ([name, args]) => {
  if (typeof ACTIONS === "undefined" || typeof ACTIONS[name] !== "function") {
    return { ok: false, error: `No extension action named ${name}` };
  }
  try {
    return { ok: true, value: await ACTIONS[name](args || {}) };
  } catch (e) {
    return { ok: false, error: String((e && e.message) || e) };
  }
}"""

# Records later console errors and unhandled rejections in the worker (the console event covers most too).
_HOOK_ERRORS = """() => {
  if (globalThis.__allyTestErrors) return true;
  const errs = (globalThis.__allyTestErrors = []);
  const orig = console.error.bind(console);
  console.error = (...a) => { errs.push(a.map(String).join(" ")); orig(...a); };
  self.addEventListener("error", (e) => errs.push(String(e.message || e)));
  self.addEventListener("unhandledrejection", (e) => errs.push(String((e.reason && e.reason.message) || e.reason)));
  return true;
}"""

# Page state as plain DOM facts (marks are DOM attributes, so the main world sees them).
_PAGE_STATE = """() => {
  const marks = {};
  for (const el of document.querySelectorAll("*")) {
    if (el.id === "ally-risk-banner" || (el.closest && el.closest("#ally-risk-banner"))) continue;
    for (const a of el.attributes) {
      if (!a.name.startsWith("data-ally-") || a.name === "data-ally-pause") continue;
      marks[a.name] = (marks[a.name] || 0) + 1;
    }
  }
  const hosts = Array.from(document.querySelectorAll('[id^="ally-"]'))
    .map((el) => el.id)
    .filter((id) => id !== "ally-risk-banner")
    .sort();
  return { marks, hosts, pause: document.documentElement.getAttribute("data-ally-pause") || "" };
}"""


class AllyBrowser:
    """Playwright Chromium with the repo's extension loaded, driven through its service-worker ACTIONS."""

    def __init__(self, server: FixtureServer | None = None, headed: bool | None = None,
                 extension: Path = EXTENSION) -> None:
        self.server = server
        self.headed = os.environ.get("ALLY_HEADED") == "1" if headed is None else headed
        self.extension = Path(extension).resolve()
        self.user_data = ""
        self.pw = None
        self.context = None
        self.sw = None
        self.console: list[dict[str, str]] = []  # every console message: {source, type, text}
        self.page_errors: list[str] = []
        self._tab_ids: dict[int, int] = {}

    # ------------------------------------------------------------ lifecycle
    def start(self) -> "AllyBrowser":
        from playwright.sync_api import sync_playwright

        self.user_data = tempfile.mkdtemp(prefix="ally-browser-")
        self.pw = sync_playwright().start()
        ext = str(self.extension)
        self.context = self.pw.chromium.launch_persistent_context(
            user_data_dir=self.user_data,
            channel="chromium",
            headless=not self.headed,
            viewport={"width": 1280, "height": 800},
            args=[f"--disable-extensions-except={ext}", f"--load-extension={ext}"],
        )
        self.context.on("console", self._on_console)
        self.context.on("weberror", lambda e: self.page_errors.append(str(e.error)))
        self.sw = self._wait_worker()
        self.sw.evaluate(_HOOK_ERRORS)
        return self

    def _wait_worker(self, timeout: float = 20.0):
        for w in self.context.service_workers:
            if w.url.startswith("chrome-extension://"):
                return w
        return self.context.wait_for_event(
            "serviceworker", predicate=lambda w: w.url.startswith("chrome-extension://"), timeout=timeout * 1000)

    def _on_console(self, msg) -> None:
        source = "worker" if msg.worker else "page"
        self.console.append({"source": source, "type": msg.type, "text": msg.text})

    def close(self) -> None:
        try:
            if self.context:
                self.context.close()
        finally:
            if self.pw:
                self.pw.stop()
            self.context = self.pw = self.sw = None
            if self.user_data:
                shutil.rmtree(self.user_data, ignore_errors=True)

    def __enter__(self) -> "AllyBrowser":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------ pages
    def page(self):
        """The one test tab (the context's first page), reused so the active tab is never ambiguous."""
        pages = self.context.pages
        return pages[0] if pages else self.context.new_page()

    def open(self, path: str, page=None):
        """Loads a fixture (or a full URL) in the test tab, brings it to front and waits for load."""
        page = page or self.page()
        url = path if "://" in path else self.server.url(path)
        page.goto(url, wait_until="load")
        page.bring_to_front()
        self._tab_ids.pop(id(page), None)
        return page

    def tab_id(self, page) -> int:
        key = id(page)
        if key not in self._tab_ids:
            page.bring_to_front()
            self._tab_ids[key] = int(self.action("pageInfo")["tabId"])
        return self._tab_ids[key]

    # ------------------------------------------------------------ extension calls
    def evaluate(self, js: str, arg: Any = None) -> Any:
        """Evaluates a function in the service worker (STEPS, ACTIONS, docStyles... are globals there)."""
        return self.sw.evaluate(js, arg)

    def action(self, name: str, args: dict | None = None) -> Any:
        out = self.sw.evaluate(_CALL_ACTION, [name, args or {}])
        if not out or not out.get("ok"):
            raise ActionError(f"{name} failed: {(out or {}).get('error', 'no answer')}")
        return out.get("value")

    def steps(self) -> list[str]:
        return sorted(self.sw.evaluate("() => typeof STEPS === 'undefined' ? [] : Object.keys(STEPS)"))

    def has_step(self, name: str) -> bool:
        return name in self.steps()

    def styles(self, page) -> list[str]:
        """Names of the CSS layers inserted in the page (the record docStyles keeps)."""
        tab = self.tab_id(page)
        return sorted(self.sw.evaluate("async (id) => Object.keys(await docStyles(id))", tab))

    def marks(self, page, step: str) -> int:
        return page.evaluate("(s) => document.querySelectorAll(`[data-ally-${s}]`).length", step)

    def page_state(self, page) -> dict:
        """{marks: {attr: count}, hosts: [ids], pause} straight from the DOM (risk banner skipped)."""
        return page.evaluate(_PAGE_STATE)

    def zoom(self, page=None) -> float:
        page = page or self.page()
        return float(self.sw.evaluate("(id) => chrome.tabs.getZoom(id)", self.tab_id(page)))

    def default_zoom(self, page=None) -> float:
        page = page or self.page()
        return float(self.sw.evaluate(
            "async (id) => (await chrome.tabs.getZoomSettings(id)).defaultZoomFactor || 1", self.tab_id(page)))

    def reset_zoom(self, page=None) -> float:
        """Chrome's default zoom for the tab (zoom is per-origin, so it outlives navigation)."""
        page = page or self.page()
        self.sw.evaluate("(id) => chrome.tabs.setZoom(id, 0)", self.tab_id(page))
        deadline = time.time() + 3
        while time.time() < deadline and abs(self.zoom(page) - self.default_zoom(page)) > 0.001:
            time.sleep(0.05)
        return self.zoom(page)

    def screenshot(self, page, name: str) -> Path:
        SHOTS.mkdir(parents=True, exist_ok=True)
        path = SHOTS / f"{name}.png"
        page.screenshot(path=str(path))
        return path

    # ------------------------------------------------------------ errors
    def worker_errors(self) -> list[str]:
        """Console errors (and uncaught errors) from the service worker since launch."""
        seen = [m["text"] for m in self.console if m["source"] == "worker" and m["type"] == "error"]
        try:
            hooked = self.sw.evaluate("() => globalThis.__allyTestErrors || []")
        except Exception:  # worker restarted: the console record still has what it said
            hooked = []
        return seen + [e for e in hooked if e not in seen]

    def worker_warnings(self) -> list[str]:
        return [m["text"] for m in self.console if m["source"] == "worker" and m["type"] == "warning"]

    def page_console_errors(self) -> list[str]:
        return [m["text"] for m in self.console if m["source"] == "page" and m["type"] == "error"] + self.page_errors

    def clear_errors(self) -> None:
        self.console.clear()
        self.page_errors.clear()
        try:
            self.sw.evaluate("() => { if (globalThis.__allyTestErrors) globalThis.__allyTestErrors.length = 0; }")
        except Exception:
            pass
