// Simplify menus, page side (see docs/page_steps.md). Collects the site's navigation links into a big
// Ally menu panel (#ally-menu-host), optionally a docked "Ally menu" strip (#ally-menu-strip), a big chooser
// for long or tiny <select> lists, and marks hover menus so the ally_menus layer can delay their closing.

(() => {
  const P = globalThis.AllyPage;
  if (!P || P.modules.menus) return;

  const PANEL_ID = "ally-menu-host";
  const STRIP_ID = "ally-menu-strip";
  const RISK_ID = "ally-risk-banner";
  const STRIP_PX = 64;
  const MAX_GROUPS = 12;
  const MAX_LINKS = 15;
  const MAX_TOTAL = 60;
  const MAX_TOGGLES = 10;
  const LINKISH = "a, button, [role=menuitem], [role=menuitemradio], [role=menuitemcheckbox], [role=link], [role=button], summary";
  const LISTS = "ul, ol, [role=menubar], [role=menu], [role=list], [role=tree]";
  const ROOTS = "nav, [role=navigation], [role=menubar], [role=menu]";
  const HAMBURGER = /menu|nav|hamburger|burger|drawer/i;

  const state = {
    on: false,
    strip: false,
    listeners: [],
    observer: null,
    returnFocus: null,
    view: null, // what the panel shows: {kind: "menu"|"group"|"select", ...}
    menu: null, // last collection: {entries, groups, links, more}
  };

  // ------------------------------------------------------------ small helpers
  function clean(s) {
    return String(s || "").replace(/\s+/g, " ").trim().slice(0, 80);
  }

  function textOf(el) {
    let t = clean(el.innerText || el.textContent);
    if (!t) t = clean(el.getAttribute("aria-label") || el.getAttribute("title"));
    if (!t) {
      const img = el.querySelector("img[alt], svg[aria-label]");
      if (img) t = clean(img.getAttribute("alt") || img.getAttribute("aria-label"));
    }
    return t;
  }

  function skipped(el) {
    return P.isAlly(el) || P.allyHidden(el);
  }

  // A link that only works through the site's script: no href, "#" or javascript:.
  function jsOnly(el) {
    if (el.tagName !== "A") return true;
    const raw = (el.getAttribute("href") || "").trim();
    return !raw || raw === "#" || /^javascript:/i.test(raw);
  }

  function isToggle(el) {
    return el.tagName !== "A" && (el.hasAttribute("aria-expanded") || el.hasAttribute("aria-haspopup"));
  }

  function isSkipLink(el, text) {
    if (/^skip\b/i.test(text) || /^jump to\b/i.test(text)) return true;
    const raw = el.tagName === "A" ? el.getAttribute("href") || "" : "";
    return /^#(main|content|maincontent|main-content)$/i.test(raw);
  }

  function nextFrame() {
    return new Promise((resolve) => {
      let done = false;
      const finish = () => {
        if (done) return;
        done = true;
        setTimeout(resolve, 30);
      };
      requestAnimationFrame(() => requestAnimationFrame(finish));
      setTimeout(finish, 150); // background tabs may not paint
    });
  }

  function modalOpen() {
    for (const d of document.querySelectorAll("dialog[open]")) {
      try {
        if (d.matches(":modal") && !skipped(d)) return true;
      } catch (e) {
        // :modal not supported: fall through to aria-modal
      }
    }
    for (const el of document.querySelectorAll('[aria-modal="true"]')) {
      if (!skipped(el) && P.visible(el)) return true;
    }
    return false;
  }

  function listen(target, type, fn, opts) {
    target.addEventListener(type, fn, opts);
    state.listeners.push([target, type, fn, opts]);
  }

  function unlistenAll() {
    for (const [target, type, fn, opts] of state.listeners) target.removeEventListener(type, fn, opts);
    state.listeners = [];
  }

  // ------------------------------------------------------------ finding the menus
  function topMost(els) {
    return els.filter((el) => !els.some((o) => o !== el && o.contains(el)));
  }

  function findRoots(extra = []) {
    const found = Array.from(document.querySelectorAll(ROOTS));
    for (const header of document.querySelectorAll("header, [role=banner]")) {
      for (const ul of header.querySelectorAll("ul, ol")) {
        if (ul.querySelector(":scope > li > ul, :scope > li > ol, :scope > li > div ul")) found.push(ul);
      }
    }
    for (const el of extra) if (el && el.isConnected) found.push(el);
    return topMost(found.filter((el) => !skipped(el)));
  }

  function rootLabel(root) {
    const label = clean(root.getAttribute("aria-label"));
    if (label) return label;
    const by = root.getAttribute("aria-labelledby");
    if (by) {
      const t = clean(by.split(/\s+/).map((id) => (document.getElementById(id) || {}).textContent || "").join(" "));
      if (t) return t;
    }
    if (root.closest("footer, [role=contentinfo]")) return "Links at the bottom";
    return "More links";
  }

  // Toggles worth opening before collecting: collapsed submenu buttons in menus and hamburger buttons.
  function findToggles() {
    const out = [];
    const inMenu = (el) => el.closest(`${ROOTS}, header, [role=banner]`);
    for (const el of document.querySelectorAll("[aria-controls]")) {
      if (out.length >= MAX_TOGGLES) break;
      if (skipped(el) || !P.visible(el)) continue;
      if (el.tagName === "A" && !jsOnly(el)) continue; // would navigate
      const expanded = el.getAttribute("aria-expanded");
      if (expanded === "true") continue;
      const label = `${el.getAttribute("aria-label") || ""} ${el.id} ${el.className} ${el.textContent}`;
      const collapsedInMenu = expanded === "false" && inMenu(el);
      const hamburger = HAMBURGER.test(label) && (expanded === "false" || expanded === null);
      if (collapsedInMenu || hamburger) out.push(el);
    }
    return out;
  }

  function controlled(el) {
    const id = (el.getAttribute("aria-controls") || "").split(/\s+/)[0];
    return id ? document.getElementById(id) : null;
  }

  function toggleState(el) {
    const target = controlled(el);
    return { expanded: el.getAttribute("aria-expanded"), shown: !!(target && P.visible(target)) };
  }

  // Opens collapsed menus so their links are in the DOM, runs fn, then puts every toggle back.
  async function withExpanded(fn) {
    const toggles = findToggles();
    const before = toggles.map(toggleState);
    for (const el of toggles) {
      try {
        el.click();
      } catch (e) {
        // a broken site handler shouldn't stop the collection
      }
    }
    if (toggles.length) await nextFrame();
    const targets = toggles.map(controlled);
    try {
      return fn(targets);
    } finally {
      toggles.forEach((el, i) => {
        if (!el.isConnected) return;
        const now = toggleState(el);
        if (now.expanded !== before[i].expanded || now.shown !== before[i].shown) {
          try {
            el.click();
          } catch (e) {
            // ignore
          }
        }
      });
    }
  }

  function linkItem(el) {
    const text = textOf(el);
    if (!text || isSkipLink(el, text)) return null;
    if (jsOnly(el)) return { text, el };
    return { text, href: el.href };
  }

  // The lists at the top of a root: lists inside the root that aren't inside another list in it.
  function topLists(root) {
    if (root.matches(LISTS)) return [root];
    const lists = Array.from(root.querySelectorAll(LISTS)).filter((l) => !skipped(l));
    return topMost(lists);
  }

  function itemsOf(list) {
    return Array.from(list.children).filter((c) => !skipped(c));
  }

  function linksIn(el, except) {
    return Array.from(el.querySelectorAll(LINKISH)).filter((a) => a !== except && !skipped(a) && !isToggle(a));
  }

  // One root -> {label, loose: [link], groups: [{text, links, sub, at}]}; `at` places a group in page order.
  function readRoot(root) {
    const out = { label: rootLabel(root), loose: [], groups: [] };
    const used = new Set();
    for (const list of topLists(root)) {
      for (const item of itemsOf(list)) {
        const heading = item.matches(LINKISH) ? item : item.querySelector(LINKISH);
        if (!heading || skipped(heading)) continue;
        const subs = item === heading ? [] : linksIn(item, heading);
        used.add(heading);
        subs.forEach((a) => used.add(a));
        if (!subs.length) {
          if (!isToggle(heading)) out.loose.push(heading);
          continue;
        }
        const first = !jsOnly(heading) ? [heading] : [];
        const sub = Array.from(item.children).find((c) => c !== heading && !c.contains(heading) && subs.some((a) => c.contains(a)));
        out.groups.push({ text: textOf(heading) || "More", links: [...first, ...subs], sub, at: heading });
      }
    }
    // Menus built from buttons and panels instead of lists.
    for (const btn of root.querySelectorAll("[aria-controls]")) {
      if (used.has(btn) || skipped(btn)) continue;
      const target = controlled(btn);
      if (!target || skipped(target)) continue;
      const subs = linksIn(target).filter((a) => !used.has(a));
      if (!subs.length) continue;
      subs.forEach((a) => used.add(a));
      used.add(btn);
      out.groups.push({ text: textOf(btn) || "More", links: subs, sub: root.contains(target) ? target : null, at: btn });
    }
    for (const a of linksIn(root)) if (!used.has(a)) out.loose.push(a);
    return out;
  }

  function collect(extraRoots = []) {
    const roots = findRoots(extraRoots);
    const seen = new Set();
    let more = false;
    let total = 0;
    let looseTop = 0;
    let groupCount = 0;
    const entries = [];
    const subs = [];

    const take = (els, limit) => {
      const items = [];
      for (const el of els) {
        const item = linkItem(el);
        if (!item) continue;
        const k = item.href ? `h:${item.href.replace(/#$/, "")}` : `t:${item.text.toLowerCase()}`;
        if (seen.has(k)) continue;
        if (items.length >= limit || total >= MAX_TOTAL) {
          more = true;
          break;
        }
        seen.add(k);
        items.push(item);
        total++;
      }
      return items;
    };

    const addGroup = (text, els, sub) => {
      if (groupCount >= MAX_GROUPS || total >= MAX_TOTAL) {
        more = true;
        return false;
      }
      const items = take(els, MAX_LINKS);
      if (!items.length) return false;
      entries.push({ text, items });
      groupCount++;
      if (sub) subs.push(sub);
      return true;
    };

    for (const root of roots) {
      const r = readRoot(root);
      if (r.groups.length || r.loose.length) P.mark(root, "menus", "menu");
      if (looseTop + r.loose.length <= MAX_LINKS) {
        // Few loose links: show them on the first screen, in page order with the groups.
        const order = [...r.groups, ...r.loose.map((a) => ({ at: a, link: a }))];
        order.sort((x, y) => (x.at.compareDocumentPosition(y.at) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1));
        for (const o of order) {
          if (!o.link) {
            addGroup(o.text, o.links, o.sub);
            continue;
          }
          const [item] = take([o.link], 1);
          if (!item) continue;
          entries.push(item);
          looseTop++;
        }
      } else {
        for (const g of r.groups) addGroup(g.text, g.links, g.sub);
        addGroup(r.label, r.loose, null);
      }
    }
    for (const sub of subs) P.mark(sub, "menus", "sub");
    return { entries, groups: groupCount, links: total, more };
  }

  async function collectExpanded() {
    return await withExpanded((targets) => collect(targets));
  }

  // ------------------------------------------------------------ shadow hosts and styles
  const PANEL_CSS = `
:host { all: initial !important; position: fixed !important; z-index: 2147483646 !important; top: 0 !important;
  right: 0 !important; display: block !important; width: min(560px, 100vw) !important; max-height: 100vh !important; }
* { box-sizing: border-box; }
.panel { font: 24px/1.35 system-ui, -apple-system, "Segoe UI", Arial, sans-serif; color: #000; background: #fff;
  border: 4px solid #000; border-top-width: 0; max-height: 100vh; overflow: auto; overscroll-behavior: contain;
  box-shadow: 0 8px 32px rgba(0,0,0,.45); }
.bar { position: sticky; top: 0; display: flex; gap: 12px; align-items: center; padding: 12px; background: #fff;
  border-bottom: 3px solid #000; }
.title { flex: 1; margin: 0; font-size: 26px; font-weight: 700; overflow-wrap: anywhere; }
.note { margin: 0; padding: 12px 16px; font-size: 22px; background: #fff8c5; border-bottom: 2px solid #000; }
.note:empty { display: none; }
button { font: inherit; color: inherit; cursor: pointer; }
.bar button { min-height: 56px; min-width: 96px; padding: 8px 18px; font-size: 22px; font-weight: 700;
  background: #fff; border: 3px solid #000; border-radius: 10px; }
.bar .close { background: #000; color: #fff; }
.bar .back[hidden] { display: none; }
ul { list-style: none; margin: 0; padding: 0; }
li + li { border-top: 2px solid #bbb; }
.row { display: flex; width: 100%; min-height: 60px; align-items: center; gap: 12px; padding: 12px 18px;
  text-align: left; background: #fff; border: 0; font-size: 24px; color: #0b3d91; text-decoration: underline;
  text-underline-offset: 4px; }
.row.group { color: #000; font-weight: 700; text-decoration: none; }
.row .label { flex: 1; overflow-wrap: anywhere; }
.row .arrow { font-size: 28px; }
.row:hover { background: #e8f0ff; }
.row[aria-current="true"] { background: #e6f4ea; color: #000; font-weight: 700; text-decoration: none; }
.row:disabled { color: #666; text-decoration: none; cursor: default; background: #f2f2f2; }
.heading { padding: 14px 18px 6px; font-size: 22px; font-weight: 700; color: #333; background: #f2f2f2; }
.more { padding: 14px 18px; font-size: 22px; color: #333; }
button:focus-visible, button:focus { outline: 4px solid #ffbf00; outline-offset: -4px; box-shadow: inset 0 0 0 7px #000; }
`;

  const STRIP_CSS = `
:host { all: initial !important; position: fixed !important; z-index: 2147483646 !important; top: 0 !important;
  left: 0 !important; right: 0 !important; height: ${STRIP_PX}px !important; display: block !important; }
:host([hidden]) { display: none !important; }
* { box-sizing: border-box; }
.strip { height: ${STRIP_PX}px; display: flex; align-items: center; padding: 6px 12px; background: #fff;
  border-bottom: 3px solid #000; font: 22px/1.2 system-ui, -apple-system, "Segoe UI", Arial, sans-serif; }
button { display: inline-flex; align-items: center; gap: 12px; height: 52px; padding: 0 22px; font: inherit;
  font-weight: 700; color: #fff; background: #000; border: 3px solid #000; border-radius: 10px; cursor: pointer; }
button:focus-visible, button:focus { outline: 4px solid #ffbf00; outline-offset: 2px; }
svg { width: 28px; height: 28px; }
`;

  function applyStyles(root, css) {
    try {
      const sheet = new CSSStyleSheet();
      sheet.replaceSync(css);
      root.adoptedStyleSheets = [sheet];
    } catch (e) {
      const style = document.createElement("style");
      style.textContent = css;
      root.appendChild(style);
    }
  }

  function makeHost(id, css) {
    const host = document.createElement("div");
    host.id = id;
    const root = host.attachShadow({ mode: "open" });
    applyStyles(root, css);
    // Keys typed in Ally's overlays must not trigger the site's shortcuts.
    for (const type of ["keydown", "keyup", "keypress"]) host.addEventListener(type, (e) => e.stopPropagation());
    document.documentElement.appendChild(host);
    return { host, root };
  }

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  // ------------------------------------------------------------ the panel
  function panelParts() {
    const host = document.getElementById(PANEL_ID);
    if (host && host.shadowRoot && host.__ally) return host.__ally;
    if (host) host.remove();
    const { host: h, root } = makeHost(PANEL_ID, PANEL_CSS);
    const panel = el("div", "panel");
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "Ally menu");
    const bar = el("div", "bar");
    const back = el("button", "back", "Back");
    const title = el("h2", "title", "Menu");
    const close = el("button", "close", "Close");
    back.type = close.type = "button";
    bar.append(back, title, close);
    const note = el("p", "note");
    note.setAttribute("role", "status");
    const list = el("ul", "list");
    panel.append(bar, note, list);
    root.appendChild(panel);
    back.addEventListener("click", () => showMenu(state.view && state.view.from));
    close.addEventListener("click", () => closePanel());
    panel.addEventListener("keydown", onPanelKey);
    h.__ally = { host: h, root, panel, back, title, close, note, list };
    return h.__ally;
  }

  function focusables(parts) {
    return Array.from(parts.panel.querySelectorAll("button")).filter((b) => !b.disabled && !b.hidden);
  }

  function onPanelKey(e) {
    const parts = panelParts();
    if (e.key === "Escape") {
      e.preventDefault();
      closePanel();
      return;
    }
    const keys = { ArrowDown: 1, ArrowUp: -1, Tab: e.shiftKey ? -1 : 1, Home: "first", End: "last" };
    if (!(e.key in keys)) return;
    const items = focusables(parts);
    if (!items.length) return;
    e.preventDefault();
    const now = items.indexOf(parts.root.activeElement);
    let next;
    if (keys[e.key] === "first") next = 0;
    else if (keys[e.key] === "last") next = items.length - 1;
    else next = (now + keys[e.key] + items.length) % items.length;
    if (now < 0) next = keys[e.key] === -1 ? items.length - 1 : 0;
    items[next].focus();
  }

  function row(text, cls, onChoose) {
    const li = el("li");
    const b = el("button", `row ${cls || ""}`.trim());
    b.type = "button";
    b.appendChild(el("span", "label", text));
    if (cls === "group") b.appendChild(el("span", "arrow", "›")).setAttribute("aria-hidden", "true");
    b.addEventListener("click", onChoose);
    li.appendChild(b);
    return li;
  }

  function openPanel() {
    const parts = panelParts();
    const active = document.activeElement;
    if (!state.returnFocus && active && active !== parts.host && !P.isAlly(active)) state.returnFocus = active;
    return parts;
  }

  function focusFirst(parts, prefer) {
    const target = prefer || parts.list.querySelector("button:not(:disabled)") || parts.close;
    target.focus({ preventScroll: true });
    if (prefer) prefer.scrollIntoView({ block: "nearest" });
  }

  function showMenu(focusIndex) {
    const parts = openPanel();
    const menu = state.menu || { entries: [], more: false };
    state.view = { kind: "menu" };
    parts.title.textContent = "Menu";
    parts.back.hidden = true;
    parts.note.textContent = menu.entries.length ? "" : "I didn't find a menu on this page.";
    parts.list.replaceChildren();
    let prefer = null;
    menu.entries.forEach((entry, i) => {
      const li = entry.items
        ? row(entry.text, "group", () => showGroup(i))
        : row(entry.text, "link", () => choose(entry));
      parts.list.appendChild(li);
      if (i === focusIndex) prefer = li.firstChild;
    });
    if (menu.more) parts.list.appendChild(el("li", "more", "And more on the page."));
    focusFirst(parts, prefer);
  }

  function showGroup(index) {
    const parts = openPanel();
    const entry = state.menu.entries[index];
    state.view = { kind: "group", from: index };
    parts.title.textContent = entry.text;
    parts.back.hidden = false;
    parts.note.textContent = "";
    parts.list.replaceChildren(...entry.items.map((item) => row(item.text, "link", () => choose(item))));
    focusFirst(parts);
  }

  function closePanel() {
    const host = document.getElementById(PANEL_ID);
    if (host) host.remove();
    state.view = null;
    const back = state.returnFocus;
    state.returnFocus = null;
    if (back && back.isConnected && typeof back.focus === "function") {
      try {
        back.focus({ preventScroll: true });
      } catch (e) {
        // ignore
      }
    }
  }

  // An element removed when its menu closed again: find the same item by its text.
  function refind(item) {
    if (item.el && item.el.isConnected) return item.el;
    for (const n of document.querySelectorAll(LINKISH)) {
      if (!skipped(n) && textOf(n) === item.text) return n;
    }
    return null;
  }

  function choose(item) {
    closePanel();
    if (item.href) {
      location.assign(item.href);
      return;
    }
    const target = refind(item);
    if (target) target.click();
  }

  // ------------------------------------------------------------ the select chooser
  function bigSelect(sel) {
    if (sel.multiple || sel.disabled || sel.size > 1 || skipped(sel)) return false;
    if (sel.options.length > 7) return true;
    return (parseFloat(getComputedStyle(sel).fontSize) || 16) < 16;
  }

  function selectLabel(sel) {
    const label = sel.labels && sel.labels[0];
    return clean((label && label.textContent) || sel.getAttribute("aria-label") || sel.getAttribute("title")) || "Choose one";
  }

  function showSelect(sel) {
    const parts = openPanel();
    if (!state.returnFocus || state.returnFocus !== sel) state.returnFocus = sel;
    state.view = { kind: "select" };
    parts.title.textContent = selectLabel(sel);
    parts.back.hidden = true;
    parts.note.textContent = "";
    parts.list.replaceChildren();
    let current = null;
    let group = null;
    Array.from(sel.options).forEach((opt, i) => {
      const og = opt.parentElement && opt.parentElement.tagName === "OPTGROUP" ? opt.parentElement : null;
      if (og && og !== group) parts.list.appendChild(el("li", "heading", clean(og.label) || " "));
      group = og;
      if (opt.hidden) return;
      const li = row(clean(opt.label || opt.text) || " ", "option", () => pickOption(sel, i));
      const b = li.firstChild;
      if (opt.disabled || (og && og.disabled)) b.disabled = true;
      if (i === sel.selectedIndex) {
        b.setAttribute("aria-current", "true");
        current = b;
      }
      parts.list.appendChild(li);
    });
    focusFirst(parts, current && !current.disabled ? current : null);
  }

  function pickOption(sel, index) {
    closePanel();
    if (sel.selectedIndex !== index) {
      sel.selectedIndex = index;
      sel.dispatchEvent(new Event("input", { bubbles: true }));
      sel.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }

  function onMouseDown(e) {
    if (e.button !== 0) return;
    const sel = e.target && e.target.closest ? e.target.closest("select") : null;
    if (!sel || !bigSelect(sel)) return;
    e.preventDefault(); // the native popup must not open as well
    sel.focus({ preventScroll: true });
    showSelect(sel);
  }

  function onKeyDown(e) {
    const sel = e.target;
    if (!sel || sel.tagName !== "SELECT" || !bigSelect(sel)) return;
    const opens = e.key === "Enter" || e.key === " " || (e.altKey && e.key === "ArrowDown");
    if (!opens || e.ctrlKey || e.metaKey) return;
    e.preventDefault();
    showSelect(sel);
  }

  // ------------------------------------------------------------ the strip
  function riskShowing() {
    return !!document.getElementById(RISK_ID);
  }

  function syncStrip() {
    const host = document.getElementById(STRIP_ID);
    if (!host) return;
    const hide = riskShowing();
    host.hidden = hide;
    if (hide) document.documentElement.removeAttribute("data-ally-menus-strip");
    else document.documentElement.setAttribute("data-ally-menus-strip", "");
  }

  function svgIcon() {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    for (const y of [5, 11, 17]) {
      const r = document.createElementNS(ns, "rect");
      r.setAttribute("x", "3");
      r.setAttribute("y", String(y));
      r.setAttribute("width", "18");
      r.setAttribute("height", "2.6");
      r.setAttribute("rx", "1.3");
      r.setAttribute("fill", "currentColor");
      svg.appendChild(r);
    }
    return svg;
  }

  // Fixed or sticky bars stuck to the top would hide under the strip: mark them so the layer moves them down.
  function markTopBars() {
    P.unmark("menus", "shift");
    P.withPaused(["menus"], () => {
      const all = document.body ? document.body.getElementsByTagName("*") : [];
      for (let i = 0; i < all.length; i++) {
        const n = all[i];
        const cs = getComputedStyle(n);
        if (cs.position !== "fixed" && cs.position !== "sticky") continue;
        if (skipped(n) || !P.visible(n)) continue;
        if ((parseFloat(cs.top) || 0) > 4 || cs.top === "auto") continue;
        const r = n.getBoundingClientRect();
        if (r.top > 4 || r.width < innerWidth * 0.3) continue;
        P.mark(n, "menus", "shift");
      }
    });
  }

  function showStrip() {
    let host = document.getElementById(STRIP_ID);
    if (!host || !host.shadowRoot) {
      if (host) host.remove();
      const made = makeHost(STRIP_ID, STRIP_CSS);
      host = made.host;
      const bar = el("div", "strip");
      bar.setAttribute("role", "region");
      bar.setAttribute("aria-label", "Ally");
      const btn = el("button");
      btn.type = "button";
      btn.append(svgIcon(), el("span", null, "Ally menu"));
      btn.addEventListener("click", () => {
        openFromStrip(btn);
      });
      bar.appendChild(btn);
      made.root.appendChild(bar);
    }
    markTopBars();
    state.strip = true;
    syncStrip();
    if (!state.observer) {
      state.observer = new MutationObserver(syncStrip);
      state.observer.observe(document.documentElement, { childList: true });
      if (document.body) state.observer.observe(document.body, { childList: true });
    }
  }

  async function openFromStrip(btn) {
    state.returnFocus = btn;
    if (modalOpen()) return;
    state.menu = await collectExpanded();
    showMenu();
  }

  // ------------------------------------------------------------ on / off
  function startHelpers() {
    if (state.on) return;
    state.on = true;
    listen(document, "mousedown", onMouseDown, true);
    listen(document, "keydown", onKeyDown, true);
  }

  function plural(n, word) {
    return `${n} ${word}${n === 1 ? "" : "s"}`;
  }

  function basePadding() {
    if (!document.body) return 0;
    return P.withPaused(["menus"], () => parseFloat(getComputedStyle(document.body).paddingTop) || 0);
  }

  async function on(args = {}) {
    const mode = args.mode === "strip" ? "strip" : "panel";
    startHelpers();
    const modal = modalOpen();
    let said;
    if (mode === "strip") {
      state.menu = collect();
      showStrip();
      if (modal) said = "a pop-up on the page is in front";
      else if (riskShowing()) said = "the Ally menu button will show when the warning is gone";
      else said = "put a big Ally menu button at the top";
    } else if (modal) {
      state.menu = collect();
      said = "a pop-up on the page is in front";
    } else {
      state.menu = await collectExpanded();
      showMenu();
      const m = state.menu;
      said = m.links
        ? `opened the Ally menu with ${plural(m.links, "link")}${m.groups ? ` in ${plural(m.groups, "group")}` : ""}${m.more ? " and more" : ""}`
        : "didn't find a menu on this page";
    }
    const m = state.menu;
    return { said, groups: m.groups, links: m.links, more: m.more, mode, strip: state.strip, basePad: basePadding() };
  }

  function off() {
    const wasOn = state.on || state.strip || !!document.getElementById(PANEL_ID);
    closePanel();
    const strip = document.getElementById(STRIP_ID);
    if (strip) strip.remove();
    if (state.observer) state.observer.disconnect();
    state.observer = null;
    unlistenAll();
    P.unmark("menus");
    document.documentElement.removeAttribute("data-ally-menus-strip");
    state.on = false;
    state.strip = false;
    state.menu = null;
    return { said: wasOn ? "closed the Ally menu" : "", off: true };
  }

  function status() {
    return {
      on: state.on,
      strip: state.strip,
      panel: !!document.getElementById(PANEL_ID),
      view: state.view && state.view.kind,
      listeners: state.listeners.length,
    };
  }

  P.registerOff("menus", off);
  P.modules.menus = { on, off, status, close: () => (closePanel(), true) };
})();
