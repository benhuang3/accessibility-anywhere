"""Capture every user-facing Ally UI surface and build one contact sheet.

    python handoff/tools/ui_gallery.py

Writes PNGs and index.html into handoff/results/ui_gallery/. Each shot runs on its own, so one failure
leaves a red placeholder and the rest carry on. It never starts or stops Ally (run.py) and never reads
.env or config.toml: the dashboard is rendered against stubbed API replies.
"""

from __future__ import annotations

import html
import json
import socket
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "handoff" / "results" / "ui_gallery"
sys.path.insert(0, str(ROOT))

SHOTS: list[dict] = []   # {group, title, note, file, size, error}
GROUPS: list[tuple[str, str]] = []


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_port(port: int, timeout: float = 60.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.3)
    return False


def group(name: str, blurb: str) -> None:
    GROUPS.append((name, blurb))


def shot(grp: str, title: str, note: str, filename: str, fn) -> None:
    """Run fn(path) -> None; record success or a failure placeholder."""
    entry = {"group": grp, "title": title, "note": note, "file": filename, "size": "", "error": ""}
    path = OUT / filename
    try:
        if path.exists():
            path.unlink()
        fn(path)
        if not path.exists() or path.stat().st_size < 1000:
            raise RuntimeError("no image was written")
        from PIL import Image
        with Image.open(path) as im:
            entry["size"] = f"{im.width} x {im.height}"
        print(f"ok      {filename}")
    except Exception as e:  # noqa: BLE001 - keep going
        entry["error"] = f"{type(e).__name__}: {e}".strip()[:300]
        print(f"FAILED  {filename}: {entry['error']}")
        traceback.print_exc(limit=3)
    SHOTS.append(entry)


# ------------------------------------------------------------------ 1. Tk overlay

def overlay_shots() -> None:
    g = "1. Tk overlay"
    group(g, "The real ally.overlay.Overlay running in-process with fake Offer/Risk objects. Cropped from the live screen.")
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    from PIL import ImageGrab
    from ally.contracts import Offer, Risk
    from ally.overlay.overlay import Overlay
    from ally.risk.copy import render

    def in_tk(ov, fn):
        done, box = threading.Event(), {}

        def run():
            try:
                box["v"] = fn()
            except Exception as e:  # noqa: BLE001
                box["e"] = e
            done.set()
        ov._root.after(0, run)
        if not done.wait(5):
            raise RuntimeError("Tk thread did not respond")
        if "e" in box:
            raise box["e"]
        return box["v"]

    def capture(setup, path, wins=("_card_win", "_offer_win"), settle=1.6):
        ov = Overlay()
        if not ov.start():
            raise RuntimeError(f"overlay could not start ({ov.error})")
        try:
            setup(ov)
            time.sleep(settle)

            def geom():
                out = []
                for name in wins:
                    w = getattr(ov, name, None)
                    if w is not None:
                        w.update_idletasks()
                        out.append((w.winfo_rootx(), w.winfo_rooty(),
                                    w.winfo_rootx() + w.winfo_width(), w.winfo_rooty() + w.winfo_height()))
                return out
            boxes = in_tk(ov, geom)
            if not boxes:
                raise RuntimeError("no overlay window appeared")
            box = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                   max(b[2] for b in boxes), max(b[3] for b in boxes))
            ImageGrab.grab(bbox=box, all_screens=True).save(path)
        finally:
            ov.stop()
            time.sleep(0.4)

    def risk_text(key, **fields):
        w = render(key, **fields)
        return w.title + " " + w.body

    def a(path):
        def setup(ov):
            offer = Offer("Want me to close AnyDesk?", context="risk",
                          reason=Risk("remote_access", "high", app="AnyDesk", source="risk.joined"), timeout=120)
            offer.pause_seconds = 30
            offer.extra_action = ("Call Priya", lambda: "Calling Priya")
            ov.show_card(risk_text("remote_access", tool="AnyDesk"), kind="risk")
            ov.offer(offer)
        capture(setup, path)

    def b(path):
        def setup(ov):
            w = render("confirm_close")
            ov.offer(Offer(w.title + " " + w.body, options=tuple(x.label for x in w.actions),
                           context="risk", timeout=120))
        capture(setup, path)

    def c(path):
        def setup(ov):
            text = (risk_text("scam_popup") + " Ally looked at the screen: the pop-up counts down and shows a phone"
                    " number to rush you.")
            ov.show_card(text, kind="risk")
            ov.offer(Offer("Want me to close this page?", context="risk",
                           reason=Risk("scam_popup", "high", url="https://secure-fix.example/alert"), timeout=120))
        capture(setup, path)

    def d(path):
        capture(lambda ov: ov.show_card("I closed the bank page. I can't close AnyDesk myself. "
                                        "Please click the X on its window.", kind="info"), path)

    def e(path):
        capture(lambda ov: ov.offer(Offer("Make this bigger?", context="browser:example.com", timeout=120)), path)

    shot(g, "Remote-access warning", "remote_access, high: merged card + question, 30 s pause on 'Continue', 'Call Priya' extra action.", "overlay_a_remote_access.png", a)
    shot(g, "Are you sure? confirm", "Second question after 'No': 'Close it' / 'Keep it open' (no card, bubble only).", "overlay_b_confirm.png", b)
    shot(g, "Scam pop-up warning (high)", "scam_popup card with an AI explanation sentence and the close-this-page question.", "overlay_c_scam_popup.png", c)
    shot(g, "Info card", "Plain info card after an action: 'I closed the bank page...' with an OK button.", "overlay_d_info_card.png", d)
    shot(g, "Plain offer bubble", "Non-risk help question ('Make this bigger?'), shown as a white bubble.", "overlay_e_offer_bubble.png", e)


# ------------------------------------------------------------------ browser helpers

CHROME_STUB_CALL = """
window.chrome = window.chrome || {};
chrome.runtime = chrome.runtime || {};
chrome.runtime.lastError = null;
chrome.runtime.sendMessage = async (m, cb) => { const r = await window.__reply(m); if (cb) cb(r); return r; };
chrome.runtime.onMessage = { addListener: (fn) => { (window.__listeners = window.__listeners || []).push(fn); } };
chrome.tabs = { create: () => {} };
"""


def browser_shots(p) -> None:
    from playwright.sync_api import Route  # noqa: F401

    browser = p.chromium.launch()
    try:
        banner_shot(browser)
        popup_shot(browser)
        dashboard_shots(browser)
        meet_shot(browser)
    finally:
        browser.close()


def page_with_routes(browser, routes: dict, **ctx):
    """A page whose http://*.example origins are answered from `routes` (host+path -> (type, body))."""
    context = browser.new_context(**ctx)
    page = context.new_page()

    def handle(route):
        url = route.request.url
        key = url.split("://", 1)[1].split("?", 1)[0]
        hit = routes.get(key)
        if hit is None:
            route.fulfill(status=404, body="not found")
        else:
            route.fulfill(status=200, content_type=hit[0], body=hit[1])
    context.route("http://**/*", handle)
    return context, page


FAKE_VIRUS_PAGE = """<!doctype html><html><head><meta charset=utf-8><title>Security alert</title>
<style>body{margin:0;background:#0b3d91;color:#fff;font:20px Arial,sans-serif}
.box{max-width:760px;margin:70px auto;padding:36px;background:#c00;border:6px solid #ff0;text-align:center}
h1{font-size:40px;margin:0 0 14px}.num{font-size:44px;font-weight:bold;background:#fff;color:#c00;padding:10px;margin:18px 0}
p{margin:8px 0}</style></head><body><div class=box><h1>WARNING: Your computer is infected!</h1>
<p>SafeGuard Defender found 7 viruses. Your bank details are at risk.</p>
<p>Do not close this window or restart your PC.</p><div class=num>Call 1-800-555-0142 now</div>
<p>Support is waiting. Time left: 04:47</p></div></body></html>"""


def banner_shot(browser) -> None:
    g = "2. In-page risk banner"
    group(g, "extension/risk.js loaded as-is onto a fake scam page, with chrome.* stubbed; the banner is triggered through its message listener.")
    from ally.risk.copy import render

    def fn(path):
        w = render("scam_popup")
        context, page = page_with_routes(browser, {"safeguard-defender.example/alert": ("text/html", FAKE_VIRUS_PAGE)},
                                         viewport={"width": 1280, "height": 720})
        try:
            page.expose_function("__reply", lambda m: {"ok": True})
            page.add_init_script(CHROME_STUB_CALL)
            page.goto("http://safeguard-defender.example/alert")
            page.add_script_tag(path=str(ROOT / "extension" / "risk.js"))
            page.evaluate("t => window.__listeners.forEach(fn => fn({kind: 'allyRiskBanner', text: t}))",
                          w.title + " " + w.body)
            page.wait_for_timeout(400)
            page.screenshot(path=str(path))
        finally:
            context.close()
    shot(g, "Risk banner over a fake virus page", "The yellow top banner with 'Take me back to safety' and 'I understand' (closed shadow root, so shot is of the real script).", "banner_scam_popup.png", fn)


def popup_shot(browser) -> None:
    g = "3. Extension popup"
    group(g, "extension/popup.html + popup.css opened from disk at 380 x 600 with chrome.* stubbed.")

    def fn(path):
        context = browser.new_context(viewport={"width": 380, "height": 600})
        page = context.new_page()
        try:
            def reply(m):
                op = m.get("op")
                if m.get("kind") == "ally-simple":
                    return {"ok": True, "allowed": True, "active": True, "remembered": True,
                            "host": "bank.example", "count": 2}
                if op == "status":
                    return {"connected": True, "port": 8765}
                if op == "action":
                    return {"ok": True, "result": 1.25}
                return {"ok": True, "text": "Done."}
            page.expose_function("__reply", reply)
            page.add_init_script(CHROME_STUB_CALL)
            page.goto((ROOT / "extension" / "popup.html").as_uri())
            page.wait_for_timeout(400)
            page.click('button[data-action="zoomStep"] >> nth=0')
            page.wait_for_timeout(300)
            page.evaluate("document.querySelector('details').open = true")
            page.screenshot(path=str(path), full_page=True)
        finally:
            context.close()
    shot(g, "Popup, connected", "Connected status, Simple mode on, a 'Zoom 125%' reply and Settings expanded (full popup height, taller than the 600 px viewport).", "popup.png", fn)


def dashboard_shots(browser) -> None:
    g = "4. Dashboard"
    group(g, "dashboard/index.html, style.css and app.js served from disk with every /api/* call stubbed.")
    dash = ROOT / "dashboard"
    status = {
        "name": "Ally", "version": "0.9.0", "platform": "win32", "uptime": 5400,
        "brain": {"mode": "claude", "online": True, "model": "sonnet", "has_key": True},
        "browser": {"connected": True}, "voice": {"listening": False, "available": True},
        "hotkeys": {"status": "on", "error": "", "keys": [
            {"keys": "Ctrl+Alt+Up", "action": "zoom_in"}, {"keys": "Ctrl+Alt+Down", "action": "zoom_out"},
            {"keys": "Ctrl+Alt+R", "action": "read_page"}, {"keys": "Ctrl+Alt+0", "action": "reset_all"}]},
        "listener": {"status": "listening", "awake": False, "wake_words": ["ally"], "error": ""},
        "gaze": {"status": "off", "error": ""}, "config": {"error": ""},
        "speech": {"engine": "sapi", "speaking": False},
    }
    events = [
        {"id": 1, "ts": time.time() - 300, "kind": "command", "text": "make this easier to read"},
        {"id": 2, "ts": time.time() - 298, "kind": "tool", "text": "restyle_page high_contrast"},
        {"id": 3, "ts": time.time() - 297, "kind": "reply", "text": "I made the page high contrast."},
        {"id": 4, "ts": time.time() - 120, "kind": "error", "text": "magnifier: not available on this screen"},
    ]
    tools = [
        {"name": "zoom_in", "description": "Make the current page bigger.", "required": [], "source": "builtin", "ai": True, "disabled": False},
        {"name": "read_page", "description": "Read the page aloud.", "required": [], "source": "builtin", "ai": True, "disabled": False},
        {"name": "my_note", "description": "Say a saved note.", "required": [], "source": "plugin", "ai": False, "disabled": True},
    ]

    def make(speaking: bool):
        def fn(path):
            st = dict(status, speech={"engine": "sapi", "speaking": speaking})
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()
            try:
                def handle(route):
                    url = route.request.url
                    rel = url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
                    if rel in ("", "index.html"):
                        body = (dash / "index.html").read_text("utf-8").replace("__ALLY_TOKEN__", "demo").replace("__ALLY_NAME__", "Ally")
                        return route.fulfill(content_type="text/html", body=body)
                    if rel in ("style.css", "app.js", "favicon.svg"):
                        ctype = {"css": "text/css", "js": "text/javascript", "svg": "image/svg+xml"}[rel.rsplit(".", 1)[1]]
                        return route.fulfill(content_type=ctype, body=(dash / rel).read_bytes())
                    data = {"api/status": st, "api/events": {"events": events}, "api/tools": {"tools": tools},
                            "api/config": {"text": '[general]\nname = "Ally"\n'}}.get(rel, {})
                    route.fulfill(content_type="application/json", body=json.dumps(data))
                context.route("http://ally.test/**", handle)
                page.goto("http://ally.test/")
                page.wait_for_timeout(1200)
                if speaking:
                    page.wait_for_function("document.getElementById('mascot').classList.contains('talking')")
                else:
                    page.wait_for_function("!document.getElementById('mascot').classList.contains('talking')")
                page.evaluate("document.activeElement && document.activeElement.blur()")
                page.screenshot(path=str(path), full_page=True)
            finally:
                context.close()
        return fn
    shot(g, "Dashboard, normal", "Idle: status chips, mascot with greeting bubble, quick actions, activity log.", "dashboard_normal.png", make(False))
    shot(g, "Dashboard, mascot talking", "speech.speaking = true so the mascot has the .talking class (mouth/eyes animating).", "dashboard_talking.png", make(True))


def meet_shot(browser) -> None:
    g = "5. Meet call frame"
    group(g, "extension/call.js and call.css run unmodified on a fake Meet page (dark video, a Leave call control bar).")
    page_html = """<!doctype html><html><head><meta charset=utf-8><title>Meet</title>
<style>html,body{margin:0;height:100%;background:#202124;font-family:Arial}
.video{position:fixed;inset:0;background:radial-gradient(circle at 50% 40%,#3b4a5a,#14181d 70%)}
.face{position:absolute;left:50%;top:42%;width:240px;height:240px;margin:-120px;border-radius:50%;background:#6b7c8c}
.name{position:absolute;left:24px;bottom:110px;color:#fff;font-size:18px}
.ctrl{position:fixed;left:0;right:0;bottom:0;height:80px;background:#202124;display:flex;justify-content:center;align-items:center;gap:16px}
.ctrl button{width:56px;height:56px;border-radius:50%;border:0;background:#3c4043;color:#fff}</style></head>
<body><div class=video><div class=face></div><div class=name>Priya</div></div>
<div class=ctrl><div><button aria-label="Turn off microphone">M</button><button aria-label="Turn off camera">C</button>
<button aria-label="Leave call">X</button></div></div></body></html>"""

    def fn(path):
        context, page = page_with_routes(browser, {"meet.example/abc-defg-hij": ("text/html", page_html)},
                                         viewport={"width": 1280, "height": 720})
        try:
            page.expose_function("__reply", lambda m: {"isCall": True, "name": "Priya"} if m.get("kind") == "allyCallInfo" else {"ok": True})
            page.add_init_script(CHROME_STUB_CALL)
            page.goto("http://meet.example/abc-defg-hij")
            page.add_style_tag(path=str(ROOT / "extension" / "call.css"))
            page.add_script_tag(path=str(ROOT / "extension" / "call.js"))
            page.wait_for_function("document.getElementById('ally-call-bar') && "
                                   "document.getElementById('ally-call-bar').textContent.startsWith('Talking with')", timeout=10000)
            page.wait_for_timeout(300)
            page.screenshot(path=str(path))
        finally:
            context.close()
    shot(g, "Meet call frame", "Cream top bar 'Talking with Priya', the red End call button, Meet's own controls hidden.", "meet_call_frame.png", fn)


# ------------------------------------------------------------------ 6. Ally Call (spacecall)

def spacecall_shots(p) -> None:
    g = "6. Ally Call (spacecall)"
    group(g, "SvelteKit app from spacecall/ served by vite with the mock config (no camera or call service); fake media flags on.")
    sc = ROOT / "spacecall"
    port = free_port()
    proc = None
    try:
        proc = subprocess.Popen(["node", "node_modules/vite/bin/vite.js", "--config", "tests/phone-preview.config.mjs",
                                 "--port", str(port), "--strictPort", "--host", "127.0.0.1"],
                                cwd=sc, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        up = wait_port(port, 90)
        browser = p.chromium.launch(args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"])
        base = f"http://127.0.0.1:{port}/#room=0123456789abcdef0123456789abcdef"

        def make(role, w, h, join, mobile=False, selector=None, note_wait=1500):
            def fn(path):
                if not up:
                    raise RuntimeError("vite did not start")
                kw = dict(viewport={"width": w, "height": h}, permissions=["camera", "microphone"])
                if mobile:
                    kw.update(is_mobile=True, has_touch=True, device_scale_factor=2)
                context = browser.new_context(**kw)
                page = context.new_page()
                try:
                    page.goto(f"{base}&role={role}&name=Priya&elderName=Mom")
                    page.wait_for_selector(f".join, {selector}" if selector else ".join", timeout=30000)
                    page.wait_for_timeout(800)
                    if join:
                        for _ in range(100):
                            btn = page.query_selector(".join")
                            if btn and btn.is_enabled():
                                btn.click()
                            if page.query_selector(selector):
                                break
                            page.wait_for_timeout(100)
                        else:
                            raise RuntimeError(f"never reached {selector}")
                        page.wait_for_timeout(note_wait)
                    page.screenshot(path=str(path))
                finally:
                    context.close()
            return fn
        shot(g, "Elder, narrow 460 x 1000", "Elder view in a call: helper's face, sharing banner, big controls.", "spacecall_elder_narrow.png", make("elder", 460, 1000, True, selector=".remote-video"))
        shot(g, "Elder, wide 1280 x 800", "Same elder view on a PC-sized window.", "spacecall_elder_wide.png", make("elder", 1280, 800, True, selector=".remote-video"))
        shot(g, "Helper landing", "Helper opens the link, before joining (393 x 852 touch).", "spacecall_helper_landing.png", make("helper", 393, 852, False, mobile=True))
        shot(g, "Helper in call, portrait", "Helper sees Mom's shared screen plus her face, 393 x 852.", "spacecall_helper_portrait.png", make("helper", 393, 852, True, True, ".screen-image"))
        shot(g, "Helper in call, landscape", "Same, 852 x 393.", "spacecall_helper_landscape.png", make("helper", 852, 393, True, True, ".screen-image"))
        try:
            browser.close()
        except Exception:
            pass
    except Exception as e:  # noqa: BLE001
        for title, f in [("Elder, narrow 460 x 1000", "spacecall_elder_narrow.png"), ("Elder, wide 1280 x 800", "spacecall_elder_wide.png"),
                         ("Helper landing", "spacecall_helper_landing.png"), ("Helper in call, portrait", "spacecall_helper_portrait.png"),
                         ("Helper in call, landscape", "spacecall_helper_landscape.png")]:
            if not any(s["file"] == f for s in SHOTS):
                SHOTS.append({"group": g, "title": title, "note": "", "file": f, "size": "", "error": f"{type(e).__name__}: {e}"})
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(10)
            except Exception:
                proc.kill()


# ------------------------------------------------------------------ 7. Scam Lab

def demo_shots(p) -> None:
    g = "7. Scam Lab demo site"
    group(g, "demo/ served by python -m http.server; fictional brands. Viewport 1440 x 900 (first screen only).")
    port = free_port()
    proc = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
                            cwd=ROOT / "demo", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pages = [("index", "Landing page", "Scenario picker."), ("virus", "Fake virus alert", "Scenario page."),
             ("bank", "Fake bank login", "Scenario page."), ("trap", "Page trap", "Scenario page."),
             ("support", "Fake tech support", "Scenario page."), ("shop", "Look-alike shop", "Scenario page."),
             ("news", "News page", "Scenario page.")]
    try:
        wait_port(port, 15)
        browser = p.chromium.launch()
        for name, title, note in pages:
            def fn(path, name=name):
                context = browser.new_context(viewport={"width": 1440, "height": 900})
                page = context.new_page()
                try:
                    page.goto(f"http://127.0.0.1:{port}/{name}.html")
                    page.wait_for_timeout(900)
                    page.screenshot(path=str(path))
                finally:
                    context.close()
            shot(g, title, f"{name}.html. {note}", f"demo_{name}.png", fn)
        browser.close()
    finally:
        proc.terminate()


# ------------------------------------------------------------------ index.html

def write_index() -> None:
    css = ("body{margin:0;background:#f2f2f0;color:#1b1b1b;font:15px/1.45 system-ui,'Segoe UI',Arial,sans-serif}"
           "main{max-width:1500px;margin:0 auto;padding:24px}h1{margin:0 0 4px}h2{margin:36px 0 2px;border-bottom:2px solid #c9c9c4;padding-bottom:4px}"
           ".blurb{color:#555;margin:4px 0 14px}.grid{display:flex;flex-wrap:wrap;gap:18px;align-items:flex-start}"
           "figure{margin:0;background:#fff;border:1px solid #cfcfca;border-radius:8px;padding:10px;max-width:560px}"
           "figure img{display:block;max-width:100%;height:auto;border:1px solid #ddd}"
           "figcaption b{display:block;margin-top:8px}.size{color:#777;font-size:13px}.note{font-size:14px;color:#333}"
           ".fail{background:#fde8e8;border-color:#c00;color:#a00000;padding:18px;min-width:260px}")
    parts = [f"<!doctype html><html lang=en><head><meta charset=utf-8><title>Ally UI gallery</title><style>{css}</style></head><body><main>",
             f"<h1>Ally UI gallery</h1><p class=blurb>Generated {time.strftime('%Y-%m-%d %H:%M')} by handoff/tools/ui_gallery.py.</p>"]
    for name, blurb in GROUPS:
        parts.append(f"<h2>{html.escape(name)}</h2><p class=blurb>{html.escape(blurb)}</p><div class=grid>")
        for s in [x for x in SHOTS if x["group"] == name]:
            if s["error"]:
                parts.append(f"<figure class=fail><b>{html.escape(s['title'])}</b>could not capture: {html.escape(s['error'])}</figure>")
            else:
                parts.append(f"<figure><a href='{s['file']}'><img src='{s['file']}' alt='{html.escape(s['title'])}' loading=lazy></a>"
                             f"<figcaption><b>{html.escape(s['title'])}</b><span class=size>{s['size']} &middot; {s['file']}</span><br>"
                             f"<span class=note>{html.escape(s['note'])}</span></figcaption></figure>")
        parts.append("</div>")
    parts.append("</main></body></html>")
    (OUT / "index.html").write_text("".join(parts), "utf-8")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    stages = [("overlay", overlay_shots)]
    try:
        overlay_shots()
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        if not any(s["group"].startswith("1.") for s in SHOTS):
            group("1. Tk overlay", "")
            SHOTS.append({"group": "1. Tk overlay", "title": "Tk overlay", "note": "", "file": "", "size": "", "error": str(e)})
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            for fn in (browser_shots, spacecall_shots, demo_shots):
                try:
                    fn(p)
                except Exception as e:  # noqa: BLE001
                    traceback.print_exc()
                    SHOTS.append({"group": "(stage)", "title": fn.__name__, "note": "", "file": "", "size": "", "error": str(e)})
    except Exception as e:  # noqa: BLE001
        SHOTS.append({"group": "(playwright)", "title": "Playwright", "note": "", "file": "", "size": "", "error": str(e)})
        GROUPS.append(("(playwright)", ""))
    for s in SHOTS:  # stage-level errors need their group listed
        if s["group"] not in [g for g, _ in GROUPS]:
            GROUPS.append((s["group"], ""))
    # present groups in numeric order
    GROUPS.sort(key=lambda g: g[0])
    write_index()
    failed = [s for s in SHOTS if s["error"]]
    print(f"\n{len(SHOTS) - len(failed)} ok, {len(failed)} failed -> {OUT / 'index.html'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
