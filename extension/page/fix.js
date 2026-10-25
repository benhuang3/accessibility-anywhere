// Ally fix step, page side: describes the page for the AI checker/fixer and checks the fixer's CSS rules
// before the service worker inserts them (docs/page_steps.md, "AI check and fix").

(() => {
  const P = globalThis.AllyPage;
  if (!P || P.modules.fix) return;

  const MAX_ITEMS = 50;
  const PROTECTED = "dialog, [role=dialog], [role=alertdialog], [aria-modal=true], nav, [role=navigation], main, " +
    "[role=main], #ally-risk-banner, [role=alert], [aria-live]";

  function describe(el, why) {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    const cls = (typeof el.className === "string" ? el.className : "").trim().split(/\s+/).filter(Boolean).slice(0, 2);
    return {
      key: P.key(el), why, tag: el.tagName.toLowerCase(), id: el.id || "", cls, role: el.getAttribute("role") || "",
      box: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
      position: cs.position, display: cs.display, fontPx: parseFloat(cs.fontSize) || 0,
      overflow: cs.overflow, text: (el.innerText || el.getAttribute("aria-label") || "").trim().replace(/\s+/g, " ").slice(0, 70),
    };
  }

  // The parts of the page a fixer would act on: landmarks, things stuck to the edges, problems verify found,
  // and the biggest blocks on screen. Selectors are AllyPage.key paths the fixer can use.
  function outline() {
    const items = [];
    const seen = new Set();
    const add = (el, why) => {
      if (!el || seen.has(el) || P.isAlly(el) || P.allyHidden(el) || items.length >= MAX_ITEMS) return;
      if (!P.visible(el)) return;
      seen.add(el);
      items.push(describe(el, why));
    };
    const base = P.getBaseline();
    const v = P.verify({});
    for (const k of [...(v.checks.clipped.new || []), ...(v.checks.covered.new || [])]) {
      try {
        add(document.querySelector(k), "problem found by the layout check");
      } catch {}
    }
    for (const el of document.querySelectorAll("header, nav, main, aside, footer, [role=banner], [role=navigation], [role=main]")) {
      add(el, "landmark");
    }
    for (const el of document.body ? document.body.getElementsByTagName("*") : []) {
      const pos = getComputedStyle(el).position;
      if ((pos === "fixed" || pos === "sticky") && el.getBoundingClientRect().width > innerWidth * 0.3) add(el, `${pos} bar`);
    }
    const blocks = Array.from(document.querySelectorAll("body > *, main > *, article, section, [class*=content]"))
      .filter((el) => P.visible(el))
      .map((el) => [el, el.getBoundingClientRect()])
      .filter(([, r]) => r.bottom > 0 && r.top < innerHeight)
      .sort((a, b) => b[1].width * b[1].height - a[1].width * a[1].height)
      .slice(0, 15);
    for (const [el] of blocks) add(el, "large block on screen");
    return {
      url: location.href, viewport: [innerWidth, innerHeight], scrollWidth: document.documentElement.scrollWidth,
      verify: { ok: v.ok, checks: v.checks }, baselineTaken: !!base, items,
    };
  }

  // Throws if a rule could hide or break something it must not. Returns how many elements each rule matches.
  function check(rules) {
    const counts = [];
    for (const r of rules) {
      let els;
      try {
        els = Array.from(document.querySelectorAll(r.selector));
      } catch {
        throw new Error(`not a valid selector: ${r.selector}`);
      }
      if (els.some((el) => P.isAlly(el))) throw new Error(`can't change Ally's own parts: ${r.selector}`);
      if (r.property === "display" && r.value === "none") {
        if (els.length > 20) throw new Error(`hiding ${els.length} elements is too many: ${r.selector}`);
        for (const el of els) {
          if (el.matches(PROTECTED) || el.querySelector(PROTECTED)) throw new Error(`won't hide an important part: ${r.selector}`);
          const b = el.getBoundingClientRect();
          if (b.width * b.height > innerWidth * innerHeight * 0.4) throw new Error(`won't hide a big part of the page: ${r.selector}`);
        }
      }
      counts.push(els.length);
    }
    return counts;
  }

  P.modules.fix = { outline, check };
})();
