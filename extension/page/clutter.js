// Hide clutter, page side (docs/page_steps.md): finds cookie banners, promo pop-ups, chat buttons, ads and
// share bars and marks them data-ally-clutter="<kind>"; the ally_clutter layer hides marked elements.
// Detection only reads; marking happens in one burst so the risk scanner sees one mutation batch.

(() => {
  const P = globalThis.AllyPage;
  if (!P || P.modules.clutter) return;

  const PAGE_KINDS = ["cookie", "promo", "chat", "ads", "share"];
  const UNLOCK = "data-ally-clutter-unlock";

  // Consent managers: hidden without size or text limits (they are big and full of links and switches).
  const CMP = [
    "#onetrust-banner-sdk", "#onetrust-consent-sdk", ".onetrust-pc-dark-filter", "#CybotCookiebotDialog",
    "#usercentrics-root", "div[id^='sp_message_container_']", ".cc-window", ".cc-banner", "#truste-consent-track",
    ".fc-consent-root", "#didomi-host", ".qc-cmp2-container", "#cmpbox",
  ].join(",");
  const COOKIE_ID = /cookie[-_]?(banner|bar|notice|consent|popup|law|message|wall)|consent[-_]?(banner|bar|popup)|gdpr[-_]?(banner|consent|popup)/i;
  const CHAT = /intercom|^drift|drift[-_](widget|frame|conductor)|zendesk|ze-snippet|webwidget|tawk|livechat|crisp-client|hubspot-messages|freshchat|fc_frame|fc_widget|olark|tidio/i;
  const ADS = "ins.adsbygoogle, [id^='google_ads_iframe'], [id^='div-gpt-ad'], iframe[src*='doubleclick'], [data-ad-slot], [aria-label]";
  const AD_LABEL = /^\s*(advertisement|advert|ads?|sponsored( ad| content)?)\s*$/i;
  const SHARE = /facebook\.com\/(sharer|share|dialog\/share)|twitter\.com\/(intent|share)|\/\/(www\.)?x\.com\/intent|linkedin\.com\/(share|sharing|shareArticle)|pinterest\.[a-z.]+\/pin\/create|wa\.me\/|whatsapp\.com\/send|^whatsapp:/i;

  const KEYWORD = /cookie|consent|we use cookies|subscribe|newsletter|sign up for|get the app|download (our|the) app|install (our|the) app/i;
  const COOKIE_WORD = /cookie|consent/i;
  const SCAM = /virus|infected|locked|call (now|support|microsoft|apple)|support number|refund|gift card/i;
  const PAYWALL = /subscribe to (read|continue)|paywall|already a subscriber|subscribers only|continue reading|keep reading/i;
  const BACKDROP = /backdrop|overlay|modal-bg|dimmer|scrim|shade|mask|dark-filter/i;

  const SECRET_INPUT = "input[type=password], [autocomplete~='one-time-code'], [autocomplete^='cc-'], [autocomplete*=' cc-']";
  const SECRET_NAME = /card|cvv|cvc|iban|otp|one.?time|passcode|security code|verification code/i;
  const NAV = "nav, [role=navigation], [role=menubar]";
  const MAIN = "main, article, [role=main]";
  const LIVE = "[role=alert], [role=alertdialog], [aria-live]";
  const DIALOG = "[role=dialog], [role=alertdialog], [aria-modal=true], dialog[open]";
  const BUTTONISH = new Set(["hidden", "submit", "button", "reset", "image"]);
  const LOCK_CLASS = /(^|\s)(modal-open|didomi-popup-open|sp-message-open|overflow-hidden|no-scroll|noscroll)(\s|$)/;
  const SKIP_TAGS = new Set(["SCRIPT", "STYLE", "LINK", "META", "NOSCRIPT", "TEMPLATE", "BR", "HEAD", "TITLE"]);
  const SHADOW_HOSTS = new Set(["usercentrics-root", "usercentrics-cmp-ui", "didomi-host", "cmpbox"]);
  const HTML_NS = "http://www.w3.org/1999/xhtml";

  let unlockY = 0;
  // Backdrops are hidden with their pop-up and not counted on their own.
  const backdropMarks = new WeakSet();

  // ------------------------------------------------------------ small readers
  // Closed roots are only looked for on custom elements and known consent hosts: asking Chrome for every
  // element would be slow on big pages.
  function shadowOf(el) {
    if (el.shadowRoot) return el.shadowRoot;
    if (!el.tagName.includes("-") && !SHADOW_HOSTS.has(el.id)) return null;
    try {
      if (globalThis.chrome && chrome.dom && chrome.dom.openOrClosedShadowRoot) return chrome.dom.openOrClosedShadowRoot(el) || null;
    } catch {
      // not an element that can host a shadow root
    }
    return null;
  }

  // querySelectorAll that also looks inside shadow roots (open, and closed ones Chrome lets us read).
  function deepAll(root, sel, limit = 200) {
    const out = [];
    const walk = (r, depth) => {
      if (out.length >= limit || depth > 4) return;
      if (r.nodeType === 1 && r.matches(sel)) out.push(r);
      for (const el of r.querySelectorAll(sel)) {
        out.push(el);
        if (out.length >= limit) return;
      }
      const hosts = r.nodeType === 1 ? [r, ...r.querySelectorAll("*")] : Array.from(r.querySelectorAll("*"));
      for (const h of hosts) {
        const sr = shadowOf(h);
        if (sr) walk(sr, depth + 1);
      }
    };
    walk(root, 0);
    return out;
  }

  function attrText(el) {
    return [el.id, el.getAttribute("class"), el.getAttribute("name"), el.getAttribute("title"),
      el.tagName === "IFRAME" ? el.getAttribute("src") : ""].filter(Boolean).join(" ");
  }

  function shadowText(root, depth = 0) {
    let t = "";
    for (const el of root.querySelectorAll("*")) {
      const sr = shadowOf(el);
      if (!sr || depth > 3) continue;
      for (const c of sr.children) t += " " + (c.innerText || c.textContent || "");
      t += shadowText(sr, depth + 1);
    }
    return t;
  }

  function textOf(el) {
    const own = el.innerText || "";
    const sr = shadowOf(el);
    let extra = sr ? Array.from(sr.children).map((c) => c.innerText || c.textContent || "").join(" ") : "";
    extra += shadowText(el);
    return (own + " " + extra).replace(/\s+/g, " ").trim();
  }

  function coverage(r) {
    const w = Math.max(0, Math.min(r.right, innerWidth) - Math.max(r.left, 0));
    const h = Math.max(0, Math.min(r.bottom, innerHeight) - Math.max(r.top, 0));
    return (w * h) / Math.max(1, innerWidth * innerHeight);
  }

  function isFixed(el) {
    const pos = getComputedStyle(el).position;
    return pos === "fixed" || pos === "sticky";
  }

  // The fixed/sticky box an element sits in: itself, an ancestor within `depth` levels, or (for a shadow
  // host) a fixed child of its shadow root.
  function fixedBox(el, depth = 3) {
    for (let n = el, i = 0; n && n.nodeType === 1 && n !== document.body && i <= depth; n = n.parentElement, i++) {
      if (isFixed(n)) return n;
    }
    const sr = shadowOf(el);
    if (sr) for (const c of sr.children) if (isFixed(c) && P.visible(c)) return c;
    return null;
  }

  // Visible itself, or through a visible descendant (consent containers are often 0px tall with fixed children).
  function shown(el) {
    if (P.visible(el)) return true;
    let n = 0;
    for (const d of deepAll(el, "*", 300)) {
      if (++n > 300) break;
      if (d !== el && P.visible(d)) return true;
    }
    return false;
  }

  function rectOf(el) {
    if (P.visible(el)) return el.getBoundingClientRect();
    const box = fixedBox(el, 0);
    return box ? box.getBoundingClientRect() : el.getBoundingClientRect();
  }

  // ------------------------------------------------------------ guards
  // Never hidden, however it was found.
  function hardBlock(el) {
    if (el === document.body || el === document.documentElement) return "page";
    if (P.isAlly(el) || el.querySelector("[id^='ally-']")) return "ally";
    if (el.matches(MAIN) || el.querySelector(MAIN)) return "main content";
    if (deepAll(el, SECRET_INPUT, 1).length) return "secret field";
    for (const i of deepAll(el, "input", 50)) {
      if (SECRET_NAME.test([i.name, i.id, i.placeholder, i.getAttribute("aria-label")].join(" "))) return "secret field";
    }
    if (SCAM.test(textOf(el))) return "scam words";
    return "";
  }

  // Extra limits for elements found only by their text.
  function softBlock(el, text) {
    if (el.matches(NAV) || deepAll(el, NAV, 1).length) return "navigation";
    if (deepAll(el, "a[href]", 9).length > 8) return "many links";
    if (el.matches(LIVE) || deepAll(el, LIVE, 1).length) return "alert";
    const inputs = deepAll(el, "input, select, textarea", 5).filter((i) => !BUTTONISH.has((i.type || "").toLowerCase()));
    if (inputs.length > 1) return "form";
    if (inputs.length === 1 && !(inputs[0].type === "email" || /email/i.test(inputs[0].autocomplete || ""))) return "form";
    if (text.length > 600) return "long text";
    if (deepAll(el, "a[href^='tel:']", 1).length) return "phone link";
    return "";
  }

  function isDialog(el) {
    return el.matches(DIALOG) || !!el.querySelector(DIALOG);
  }

  // Keyword text must make up most of the element's own text, or lead it.
  function keywordText(text) {
    if (!text || text.length > 600) return false;
    const sentences = text.split(/(?<=[.!?])\s+/).map((s) => s.trim()).filter(Boolean);
    let hit = 0;
    for (const s of sentences) if (KEYWORD.test(s)) hit += s.length;
    if (!hit) return false;
    return hit / text.length >= 0.4 || sentences.slice(0, 2).some((s) => KEYWORD.test(s));
  }

  // ------------------------------------------------------------ detection
  function fixedElements() {
    const out = [];
    const all = document.body ? document.body.getElementsByTagName("*") : [];
    for (const el of all) {
      if (el.namespaceURI !== HTML_NS || SKIP_TAGS.has(el.tagName)) continue;
      if (P.allyHidden(el) || P.isAlly(el)) continue;
      if (isFixed(el) || (el.tagName.includes("-") && fixedBox(el, 0))) out.push(el);
    }
    return out;
  }

  function candidates(sel, test) {
    const out = [];
    for (const el of document.body ? document.body.querySelectorAll(sel) : []) {
      if (el.namespaceURI !== HTML_NS || SKIP_TAGS.has(el.tagName)) continue;
      if (P.allyHidden(el) || (test && !test(el))) continue;
      out.push(el);
    }
    return out;
  }

  // Cookie-banner ids/classes count as known only on a fixed or sticky box; mark the box when it holds
  // little else.
  function looseCookie(el) {
    const box = fixedBox(el);
    if (!box || box === el) return box ? el : null;
    const t = textOf(el).length;
    return textOf(box).length <= t * 1.1 + 10 ? box : el;
  }

  function heuristic(f, add) {
    if (hardBlock(f)) return;
    const text = textOf(f);
    if (PAYWALL.test(text)) return;
    const soft = softBlock(f, text);
    if (!soft && keywordText(text)) {
      if (coverage(rectOf(f)) < 0.25) return add(f, COOKIE_WORD.test(text) ? "cookie" : "promo", "text");
      // A big fixed box is fine only as a backdrop around a small box that holds its text.
      const inner = Array.from(f.querySelectorAll("div, section, aside, form, [role=dialog]")).find((d) =>
        P.visible(d) && coverage(d.getBoundingClientRect()) < 0.25 && textOf(d).length >= text.length * 0.9);
      if (inner) add(f, COOKIE_WORD.test(text) ? "cookie" : "promo", "text in backdrop");
      return;
    }
    // A header full of links may still hold a promo strip; never look inside dialogs, alerts or forms.
    if (!["navigation", "many links", "long text"].includes(soft) || isDialog(f)) return;
    for (const d of f.querySelectorAll(":scope > *, :scope > * > *")) {
      if (/^(A|BUTTON|LABEL|LI|INPUT|SELECT|TEXTAREA|NAV|UL|OL)$/.test(d.tagName) || !P.visible(d)) continue;
      const t = textOf(d);
      if (t.length < 20 || PAYWALL.test(t) || isDialog(d) || hardBlock(d) || softBlock(d, t) || !keywordText(t)) continue;
      if (coverage(d.getBoundingClientRect()) >= 0.25) continue;
      add(d, COOKIE_WORD.test(t) ? "cookie" : "promo", "text in sticky bar");
    }
  }

  function shareBar(f) {
    const links = deepAll(f, "a[href]", 30);
    const share = links.filter((a) => SHARE.test(a.getAttribute("href") || ""));
    if (share.length < 2 || share.length / links.length < 0.6) return false;
    if (textOf(f).length > 300 || deepAll(f, "input, select, textarea", 1).length || isDialog(f)) return false;
    return true;
  }

  // Backdrops: big, empty fixed layers next to something we hid. Only when no other dialog is still showing.
  function backdrops(fixed, hits) {
    const hidden = Array.from(hits.keys()).filter((el) => ["cookie", "promo"].includes(hits.get(el).kind));
    if (!hidden.length) return [];
    const siblings = (b) => Array.from(b.parentElement ? b.parentElement.children : []);
    // The hidden hit sitting closest to the backdrop among its siblings (a modal is usually right next to it).
    const near = (b) => {
      const list = siblings(b);
      const at = list.indexOf(b);
      let best = null;
      let dist = Infinity;
      for (const h of hidden) {
        for (let n = h, i = 0; n && i <= 3; n = n.parentElement, i++) {
          const j = list.indexOf(n);
          const d = Math.abs(j - at) - (isDialog(h) ? 0.5 : 0);
          if (j >= 0 && d < dist) {
            dist = d;
            best = h;
          }
        }
      }
      return best;
    };
    const out = [];
    for (const b of fixed) {
      if (hits.has(b) || hidden.some((h) => b.contains(h) || h.contains(b)) || !P.visible(b)) continue;
      if (coverage(b.getBoundingClientRect()) < 0.5 || textOf(b).length > 2) continue;
      if (b.querySelector(P.CLICKABLE) || hardBlock(b)) continue;
      const by = near(b) || (BACKDROP.test(attrText(b)) ? hidden[0] : null);
      if (by) out.push([b, hits.get(by).kind]);
    }
    return out;
  }

  // Returns Map(el -> {kind, why}) of new hits, outermost only, guards applied. Reads only.
  function scan(kinds) {
    const want = new Set(kinds);
    const hits = new Map();
    const add = (el, kind, why) => {
      if (!want.has(kind) || hits.has(el) || P.allyHidden(el)) return;
      hits.set(el, { kind, why });
    };
    const fixed = fixedElements();

    if (want.has("cookie")) {
      for (const el of candidates(CMP)) if (shown(el) && !hardBlock(el)) add(el, "cookie", "consent manager");
      for (const el of candidates("[id], [class]", (e) => COOKIE_ID.test(attrText(e)))) {
        const target = looseCookie(el);
        if (target && shown(target) && !hardBlock(target) && !PAYWALL.test(textOf(target))) add(target, "cookie", "cookie id");
      }
    }
    if (want.has("chat")) {
      for (const el of candidates("iframe, [id], [class], [name], [title]", (e) => CHAT.test(attrText(e)))) {
        if (fixedBox(el, 20) && shown(el) && !hardBlock(el)) add(el, "chat", "chat widget");
      }
    }
    if (want.has("ads")) {
      const ads = candidates(ADS, (e) => !e.hasAttribute("aria-label") || e.matches("ins.adsbygoogle, [id^='google_ads_iframe'], [id^='div-gpt-ad'], iframe, [data-ad-slot]") ||
        (AD_LABEL.test(e.getAttribute("aria-label")) && !e.matches(P.CLICKABLE)));
      for (const el of ads) if (P.visible(el) && !hardBlock(el) && !el.querySelector(NAV)) add(el, "ads", "ad slot");
    }
    if (want.has("share")) {
      for (const f of fixed) if (P.visible(f) && !hardBlock(f) && shareBar(f)) add(f, "share", "share links");
    }
    if (want.has("cookie") || want.has("promo")) {
      for (const f of fixed) if (shown(f)) heuristic(f, add);
      for (const [b, kind] of backdrops(fixed, hits)) if (!dialogShowing(hits, b)) add(b, kind, "backdrop");
    }
    // Keep only the outermost hit of each nested group.
    for (const el of Array.from(hits.keys())) {
      for (let n = el.parentElement; n; n = n.parentElement) {
        if (hits.has(n)) {
          hits.delete(el);
          break;
        }
      }
    }
    return { hits, fixed };
  }

  // A dialog or big overlay is still showing that Ally didn't hide (and isn't about to).
  function dialogShowing(hits, except) {
    const ours = (el) => {
      for (let n = el; n; n = n.parentElement) if (hits && hits.has(n)) return true;
      return false;
    };
    for (const d of document.querySelectorAll(DIALOG)) {
      if (d === except || P.isAlly(d) || P.allyHidden(d) || ours(d) || !P.visible(d)) continue;
      return true;
    }
    return false;
  }

  function overlayShowing(fixed) {
    for (const f of fixed) {
      if (!f.isConnected || P.allyHidden(f) || P.isAlly(f) || !P.visible(f)) continue;
      if (coverage(f.getBoundingClientRect()) >= 0.5) return true;
    }
    return false;
  }

  // ------------------------------------------------------------ scroll lock
  function scrollLock() {
    const html = document.documentElement;
    const body = document.body;
    if (!body) return null;
    const hs = getComputedStyle(html);
    const bs = getComputedStyle(body);
    const locked = hs.overflowY === "hidden" || bs.overflowY === "hidden" || bs.position === "fixed" ||
      LOCK_CLASS.test(html.className) || LOCK_CLASS.test(body.className);
    if (!locked) return null;
    const top = bs.position === "fixed" ? -parseFloat(body.style.top || bs.top) : 0;
    return { y: Number.isFinite(top) && top > 0 ? top : 0 };
  }

  function maybeUnlock(fixed) {
    const html = document.documentElement;
    if (html.hasAttribute(UNLOCK)) return false;
    if (!P.marked("clutter", "cookie").length && !P.marked("clutter", "promo").length) return false;
    const lock = scrollLock();
    if (!lock || dialogShowing(null) || overlayShowing(fixed)) return false;
    html.setAttribute(UNLOCK, "");
    unlockY = lock.y;
    void html.offsetHeight; // apply the unlock before scrolling
    if (lock.y) window.scrollTo({ top: lock.y, left: 0, behavior: "instant" });
    return true;
  }

  function relock() {
    const html = document.documentElement;
    if (!html.hasAttribute(UNLOCK)) return false;
    html.removeAttribute(UNLOCK);
    unlockY = 0;
    return true;
  }

  // ------------------------------------------------------------ module calls
  function kindsFrom(args) {
    const kinds = (args && args.kinds) || PAGE_KINDS;
    return kinds.filter((k) => PAGE_KINDS.includes(k));
  }

  function countMarked(kind) {
    const els = P.marked("clutter", kind);
    const n = els.filter((el) => !backdropMarks.has(el)).length;
    return n || (els.length ? 1 : 0);
  }

  function totals() {
    const out = {};
    for (const k of PAGE_KINDS) {
      const n = countMarked(k);
      if (n) out[k] = n;
    }
    return out;
  }

  function counts(hits) {
    const out = {};
    const backdropOnly = {};
    for (const { kind, why } of hits.values()) {
      if (why === "backdrop") backdropOnly[kind] = true;
      else out[kind] = (out[kind] || 0) + 1;
    }
    for (const k of Object.keys(backdropOnly)) if (!out[k]) out[k] = 1;
    return out;
  }

  function describe(hits) {
    return Array.from(hits, ([el, h]) => ({ kind: h.kind, why: h.why, key: P.key(el) }));
  }

  function detect(args) {
    const { hits } = scan(kindsFrom(args));
    return { found: counts(hits), items: describe(hits) };
  }

  // Marks every hit in one burst, then frees scrolling if a hidden pop-up had locked it.
  // The ally_clutter layer must already be inserted (the service worker does that first).
  function hide(args) {
    const { hits, fixed } = scan(kindsFrom(args));
    for (const [el, h] of hits) {
      P.mark(el, "clutter", h.kind);
      if (h.why === "backdrop") backdropMarks.add(el);
    }
    const unlocked = maybeUnlock(fixed);
    return { hidden: counts(hits), total: totals(), unlocked, items: describe(hits) };
  }

  function show(args) {
    const shownNow = {};
    for (const k of kindsFrom(args)) {
      const n = countMarked(k);
      if (!n) continue;
      P.unmark("clutter", k);
      shownNow[k] = n;
    }
    // Lock scrolling again once the pop-up that locked it is back (the page expects it locked).
    let relocked = false;
    const noPopups = !P.marked("clutter", "cookie").length && !P.marked("clutter", "promo").length;
    if (document.documentElement.hasAttribute(UNLOCK) && (noPopups || dialogShowing(null) || overlayShowing(fixedElements()))) {
      relocked = relock();
    }
    return { shown: shownNow, total: totals(), relocked };
  }

  // The page's main content is a video: motion is left alone.
  function videoMain() {
    for (const v of document.querySelectorAll("video")) {
      if (!P.isAlly(v) && P.visible(v) && coverage(v.getBoundingClientRect()) > 0.4) return true;
    }
    return false;
  }

  function state() {
    return { total: totals(), unlocked: document.documentElement.hasAttribute(UNLOCK), unlockY };
  }

  P.registerOff("clutter", () => {
    relock();
    P.unmark("clutter");
  });

  P.modules.clutter = { detect, hide, show, videoMain, state };
})();
