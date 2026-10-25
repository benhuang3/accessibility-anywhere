// Simplified mode overlays (see docs/page_steps.md): the "Simplifying this page…" card shown while Ally
// measures, and the "Simple mode on · Undo" chip shown while simple mode is on. Both hide while Ganesh's
// #ally-risk-banner is on the page and always stay below it.

(() => {
  const A = globalThis.AllyPage;
  if (!A || A.modules.simple) return;

  const CARD_ID = "ally-simplifying";
  const CHIP_ID = "ally-simple-chip";
  const CARD_MAX_MS = 15000;
  const ASK_MAX_MS = 20000;
  const Z = "2147483646"; // one below the top: the risk banner may sit above

  const CSS = `
    :host { all: initial; }
    .card-wrap {
      position: fixed; inset: 0; display: flex; align-items: center; justify-content: center;
      background: rgba(250, 250, 247, 0.97); color: #1a1a1a;
      font: 600 30px/1.4 system-ui, -apple-system, "Segoe UI", Arial, sans-serif;
    }
    .card {
      display: flex; align-items: center; gap: 22px; padding: 32px 44px; border-radius: 18px;
      background: #ffffff; border: 2px solid #c9c9c4; box-shadow: 0 8px 30px rgba(0, 0, 0, 0.12);
    }
    .dot { width: 22px; height: 22px; border-radius: 50%; background: #1b3fa0; animation: pulse 1.6s ease-in-out infinite; }
    @keyframes pulse { 0%, 100% { opacity: 0.35; } 50% { opacity: 1; } }
    @media (prefers-reduced-motion: reduce) { .dot { animation: none; } }
    .chip {
      position: fixed; left: 16px; bottom: 16px; display: flex; align-items: center; flex-wrap: wrap; gap: 10px;
      min-height: 56px; max-width: calc(100vw - 32px); box-sizing: border-box; padding: 6px 8px 6px 18px;
      border-radius: 14px; background: #1a1a1a; color: #ffffff; box-shadow: 0 4px 18px rgba(0, 0, 0, 0.3);
      font: 600 20px/1.3 system-ui, -apple-system, "Segoe UI", Arial, sans-serif;
    }
    .sep { opacity: 0.7; }
    button {
      all: unset; box-sizing: border-box; min-height: 48px; min-width: 64px; padding: 0 18px; border-radius: 10px;
      background: #ffffff; color: #1a1a1a; font: 700 20px/48px system-ui, -apple-system, "Segoe UI", Arial, sans-serif;
      text-align: center; cursor: pointer;
    }
    button.plain { background: transparent; color: #ffffff; border: 2px solid #ffffff; line-height: 44px; }
    button:focus-visible { outline: 4px solid #ffbf00; outline-offset: 2px; }
    @media (prefers-color-scheme: dark) {
      .card-wrap { background: rgba(18, 18, 18, 0.97); color: #f2f2f2; }
      .card { background: #222222; border-color: #555555; }
      .dot { background: #8fb0ff; }
    }
  `;

  let sheet = null;
  let cardTimer = 0;
  let askTimer = 0;
  let observer = null;

  function styles() {
    if (!sheet) {
      sheet = new CSSStyleSheet();
      sheet.replaceSync(CSS);
    }
    return sheet;
  }

  function riskShowing() {
    return !!document.getElementById("ally-risk-banner");
  }

  function hosts() {
    return [CARD_ID, CHIP_ID].map((id) => document.getElementById(id)).filter(Boolean);
  }

  // Hide our hosts while the risk banner is up; show them again when it goes.
  function syncRisk() {
    const hide = riskShowing();
    for (const host of hosts()) host.style.setProperty("display", hide ? "none" : "block", "important");
  }

  function watchRisk() {
    if (observer) return;
    observer = new MutationObserver(syncRisk);
    observer.observe(document.documentElement, { childList: true });
    if (document.body) observer.observe(document.body, { childList: true });
  }

  function unwatchRisk() {
    if (observer && !hosts().length) {
      observer.disconnect();
      observer = null;
    }
  }

  function makeHost(id) {
    let host = document.getElementById(id);
    if (host && host.shadowRoot) return host;
    if (host) host.remove();
    host = document.createElement("div");
    host.id = id;
    for (const [k, v] of [["position", "fixed"], ["z-index", Z], ["inset", "auto"], ["width", "0"], ["height", "0"]]) {
      host.style.setProperty(k, v, "important");
    }
    const root = host.attachShadow({ mode: "open" });
    root.adoptedStyleSheets = [styles()];
    (document.body || document.documentElement).append(host);
    watchRisk();
    syncRisk();
    return host;
  }

  function removeHost(id) {
    const host = document.getElementById(id);
    if (host) host.remove();
    unwatchRisk();
  }

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text) n.textContent = text;
    return n;
  }

  // ------------------------------------------------------------ "Simplifying this page…" card
  function working({ on = true } = {}) {
    clearTimeout(cardTimer);
    if (!on) {
      removeHost(CARD_ID);
      return { working: false };
    }
    const host = makeHost(CARD_ID);
    const root = host.shadowRoot;
    if (!root.querySelector(".card-wrap")) {
      const wrap = el("div", "card-wrap");
      const card = el("div", "card");
      card.setAttribute("role", "status");
      card.append(el("span", "dot"), el("span", "", "Simplifying this page…"));
      wrap.append(card);
      root.replaceChildren(wrap);
    }
    cardTimer = setTimeout(() => removeHost(CARD_ID), CARD_MAX_MS);
    // A demo page can ask for a pause here, so the audience sees the cluttered page and the card before the reveal:
    // <meta name="ally-demo" content="simple-mode" data-delay-ms="2500"> (demo/simple-mode).
    const demo = document.querySelector('meta[name="ally-demo"][data-delay-ms]');
    const delayMs = demo ? Math.max(0, Math.min(5000, Number(demo.getAttribute("data-delay-ms")) || 0)) : 0;
    return { working: true, delayMs };
  }

  // ------------------------------------------------------------ "Simple mode on · Undo" chip
  function send(op) {
    try {
      chrome.runtime.sendMessage({ kind: "ally-simple", op }).catch(() => {});
    } catch {
      // the extension was reloaded: nothing to talk to
    }
  }

  function chip({ text = "Simple mode on", ask = false } = {}) {
    clearTimeout(askTimer);
    const host = makeHost(CHIP_ID);
    const box = el("div", "chip");
    box.setAttribute("role", "region");
    box.setAttribute("aria-label", "Ally simple mode");
    box.dataset.mode = ask ? "ask" : "on";
    if (ask) {
      const yes = el("button", "", "Yes");
      const no = el("button", "plain", "No");
      yes.addEventListener("click", () => {
        removeHost(CHIP_ID);
        send("forget");
      });
      no.addEventListener("click", () => removeHost(CHIP_ID));
      box.append(el("span", "msg", "Turn off for this site too?"), yes, no);
      askTimer = setTimeout(() => removeHost(CHIP_ID), ASK_MAX_MS);
    } else {
      const undo = el("button", "", "Undo");
      undo.addEventListener("click", () => {
        undo.disabled = true;
        send("undo");
      });
      const sep = el("span", "sep", "·");
      sep.setAttribute("aria-hidden", "true");
      box.append(el("span", "msg", String(text)), sep, undo);
    }
    host.shadowRoot.replaceChildren(box);
    return { chip: box.dataset.mode };
  }

  function hide() {
    clearTimeout(cardTimer);
    clearTimeout(askTimer);
    removeHost(CARD_ID);
    removeHost(CHIP_ID);
    return true;
  }

  // For checks and tests: what is showing now.
  function state() {
    const c = document.getElementById(CHIP_ID);
    const box = c && c.shadowRoot && c.shadowRoot.querySelector(".chip");
    return {
      working: !!document.getElementById(CARD_ID),
      chip: box ? box.dataset.mode : "",
      text: box ? box.textContent : "",
      hiddenForRisk: riskShowing(),
    };
  }

  A.registerOff("simple", () => {
    hide();
    if (observer) observer.disconnect();
    observer = null;
  });
  A.modules.simple = { working, chip, hide, state };
})();
