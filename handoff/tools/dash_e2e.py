"""Dashboard Proactive panel: real Agent + real HTTP server + real Chromium. Usage: python dash_e2e.py <repo> <outdir>"""
import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

repo, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(repo))
sys.path.insert(0, str(repo / "tests"))

from helpers import temp_home  # noqa: E402
from ally.agent import Agent  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

PORT = 8799
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""), flush=True)


home = temp_home(f'[agent]\nport = {PORT}\nannounce_start = false\n[brain]\nmode = "offline"\n'
                 '[proactive]\noverlay = false\nsenses_behavior = false\nsenses_vision = false\nvoice_confirm = false\n'
                 'cooldown_seconds = 0\noffer_timeout_seconds = 30\nauto_apply_after = 2\n')
agent = Agent(home, start_hotkeys=False)
applied = []
agent.proactive.call_tool = lambda n, a, s: applied.append(n) or SimpleNamespace(ok=True)
agent.proactive.context_fn = lambda: {"app": "Google Chrome", "site": "chase.com", "in_browser": True}
threading.Thread(target=agent.run_forever, daemon=True).start()
time.sleep(2.0)

try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1100, "height": 1400})
        page = ctx.new_page()
        page.add_init_script("window.__csp=[];document.addEventListener('securitypolicyviolation',e=>window.__csp.push(e.violatedDirective+' '+e.blockedURI));")
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        bad = []
        def on_resp(r):
            if r.status >= 400:
                try:
                    bad.append(f"{r.status} {r.request.method} {r.url.split('8799')[1]} body={r.request.post_data} -> {r.text()[:120]}")
                except Exception as e:
                    bad.append(f"{r.status} {r.url}")
        page.on("response", on_resp)
        page.goto(f"http://127.0.0.1:{PORT}/")
        page.wait_for_selector("#signals li")
        page.wait_for_timeout(1500)

        check("calm state shows nothing pending", "Nothing to ask" in page.inner_text("#offer-text"), page.inner_text("#offer-text"))
        check("Yes/No hidden when no offer", page.is_hidden("#offer-actions"))
        page.screenshot(path=str(out / "01-calm.png"), full_page=True)

        page.click(".demo summary")
        page.click("[data-demo=squint]")
        page.wait_for_selector("#offer.open", timeout=8000)
        check("offer appears with a question", "?" in page.inner_text("#offer-text"), page.inner_text("#offer-text"))
        check("Yes/No visible", page.is_visible("#offer-yes") and page.is_visible("#offer-no"))
        page.wait_for_timeout(1200)
        fused = float(page.inner_text("#fused-value"))
        check("fused score shown", fused > 0.3, str(fused))
        check("meter exposes value", float(page.get_attribute("#fused-meter", "aria-valuenow")) > 0.3)
        check("trace shows 'Noticed' and 'Asked'", "Noticed" in page.inner_text("#trace") and "Asked" in page.inner_text("#trace"))
        page.screenshot(path=str(out / "02-offer.png"), full_page=True)

        page.click("#offer-yes")
        page.wait_for_selector("#offer:not(.open)", timeout=8000)
        page.wait_for_timeout(1500)
        check("answering yes applied the fix", applied == ["set_page_zoom"], str(applied))
        check("trace shows answer and 'Did it'", "You answered" in page.inner_text("#trace") and "Did it" in page.inner_text("#trace"))
        check("habit learned for chase.com", "chase.com" in page.inner_text("#habits") and "yes 1" in page.inner_text("#habits"),
              page.inner_text("#habits")[:80])
        page.screenshot(path=str(out / "03-after-yes.png"), full_page=True)

        # second yes -> third time Ally acts on its own
        for _ in range(2):
            page.click("[data-demo=squint]")
            try:
                page.wait_for_selector("#offer.open", timeout=4000)
                page.click("#offer-yes")
            except Exception:
                pass
            page.wait_for_timeout(1500)
        page.wait_for_timeout(1500)
        check("habit becomes automatic", "does this on its own" in page.inner_text("#habits"), page.inner_text("#habits")[:120])
        page.screenshot(path=str(out / "04-habit-auto.png"), full_page=True)

        page.click("[data-demo=scam]")
        page.wait_for_timeout(1500)
        check("scam warning appears in trace", "Warned" in page.inner_text("#trace"))

        page.once("dialog", lambda d: d.accept())
        page.click("text=Forget this site")
        page.wait_for_timeout(1200)
        check("forgetting clears the habit", "Nothing yet" in page.inner_text("#habits"), page.inner_text("#habits")[:60])

        check("no CSP violations", page.evaluate("window.__csp") == [], str(page.evaluate("window.__csp")))
        print("BAD RESPONSES:", *bad[:6], sep=chr(10) + "  ")
        check("no page errors", not errors, "; ".join(errors)[:200])
        names = page.evaluate("""[...document.querySelectorAll('#proactive button')].filter(b=>!(b.innerText||b.getAttribute('aria-label')||'').trim()).length""")
        check("every button has a name", names == 0)

        # layouts: narrow phone-ish, big-zoom-ish, dark
        page.set_viewport_size({"width": 390, "height": 1200})
        page.click("[data-demo=squint]")
        page.wait_for_timeout(1500)
        overflow = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
        check("no horizontal scroll at 390px", not overflow)
        page.screenshot(path=str(out / "05-narrow.png"), full_page=True)
        page.close()

        dark = browser.new_context(viewport={"width": 1100, "height": 1400}, color_scheme="dark")
        dp = dark.new_page()
        dp.goto(f"http://127.0.0.1:{PORT}/")
        dp.wait_for_selector("#signals li")
        dp.wait_for_timeout(1200)
        dp.screenshot(path=str(out / "06-dark.png"), full_page=True)
        browser.close()
finally:
    agent.close()

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
