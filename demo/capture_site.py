r"""Capture a website four ways for the presentation: original, bigger text, bigger buttons, simple mode.

Usage (in the VM, from C:\ally):  python demo\capture_site.py <url> <output folder>

Each version is saved as a static HTML file (BBC's scripts removed so it doesn't re-render, a <base> so images
load from bbc.com, Ally's injected CSS and its shadow-DOM overlays baked in) plus a screen-size and a full-page PNG.
"""
import os, re, sys, time
from pathlib import Path
sys.path.insert(0, r"C:\ally\tests"); sys.path.insert(0, r"C:\ally")
from browser.harness import AllyBrowser, FixtureServer, ActionError
from ally import config as cfgmod
from ally.registry import Registry, ToolError
from ally.tools import page_tools, ui_text_tools

URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.bbc.com/news"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else r"C:\ally\demo\bbc-news"); OUT.mkdir(parents=True, exist_ok=True)
# Consent boxes we know how to accept, the way a reader would: (frame selector or "", button text or CSS selector).
CONSENT = [("iframe[id^='sp_message_iframe']", "Accept and Continue"), ("iframe[id^='sp_message_iframe']", "Accept"),
           ("", "#onetrust-accept-btn-handler"), ("", "Accept All"), ("", "Accept all"), ("", "I Accept"), ("", "Agree")]
CFG = cfgmod.merge({"page_enlarge": {"ai_check": False}})

class Bridge:
    connected = focused = True
    def call(self, action, /, timeout=8.0, **args):
        try: return ally.action(action, args)
        except ActionError as e: raise ToolError(str(e).split(" failed: ", 1)[-1]) from None
class State(dict):
    def set(self, k, v): self[k] = v
class Ctx:
    browser = Bridge(); config = CFG; agent = None; os = None; state = State()
    def say(self, t, wait=False): return True
    def stop_speaking(self): pass
R = Registry()
for t in (page_tools.enlarge_buttons, page_tools.simplified_mode, ui_text_tools.enlarge_text): R.add(t.__ally_tool__)
def run(name, **args):
    t0 = time.time(); t = R.get(name)
    try: out = t.func(Ctx(), **R.prepare_args(t, args))
    except ToolError as e: out = f"ERROR {e}"
    print(f"  {name}({args}) {time.time()-t0:.1f}s -> {str(out)[:220]}", flush=True)
    return out

SNAPSHOT = r"""
(allyCss) => {
  // BBC's styles live only in the browser's CSS objects (styled-components inserts rules with insertRule and leaves
  // the <style> tags empty): write each sheet's rules into its tag so the saved copy keeps them.
  for (const st of document.querySelectorAll("style")) {
    try {
      const rules = st.sheet ? [...st.sheet.cssRules].map((r) => r.cssText).join("\n") : "";
      if (rules.length > st.textContent.length) st.textContent = rules;
    } catch (e) { /* unreadable sheet: keep the tag as it is */ }
  }
  // Linked stylesheets: copy their rules in too, so the saved copy doesn't depend on fetching them again.
  // Sheets from other sites can't be read and stay as links.
  for (const link of document.querySelectorAll('link[rel~="stylesheet"]')) {
    try {
      const rules = link.sheet ? [...link.sheet.cssRules].map((r) => r.cssText).join("\n") : "";
      if (!rules) continue;
      const st = document.createElement("style");
      st.setAttribute("data-from", link.href);
      if (link.media) st.media = link.media;
      st.textContent = rules;
      link.replaceWith(st);
    } catch (e) { /* another site's sheet: keep the link */ }
  }
  // Ally's shadow-DOM overlays: copy their adopted stylesheets into a <style> so getHTML can carry them.
  const temp = [];
  const roots = [];
  for (const host of document.querySelectorAll('[id^="ally-"]')) {
    const root = host.shadowRoot;
    if (!root) continue;
    roots.push(root);
    const css = [...(root.adoptedStyleSheets || [])].map((s) => [...s.cssRules].map((r) => r.cssText).join("\n")).join("\n");
    if (css) { const st = document.createElement("style"); st.textContent = css; root.prepend(st); temp.push(st); }
  }
  // Ally's CSS layers (injected by the extension, not in the DOM).
  const layer = document.createElement("style");
  layer.id = "ally-baked-layers";
  layer.textContent = allyCss;
  document.body.appendChild(layer);
  let html;
  try {
    html = document.documentElement.getHTML({ shadowRoots: roots });
  } finally {
    temp.forEach((s) => s.remove());
    layer.remove();
  }
  return "<!doctype html>\n<html " + [...document.documentElement.attributes].map((a) => `${a.name}="${a.value.replace(/"/g, "&quot;")}"`).join(" ") + ">" + html + "</html>";
}
"""

def save(page, name, label):
    css = ally.evaluate("async (id) => Object.values(await docStyles(id)).join('\\n')", ally.tab_id(page))
    html = page.evaluate(SNAPSHOT, css)
    html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.S | re.I)
    html = html.replace("<head>", f'<head><base href="{URL}/"><!-- Ally demo capture: {label}, '
                        f'{time.strftime("%Y-%m-%d %H:%M")}. Static copy for a presentation; site content, not for publishing. -->', 1)
    (OUT / f"{name}.html").write_text(html, encoding="utf-8")
    page.evaluate("() => scrollTo(0, 0)"); page.wait_for_timeout(300)
    page.screenshot(path=str(OUT / f"{name}.png"))
    page.screenshot(path=str(OUT / f"{name}-full.png"), full_page=True)
    print(f"  saved {name}: {len(html)//1024} KB html", flush=True)

def reset(page):
    ally.action("resetPage"); page.wait_for_timeout(600)

# Sites that turn away automated browsers (allrecipes) aren't worked around: capture those by hand instead.
with FixtureServer() as s, AllyBrowser(s) as ally:
    page = ally.open(URL)
    page.wait_for_timeout(3000)
    print("opened:", page.title(), flush=True)
    if "error" in page.title().lower():
        sys.exit("the site sent an error page (rate limited?)")
    save(page, "0-original-as-opened", "original page, as first opened")
    # Dismiss a consent box the way a reader would, so versions 1-4 show the page itself.
    accepted = False
    for frame, target in CONSENT:
        try:
            root = page.frame_locator(frame) if frame else page
            loc = root.locator(target) if target.startswith("#") else root.get_by_role("button", name=target, exact=True)
            loc.first.click(timeout=2500)
            page.wait_for_timeout(1500)
            print("consent box: accepted with", target, flush=True)
            accepted = True
            break
        except Exception:
            continue
    if not accepted:
        print("consent box: none found", flush=True)
    print("title:", page.title(), "| type:", ally.action("pageInfo").get("type"), flush=True)
    # Scroll through once so lazy images load, then back to the top.
    h = page.evaluate("() => document.documentElement.scrollHeight")
    for y in range(0, h, 600):
        page.evaluate(f"() => scrollTo(0, {y})"); page.wait_for_timeout(120)
    page.evaluate("() => scrollTo(0, 0)"); page.wait_for_timeout(1500)

    save(page, "1-original", "original page")
    run("enlarge_text", goal="bigger"); page.wait_for_timeout(800)
    save(page, "2-bigger-text", "Ally: bigger text")
    reset(page)
    run("enlarge_buttons"); page.wait_for_timeout(800)
    save(page, "3-bigger-buttons", "Ally: bigger buttons")
    reset(page)
    run("simplified_mode"); page.wait_for_timeout(1500)
    save(page, "4-simple-mode", "Ally: simple mode")
    print("worker errors:", ally.worker_errors()[-3:], flush=True)
    os._exit(0)  # a headed browser can hang on close; everything is written by now
