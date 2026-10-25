// Ally risk content script: sends what the page says to the agent (via background.js and the
// existing WebSocket bridge) and shows a calm warning banner when the agent reports a risk.
// It never calls the agent's HTTP API itself and never reads what the user types into fields.
(() => {
  if (window.__allyRisk) return;
  window.__allyRisk = true;

  const loc = location;
  const skip =
    !/^https?:$/.test(loc.protocol) ||
    ((loc.hostname === "127.0.0.1" || loc.hostname === "localhost") && loc.port === "8765");
  if (skip || window.top !== window) return;

  const MAX_TEXT = 20000;
  const MAX_RESENDS = 6;
  const MAX_FIELDS = 40;

  const SIGNAL_KEYS = [
    "pushState", "replaceState", "backTrap", "beforeunload", "fullscreenRequests",
    "keyboardLock", "contextMenuBlocked", "dialogs", "popups", "pointerLock",
  ];
  const SIGNAL_BOOLS = new Set(["beforeunload", "keyboardLock", "contextMenuBlocked", "pointerLock"]);
  let latestSignals = {};

  function acceptSignals(detail) {
    try {
      const raw = JSON.parse(detail);
      if (!raw || typeof raw !== "object") return;
      const out = {};
      for (const key of SIGNAL_KEYS) {
        const v = raw[key];
        if (SIGNAL_BOOLS.has(key)) {
          if (typeof v === "boolean") out[key] = v;
        } else if (typeof v === "number" && Number.isFinite(v)) {
          out[key] = Math.min(99, Math.max(0, Math.floor(v)));
        }
      }
      latestSignals = out;
      schedule();
    } catch {
      /* hostile or malformed detail */
    }
  }

  document.addEventListener("ally-risk-signals", (e) => {
    if (e && typeof e.detail === "string") acceptSignals(e.detail);
  });

  function signalsSig() {
    return SIGNAL_KEYS.map((k) => {
      const v = latestSignals[k];
      if (v === undefined) return "-";
      return typeof v === "boolean" ? (v ? "1" : "0") : String(v);
    }).join(",");
  }

  // Descriptors only (name, id, placeholder, label, autocomplete, type). What the user typed is never read.
  function describeFields() {
    const out = [];
    for (const el of document.querySelectorAll("input, select, textarea")) {
      if (out.length >= MAX_FIELDS) break;
      const label = (el.labels && el.labels[0] && el.labels[0].innerText) || el.getAttribute("aria-label") || "";
      out.push({
        name: el.name || "", id: el.id || "", placeholder: el.placeholder || "",
        label: label.slice(0, 80), autocomplete: el.getAttribute("autocomplete") || "", type: el.type || "",
      });
    }
    return out;
  }

  // Text of big boxes that sit on top of the page (typical fake "virus" pop-ups), found by position, not by wording.
  function overlayText() {
    const w = window.innerWidth;
    const h = window.innerHeight;
    const parts = [];
    for (const el of document.querySelectorAll("body *")) {
      const cs = getComputedStyle(el);
      if (cs.position !== "fixed" && cs.position !== "absolute") continue;
      if (cs.visibility === "hidden" || cs.display === "none") continue;
      const r = el.getBoundingClientRect();
      if (r.width * r.height < w * h * 0.25) continue;
      const t = (el.innerText || "").trim();
      if (t) parts.push(t.slice(0, 2000));
      if (parts.length >= 3) break;
    }
    return parts.join("\n");
  }

  function snapshot() {
    const text = ((document.body && document.body.innerText) || "").replace(/\n{3,}/g, "\n\n").slice(0, MAX_TEXT);
    return {
      kind: "pageRisk", url: loc.href, title: document.title || "", text,
      overlay_text: overlayText(), fullscreen: !!document.fullscreenElement,
      has_tel_link: !!document.querySelector('a[href^="tel:"]'), fields: describeFields(),
      signals: { ...latestSignals },
    };
  }

  let lastSig = "";
  let resends = 0;

  function report(isResend) {
    const snap = snapshot();
    const sig = `${snap.text.length}:${snap.overlay_text.length}:${snap.fields.length}:${snap.fullscreen}:${signalsSig()}`;
    if (sig === lastSig) return;
    if (isResend && resends >= MAX_RESENDS) return;
    if (isResend) resends += 1;
    lastSig = sig;
    try {
      chrome.runtime.sendMessage(snap, (reply) => {
        void chrome.runtime.lastError;
        // The agent may not be connected yet (worker just woke up): try once more shortly.
        if (!reply || !reply.ok) setTimeout(() => { lastSig = ""; report(true); }, 3000);
      });
    } catch {
      /* extension was reloaded; nothing to do */
    }
  }

  let timer = null;
  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(() => report(true), 2000);
  }

  report(false);
  if (document.body) {
    new MutationObserver(schedule).observe(document.body, { childList: true, subtree: true, characterData: true });
  }

  // ---------------------------------------------------------------- warning banner

  const STYLE = `
    :host { all: initial; }
    .bar {
      position: fixed; top: 0; left: 0; right: 0; z-index: 2147483647;
      box-sizing: border-box; padding: 18px 24px;
      display: flex; flex-wrap: wrap; align-items: center; gap: 16px;
      background: #fff4c2; color: #1a1a1a; border-bottom: 4px solid #8a5a00;
      font: 600 26px/1.35 "Segoe UI", system-ui, sans-serif;
      box-shadow: 0 4px 16px rgba(0,0,0,.25);
    }
    .msg { flex: 1 1 320px; }
    button {
      font: 700 22px "Segoe UI", system-ui, sans-serif; padding: 12px 22px; cursor: pointer;
      border-radius: 8px; border: 3px solid #1a1a1a; background: #ffffff; color: #1a1a1a;
    }
    button.primary { background: #1a1a1a; color: #ffffff; }
    button:focus-visible { outline: 4px solid #0b57d0; outline-offset: 3px; }
  `;

  function showBanner(text) {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    const old = document.getElementById("ally-risk-banner");
    if (old) old.remove();
    const host = document.createElement("div");
    host.id = "ally-risk-banner";
    const root = host.attachShadow({ mode: "closed" });
    const style = document.createElement("style");
    style.textContent = STYLE;
    const bar = document.createElement("div");
    bar.className = "bar";
    bar.setAttribute("role", "alert");
    bar.tabIndex = -1;
    const msg = document.createElement("div");
    msg.className = "msg";
    msg.textContent = String(text || "This page might not be safe.");
    const back = document.createElement("button");
    back.className = "primary";
    back.textContent = "Take me back to safety";
    back.addEventListener("click", () => {
      if (history.length > 1) history.back();
      else loc.href = "about:blank";
    });
    const ok = document.createElement("button");
    ok.textContent = "I understand";
    ok.addEventListener("click", () => host.remove());
    bar.append(msg, back, ok);
    root.append(style, bar);
    (document.documentElement || document.body).append(host);
    bar.focus();
  }

  chrome.runtime.onMessage.addListener((msg) => {
    if (msg && msg.kind === "allyRiskBanner") showBanner(msg.text);
  });
})();
