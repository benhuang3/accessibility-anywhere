// Ally "show me where to click" and "highlight the menu": finds controls, page areas and sections (on screen or
// not) and draws a yellow box with an arrow, or a spotlight that dims the rest, in a shadow-DOM overlay that
// lets clicks through. The page itself is only read. See docs/tabs_and_pointing.md.

(() => {
  const P = globalThis.AllyPage;
  if (!P || P.modules.highlight) return;

  const HOST_ID = "ally-highlight-host";
  if (!P.HOSTS.includes(HOST_ID)) P.HOSTS.push(HOST_ID);
  const MAX_SECTION_SCREENS = 3;
  const AREAS = [
    ["main, [role=main]", "main content text"],
    ["article", "article main text"],
    ["nav, [role=navigation]", "menu navigation"],
    ["header, [role=banner]", "top header"],
    ["footer, [role=contentinfo]", "bottom footer"],
    ["aside, [role=complementary]", "side panel"],
    ["[role=search], input[type=search]", "search box"],
    ["form", "form"],
  ];

  let ids = new Map(); // "h1" -> WeakRef(element), rebuilt by every find()
  let generation = 0;
  let ui = null; // {host, root, els: [element], mode, note, raf, onScroll, onKey, onDown}

  const clean = (t) => String(t || "").replace(/\s+/g, " ").trim().slice(0, 80);

  function refText(el, attr) {
    const ref = el.getAttribute(attr);
    if (!ref) return "";
    return ref.split(/\s+/).map((id) => document.getElementById(id)).filter(Boolean).map((n) => n.textContent).join(" ");
  }

  function controlLabel(el) {
    const tries = [el.getAttribute("aria-label"), refText(el, "aria-labelledby")];
    if (el.labels && el.labels.length) tries.push(Array.from(el.labels).map((l) => l.innerText).join(" "));
    tries.push(el.innerText, el.value && el.type !== "text" ? el.value : "", el.getAttribute("title"),
      el.getAttribute("placeholder"), (el.querySelector && el.querySelector("img[alt]") || {}).alt);
    for (const t of tries) if (clean(t)) return clean(t);
    return "";
  }

  // The page's own name for the area plus the words Ally adds for matching, without repeats:
  // "Primary navigation" + "menu navigation" -> "Primary navigation menu".
  function areaLabel(el, base) {
    const own = clean(el.getAttribute("aria-label") || refText(el, "aria-labelledby"));
    if (!own) return base;
    const have = new Set(own.toLowerCase().split(/\s+/));
    return [own, ...base.split(" ").filter((w) => !have.has(w))].join(" ");
  }

  // A heading's section: its <section>/<article> when that isn't huge; otherwise (Wikipedia and most sites don't
  // wrap sections) the heading itself, drawn as the heading plus what follows it up to the next heading of the
  // same or a higher level (see sectionParts).
  function sectionOf(h) {
    const box = h.closest("section, article");
    if (box && box !== document.body && box.getBoundingClientRect().height <= innerHeight * MAX_SECTION_SCREENS &&
        box.querySelectorAll("h1, h2, h3, h4").length <= 3) return { el: box, range: false };
    return { el: h, range: true };
  }

  const HEADINGS = "h1, h2, h3, h4, h5, h6";

  function levelOf(n) {
    const h = n.matches(HEADINGS) ? n : n.querySelector(`:scope > :is(${HEADINGS})`);
    return h ? Number(h.tagName[1]) : 0;
  }

  function sectionParts(h) {
    // Some sites wrap each heading in its own box (Wikipedia: <div class="mw-heading">).
    const start = h.parentElement && h.parentElement.children.length <= 3 && levelOf(h.parentElement) ? h.parentElement : h;
    const level = Number(h.tagName[1]);
    const parts = [start];
    let height = start.getBoundingClientRect().height;
    for (let n = start.nextElementSibling; n && height < innerHeight * MAX_SECTION_SCREENS; n = n.nextElementSibling) {
      const l = levelOf(n);
      if (l && l <= level) break;
      if (!P.visible(n)) continue;
      parts.push(n);
      height += n.getBoundingClientRect().height;
    }
    return parts;
  }

  function rectOf(item) {
    if (!item.range) return item.el.getBoundingClientRect();
    let x1 = Infinity;
    let y1 = Infinity;
    let x2 = -Infinity;
    let y2 = -Infinity;
    for (const n of sectionParts(item.el)) {
      const r = n.getBoundingClientRect();
      if (!r.width && !r.height) continue;
      x1 = Math.min(x1, r.left);
      y1 = Math.min(y1, r.top);
      x2 = Math.max(x2, r.right);
      y2 = Math.max(y2, r.bottom);
    }
    return x1 === Infinity ? item.el.getBoundingClientRect() : new DOMRect(x1, y1, x2 - x1, y2 - y1);
  }

  function inView(r) {
    return r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
  }

  function find({ max = 400 } = {}) {
    const found = [];
    const seen = new Set();
    const add = (el, label, kind, range = false) => {
      if (!el || seen.has(el) || P.isAlly(el) || !P.visible(el) || !label) return;
      seen.add(el);
      found.push({ el, label, kind, range });
    };
    for (const [sel, base] of AREAS) for (const el of document.querySelectorAll(sel)) add(el, areaLabel(el, base), "area");
    for (const h of document.querySelectorAll("h1, h2, h3, h4")) {
      const s = sectionOf(h);
      add(s.el, clean(h.innerText), "section", s.range);
    }
    for (const el of document.querySelectorAll(P.CLICKABLE)) {
      if (found.length >= max) break;
      add(el, controlLabel(el), "control");
    }
    ids = new Map();
    generation++;
    const items = found.slice(0, max).map((f, i) => {
      const id = `h${i + 1}`;
      ids.set(id, { ref: new WeakRef(f.el), range: f.range });
      const r = rectOf(f);
      return { id, label: f.label, kind: f.kind, x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width),
        h: Math.round(r.height), inView: inView(r) };
    });
    return { gen: generation, view: { w: innerWidth, h: innerHeight }, items };
  }

  // ------------------------------------------------------------ drawing
  const CSS = `
    .box { position: fixed; box-sizing: border-box; border: 6px solid #ffeb3b; outline: 3px solid #000;
           border-radius: 8px; pointer-events: none; }
    .shade { position: fixed; background: rgba(0, 0, 0, 0.6); pointer-events: none; }
    .arrow { position: fixed; font: 700 44px/1 Arial, sans-serif; color: #ffeb3b; pointer-events: none;
             text-shadow: 0 0 3px #000, 0 0 3px #000, 0 0 6px #000; }
    .note { position: fixed; background: #ffeb3b; color: #000; border: 3px solid #000; border-radius: 10px;
            font: 700 22px/1.25 Arial, sans-serif; padding: 8px 14px; max-width: 360px; pointer-events: none; }
    @media (prefers-reduced-motion: no-preference) {
      .box.pulse { animation: pulse 1.2s ease-in-out infinite; }
      @keyframes pulse { 50% { border-color: #ff9800; } }
    }`;

  function el(cls, text) {
    const d = document.createElement("div");
    d.className = cls;
    if (text) d.textContent = text;
    return d;
  }

  function place(node, x, y, w, h) {
    node.style.left = `${x}px`;
    node.style.top = `${y}px`;
    if (w !== undefined) node.style.width = `${Math.max(0, w)}px`;
    if (h !== undefined) node.style.height = `${Math.max(0, h)}px`;
  }

  function rects() {
    return ui.els.map((it) => (it.el.isConnected ? rectOf(it) : null)).filter(Boolean);
  }

  function draw() {
    if (!ui) return;
    ui.raf = 0;
    const layer = ui.layer;
    layer.textContent = "";
    const pad = 6;
    const boxes = rects().map((r) => ({ x: r.left - pad, y: r.top - pad, w: r.width + pad * 2, h: r.height + pad * 2 }));
    if (!boxes.length) return clear();
    if (ui.mode === "spotlight") {
      const b = boxes[0];
      const vw = innerWidth;
      const vh = innerHeight;
      for (const [x, y, w, h] of [[0, 0, vw, b.y], [0, b.y + b.h, vw, vh - b.y - b.h], [0, b.y, b.x, b.h],
        [b.x + b.w, b.y, vw - b.x - b.w, b.h]]) {
        const s = el("shade");
        place(s, x, y, w, h);
        layer.append(s);
      }
    }
    for (const b of boxes) {
      const box = el(ui.mode === "point" ? "box pulse" : "box");
      place(box, b.x, b.y, b.w, b.h);
      layer.append(box);
      if (ui.mode !== "point") continue;
      const above = b.y > 60;
      const arrow = el("arrow", above ? "▼" : "▲");
      place(arrow, Math.max(0, b.x + b.w / 2 - 16), above ? b.y - 48 : b.y + b.h + 4);
      layer.append(arrow);
    }
    if (ui.note) {
      const b = boxes[0];
      const note = el("note", ui.note);
      const x = Math.min(Math.max(8, b.x), innerWidth - 380);
      const y = b.y > 120 ? b.y - 110 : Math.min(innerHeight - 70, b.y + b.h + 56);
      place(note, Math.max(8, x), Math.max(8, y));
      layer.append(note);
    }
  }

  function redraw() {
    if (ui && !ui.raf) ui.raf = requestAnimationFrame(draw);
  }

  function show({ ids: want = [], gen = 0, mode = "point", note = "" } = {}) {
    if (gen && gen !== generation) throw new Error("The page changed. Please ask again.");
    const els = want.map((id) => ids.get(String(id)))
      .map((v) => v && { el: v.ref.deref(), range: v.range })
      .filter((it) => it && it.el && it.el.isConnected);
    if (!els.length) throw new Error("That isn't on the page any more.");
    clear();
    const r = rectOf(els[0]);
    if (r.top < 0 || r.left < 0 || r.bottom > innerHeight || r.right > innerWidth) {
      const top = els[0].range || r.height > innerHeight * 0.8;
      els[0].el.scrollIntoView({ block: top ? "start" : "center", inline: "nearest" });
      if (top) scrollBy(0, -Math.round(innerHeight * 0.12)); // room above it, so its box and title show
    }
    const host = document.createElement("div");
    host.id = HOST_ID;
    const set = (k, v) => host.style.setProperty(k, v, "important");
    set("all", "initial");
    set("position", "fixed");
    set("inset", "0");
    set("z-index", "2147483644");
    set("pointer-events", "none");
    set("display", "block");
    const root = host.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent = CSS;
    const layer = document.createElement("div");
    root.append(style, layer);
    document.documentElement.appendChild(host);
    ui = { host, layer, els, mode: mode === "spotlight" ? "spotlight" : "point", note: clean(note).slice(0, 120), raf: 0 };
    ui.onScroll = () => redraw();
    ui.onKey = (e) => {
      if (e.key === "Escape") clear();
    };
    ui.onDown = (e) => {
      if (e.isTrusted && ui && ui.mode === "point") clear(); // the user clicked: the pointer did its job
    };
    addEventListener("scroll", ui.onScroll, { capture: true, passive: true });
    addEventListener("resize", ui.onScroll);
    addEventListener("keydown", ui.onKey, true);
    addEventListener("pointerdown", ui.onDown, true);
    draw();
    console.debug("Ally: highlight", ui.mode, els.length, note);
    return { shown: els.length, boxes: rects().map((b) => ({ x: Math.round(b.left), y: Math.round(b.top),
      w: Math.round(b.width), h: Math.round(b.height) })), view: { w: innerWidth, h: innerHeight } };
  }

  function clear() {
    if (!ui) {
      const stale = document.getElementById(HOST_ID);
      if (stale) stale.remove();
      return { said: stale ? "cleared the highlight" : "" };
    }
    const u = ui;
    ui = null;
    if (u.raf) cancelAnimationFrame(u.raf);
    removeEventListener("scroll", u.onScroll, { capture: true });
    removeEventListener("resize", u.onScroll);
    removeEventListener("keydown", u.onKey, true);
    removeEventListener("pointerdown", u.onDown, true);
    u.host.remove();
    return { said: "cleared the highlight" };
  }

  function state() {
    return { shown: !!ui, mode: ui ? ui.mode : "", count: ui ? ui.els.length : 0 };
  }

  addEventListener("pageshow", (e) => {
    if (e.persisted) clear();
  });

  P.registerOff("highlight", clear);
  P.modules.highlight = { find, show, clear, state };
})();
