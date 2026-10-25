"""Try Ally's page steps on real websites through the whole chain, the way the extension popup does it.

    (Windows) start Ally first:  C:\\ally\\.venv\\Scripts\\python.exe run.py agent
    then:                        C:\\ally\\.venv\\Scripts\\python.exe scripts\\real_sites.py [--only cnn,wiki] [--headless]

For each site: open it in Chromium with the Ally extension, send typed commands through the extension to
the running agent (popup path: extension -> WebSocket -> agent -> brain -> tools -> extension), and record
Ally's reply, the page's state (Ally layers, hidden items, overlays, zoom), the layout check, and a
screenshot per step. Report and screenshots: logs/real_sites/.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from browser.harness import AllyBrowser  # noqa: E402

OUT = ROOT / "logs" / "real_sites"

SITES = {
    "recipe": "https://www.allrecipes.com/recipe/10813/best-chocolate-chip-cookies/",
    "cnn": "https://www.cnn.com/",
    "bbc": "https://www.bbc.com/news",
    "guardian": "https://www.theguardian.com/us",
    "weather": "https://weather.com/",
    "wiki": "https://en.wikipedia.org/wiki/Accessibility",
    "usagov": "https://www.usa.gov/",
    "github_login": "https://github.com/login",
}
PAUSE = 0.8
STEPS = ["hide the pop-ups", "bigger buttons", "simplify the menu", "check the page", "reset",
         "simple mode", "check the page", "simple mode off"]


def state(ally: AllyBrowser, page) -> dict:
    s = ally.page_state(page)
    try:
        verify = ally.action("pageVerify")
        s["verify_ok"] = verify.get("ok")
        s["verify_fail"] = [k for k, v in (verify.get("checks") or {}).items() if not v.get("ok")]
    except Exception as e:
        s["verify_ok"] = f"error: {e}"
    s["layers"] = [n for n in ally.styles(page) if n.startswith("ally_")]
    s["zoom"] = ally.zoom(page)
    return s


def send(ally: AllyBrowser, text: str) -> dict:
    """The popup's command path: the extension sends the text to the agent and waits for its reply."""
    return ally.evaluate("(t) => sendCommand(t)", text)


def connected(ally: AllyBrowser, wait: float = 20.0) -> bool:
    end = time.time() + wait
    while time.time() < end:
        if ally.evaluate("() => isOpen()"):
            return True
        time.sleep(0.5)
    return False


def run_site(ally: AllyBrowser, name: str, url: str) -> dict:
    rec = {"site": name, "url": url, "steps": []}
    page = ally.page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception as e:
        rec["error"] = f"couldn't open: {e}"
        return rec
    page.bring_to_front()
    page.wait_for_timeout(4000)  # consent banners and chat widgets load late
    ally._tab_ids.clear()
    ally.reset_zoom(page)
    info = ally.action("pageInfo")
    rec["type"], rec["reasons"] = info.get("type"), info.get("reasons")
    rec["before"] = state(ally, page)
    ally.screenshot(page, f"../real_sites/{name}_0_before")
    for i, text in enumerate(STEPS, 1):
        t0 = time.time()
        try:
            reply = send(ally, text)
        except Exception as e:
            reply = {"ok": False, "error": str(e)}
        took = round(time.time() - t0, 1)  # how long the person waits for the answer
        page.wait_for_timeout(int(PAUSE * 1000))  # lets the background AI check finish before the next command
        step = {"say": text, "ok": reply.get("ok"), "reply": reply.get("text") or reply.get("error"), "seconds": took}
        try:
            step["state"] = state(ally, page)
        except Exception as e:
            step["state"] = f"error: {e}"
        ally.screenshot(page, f"../real_sites/{name}_{i}_{text.replace(' ', '_')}")
        if text == "simplify the menu":
            page.keyboard.press("Escape")  # close the panel before the next step
        rec["steps"].append(step)
        print(f"  {text!r:24} -> {step['reply']!r} ({step['seconds']} s)", flush=True)
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", default="", help="comma-separated site names: " + ", ".join(SITES))
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--steps", default="", help="commands separated by | (default: the full list)")
    ap.add_argument("--limit", type=float, default=300, help="give up after this many seconds (prints where it hung)")
    ap.add_argument("--pause", type=float, default=0.8, help="seconds to wait after each command")
    a = ap.parse_args(argv)
    global PAUSE
    PAUSE = a.pause
    import faulthandler

    faulthandler.dump_traceback_later(a.limit, exit=True)
    if a.steps:
        STEPS[:] = [s.strip() for s in a.steps.split("|") if s.strip()]
    names = [n for n in a.only.split(",") if n] or list(SITES)
    OUT.mkdir(parents=True, exist_ok=True)
    report = []
    with AllyBrowser(headed=not a.headless, connect_agent=True) as ally:
        if not connected(ally):
            print("The extension couldn't reach the Ally agent on port 8765. Start it with: run.py agent")
            return 2
        print("Extension connected to the agent.")
        for name in names:
            print(f"== {name}: {SITES[name]}", flush=True)
            report.append(run_site(ally, name, SITES[name]))
            (OUT / "report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
        errors = ally.worker_errors()
    print(f"\nReport: {OUT / 'report.json'}  Screenshots: {OUT}")
    if errors:
        print("Extension errors:", *errors[:10], sep="\n  ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
