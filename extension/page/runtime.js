// Ally page runtime: injected as a file into each page's isolated world (see docs/page_steps.md).
// It survives until the document goes away; navigation starts fresh. Step files add AllyPage.modules.<step>.

(() => {
  const VERSION = 1;
  if (globalThis.AllyPage && globalThis.AllyPage.version === VERSION) return;

  // Overlays Ally creates. Never includes Ganesh's #ally-risk-banner: reset must not remove it.
  const HOSTS = ["ally-reader-host", "ally-menu-host", "ally-menu-strip", "ally-simple-chip", "ally-simplifying"];
  const ALLY_IDS = new Set([...HOSTS, "ally-risk-banner"]);
  const CLICKABLE = "a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button], [role=link], [role=menuitem], [role=tab], [onclick]";
  const MAX_TARGETS = 300;
  const offs = {};

  function isAlly(el) {
    for (let n = el; n; n = n.parentNode || n.host) {
      if (n.nodeType === 1 && n.id && ALLY_IDS.has(n.id)) return true;
    }
    return false;
  }

  function visible(el) {
    if (!el || !el.isConnected) return false;
    if (typeof el.checkVisibility === "function") {
      if (!el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return false;
    } else {
      const cs = getComputedStyle(el);
      if (cs.display === "none" || cs.visibility === "hidden" || cs.opacity === "0") return false;
    }
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }

  function allyHidden(el) {
    return !!(el.closest && el.closest("[data-ally-clutter]"));
  }

  // A short, stable-enough path used to compare elements between a baseline and a later measure.
  function key(el) {
    const parts = [];
    for (let n = el; n && n.nodeType === 1 && parts.length < 6; n = n.parentElement) {
      if (n.id && !/\d{3,}/.test(n.id)) {
        parts.unshift(`#${CSS.escape(n.id)}`);
        break;
      }
      let i = 1;
      for (let s = n.previousElementSibling; s; s = s.previousElementSibling) if (s.tagName === n.tagName) i++;
      parts.unshift(`${n.tagName.toLowerCase()}:nth-of-type(${i})`);
    }
    return parts.join(">");
  }

  // ------------------------------------------------------------ marks
  function mark(el, step, kind = "1") {
    const attr = `data-ally-${step}`;
    const cur = (el.getAttribute(attr) || "").split(/\s+/).filter(Boolean);
    if (!cur.includes(kind)) cur.push(kind);
    el.setAttribute(attr, cur.join(" "));
  }

  function marked(step, kind) {
    const sel = kind ? `[data-ally-${step}~="${kind}"]` : `[data-ally-${step}]`;
    return Array.from(document.querySelectorAll(sel));
  }

  function unmark(step, kind) {
    const attr = `data-ally-${step}`;
    for (const el of marked(step, kind)) {
      if (!kind) {
        el.removeAttribute(attr);
        continue;
      }
      const rest = (el.getAttribute(attr) || "").split(/\s+/).filter((k) => k && k !== kind);
      if (rest.length) el.setAttribute(attr, rest.join(" "));
      else el.removeAttribute(attr);
    }
  }

  // ------------------------------------------------------------ pausing Ally's CSS layers
  function setPaused(layers, on) {
    const html = document.documentElement;
    const cur = new Set((html.getAttribute("data-ally-pause") || "").split(/\s+/).filter(Boolean));
    for (const l of layers) on ? cur.add(l) : cur.delete(l);
    if (cur.size) html.setAttribute("data-ally-pause", [...cur].join(" "));
    else html.removeAttribute("data-ally-pause");
  }

  // Runs fn with the layers paused. Synchronous: no paint happens in between, so nothing flickers.
  function withPaused(layers, fn) {
    if (!layers || !layers.length) return fn();
    setPaused(layers, true);
    try {
      return fn();
    } finally {
      setPaused(layers, false);
    }
  }

  // ------------------------------------------------------------ measuring
  function textSamples() {
    const sizes = [];
    let small = 0;
    let total = 0;
    let cutOff = 0;
    const vw = document.documentElement.clientWidth;
    const walker = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_TEXT, {
      acceptNode: (t) => (t.nodeValue.trim().length >= 3 ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT),
    });
    let seen = 0;
    const parents = new Set();
    while (walker.nextNode() && seen < 4000) {
      const el = walker.currentNode.parentElement;
      if (!el || parents.has(el) || isAlly(el) || allyHidden(el)) continue;
      parents.add(el);
      seen++;
      if (!visible(el)) continue;
      const len = walker.currentNode.nodeValue.trim().length;
      const px = parseFloat(getComputedStyle(el).fontSize) || 0;
      sizes.push([px, len]);
      total += len;
      const r = el.getBoundingClientRect();
      if (r.right > vw + 2 && r.left < vw) cutOff++;
    }
    sizes.sort((a, b) => a[0] - b[0]);
    let acc = 0;
    let median = 0;
    for (const [px, len] of sizes) {
      acc += len;
      if (acc >= total / 2) {
        median = px;
        break;
      }
    }
    return { median, sizes, total, cutOff };
  }

  function clippedBoxes() {
    const out = [];
    const all = document.body ? document.body.getElementsByTagName("*") : [];
    for (let i = 0; i < all.length && out.length < 200; i++) {
      const el = all[i];
      if (isAlly(el) || allyHidden(el)) continue;
      const cs = getComputedStyle(el);
      if (!/(hidden|clip)/.test(cs.overflow + cs.overflowX + cs.overflowY)) continue;
      if (cs.textOverflow === "ellipsis" || cs.webkitLineClamp !== "none" || cs.clipPath !== "none") continue;
      if (el.clientWidth <= 2 || el.clientHeight <= 2) continue; // visually-hidden text, collapsed menus
      if (el.getAttribute("aria-hidden") === "true" || !visible(el)) continue;
      if (parseFloat(cs.maxHeight) === 0) continue;
      const overY = el.scrollHeight - el.clientHeight > 4;
      const overX = el.scrollWidth - el.clientWidth > 4;
      if ((overY || overX) && (el.innerText || "").trim().length > 20) out.push(key(el));
    }
    return out;
  }

  function stickyCover() {
    const vw = innerWidth;
    const vh = innerHeight;
    const rows = new Uint8Array(Math.max(1, Math.ceil(vh)));
    const all = document.body ? document.body.getElementsByTagName("*") : [];
    for (let i = 0; i < all.length; i++) {
      const el = all[i];
      const pos = getComputedStyle(el).position;
      if (pos !== "fixed" && pos !== "sticky") continue;
      if (isAlly(el) || allyHidden(el) || !visible(el)) continue;
      const r = el.getBoundingClientRect();
      if (r.bottom <= 0 || r.top >= vh || r.width < vw * 0.3) continue;
      if (pos === "sticky" && r.top > 4 && r.bottom < vh - 4) continue; // sticky but not stuck to an edge
      for (let y = Math.max(0, Math.floor(r.top)); y < Math.min(vh, Math.ceil(r.bottom)); y++) rows[y] = 1;
    }
    let n = 0;
    for (const v of rows) n += v;
    return n / rows.length;
  }

  function targetsInView() {
    const out = [];
    for (const el of document.querySelectorAll(CLICKABLE)) {
      if (out.length >= MAX_TARGETS) break;
      if (isAlly(el) || allyHidden(el) || !visible(el)) continue;
      const r = el.getBoundingClientRect();
      if (r.bottom < 0 || r.top > innerHeight || r.right < 0 || r.left > innerWidth) continue;
      out.push([el, r]);
    }
    return out;
  }

  function coveredTargets(targets) {
    const out = [];
    for (const [el, r] of targets) {
      const x = Math.min(innerWidth - 1, Math.max(0, r.left + r.width / 2));
      const y = Math.min(innerHeight - 1, Math.max(0, r.top + r.height / 2));
      const stack = document.elementsFromPoint(x, y).filter((n) => !isAlly(n));
      const top = stack[0];
      if (!top) continue;
      if (top === el || el.contains(top) || top.contains(el)) continue;
      if (top.tagName === "LABEL" && (top.control === el || top.contains(el))) continue;
      out.push(key(el));
    }
    return out;
  }

  function measure() {
    const html = document.documentElement;
    const t = textSamples();
    const targets = targetsInView();
    const small = [];
    for (const [el, r] of targets) {
      if (Math.min(r.width, r.height) < 44) small.push({ key: key(el), w: Math.round(r.width), h: Math.round(r.height) });
    }
    return {
      href: location.href,
      vw: html.clientWidth,
      vh: innerHeight,
      textPx: t.median,
      textSizes: summarizeSizes(t),
      overflowX: html.scrollWidth > html.clientWidth + 1,
      overflowPx: Math.max(0, html.scrollWidth - html.clientWidth),
      cutOffText: t.cutOff,
      clipped: clippedBoxes(),
      stickyCover: Math.round(stickyCover() * 1000) / 1000,
      covered: coveredTargets(targets),
      smallTargets: small.slice(0, 100),
      targets: targets.length,
    };
  }

  // The share of text below each size, so Python can test a goal at any zoom: [[px, shareAtOrBelow], ...].
  function summarizeSizes(t) {
    const out = [];
    let acc = 0;
    let last = -1;
    for (const [px, len] of t.sizes) {
      acc += len;
      if (px !== last) out.push([px, 0]);
      out[out.length - 1][1] = Math.round((acc / Math.max(1, t.total)) * 1000) / 1000;
      last = px;
    }
    return out;
  }

  // ------------------------------------------------------------ baseline and verify
  const ALL_LAYERS = ["fit", "buttons", "clutter", "motion", "menus", "simple"];
  let baseline = null;

  function takeBaseline(meta = {}) {
    if (baseline && baseline.href === location.href && !meta.force) return baseline;
    const metrics = withPaused(ALL_LAYERS, measure);
    baseline = { href: location.href, zoom: meta.zoom || 1, metrics, at: Date.now() };
    return baseline;
  }

  function getBaseline() {
    if (baseline && baseline.href !== location.href) baseline = null; // single-page navigation
    return baseline;
  }

  function verify(meta = {}) {
    const base = getBaseline() || takeBaseline(meta);
    const now = measure();
    const b = base.metrics;
    const budget = typeof meta.stickyBudget === "number" ? meta.stickyBudget : 0.25;
    const newClipped = now.clipped.filter((k) => !b.clipped.includes(k));
    const newCovered = now.covered.filter((k) => !b.covered.includes(k));
    const checks = {
      overflow: {
        ok: !(now.overflowX && now.overflowPx > b.overflowPx + 8) && now.cutOffText <= b.cutOffText,
        overflowPx: now.overflowPx,
        before: b.overflowPx,
        cutOffText: now.cutOffText,
      },
      clipped: { ok: newClipped.length === 0, new: newClipped.slice(0, 10) },
      sticky: { ok: now.stickyCover <= Math.max(budget, b.stickyCover + 0.02), cover: now.stickyCover, before: b.stickyCover },
      covered: { ok: newCovered.length <= 1, new: newCovered.slice(0, 10) },
    };
    return { ok: Object.values(checks).every((c) => c.ok), checks, metrics: now };
  }

  // ------------------------------------------------------------ teardown
  function registerOff(step, fn) {
    offs[step] = fn;
  }

  function teardown() {
    for (const [step, fn] of Object.entries(offs)) {
      try {
        fn();
      } catch (e) {
        console.warn("Ally teardown", step, e);
      }
    }
    for (const el of document.querySelectorAll("*")) {
      for (const a of Array.from(el.attributes)) if (a.name.startsWith("data-ally-")) el.removeAttribute(a.name);
    }
    for (const id of HOSTS) {
      const host = document.getElementById(id);
      if (host) host.remove();
    }
    if (typeof globalThis.__allyReaderClose === "function") globalThis.__allyReaderClose();
    baseline = null;
    return true;
  }

  // ------------------------------------------------------------ module calls from the service worker
  const modules = {};

  async function run(path, args) {
    if (path === "baseline") return takeBaseline(args || {});
    if (path === "verify") return verify(args || {});
    if (path === "measure") return measure();
    if (path === "teardown") return teardown();
    const [name, fn] = String(path).split(".");
    const mod = modules[name];
    if (!mod || typeof mod[fn] !== "function") throw new Error(`Ally page step "${path}" isn't loaded`);
    return await mod[fn](args || {});
  }

  globalThis.AllyPage = {
    version: VERSION, HOSTS, CLICKABLE, isAlly, visible, allyHidden, key, mark, marked, unmark, withPaused,
    measure, takeBaseline, getBaseline, verify, registerOff, teardown, modules, run,
  };
})();
