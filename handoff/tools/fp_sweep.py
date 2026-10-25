"""False-alarm sweep: run Ally's local risk pipeline over real popular sites. No model calls.

Usage: python fp_sweep.py <repo> <tranco.csv> <outdir> [target=500] [concurrency=8]
Loads each site's home page in headless Chromium with the extension's behavior recorder (risk-main.js) injected,
then asks: would Ally warn (rules, behavior), and would it spend a model review (rules or weak-sign gate)?
Sites from the Tranco list (https://tranco-list.eu) are tried in rank order until <target> pages load with text.
"""
import asyncio
import csv
import json
import sys
import time
from pathlib import Path

repo, tranco, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
args = [a for a in sys.argv if a != "--rejudge"]
target = int(args[4]) if len(args) > 4 else 500
workers = int(args[5]) if len(args) > 5 else 8
out.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(repo))

from ally.contracts import EventBus  # noqa: E402
from ally.risk import behavior, soft, text  # noqa: E402
from ally.risk.wire import RiskWire, _page, _scan  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

RECORDER = (repo / "extension" / "risk-main.js").read_text(encoding="utf-8")
# Keep the latest counters where the sweep can read them (the extension's isolated script does this in Chrome).
CATCH = ("document.addEventListener('ally-risk-signals', e => { try { window.__allySignals = JSON.parse(e.detail); }"
         " catch (_) {} });")
COLLECT = """() => {
  const w = window.innerWidth, h = window.innerHeight, parts = [];
  for (const el of document.querySelectorAll('body *')) {
    const cs = getComputedStyle(el);
    if ((cs.position !== 'fixed' && cs.position !== 'absolute') || cs.display === 'none' || cs.visibility === 'hidden') continue;
    const r = el.getBoundingClientRect();
    if (r.width * r.height < w * h * 0.25) continue;
    const t = (el.innerText || '').trim(); if (t) parts.push(t.slice(0, 2000)); if (parts.length >= 3) break;
  }
  const fields = [...document.querySelectorAll('input, select, textarea')].slice(0, 40).map(el => ({
    name: el.name || '', id: el.id || '', placeholder: el.placeholder || '', type: el.type || '',
    autocomplete: el.getAttribute('autocomplete') || '',
    label: ((el.labels && el.labels[0] && el.labels[0].innerText) || el.getAttribute('aria-label') || '').slice(0, 80)}));
  return { url: location.href, title: document.title || '',
           text: ((document.body && document.body.innerText) || '').slice(0, 20000),
           overlay_text: parts.join('\\n'), fullscreen: !!document.fullscreenElement,
           has_tel_link: !!document.querySelector('a[href^="tel:"]'), fields, signals: window.__allySignals || {} };
}"""


def judge(page_msg: dict) -> dict:
    """What Ally would do with this page, without a model: warnings, and whether it would ask for a review."""
    page = _page(page_msg)
    risks = _scan(page)
    beh = behavior.behavior_flags(page["signals"])
    phone = "phone" in text.score_text(page["text"], page["url"], page["overlay_text"], False,
                                       page["has_tel_link"]).signals
    weak = soft.soft_flags(page["text"], phone)
    gate = (not risks and not beh.suspicious and not text.is_allowlisted(page["url"]) and soft.soft_gate(weak))
    return {"warnings": [(r.kind, r.severity, r.source, r.evidence[:120]) for r in risks]
            + ([("scam_popup", "medium", "risk.behavior", beh.evidence)] if beh.suspicious else []),
            "behavior_flags": list(beh.flags), "weak_signs": list(weak),
            "would_review": bool(risks) or beh.suspicious or gate, "review_by_weak_signs_only": gate}


async def visit(browser, domain: str) -> dict:
    ctx = await browser.new_context(user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                                "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"),
                                    viewport={"width": 1280, "height": 800}, locale="en-US")
    await ctx.add_init_script(CATCH)
    await ctx.add_init_script(RECORDER)
    page = await ctx.new_page()
    started = time.time()
    try:
        await page.goto(f"https://{domain}", wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(3500)  # let pop-ups, consent walls and the recorder's 1.5 s report happen
        msg = await page.evaluate(COLLECT)
        row = {"domain": domain, "ok": len(msg["text"].strip()) >= 200, "final_url": msg["url"],
               "title": msg["title"][:100], "chars": len(msg["text"]), "seconds": round(time.time() - started, 1)}
        if row["ok"]:
            row.update(judge(msg))
            row["page"] = msg  # kept so rule changes can be re-judged offline: python fp_sweep.py ... --rejudge
        return row
    except Exception as e:
        return {"domain": domain, "ok": False, "error": type(e).__name__ + ": " + str(e)[:120]}
    finally:
        await ctx.close()


async def main():
    with open(tranco, newline="", encoding="utf-8") as f:
        domains = [row[1] for row in csv.reader(f) if len(row) > 1]
    rows, loaded = [], 0
    queue = asyncio.Queue()
    for d in domains:
        queue.put_nowait(d)
    lock = asyncio.Lock()

    async with async_playwright() as p:
        browser = await p.chromium.launch()

        async def worker():
            nonlocal loaded
            while loaded < target and not queue.empty():
                row = await visit(browser, queue.get_nowait())
                async with lock:
                    if loaded >= target and row.get("ok"):
                        continue
                    rows.append(row)
                    if row.get("ok"):
                        loaded += 1
                        if loaded % 25 == 0:
                            print(f"{loaded}/{target} loaded ({len(rows)} tried)", flush=True)

        await asyncio.gather(*(worker() for _ in range(workers)))
        await browser.close()

    good = [r for r in rows if r.get("ok")]
    warned = [r for r in good if r["warnings"]]
    review = [r for r in good if r["would_review"]]
    weak_only = [r for r in good if r["review_by_weak_signs_only"]]
    summary = {"tried": len(rows), "loaded": len(good), "warned": len(warned), "would_review": len(review),
               "reviews_from_weak_signs_only": len(weak_only),
               "warned_rate": round(len(warned) / max(1, len(good)), 4),
               "review_rate": round(len(review) / max(1, len(good)), 4)}
    (out / "sweep.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    print("\nWould warn:")
    for r in warned:
        print(f"  {r['domain']:28} {r['warnings']}")
    print("\nWould ask the model only because of weak signs:")
    for r in weak_only:
        print(f"  {r['domain']:28} {r['weak_signs']}")


def rejudge():
    """Re-run the rules over the pages saved by the last sweep (no browser, no network)."""
    data = json.loads((out / "sweep.json").read_text(encoding="utf-8"))
    rows = [dict(r, **judge(r["page"])) for r in data["rows"] if r.get("ok") and r.get("page")]
    warned = [r for r in rows if r["warnings"]]
    print(json.dumps({"loaded": len(rows), "warned": len(warned), "warned_rate": round(len(warned) / max(1, len(rows)), 4),
                      "high": sum(1 for r in warned if any(w[1] == "high" for w in r["warnings"])),
                      "would_review": sum(1 for r in rows if r["would_review"])}, indent=1))
    for r in warned:
        print(f"  {r['domain']:28} {r['warnings']}")


if "--rejudge" in sys.argv:
    rejudge()
else:
    asyncio.run(main())
