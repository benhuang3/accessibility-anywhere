// Page sorting for Simplified mode (see docs/page_steps.md): excluded, article, form or general.
// Read-only: it never changes the page.

(() => {
  const P = globalThis.AllyPage;
  if (!P || P.modules.classify) return;

  const FIELDS = "input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=reset]):not([type=image]), select, textarea";
  const CARD = /card.?(num|no|code|holder|exp|cvv|cvc)|^cc[-_]?(num|number|exp|cvv|cvc|csc)|cardnumber|cvv|cvc|\bcsc\b|securitycode|security.code/i;
  const BANK_FIELD = /iban|routing|sort.?code|account.?(number|num|no)\b|acct.?(number|num|no)\b|bank.?account/i;
  const PAY_URL = /checkout|payment|\/pay(\/|$|[?#._-]|ment)|billing|^pay\./i;
  const LOGIN_URL = /signin|sign-in|login|log-in|\/(auth|oauth2?|authorize|authenticate)(\/|$|[?#])/i;
  const BANKS = [
    "chase.com", "bankofamerica.com", "wellsfargo.com", "citi.com", "citibank.com", "usbank.com", "capitalone.com",
    "pnc.com", "truist.com", "tdbank.com", "schwab.com", "fidelity.com", "vanguard.com", "americanexpress.com",
    "discover.com", "ally.com", "navyfederal.org", "usaa.com", "hsbc.com", "hsbc.co.uk", "barclays.co.uk",
    "lloydsbank.com", "natwest.com", "santander.com", "santander.co.uk", "rbc.com", "scotiabank.com", "bmo.com",
    "paypal.com", "venmo.com", "zellepay.com",
  ];
  // Readability's isProbablyReaderable hints.
  const UNLIKELY = /-ad-|ai2html|banner|breadcrumbs|combx|comment|community|cover-wrap|disqus|extra|footer|gdpr|header|legends|menu|related|remark|replies|rss|shoutbox|sidebar|skyscraper|social|sponsor|supplemental|ad-break|agegate|pagination|pager|popup|yom-remote/i;
  const MAYBE = /and|article|body|column|content|main|shadow/i;
  const VIDEO_SRC = /youtube\.com\/embed|youtube-nocookie\.com|youtu\.be|player\.vimeo\.com|vimeo\.com\/video/i;

  function vpArea() {
    return Math.max(1, innerWidth * innerHeight);
  }

  // The part of el's box inside the viewport, as a share of the viewport.
  function viewShare(el) {
    const r = el.getBoundingClientRect();
    const w = Math.max(0, Math.min(r.right, innerWidth) - Math.max(r.left, 0));
    const h = Math.max(0, Math.min(r.bottom, innerHeight) - Math.max(r.top, 0));
    return (w * h) / vpArea();
  }

  function shown(el) {
    return !P.isAlly(el) && !P.allyHidden(el) && P.visible(el);
  }

  function fieldText(el) {
    return [el.name, el.id, el.getAttribute("autocomplete"), el.getAttribute("placeholder"), el.getAttribute("aria-label")]
      .filter(Boolean).join(" ");
  }

  function formControls() {
    return Array.from(document.querySelectorAll(FIELDS)).filter(shown);
  }

  function isBankHost(host) {
    host = host.replace(/^www\./, "");
    return host.includes("bank") || BANKS.some((d) => host === d || host.endsWith(`.${d}`));
  }

  function openDialog() {
    const sel = "[aria-modal=true], [role=dialog], [role=alertdialog], dialog[open]";
    for (const el of document.querySelectorAll(sel)) {
      if (el.closest("[data-ally-clutter]") || !shown(el)) continue;
      const modal = el.getAttribute("aria-modal") === "true" || (el.matches && safeMatches(el, ":modal"));
      // A tiny non-modal dialog (a tooltip or toast) doesn't stop Ally.
      if (modal || viewShare(el) >= 0.15) return el;
    }
    return null;
  }

  function safeMatches(el, sel) {
    try {
      return el.matches(sel);
    } catch {
      return false;
    }
  }

  function isPdf() {
    if (document.contentType === "application/pdf") return true;
    for (const el of document.querySelectorAll("embed[type='application/pdf'], object[type='application/pdf']")) {
      if (viewShare(el) >= 0.8) return true;
    }
    return false;
  }

  function excludedReasons() {
    const reasons = [];
    const add = (r) => reasons.includes(r) || reasons.push(r);
    const all = Array.from(document.querySelectorAll("input, select, textarea")).filter((el) => !P.isAlly(el));
    if (all.some((el) => el.type === "password" && P.visible(el))) add("this page asks for a password");
    const live = all.filter((el) => el.type !== "hidden" && P.visible(el));
    if (live.some((el) => /^cc-/.test(el.getAttribute("autocomplete") || "") || CARD.test(fieldText(el)))) {
      add("this page asks for card details");
    }
    if (live.some((el) => BANK_FIELD.test(fieldText(el)))) add("this page asks for bank details");
    const host = location.hostname.toLowerCase();
    const where = `${host}${location.pathname.toLowerCase()}`;
    if (PAY_URL.test(where)) add("this page handles payments");
    if (LOGIN_URL.test(where)) add("this is a sign-in page");
    if (isBankHost(host)) add("this is a bank's website");
    if (document.getElementById("ally-risk-banner")) add("a safety warning is showing");
    if (openDialog()) add("a pop-up is open");
    if (isPdf()) add("this is a PDF");
    return reasons;
  }

  function articleScore() {
    let score = 0;
    const nodes = new Set(document.querySelectorAll("p, pre, article"));
    for (const br of document.querySelectorAll("div > br")) nodes.add(br.parentNode);
    for (const node of nodes) {
      if (P.isAlly(node) || P.allyHidden(node) || !P.visible(node)) continue;
      const hint = `${typeof node.className === "string" ? node.className : ""} ${node.id}`;
      if (UNLIKELY.test(hint) && !MAYBE.test(hint)) continue;
      if (node.matches("li p")) continue;
      const len = node.textContent.trim().length;
      if (len < 140) continue;
      score += Math.sqrt(len - 140);
    }
    return score;
  }

  function appLike(controls) {
    if (controls > 5) return true;
    if (document.querySelector("[role=application]")) return true;
    return Array.from(document.querySelectorAll("canvas")).some((c) => shown(c) && viewShare(c) >= 0.4);
  }

  function mainVideo() {
    for (const el of document.querySelectorAll("video, iframe")) {
      if (el.tagName === "IFRAME" && !VIDEO_SRC.test(el.src || "")) continue;
      if (!shown(el)) continue;
      const share = viewShare(el);
      if (share <= 0.4) continue;
      const src = el.tagName === "VIDEO" ? el.currentSrc || el.src : el.src;
      const kind = el.tagName === "VIDEO" ? "video" : /vimeo/i.test(src) ? "vimeo" : "youtube";
      return { kind, key: P.key(el), share: Math.round(share * 100) / 100 };
    }
    return null;
  }

  function info() {
    const fields = formControls();
    const score = articleScore();
    const out = {
      type: "general",
      reasons: [],
      articleScore: Math.round(score * 10) / 10,
      mainVideo: mainVideo(),
      formControls: fields.length,
    };
    const excluded = excludedReasons();
    if (excluded.length) return { ...out, type: "excluded", reasons: excluded };
    if (score > 20 && !appLike(fields.length)) return { ...out, type: "article", reasons: ["the page is mostly text"] };
    if (fields.length > 5) return { ...out, type: "form", reasons: [`the page has ${fields.length} form fields`] };
    return out;
  }

  P.modules.classify = { info };
})();
