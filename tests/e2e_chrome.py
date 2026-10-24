"""End-to-end test: real Chromium + the Ally extension + a running agent.

Needs: pip install playwright && python -m playwright install chromium
Run:   python tests/e2e_chrome.py
It uses a temporary Ally home (so your config is untouched) and port 8765 (the extension default),
so stop your own Ally first.
"""

from __future__ import annotations

import http.server
import json
import shutil
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ally.agent import Agent  # noqa: E402

PAGE = """<!doctype html><html><head><title>Ally test page</title>
<style>body{font:16px Arial;color:#777;background:#fff} p{line-height:1.2}</style></head>
<body><header>Menu Home About</header><main><h1>Test article</h1>
<p id="p1">The quick brown fox jumps over the lazy dog. This paragraph is long enough to be the main text.</p>
<p>Second paragraph with more words so that the article is clearly the biggest block on the page.
Accessibility matters for everyone, and this sentence makes the article longer than four hundred characters
so that the reader view picks it instead of the whole body element of this small test page.</p>
<a href="#x" id="link">A link</a></main><div style="height:3000px"></div></body></html>"""

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""), flush=True)


def wait_for(fn, timeout=15.0, step=0.2):
    end = time.time() + timeout
    while time.time() < end:
        try:
            v = fn()
            if v:
                return v
        except Exception:
            pass
        time.sleep(step)
    return None


def main() -> int:
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp(prefix="ally-e2e-"))
    (home / "config.toml").write_text(
        '[agent]\nport = 8765\nannounce_start = false\nzoom_target = "page"\n'
        '[speech]\nengine = "off"\n[brain]\nmode = "offline"\n', encoding="utf-8")
    (home / "plugins").mkdir()

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    site = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=site.serve_forever, daemon=True).start()
    site_url = f"http://127.0.0.1:{site.server_address[1]}/"

    agent = Agent(home, start_hotkeys=False)
    t = threading.Thread(target=agent.run_forever, daemon=True)
    t.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def api(method, path, body=None):
        req = urllib.request.Request("http://127.0.0.1:8765" + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"X-Ally-Token": agent.token, "Content-Type": "application/json"})
        try:
            with opener.open(req, timeout=20) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            return json.loads(e.read())

    def tool(name, **args):
        return api("POST", "/api/tool", {"name": name, "args": args})

    ext = str(ROOT / "extension")
    profile = tempfile.mkdtemp(prefix="ally-chrome-")
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                profile, channel="chromium", headless=True,
                args=[f"--disable-extensions-except={ext}", f"--load-extension={ext}"])
            sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=15000)
            ext_id = sw.url.split("/")[2]
            check("extension service worker started", bool(ext_id), ext_id)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(site_url)
            page.bring_to_front()

            ok = wait_for(lambda: agent.bridge.connected, 20)
            check("extension connected to agent", ok)
            h = json.loads(opener.open("http://127.0.0.1:8765/health", timeout=5).read())
            check("/health reports the extension", h.get("extension") is True)

            r = tool("set_page_zoom", zoom=1.5)
            check("set_page_zoom works", r.get("ok") and "150%" in r.get("text", ""), r.get("text") or r.get("error"))
            dpr = page.evaluate("window.devicePixelRatio")
            check("page really zoomed (devicePixelRatio 1.5)", abs(dpr - 1.5) < 0.01, str(dpr))

            r = api("POST", "/api/command", {"text": "zoom in"})
            check("'zoom in' steps to 175%", "175%" in r.get("text", ""), r.get("text"))

            r = tool("restyle_page", style="high_contrast")
            bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
            check("high_contrast style applied", r.get("ok") and bg == "rgb(0, 0, 0)", bg)
            r = tool("restyle_page", style="spacing")
            lh = page.evaluate("getComputedStyle(document.getElementById('p1')).letterSpacing")
            check("styles stack (spacing on top)", r.get("ok") and lh not in ("normal", "0px"), lh)
            r = tool("restyle_page", style="high_contrast", on=False)
            bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
            check("one style removed, other kept", bg == "rgb(255, 255, 255)" and page.evaluate(
                "getComputedStyle(document.getElementById('p1')).letterSpacing") not in ("normal", "0px"), bg)

            r = tool("get_page_text", max_chars=2000)
            check("get_page_text returns the article", "quick brown fox" in json.dumps(r), (r.get("text") or "")[:60])

            r = tool("reader_view", on=True)
            has = page.evaluate("!!document.getElementById('ally-reader-host')")
            check("reader view opens", r.get("ok") and has)
            page.keyboard.press("Escape")
            gone = wait_for(lambda: not page.evaluate("!!document.getElementById('ally-reader-host')"), 3)
            check("Escape closes reader view", bool(gone))

            r = tool("scroll_page", direction="down", amount=1)
            y = page.evaluate("window.scrollY")
            check("scroll_page scrolls", r.get("ok") and y > 100, str(y))

            r = tool("set_min_font_size", px=20)
            check("set_min_font_size", r.get("ok") and "20" in r.get("text", ""), r.get("text") or r.get("error"))

            r = tool("reset_all")
            dpr = page.evaluate("window.devicePixelRatio")
            ls = page.evaluate("getComputedStyle(document.getElementById('p1')).letterSpacing")
            check("reset_all restores zoom and styles", r.get("ok") and abs(dpr - 1.0) < 0.01 and ls in ("normal", "0px"),
                  f"{dpr} {ls} {r.get('text')}")

            r = api("POST", "/api/command", {"text": "reading mode"})
            dpr = page.evaluate("window.devicePixelRatio")
            check("config command 'reading mode' runs 4 steps", abs(dpr - 1.5) < 0.01 and
                  page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(0, 0, 0)", r.get("text"))

            r = tool("remember_site_style", styles=["dyslexia"])
            check("remember_site_style", r.get("ok") and "127.0.0.1" in r.get("text", ""), r.get("text") or r.get("error"))
            page.reload()
            fam = wait_for(lambda: "Verdana" in page.evaluate("getComputedStyle(document.getElementById('p1')).fontFamily")
                           and page.evaluate("getComputedStyle(document.getElementById('p1')).fontFamily"), 8)
            check("remembered style re-applies after reload", bool(fam), str(fam))
            tool("remember_site_style", styles=[])

            # Popup page talks to the worker.
            popup = ctx.new_page()
            popup.goto(f"chrome-extension://{ext_id}/popup.html")
            status = wait_for(lambda: "Connected" in popup.inner_text("#status") and popup.inner_text("#status"), 8)
            check("popup shows connected", bool(status), str(status))
            popup.close()
            page.bring_to_front()

            # Agent restart -> extension reconnects by itself.
            agent.request_exit(0)
            t.join(10)
            gone = wait_for(lambda: not urllib.request.urlopen("http://127.0.0.1:8765/health", timeout=1), 3)
            agent2 = Agent(home, start_hotkeys=False)
            t2 = threading.Thread(target=agent2.run_forever, daemon=True)
            t2.start()
            back = wait_for(lambda: agent2.bridge.connected, 30)
            check("extension reconnects after agent restart", bool(back))
            agent2.request_exit(0)
            t2.join(10)
            ctx.close()
    finally:
        site.shutdown()
        shutil.rmtree(profile, ignore_errors=True)
        shutil.rmtree(home, ignore_errors=True)
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
