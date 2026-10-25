// Ally text step, page side: makes the page's own text bigger (no zoom). Each element that holds text is
// marked with its original font size (data-ally-text="14"); the service worker turns the marks into one CSS
// layer, "ally_text", that sets a bigger size per original size, so headings stay bigger than body text and
// nested text doesn't grow twice. See docs/page_steps.md.

(() => {
  const P = globalThis.AllyPage;
  if (!P || P.modules.text) return;

  const SKIP = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "SVG", "MATH", "CANVAS", "IFRAME"]);
  const CONTROLS = "input:not([type=hidden]), select, textarea, button, [role=button]";
  const MAX_ELEMENTS = 6000;
  let scale = 1;

  function sizeOf(el) {
    return parseFloat(getComputedStyle(el).fontSize) || 0;
  }

  // The elements whose text is on screen: parents of text nodes, plus form controls (their text isn't a node).
  function textElements() {
    const out = new Set();
    const walker = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_TEXT, {
      acceptNode: (t) => (t.nodeValue.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT),
    });
    while (walker.nextNode() && out.size < MAX_ELEMENTS) {
      const el = walker.currentNode.parentElement;
      if (el && !SKIP.has(el.tagName)) out.add(el);
    }
    for (const el of document.querySelectorAll(CONTROLS)) out.add(el);
    // Text Ally itself hid (clutter) counts too: it comes back when clutter is turned off, and must then be
    // the same size whichever step ran first.
    return [...out].filter((el) => !P.isAlly(el) && (P.visible(el) || P.allyHidden(el)));
  }

  // What each original size becomes. Small text gets at least close to the goal; huge headings are capped.
  function target(px, s, goalPx, maxPx) {
    let t = Math.round(px * s);
    if (s > 1) t = Math.max(t, Math.round(Math.min(goalPx * 0.9, px * 2)));
    return Math.max(Math.round(px), Math.min(t, Math.max(Math.round(px), maxPx)));
  }

  // Marks every text element with its original size (measured with Ally's text layer paused, so applying
  // again at a new scale starts from the page's own sizes) and returns {scale, sizes: {px: newPx}}.
  function apply({ scale: s = 1.25, goalPx = 20, maxPx = 72 } = {}) {
    const sizes = {};
    let marked = 0;
    P.withPaused(["text"], () => {
      P.unmark("text");
      const els = textElements();
      const measured = els.map((el) => [el, Math.round(sizeOf(el))]); // all reads before any write
      for (const [el, px] of measured) {
        if (px <= 0) continue;
        el.setAttribute("data-ally-text", String(px));
        sizes[px] = target(px, s, goalPx, maxPx);
        marked++;
      }
    });
    scale = s;
    document.documentElement.setAttribute("data-ally-text-scale", String(s));
    return { scale: s, sizes, marked };
  }

  // Boxes with a fixed height that the bigger text now overflows (they weren't clipped before Ally: the
  // baseline excludes things clipped on purpose, like collapsed menus) may grow to fit it.
  function grow() {
    for (const el of document.querySelectorAll("[data-ally-text-grow]")) el.removeAttribute("data-ally-text-grow");
    let n = 0;
    for (const k of P.verify({}).checks.clipped.new || []) {
      let el = null;
      try {
        el = document.querySelector(k);
      } catch {}
      if (el && !P.isAlly(el)) {
        el.setAttribute("data-ally-text-grow", "");
        n++;
      }
    }
    return n;
  }

  function clear() {
    for (const el of document.querySelectorAll("[data-ally-text-grow]")) el.removeAttribute("data-ally-text-grow");
    P.unmark("text");
    document.documentElement.removeAttribute("data-ally-text-scale");
    scale = 1;
    return true;
  }

  function state() {
    return { scale: Number(document.documentElement.getAttribute("data-ally-text-scale")) || scale || 1 };
  }

  P.registerOff("text", clear);
  P.modules.text = { apply, grow, clear, state };
})();
