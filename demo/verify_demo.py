"""Render the local demos, collect extension-shaped snapshots, and check Ally's rules.

Usage: python demo/verify_demo.py
Only the standard library and an installed Chromium browser are needed. No Ally
agent, API, credentials, real process inspection, or AI review is used.
"""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from dataclasses import asdict
from functools import partial
from hashlib import sha1
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
from urllib.parse import urlsplit
from urllib.request import urlopen

sys.dont_write_bytecode = True
DEMO = Path(__file__).resolve().parent
REPO = DEMO.parent
sys.path.insert(0, str(REPO))

from ally.contracts import EventBus  # noqa: E402
from ally.risk.behavior import behavior_flags  # noqa: E402
from ally.risk.wire import RiskWire, scan_page  # noqa: E402

EXPECTED = {
    "index.html": [],
    "virus.html": [("scam_popup", "high")],
    "bank.html": [("scam_popup", "medium"), ("payment_field", "medium")],
    "trap.html": [("scam_popup", "medium")],
    "support.html": [("scam_popup", "medium")],
    "shop.html": [],
    "news.html": [],
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class HTMLAudit(HTMLParser):
    def __init__(self, filename: str):
        super().__init__()
        self.filename = filename
        self.links = []
        self.has_lang = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "html":
            self.has_lang = attrs.get("lang") == "en"
        require(not any(key.startswith("on") or key == "style" for key in attrs),
                f"{self.filename}: inline handler or style")
        require(tag != "style", f"{self.filename}: inline CSS")
        if tag == "script":
            require(bool(attrs.get("src")), f"{self.filename}: inline JavaScript")
        for key in ("href", "src", "action"):
            value = attrs.get(key, "")
            parsed = urlsplit(value)
            require(not parsed.netloc and parsed.scheme in ("", "data", "tel"),
                    f"{self.filename}: external URL {value}")
            if value and not parsed.scheme and parsed.path:
                target = (DEMO / parsed.path).resolve()
                require(target.is_relative_to(DEMO) and target.is_file(),
                        f"{self.filename}: missing or out-of-scope asset {value}")
                self.links.append(parsed.path)


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@contextmanager
def serve():
    handler = partial(QuietHandler, directory=str(DEMO))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://localhost:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def browser_path(override: str | None) -> str:
    candidates = [override] if override else []
    for name in ("chrome", "chromium", "chromium-browser", "msedge"):
        candidates.append(shutil.which(name))
    for root in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"),
                 os.environ.get("LOCALAPPDATA")):
        if root:
            candidates += [str(Path(root) / "Google/Chrome/Application/chrome.exe"),
                           str(Path(root) / "Microsoft/Edge/Application/msedge.exe")]
    candidates += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            candidate = playwright.chromium.executable_path
        if Path(candidate).is_file():
            return candidate
    except ImportError:
        pass
    raise RuntimeError("No Chromium browser found. Pass --browser PATH to Chrome or Edge.")


class CDP:
    """Small loopback-only WebSocket client for Chromium's debugging protocol."""

    def __init__(self, url: str):
        parsed = urlsplit(url)
        require(parsed.hostname in ("localhost", "127.0.0.1"), "Nonlocal debugger URL")
        self.socket = socket.create_connection((parsed.hostname, parsed.port), timeout=20)
        self.reader = self.socket.makefile("rb")
        self.next_id = 0
        self.dialogs = []
        self.requests = []
        self.errors = []
        key = base64.b64encode(os.urandom(16)).decode()
        handshake = (f"GET {parsed.path} HTTP/1.1\r\nHost: {parsed.netloc}\r\n"
                     f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                     f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.socket.sendall(handshake.encode())
        status = self.reader.readline()
        require(b" 101 " in status, f"Debugger handshake failed: {status!r}")
        headers = {}
        while (line := self.reader.readline()) not in (b"\r\n", b""):
            name, value = line.decode().split(":", 1)
            headers[name.lower()] = value.strip()
        expected = base64.b64encode(sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        require(headers.get("sec-websocket-accept") == expected, "Invalid debugger handshake")

    def close(self):
        self.reader.close()
        self.socket.close()

    def send(self, method: str, params: dict | None = None) -> int:
        self.next_id += 1
        payload = json.dumps({"id": self.next_id, "method": method, "params": params or {}}).encode()
        length = len(payload)
        header = bytes([0x81, 0x80 | length]) if length < 126 else (
            bytes([0x81, 0xFE]) + struct.pack("!H", length) if length < 65536
            else bytes([0x81, 0xFF]) + struct.pack("!Q", length))
        mask = os.urandom(4)
        self.socket.sendall(header + mask + bytes(value ^ mask[i % 4] for i, value in enumerate(payload)))
        return self.next_id

    def receive(self) -> dict:
        fragments = bytearray()
        while True:
            header = self.reader.read(2)
            if len(header) != 2:
                raise RuntimeError("Debugger disconnected")
            first, second = header
            require(first & 15 in (0, 1), "Unexpected debugger WebSocket frame")
            length = second & 127
            if length in (126, 127):
                length = struct.unpack("!H" if length == 126 else "!Q",
                                       self.reader.read(2 if length == 126 else 8))[0]
            mask = self.reader.read(4) if second & 128 else None
            payload = self.reader.read(length)
            if mask:
                payload = bytes(value ^ mask[i % 4] for i, value in enumerate(payload))
            fragments.extend(payload)
            if first & 128:
                return json.loads(fragments)

    def call(self, method: str, params: dict | None = None) -> dict:
        wanted = self.send(method, params)
        while True:
            message = self.receive()
            event = message.get("method")
            data = message.get("params", {})
            if event == "Page.javascriptDialogOpening":
                self.dialogs.append(data.get("type"))
                self.send("Page.handleJavaScriptDialog", {"accept": True})
            elif event == "Network.requestWillBeSent":
                self.requests.append(data["request"]["url"])
            elif event == "Runtime.exceptionThrown":
                self.errors.append(data["exceptionDetails"])
            if message.get("id") == wanted:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})

    def evaluate(self, expression: str):
        result = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True,
                                               "awaitPromise": True, "userGesture": True})
        require("exceptionDetails" not in result, f"Browser JavaScript failed: {result}")
        return result.get("result", {}).get("value")

    def wait_for(self, expression: str, seconds: float = 10):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.evaluate(expression):
                return
            time.sleep(.05)
        raise RuntimeError(f"Timed out waiting for {expression}")


@contextmanager
def chromium(executable: str):
    # All temporary browser data stays within demo and is removed after Chrome exits.
    with TemporaryDirectory(prefix=".verify-", dir=DEMO, ignore_cleanup_errors=True) as profile:
        process = subprocess.Popen([
            executable, "--headless=new", "--no-first-run", "--no-default-browser-check",
            "--disable-background-networking", "--disable-component-update", "--disable-sync",
            "--disable-extensions", "--disable-default-apps", "--no-proxy-server",
            "--no-sandbox", "--disable-gpu", "--disable-breakpad", "--disable-crash-reporter",
            "--password-store=basic", "--remote-debugging-port=0",
            "--remote-debugging-address=127.0.0.1", f"--user-data-dir={profile}", "about:blank",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        client = None
        try:
            active = Path(profile) / "DevToolsActivePort"
            deadline = time.monotonic() + 15
            while not active.is_file():
                require(process.poll() is None, "Browser exited before debugging was ready")
                require(time.monotonic() < deadline, "Browser launch timed out")
                time.sleep(.05)
            port = active.read_text().splitlines()[0]
            with urlopen(f"http://127.0.0.1:{port}/json/list", timeout=10) as response:
                targets = json.load(response)
            target = next(item for item in targets if item.get("type") == "page")
            client = CDP(target["webSocketDebuggerUrl"])
            for domain in ("Page", "Runtime", "Network"):
                client.call(f"{domain}.enable")
            yield client
        finally:
            if client:
                try:
                    client.send("Browser.close")
                    process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
                client.close()
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def summarize(risks) -> list[tuple[str, str]]:
    return [(risk.kind, risk.severity) for risk in risks]


def page_url(base: str, filename: str) -> str:
    if filename == "index.html":
        return f"{base}/{filename}"
    return f"http://{Path(filename).stem}.localhost:{urlsplit(base).port}/{filename}"


def published(page: dict, remote: str | None = None):
    bus, got = EventBus(), []
    bus.subscribe("risk", got.append)
    wire = RiskWire(bus, snapshot_fn=lambda: [{"name": remote}] if remote else [])
    wire.poll_once()
    wire.on_page(page)
    return got


def verify(args) -> None:
    pages = sorted(path.name for path in DEMO.glob("*.html"))
    require(set(pages) == set(EXPECTED), "Update expectations for every HTML page")
    for filename in pages:
        audit = HTMLAudit(filename)
        audit.feed((DEMO / filename).read_text(encoding="utf-8"))
        require(audit.has_lang, f"{filename}: missing language")
    collector = (DEMO / "collect-page.js").read_text(encoding="utf-8")
    instrumentation = (REPO / "extension/risk-main.js").read_text(encoding="utf-8")
    listener = """window.__demoSignals = {}; document.addEventListener('ally-risk-signals',
        event => { window.__demoSignals = JSON.parse(event.detail); });"""
    rows, screenshots, snapshots = [], [], {}
    checks = []
    executable = browser_path(args.browser)

    with serve() as base, chromium(executable) as browser:
        # Observe the actual production counters before the page's scripts run.
        browser.call("Page.addScriptToEvaluateOnNewDocument", {"source": listener + instrumentation})
        browser.call("Emulation.setEmulatedMedia", {"features": [
            {"name": "prefers-color-scheme", "value": "light"},
            {"name": "prefers-reduced-motion", "value": "reduce"}]})
        for width, height in ((1440, 900), (390, 844)):
            browser.call("Emulation.setDeviceMetricsOverride", {"width": width, "height": height,
                                                                 "deviceScaleFactor": 1, "mobile": False})
            for filename in pages:
                browser.call("Page.navigate", {"url": page_url(base, filename)})
                browser.wait_for(f"document.readyState === 'complete' && location.pathname === '/{filename}'")
                browser.wait_for("document.fonts.status === 'loaded'")
                require(browser.evaluate("document.documentElement.scrollWidth <= innerWidth"),
                        f"{filename}: horizontal overflow at {width}px")
                page = browser.evaluate(collector)
                if filename == "index.html":
                    links = browser.evaluate("Array.from(document.querySelectorAll('.scenario-card'), link => link.href)")
                    require(set(links) == {page_url(base, name) for name in pages if name != "index.html"},
                            "Landing links do not isolate the scenario hosts")
                risks = scan_page(page)
                require(summarize(risks) == EXPECTED[filename],
                        f"{filename} at {width}px: expected {EXPECTED[filename]}, got {summarize(risks)}")
                # Also exercise the on_page entry point used by the live agent.
                actual = published(page)
                require(bool(actual) == bool(EXPECTED[filename]), f"{filename}: on_page mismatch")
                if width == 1440:
                    snapshots[filename] = page
                    rows.append({"page": filename, "scan_page": [asdict(risk) for risk in risks]})
                if filename == "trap.html":
                    dialog_count = len(browser.dialogs)
                    browser.evaluate("document.getElementById('run-tricks').click()")
                    browser.wait_for("(window.__demoSignals.dialogs || 0) >= 2", seconds=10)
                    # Force a real Back event and right-click event, both observed by risk-main.js.
                    browser.evaluate("history.back(); document.dispatchEvent(new MouseEvent('contextmenu', {bubbles:true,cancelable:true}))")
                    browser.wait_for("(window.__demoSignals.backTrap || 0) >= 1 && window.__demoSignals.contextMenuBlocked")
                    active = browser.evaluate(collector)
                    require(summarize(scan_page(active)) == [("scam_popup", "high")], "Active trap should score high")
                    flags = behavior_flags(active["signals"])
                    require(flags.suspicious and "dialog_spam" in flags.flags and "fullscreen" in flags.flags,
                            f"Missing real trap signals: {flags}")
                    require(len(browser.dialogs) - dialog_count >= 2, "Dialogs were not actually opened")
                    behavior_only = dict(active, text="", overlay_text="", has_tel_link=False, fullscreen=False)
                    require(scan_page(behavior_only) == [], "scan_page unexpectedly checks behavior")
                    require(any(risk.source == "risk.behavior" for risk in published(behavior_only)),
                            "on_page did not detect the trap's behavior")
                    if width == 1440:
                        snapshots["trap.html (active)"] = active
                        rows.append({"page": "trap.html (active)", "scan_page": [asdict(risk) for risk in scan_page(active)]})
                        checks.append({"check": "real trap counters -> on_page", "result": list(flags.flags)})
                    browser.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
                    browser.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
                    require(browser.evaluate("document.getElementById('trap-overlay').hidden && !document.getElementById('run-tricks').disabled"),
                            "Escape failed to release trap")
                elif filename in ("bank.html", "shop.html"):
                    form = "bank" if filename == "bank.html" else "shop"
                    browser.evaluate("document.querySelector('[name=" + ("cardnumber" if form == "bank" else "customer") + "]').value = 'Demo Person'")
                    if form == "shop":
                        browser.evaluate("document.querySelector('[name=email]').value = 'demo@example.invalid'")
                    before = len(browser.requests)
                    browser.evaluate("document.querySelector('form').requestSubmit()")
                    require(browser.evaluate("document.querySelector('[role=status]').textContent.length > 0"), "Form did not finish locally")
                    require(len(browser.requests) == before, "Form made a network request")
                    require(summarize(scan_page(browser.evaluate(collector))) == EXPECTED[filename], "Form changed risk unexpectedly")
                elif filename == "support.html":
                    before = len(browser.requests)
                    browser.evaluate("document.getElementById('remote-tool').click()")
                    require(browser.evaluate("document.getElementById('remote-status').textContent.includes('Nothing was downloaded')"), "Install button failed")
                    require(len(browser.requests) == before, "Install button made a request")

        for remote in ("AnyDesk.exe", "QuickAssist.exe"):
            risks = published(snapshots["bank.html"], remote)
            require(any(r.kind == "remote_access" and r.severity == "high" and r.source == "risk.joined" for r in risks),
                    f"Joined warning missing for {remote}")
            checks.append({"check": f"bank + {remote} -> on_page", "result": "remote_access HIGH (risk.joined)"})
            process_snapshot = [{"name": remote}]
            bus, got = EventBus(), []
            bus.subscribe("risk", got.append)
            wire = RiskWire(bus, snapshot_fn=lambda: list(process_snapshot))
            wire.poll_once()
            wire.on_page(snapshots["support.html"])
            got.clear()
            process_snapshot.append({"name": "eventvwr.exe"})
            wire.poll_once()
            require(any(r.kind == "remote_access" and r.severity == "high" and r.source == "risk.fakescan" for r in got),
                    f"Fake-scan warning missing for {remote}")
            checks.append({"check": f"{remote} then Event Viewer -> poll_once", "result": "remote_access HIGH (risk.fakescan)"})

        # All live beats can run consecutively without the same-host deduper hiding later ones.
        bus, sequence = EventBus(), []
        bus.subscribe("risk", sequence.append)
        wire = RiskWire(bus, snapshot_fn=lambda: [])
        for name in ("virus.html", "bank.html", "trap.html", "support.html"):
            before = len(sequence)
            wire.on_page(snapshots[name])
            require(len(sequence) > before, f"Consecutive demo warning was deduplicated: {name}")
        checks.append({"check": "consecutive scenario hosts", "result": "all four warnings published"})

        # Check dark-mode geometry and page results as well as the light-mode interactions above.
        browser.call("Emulation.setEmulatedMedia", {"features": [
            {"name": "prefers-color-scheme", "value": "dark"},
            {"name": "prefers-reduced-motion", "value": "reduce"}]})
        for width, height in ((1440, 900), (390, 844)):
            browser.call("Emulation.setDeviceMetricsOverride", {"width": width, "height": height,
                                                                 "deviceScaleFactor": 1, "mobile": False})
            for filename in pages:
                browser.call("Page.navigate", {"url": page_url(base, filename)})
                browser.wait_for(f"document.readyState === 'complete' && location.pathname === '/{filename}'")
                require(browser.evaluate("document.documentElement.scrollWidth <= innerWidth"),
                        f"{filename}: dark-mode horizontal overflow at {width}px")
                require(summarize(scan_page(browser.evaluate(collector))) == EXPECTED[filename],
                        f"{filename}: dark-mode scan mismatch at {width}px")
        checks.append({"check": "dark mode at both viewports", "result": "all pages passed"})

        if not args.no_screenshots:
            shots = DEMO / "shots"
            shots.mkdir(exist_ok=True)
            for scheme in ("light", "dark"):
                browser.call("Emulation.setEmulatedMedia", {"features": [
                    {"name": "prefers-color-scheme", "value": scheme},
                    {"name": "prefers-reduced-motion", "value": "reduce"}]})
                for width, height in ((1440, 900), (390, 844)):
                    browser.call("Emulation.setDeviceMetricsOverride", {"width": width, "height": height,
                                                                         "deviceScaleFactor": 1, "mobile": False})
                    browser.call("Page.navigate", {"url": f"{base}/index.html"})
                    browser.wait_for("document.readyState === 'complete' && location.pathname === '/index.html'")
                    browser.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
                    result = browser.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
                    destination = shots / f"index-{scheme}-{width}x{height}.png"
                    destination.write_bytes(base64.b64decode(result["data"]))
                    screenshots.append(destination.relative_to(DEMO).as_posix())
                    # Full-page shots also make the lower cards and decision strip reviewable.
                    metrics = browser.call("Page.getLayoutMetrics")["cssContentSize"]
                    result = browser.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True,
                        "clip": {"x": 0, "y": 0, "width": width, "height": metrics["height"], "scale": 1}})
                    destination = shots / f"index-{scheme}-{width}-full.png"
                    destination.write_bytes(base64.b64decode(result["data"]))
                    screenshots.append(destination.relative_to(DEMO).as_posix())

        require(not browser.errors, f"Browser errors: {browser.errors}")
        require(all(urlsplit(url).hostname in ("localhost", None)
                    or urlsplit(url).hostname.endswith(".localhost") for url in browser.requests),
                "An external request was made")

    report = {"rows": rows, "integration_checks": checks, "screenshots": screenshots,
              "viewports": [[1440, 900], [390, 844]], "ai_calls": 0,
              "notes": ["Separate .localhost hosts let live demo beats warn consecutively.",
                        "Process snapshots are simulated; no remote tools or Event Viewer are launched."]}
    (DEMO / "verification.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (DEMO / "page-snapshots.json").write_text(json.dumps(snapshots, indent=2) + "\n", encoding="utf-8")
    print("page                         scan_page")
    for row in rows:
        result = ", ".join(f"{risk['kind']} {risk['severity'].upper()}" for risk in row["scan_page"]) or "none"
        print(f"{row['page']:28} {result}")
    for check in checks:
        print(f"PASS {check['check']}: {check['result']}")
    print(f"PASS all pages at both viewports; local forms; script/asset audit; {len(screenshots)} screenshots")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", help="Path to an installed Chromium browser")
    parser.add_argument("--no-screenshots", action="store_true", help="Check pages without refreshing shots")
    verify(parser.parse_args())
