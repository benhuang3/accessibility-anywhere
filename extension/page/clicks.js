// Click by voice / by number: numbers the clickable things on screen with badges in a shadow-DOM overlay and
// clicks one by number or by id. The page DOM is only read: no marks, no CSS layers. See docs/click_by_voice.md.

(() => {
  const P = globalThis.AllyPage;
  if (!P || P.modules.clicks) return;

  const HOST_ID = "ally-click-host";
  const READER_ID = "ally-reader-host";
  const GONE = "That button isn't on the page any more.";
  const STALE = "The page changed. Please ask again.";
  const SELECTOR = `${P.CLICKABLE}, [role=checkbox], [role=radio], [role=switch], [role=option], summary`;
  const FOCUS_TYPES = new Set(["", "text", "email", "search", "tel", "url", "password", "number", "date",
    "datetime-local", "month", "week", "time"]);
  const BUTTON_TYPES = new Set(["button", "submit", "reset", "image"]);

  // A runtime injected before this host existed doesn't know it; reset still has to remove it.
  if (!P.HOSTS.includes(HOST_ID)) P.HOSTS.push(HOST_ID);

  const CSS = `
    .b { position: fixed; left: 0; top: 0; box-sizing: border-box; height: 28px; min-width: 28px; padding: 0 5px;
         background: #ffeb3b; color: #000; border: 2px solid #000; border-radius: 4px;
         box-shadow: 0 0 0 1px #fff; font: 700 18px/24px Arial, sans-serif; text-align: center;
         white-space: nowrap; pointer-events: none; }
    .b[hidden] { display: none; }
    .flash { position: fixed; box-sizing: border-box; border: 4px solid #ffeb3b; border-radius: 4px;
             box-shadow: 0 0 0 2px #000, inset 0 0 0 2px #000; pointer-events: none; }`;

  let ids = new Map(); // "c1" -> WeakRef(element), rebuilt by every list()
  let numbers = new Map(); // 7 -> {ref: WeakRef(element), label, role, href, badge}, rebuilt by every show()
  let generation = 0; // bumped by every show() and list()
  let ui = null; // {host, root, badges, frame, timer} while badges or a flash are on screen
  let flashTimer = 0;

  // ------------------------------------------------------------ labels
  function clean(text) {
    return String(text || "").replace(/\s+/g, " ").trim().slice(0, 80);
  }

  function refText(el, attr) {
    const root = el.getRootNode();
    const find = (id) => (typeof root.getElementById === "function" ? root.getElementById(id) : document.getElementById(id));
    return (el.getAttribute(attr) || "").split(/\s+/).filter(Boolean)
      .map(find).filter(Boolean).map((n) => n.innerText || n.textContent).join(" ");
  }

  function isField(el) {
    return /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName);
  }

  function labelOf(el) {
    const tries = [() => el.getAttribute("aria-label"), () => refText(el, "aria-labelledby")];
    if (isField(el)) {
      tries.push(() => Array.from(el.labels || []).map((l) => l.innerText || l.textContent).join(" "));
      if (el.tagName === "INPUT" && BUTTON_TYPES.has(el.type)) tries.push(() => el.value || (el.type === "image" ? el.alt : ""));
    } else {
      tries.push(() => el.innerText);
      tries.push(() => {
        const img = el.querySelector("img[alt]");
        return img ? img.getAttribute("alt") : "";
      });
    }
    tries.push(() => el.getAttribute("title"), () => el.getAttribute("placeholder"));
    for (const t of tries) {
      const text = clean(t());
      if (text) return text;
    }
    return "";
  }

  // Every name the element has (aria-label can hide "Place order" inside a "Primary action" button; icon
  // buttons may only have an SVG title), for Python's careful-click check.
  function allText(el) {
    const parts = [labelOf(el), el.getAttribute("aria-label"), el.innerText, el.getAttribute("title")];
    for (const d of el.querySelectorAll("[aria-label], [title], title")) {
      parts.push(d.tagName.toLowerCase() === "title" ? d.textContent : d.getAttribute("aria-label") || d.getAttribute("title"));
    }
    return clean(Array.from(new Set(parts.map((p) => clean(p)).filter(Boolean))).join(" | ")).slice(0, 300);
  }

  function roleOf(el) {
    const role = (el.getAttribute("role") || "").trim().toLowerCase().split(/\s+/)[0];
    if (role) return role;
    const tag = el.tagName;
    if (tag === "A" || tag === "AREA") return "link";
    if (tag === "BUTTON" || tag === "SUMMARY") return "button";
    if (tag === "SELECT") return "combobox";
    if (tag === "TEXTAREA") return "textbox";
    if (tag === "INPUT") {
      if (BUTTON_TYPES.has(el.type)) return "button";
      if (el.type === "checkbox" || el.type === "radio") return el.type;
      if (el.type === "range") return "slider";
      return FOCUS_TYPES.has(el.type) ? "textbox" : el.type;
    }
    return "button";
  }

  function hrefOf(el) {
    return (el.tagName === "A" || el.tagName === "AREA") && el.hasAttribute("href") ? el.href : "";
  }

  // A link that leaves this page: http(s) and not just a #hash on the same page.
  function navigates(el) {
    const href = hrefOf(el);
    if (!/^https?:/i.test(href)) return false;
    if (/^\s*#/.test(el.getAttribute("href") || "")) return false; // href="#" menus and the like: their own script
    try {
      const to = new URL(href);
      const here = new URL(location.href);
      return to.href.split("#")[0] !== here.href.split("#")[0] || !to.href.includes("#");
    } catch {
      return false;
    }
  }

  // ------------------------------------------------------------ finding targets
  function view() {
    const html = document.documentElement;
    // min(): in quirks mode <html>'s client size is the whole document
    return { w: Math.min(html.clientWidth || innerWidth, innerWidth), h: Math.min(html.clientHeight || innerHeight, innerHeight) };
  }

  function candidates() {
    const out = [];
    for (const el of document.querySelectorAll(SELECTOR)) if (!P.isAlly(el)) out.push([el, document]);
    // Ally's reader view shows the article's links in its shadow root; those count too.
    const reader = document.getElementById(READER_ID);
    const root = reader && reader.shadowRoot;
    if (root) for (const el of root.querySelectorAll(SELECTOR)) out.push([el, root]);
    return out;
  }

  // Whether el is what the user sees at (x, y), as in the picture viewer: parents, click-catching layers and
  // see-through layers above it don't hide it; Ally's own overlays (reader, picture viewer, risk banner) do.
  function hits(el, root, x, y) {
    for (const hit of root.elementsFromPoint(x, y)) {
      if (hit === el || el.contains(hit)) return true;
      if (hit.contains(el)) continue;
      if (hit.tagName === "LABEL" && hit.control === el) return true;
      if (root === document && P.isAlly(hit)) return false;
      if (!seeThrough(hit)) return false;
    }
    return false;
  }

  function seeThrough(node) {
    const cs = getComputedStyle(node);
    if (/url\(/.test(cs.backgroundImage)) return false;
    const c = /rgba?\(([^)]+)\)/.exec(cs.backgroundColor);
    const alpha = c ? (c[1].split(",")[3] === undefined ? 1 : parseFloat(c[1].split(",")[3])) : 0;
    return alpha < 0.5 && parseFloat(cs.opacity) > 0 && !/^(IMG|VIDEO|CANVAS|IFRAME|svg)$/i.test(node.tagName);
  }

  function onScreen(r, v) {
    return r.right > 0 && r.bottom > 0 && r.left < v.w && r.top < v.h;
  }

  function seen(el, root, v) {
    if (!P.visible(el)) return null;
    const r = el.getBoundingClientRect();
    if (!onScreen(r, v)) return null;
    const x1 = Math.max(0, r.left);
    const y1 = Math.max(0, r.top);
    const x2 = Math.min(v.w, r.right);
    const y2 = Math.min(v.h, r.bottom);
    if (!hits(el, root, (x1 + x2) / 2, (y1 + y2) / 2)) return null;
    return r;
  }

  // Top to bottom in 8 px rows, then left to right.
  function readingOrder(items) {
    return items.sort((a, b) => Math.round(a.r.top / 8) - Math.round(b.r.top / 8) || a.r.left - b.r.left);
  }

  // A button inside a link with the same label is one thing to the user: the outer one counts.
  function dropNested(found) {
    const byEl = new Map(found.map((t) => [t.el, t]));
    return found.filter((t) => {
      for (let n = t.el.parentElement; n; n = n.parentElement) {
        const outer = byEl.get(n);
        if (outer && outer.label === t.label) return false;
      }
      return true;
    });
  }

  function targets(only) {
    const v = view();
    const found = [];
    const pool = only ? only : candidates();
    for (const [el, root] of pool) {
      const r = seen(el, root, v);
      if (r) found.push({ el, r, label: labelOf(el), role: roleOf(el), href: hrefOf(el), text: allText(el) });
    }
    return { v, items: readingOrder(dropNested(found)) };
  }

  function box(r) {
    return { x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) };
  }

  function deref(ref) {
    const el = ref && ref.deref();
    return el && el.isConnected ? el : null;
  }

  // ------------------------------------------------------------ overlay
  function styleHost(host) {
    const set = (k, v) => host.style.setProperty(k, v, "important");
    set("all", "initial");
    set("position", "fixed");
    set("inset", "0");
    set("z-index", "2147483645");
    set("pointer-events", "none");
    set("display", "block");
  }

  function ensureHost() {
    if (ui && ui.host.isConnected) return ui;
    const stale = document.getElementById(HOST_ID);
    if (stale) stale.remove();
    const host = document.createElement("div");
    host.id = HOST_ID;
    styleHost(host);
    const root = host.attachShadow({ mode: "open" });
    try {
      const s = new CSSStyleSheet();
      s.replaceSync(CSS);
      root.adoptedStyleSheets = [s];
    } catch {
      const style = document.createElement("style");
      style.textContent = CSS;
      root.append(style);
    }
    (document.body || document.documentElement).append(host);
    ui = { host, root, badges: false, frame: 0, timer: 0 };
    return ui;
  }

  function maybeRemoveHost() {
    if (!ui || ui.badges || flashTimer) return;
    ui.host.remove();
    ui = null;
  }

  // Reads every rect first, then writes, so moving 99 badges costs one layout.
  function place() {
    if (!ui) return;
    ui.frame = 0;
    const v = view();
    const spots = [];
    for (const it of numbers.values()) {
      const el = deref(it.ref);
      const r = el && el.getBoundingClientRect();
      spots.push([it.badge, r && onScreen(r, v) ? r : null, it.badge.offsetWidth || 28]);
    }
    for (const [badge, r, w] of spots) {
      badge.hidden = !r;
      if (!r) continue;
      const x = Math.min(Math.max(0, r.left), Math.max(0, v.w - w));
      const y = Math.min(Math.max(0, r.top), Math.max(0, v.h - 28));
      badge.style.setProperty("transform", `translate(${Math.round(x)}px, ${Math.round(y)}px)`);
    }
  }

  function schedule() {
    if (ui && ui.badges && !ui.frame) ui.frame = requestAnimationFrame(place);
  }

  function listen(on) {
    const fn = on ? addEventListener : removeEventListener;
    // Capturing, so scrolling inside a scroll box moves the badges too.
    fn("scroll", schedule, { capture: true, passive: true });
    fn("resize", schedule, { passive: true });
  }

  function removeBadges() {
    if (!ui || !ui.badges) return false;
    listen(false);
    if (ui.frame) cancelAnimationFrame(ui.frame);
    clearInterval(ui.timer);
    for (const b of ui.root.querySelectorAll(".b")) b.remove();
    ui.badges = false;
    ui.frame = 0;
    maybeRemoveHost();
    return true;
  }

  function flash(el) {
    const r = el.getBoundingClientRect();
    const u = ensureHost();
    for (const old of u.root.querySelectorAll(".flash")) old.remove();
    const f = document.createElement("div");
    f.className = "flash";
    for (const [k, val] of [["left", r.left - 4], ["top", r.top - 4], ["width", r.width + 8], ["height", r.height + 8]]) {
      f.style.setProperty(k, `${Math.round(val)}px`);
    }
    u.root.append(f);
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => {
      flashTimer = 0;
      f.remove();
      maybeRemoveHost();
    }, 400);
  }

  // ------------------------------------------------------------ module calls
  function list({ max = 300 } = {}) {
    const limit = Math.min(300, Math.max(1, Number(max) || 300));
    const { v, items } = targets(null);
    ids = new Map();
    generation++;
    return {
      gen: generation,
      view: v,
      items: items.slice(0, limit).map((t, i) => {
        const id = `c${i + 1}`;
        ids.set(id, new WeakRef(t.el));
        const inView = t.r.left >= 0 && t.r.top >= 0 && t.r.right <= v.w && t.r.bottom <= v.h;
        return { id, label: t.label, role: t.role, ...box(t.r), href: t.href, text: t.text, inView };
      }),
    };
  }

  function show({ only = null, max = 99 } = {}) {
    const limit = Math.min(99, Math.max(1, Number(max) || 99));
    let pool = null;
    if (Array.isArray(only)) {
      pool = [];
      for (const id of only) {
        const el = deref(ids.get(String(id)));
        if (el) pool.push([el, el.getRootNode() instanceof ShadowRoot ? el.getRootNode() : document]);
      }
      if (!pool.length) throw new Error(GONE);
    }
    const { items } = targets(pool);
    removeBadges();
    numbers = new Map();
    generation++;
    const u = ensureHost();
    const out = items.slice(0, limit).map((t, i) => {
      const n = i + 1;
      const badge = document.createElement("div");
      badge.className = "b";
      badge.textContent = String(n);
      u.root.append(badge);
      numbers.set(n, { ref: new WeakRef(t.el), label: t.label, role: t.role, href: t.href, text: t.text, badge });
      return { n, label: t.label, role: t.role, ...box(t.r), href: t.href, text: t.text };
    });
    if (out.length) {
      u.badges = true;
      listen(true);
      // Catches layout changes and removed elements that no scroll or resize announces.
      u.timer = setInterval(schedule, 500);
      place();
    } else {
      maybeRemoveHost();
    }
    return { gen: generation, items: out };
  }

  function find({ n, id, gen } = {}) {
    if (n !== undefined && n !== null && n !== "") {
      const it = numbers.get(Number(n));
      if (!it || !ui || !ui.badges) throw new Error(`There's no number ${n} on the screen.`);
      const el = deref(it.ref);
      if (!el) throw new Error(GONE);
      return el;
    }
    if (Number(gen) !== generation) throw new Error(STALE);
    const el = deref(ids.get(String(id)));
    if (!el) throw new Error(GONE);
    return el;
  }

  function isTyping(el) {
    if (el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable) return true;
    return el.tagName === "INPUT" && FOCUS_TYPES.has(el.type);
  }

  function click(args = {}) {
    const el = find(args);
    const result = { clicked: true, label: labelOf(el), role: roleOf(el), kind: "click", navigates: navigates(el) };
    hide();
    const v = view();
    const r = el.getBoundingClientRect();
    if (r.left < 0 || r.top < 0 || r.right > v.w || r.bottom > v.h) el.scrollIntoView({ block: "nearest", inline: "nearest" });
    if (typeof el.focus === "function") el.focus({ preventScroll: true });
    flash(el);
    const link = el.matches && el.matches("a[href]") ? el : null; // a button inside a link card is the button
    if (isTyping(el)) result.kind = "focus";
    else if (link && navigates(link) && !link.hasAttribute("download")) {
      // A link to another page: the service worker opens it. A script click isn't a user gesture, so Chrome
      // would block a new tab, and mark this page as one "Back" skips.
      result.open = link.href;
      result.newTab = /^_(blank|new)$/i.test(link.target || "");
    } else el.click();
    return result;
  }

  function hide() {
    const had = removeBadges();
    numbers = new Map();
    return { said: had ? "hid the numbers" : "" };
  }

  // Everything off, including a flash still showing (reset).
  function off() {
    hide();
    clearTimeout(flashTimer);
    flashTimer = 0;
    if (ui) ui.host.remove();
    ui = null;
    const stale = document.getElementById(HOST_ID);
    if (stale) stale.remove();
  }

  function state() {
    const shown = !!(ui && ui.badges);
    const items = [];
    if (shown) {
      for (const [n, it] of numbers) if (deref(it.ref)) items.push({ n, label: it.label, role: it.role, href: it.href, text: it.text });
    }
    return { shown, count: shown ? numbers.size : 0, items };
  }

  // Back/forward cache restore: the old numbers point at a page state the user may not remember.
  addEventListener("pageshow", (e) => {
    if (e.persisted) hide();
  });

  P.registerOff("clicks", off);
  P.modules.clicks = { show, list, click, hide, state };
})();
