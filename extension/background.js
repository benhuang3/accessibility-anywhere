// Ally extension service worker.
// Keeps a WebSocket open to the Ally agent (ws://127.0.0.1:<port>/ws) and runs page actions for it.
// Works on its own too: keyboard shortcuts and popup buttons for zoom, reset, reader and read-aloud.

const DEFAULT_PORT = 8765;
const ZOOM_LEVELS = [0.25, 0.33, 0.5, 0.67, 0.75, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3, 4, 5];

let ws = null;
let backoff = 1000;
let pingTimer = null;
let reconnectTimer = null;
let nextCommandId = 1;
const pendingReplies = new Map();
let callCreation = Promise.resolve();
const callKey = (tabId) => `allyCall:${tabId}`;

const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, n));

// Page steps (docs/page_steps.md). Each file is loaded on its own so one broken step can't stop the extension.
for (const file of ["steps/core.js", "steps/classify.js", "steps/clutter.js", "steps/menus.js", "steps/enlarge.js",
  "steps/simple.js", "steps/fix.js", "steps/picture.js", "steps/clicks.js"]) {
  try {
    importScripts(file);
  } catch (e) {
    console.warn(`Ally: couldn't load ${file}:`, e);
  }
}

// ---------------------------------------------------------------- connection

async function getPort() {
  try {
    const { port } = await chrome.storage.local.get("port");
    return Number(port) || DEFAULT_PORT;
  } catch {
    return DEFAULT_PORT;
  }
}

function isOpen() {
  return !!ws && ws.readyState === WebSocket.OPEN;
}

let connecting = false;

async function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  // Startup fires connect() from several places at once; without this flag each one passes the check
  // above during the await below and opens its own socket, leaving orphans the agent still talks to.
  if (connecting) return;
  connecting = true;
  clearTimeout(reconnectTimer);
  let sock;
  try {
    const port = await getPort();
    sock = new WebSocket(`ws://127.0.0.1:${port}/ws`);
  } catch {
    scheduleReconnect();
    return;
  } finally {
    connecting = false;
  }
  ws = sock;
  sock.onopen = async () => {
    backoff = 1000;
    send({ type: "hello", version: chrome.runtime.getManifest().version, focused: await chromeFocused(), tab: await tabInfo() });
    clearInterval(pingTimer);
    pingTimer = setInterval(() => send({ type: "ping" }), 20000); // keeps the service worker and link alive
    setBadge(true);
  };
  sock.onmessage = (event) => onMessage(event.data);
  sock.onclose = () => {
    if (ws === sock) ws = null;
    clearInterval(pingTimer);
    setBadge(false);
    scheduleReconnect();
  };
  sock.onerror = () => {}; // onclose always follows
}

function scheduleReconnect() {
  clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(connect, backoff);
  backoff = Math.min(backoff * 2, 15000);
}

function send(msg) {
  if (!isOpen()) return false;
  try {
    ws.send(JSON.stringify(msg));
    return true;
  } catch {
    return false;
  }
}

function setBadge(on) {
  chrome.action.setBadgeText({ text: on ? "" : "off" }).catch(() => {});
  chrome.action.setBadgeBackgroundColor({ color: "#b00020" }).catch(() => {});
  chrome.action.setTitle({ title: on ? "Ally: connected" : "Ally: agent not running (basic controls still work)" }).catch(() => {});
}

async function onMessage(raw) {
  let msg;
  try {
    msg = JSON.parse(raw);
  } catch {
    return;
  }
  if (msg.type === "call") {
    try {
      const fn = ACTIONS[msg.action];
      if (!fn) throw new Error(`Unknown action: ${msg.action}`);
      const result = await fn(msg.args || {});
      send({ type: "result", id: msg.id, ok: true, result: result === undefined ? null : result });
    } catch (e) {
      send({ type: "result", id: msg.id, ok: false, error: friendlyError(e) });
    }
  } else if (msg.type === "reply") {
    const resolve = pendingReplies.get(msg.id);
    if (resolve) {
      pendingReplies.delete(msg.id);
      resolve(msg);
    }
  }
}

function friendlyError(e) {
  const m = String((e && e.message) || e);
  if (/Cannot access|cannot be scripted|chrome:\/\/|chrome-extension:\/\/|extensions gallery|Missing host permission|webstore/i.test(m)) {
    return "Chrome doesn't let extensions change this page (for example chrome:// pages or the Chrome Web Store). Try a normal website.";
  }
  if (/No tab/i.test(m)) return "There's no open Chrome tab.";
  return m;
}

// ---------------------------------------------------------------- tabs

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tab) throw new Error("No tab is open.");
  return tab;
}

async function tabInfo() {
  try {
    const t = await activeTab();
    return { title: t.title || "", url: t.url || "", zoom: await chrome.tabs.getZoom(t.id) };
  } catch {
    return {};
  }
}

async function chromeFocused() {
  try {
    const w = await chrome.windows.getLastFocused();
    return !!(w && w.focused);
  } catch {
    return false;
  }
}

function stepZoom(current, dir) {
  if (dir > 0) return ZOOM_LEVELS.find((z) => z > current + 0.001) ?? ZOOM_LEVELS[ZOOM_LEVELS.length - 1];
  const lower = ZOOM_LEVELS.filter((z) => z < current - 0.001);
  return lower.length ? lower[lower.length - 1] : ZOOM_LEVELS[0];
}

// ---------------------------------------------------------------- page styles
// The record of inserted styles lives in the page's isolated world (globalThis.__allyStyles),
// so it disappears with the document: navigation can never leave it out of date.

async function docStyles(tabId) {
  const [r] = await chrome.scripting.executeScript({ target: { tabId }, func: () => globalThis.__allyStyles || {} });
  return (r && r.result) || {};
}

async function saveDocStyles(tabId, map) {
  await chrome.scripting.executeScript({ target: { tabId }, func: (m) => { globalThis.__allyStyles = m; }, args: [map] });
}

async function insertStyle(tabId, name, css) {
  const applied = await docStyles(tabId);
  if (applied[name] === css) return;
  if (applied[name]) {
    await chrome.scripting.removeCSS({ target: { tabId, allFrames: true }, css: applied[name] }).catch(() => {});
  }
  await chrome.scripting.insertCSS({ target: { tabId, allFrames: true }, css });
  applied[name] = css;
  await saveDocStyles(tabId, applied);
}

async function removeStyleFrom(tabId, name) {
  const applied = await docStyles(tabId);
  if (!applied[name]) return false;
  await chrome.scripting.removeCSS({ target: { tabId, allFrames: true }, css: applied[name] }).catch(() => {});
  delete applied[name];
  await saveDocStyles(tabId, applied);
  return true;
}

async function clearStylesFrom(tabId) {
  const applied = await docStyles(tabId);
  for (const css of Object.values(applied)) {
    await chrome.scripting.removeCSS({ target: { tabId, allFrames: true }, css }).catch(() => {});
  }
  await saveDocStyles(tabId, {});
}

// Apply the profile's global styles, the site's remembered styles and the profile zoom on page load.
async function applySaved(tab) {
  if (!tab || !/^https?:/i.test(tab.url || "")) return;
  const { profile = {}, sites = {} } = await chrome.storage.local.get(["profile", "sites"]);
  const library = profile.styles || {};
  let origin = "";
  try {
    origin = new URL(tab.url).origin;
  } catch {
    return;
  }
  const names = new Set([...(profile.globalStyles || []), ...(sites[origin] || [])]);
  for (const name of names) {
    if (library[name]) await insertStyle(tab.id, name, library[name]).catch(() => {});
  }
  if (profile.pageZoom > 0) {
    const z = await chrome.tabs.getZoom(tab.id).catch(() => 0);
    if (Math.abs(z - 1) < 0.001) await chrome.tabs.setZoom(tab.id, clamp(profile.pageZoom, 0.25, 5)).catch(() => {});
  }
}

// ---------------------------------------------------------------- functions injected into pages
// (Each must be self-contained: Chrome serializes it and runs it inside the page.)

function pageText(maxChars, selectionOnly) {
  const sel = String(window.getSelection ? window.getSelection() : "").trim();
  const base = { title: document.title, url: location.href };
  if (sel || selectionOnly) {
    return { ...base, kind: sel ? "selection" : "none", text: sel.slice(0, maxChars), truncated: sel.length > maxChars };
  }
  let best = null;
  let bestLen = 0;
  for (const el of document.querySelectorAll("article, main, [role=main]")) {
    const n = (el.innerText || "").length;
    if (n > bestLen) {
      best = el;
      bestLen = n;
    }
  }
  const root = bestLen > 400 ? best : document.body;
  let text = (root && root.innerText) || "";
  text = text.replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
  return { ...base, kind: "page", text: text.slice(0, maxChars), truncated: text.length > maxChars };
}

function toggleReader(on, fontPx, customTitle, customText) {
  if (typeof globalThis.__allyReaderClose === "function") globalThis.__allyReaderClose();
  if (!on) return false;
  let text = typeof customText === "string" ? customText.trim() : "";
  if (!text) {
    let best = null;
    let bestLen = 0;
    for (const el of document.querySelectorAll("article, main, [role=main]")) {
      const n = (el.innerText || "").length;
      if (n > bestLen) {
        best = el;
        bestLen = n;
      }
    }
    const source = bestLen > 400 ? best : document.body;
    text = ((source && source.innerText) || "").trim();
  }
  const host = document.createElement("div");
  host.id = "ally-reader-host";
  host.setAttribute("style", "all: initial; position: fixed; inset: 0; z-index: 2147483647;");
  const root = host.attachShadow({ mode: "open" });
  const style = document.createElement("style");
  style.textContent = `
    .wrap { position: fixed; inset: 0; overflow: auto; background: #111; color: #f5f5f5;
            font: ${Number(fontPx) || 26}px/1.6 Verdana, "Atkinson Hyperlegible", Arial, sans-serif; }
    article { max-width: 36em; margin: 0 auto; padding: 2.5em 1.2em 6em; }
    h1 { font-size: 1.35em; line-height: 1.3; margin: 0 0 1em; color: #fff; }
    p { margin: 0 0 1em; letter-spacing: 0.02em; word-spacing: 0.08em; }
    button { position: fixed; top: 12px; right: 16px; font: 700 18px Arial, sans-serif; padding: 10px 16px;
             background: #ffeb3b; color: #000; border: 2px solid #000; border-radius: 8px; cursor: pointer; }
    button:focus-visible { outline: 4px solid #4fc3f7; outline-offset: 2px; }
    @media (prefers-color-scheme: light) { .wrap { background: #fffbea; color: #111; } h1 { color: #000; } }`;
  const wrap = document.createElement("div");
  wrap.className = "wrap";
  wrap.setAttribute("role", "dialog");
  wrap.setAttribute("aria-label", "Reader view");
  wrap.tabIndex = -1;
  const button = document.createElement("button");
  button.textContent = "Close (Esc)";
  const article = document.createElement("article");
  const h1 = document.createElement("h1");
  h1.textContent = customTitle || document.title || "Reader view";
  article.appendChild(h1);
  for (const line of text.split(/\n+/)) {
    const t = line.trim();
    if (t.length < 2) continue;
    const p = document.createElement("p");
    p.textContent = t;
    article.appendChild(p);
  }
  wrap.append(button, article);
  root.append(style, wrap);
  const prevOverflow = document.documentElement.style.overflow;
  const onKey = (e) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      close();
    }
  };
  function close() {
    host.remove();
    document.removeEventListener("keydown", onKey, true);
    document.documentElement.style.overflow = prevOverflow;
    globalThis.__allyReaderClose = null;
  }
  button.addEventListener("click", close);
  document.addEventListener("keydown", onKey, true);
  document.documentElement.style.overflow = "hidden";
  document.documentElement.appendChild(host);
  globalThis.__allyReaderClose = close;
  wrap.focus();
  return true;
}

function scrollPage(direction, amount) {
  const el = document.scrollingElement || document.documentElement;
  const h = window.innerHeight * amount;
  if (direction === "top") window.scrollTo({ top: 0 });
  else if (direction === "bottom") window.scrollTo({ top: el.scrollHeight });
  else window.scrollBy({ top: direction === "up" ? -h : h });
  return Math.round(window.scrollY);
}

// ---------------------------------------------------------------- actions (called by the agent)

// Ring in the corner first; save docking and restoration details across worker restarts.
// Returns null when the work area cannot be determined, keeping the maximized fallback.
async function openDockedCall(url) {
  try {
    const browser = await chrome.windows.getLastFocused({ windowTypes: ["normal"] });
    if (!browser || browser.type !== "normal" || browser.state === "minimized" || browser.state === "fullscreen") return null;
    let area = null;
    if (chrome.system && chrome.system.display) {
      const displays = await chrome.system.display.getInfo();
      const cx = browser.left + browser.width / 2, cy = browser.top + browser.height / 2;
      const d = displays.find(x => cx >= x.bounds.left && cx < x.bounds.left + x.bounds.width
        && cy >= x.bounds.top && cy < x.bounds.top + x.bounds.height) || displays[0];
      if (d) area = { left: d.workArea.left, top: d.workArea.top, width: d.workArea.width, height: d.workArea.height };
    }
    if (!area) {
      // No display permission: a maximized window fills the work area (Windows reports a few hidden border pixels).
      if (browser.state !== "maximized") return null;
      const b = browser.left < 0 ? 8 : 0;
      area = { left: Math.max(0, browser.left), top: Math.max(0, browser.top), width: browser.width - 2 * b, height: browser.height - b };
    }
    if (!(area.width >= 800 && area.height >= 500)) return null;
    const win = await chrome.windows.create({ url, type: "popup", focused: true, ...callWindowGeometry(area) });
    return { ...win, callLayout: {
      workArea: area, browserWindowId: browser.id,
      browserBounds: { left: browser.left, top: browser.top, width: browser.width, height: browser.height, state: browser.state },
      expanded: false, browserShrunk: false,
    } };
  } catch {
    return null;
  }
}

function callWindowGeometry(area, expanded = false) {
  const width = expanded ? Math.round(Math.min(520, Math.max(380, area.width * 0.3))) : 360;
  const height = expanded ? area.height : 340;
  const margin = expanded ? 0 : 16;
  return { left: area.left + area.width - width - margin,
    top: expanded ? area.top : area.top + area.height - height - margin, width, height };
}

const ACTIONS = {
  async getTab() {
    return await tabInfo();
  },
  // A scam page that took over the screen would hide Ally's warning: put the window back the way people use it.
  async exitFullscreen({ url }) {
    const tabs = await chrome.tabs.query({});
    let left = 0;
    for (const t of tabs) {
      if (t.url !== url) continue;
      try {
        const w = await chrome.windows.get(t.windowId);
        if (w.state === "fullscreen") {
          await chrome.windows.update(t.windowId, { state: "maximized" });
          left += 1;
        }
      } catch { /* window already gone */ }
    }
    return { left };
  },
  // Agent found a risk on a page and Ally's own overlay is not running: show the in-page banner instead.
  async showRiskBanner({ url, text }) {
    const tabs = await chrome.tabs.query({});
    let shown = 0;
    for (const t of tabs) {
      if (t.url === url) {
        await chrome.tabs.sendMessage(t.id, { kind: "allyRiskBanner", text }).then(() => { shown += 1; }, () => {});
      }
    }
    return { shown };
  },
  // Only after the user said yes in Ally: prefer the source tab if it is still on the same origin.
  async closeTab({ url, tabId }) {
    if (typeof tabId === "number") {
      let tab;
      try { tab = await chrome.tabs.get(tabId); } catch { /* tab was closed */ }
      let sameOrigin = false;
      try {
        const origin = new URL(url).origin;
        sameOrigin = origin !== "null" && new URL(tab.url).origin === origin;
      } catch { /* missing tab or invalid address: use the address fallback */ }
      if (sameOrigin) {
        await chrome.tabs.remove(tabId);
        return { closed: 1 };
      }
    }
    if (typeof url !== "string" || !url) return { closed: 0 };
    const tabs = await chrome.tabs.query({});
    const address = url.split("#")[0];
    const ids = tabs.filter((t) => typeof t.url === "string" && t.url.split("#")[0] === address)
      .map((t) => t.id);
    if (ids.length) await chrome.tabs.remove(ids);
    return { closed: ids.length };
  },
  async setZoom({ factor }) {
    const t = await activeTab();
    await chrome.tabs.setZoom(t.id, clamp(Number(factor) || 1, 0.25, 5));
    return await chrome.tabs.getZoom(t.id);
  },
  async zoomStep({ direction }) {
    const t = await activeTab();
    // While the picture viewer is open, zoom acts on the picture and the page zoom stays as it is.
    if (typeof pictureProbe === "function" && (await pictureProbe(t.id)).open) {
      await withTab(t.id, () => inPage(t.id, "picture.zoom", { direction: direction === "out" ? "out" : "in", closeAtFit: false }));
      return await chrome.tabs.getZoom(t.id);
    }
    const next = stepZoom(await chrome.tabs.getZoom(t.id), direction === "out" ? -1 : 1);
    await chrome.tabs.setZoom(t.id, next);
    return next;
  },
  async setMinFont({ px }) {
    const size = Math.round(Number(px) || 0);
    if (size <= 0) {
      await chrome.fontSettings.clearMinimumFontSize();
      return 0;
    }
    await chrome.fontSettings.setMinimumFontSize({ pixelSize: size });
    const r = await chrome.fontSettings.getMinimumFontSize();
    return r && typeof r.pixelSize === "number" ? r.pixelSize : size;
  },
  async applyStyle({ name, css }) {
    const t = await activeTab();
    await insertStyle(t.id, String(name), String(css));
    return name;
  },
  async removeStyle({ name }) {
    const t = await activeTab();
    return await removeStyleFrom(t.id, String(name));
  },
  async clearStyles() {
    // The user's styles only: Ally's page-step layers (ally_*) have their own off switches.
    const t = await activeTab();
    const applied = await docStyles(t.id);
    for (const name of Object.keys(applied)) if (!/^ally_/.test(name)) await removeStyleFrom(t.id, name);
    return true;
  },
  async getText({ maxChars = 8000, selectionOnly = false }) {
    const t = await activeTab();
    const [r] = await chrome.scripting.executeScript({ target: { tabId: t.id }, func: pageText, args: [Number(maxChars) || 8000, !!selectionOnly] });
    return r ? r.result : null;
  },
  async reader({ on = true, fontPx = 26, withStrip = false }) {
    const t = await activeTab();
    if (typeof STEPS !== "undefined" && STEPS.reader) {
      // The structured reader (headings, links and lists kept); falls back to the plain one below.
      await withTab(t.id, () => (on ? STEPS.reader.on(t.id, { fontPx, withStrip }) : STEPS.reader.off(t.id)));
      return !!on;
    }
    await chrome.scripting.executeScript({ target: { tabId: t.id }, func: toggleReader, args: [!!on, Number(fontPx) || 26] });
    return !!on;
  },
  async showText({ title = "", text = "", fontPx = 26 }) {
    const t = await activeTab();
    await chrome.scripting.executeScript({ target: { tabId: t.id }, func: toggleReader, args: [true, Number(fontPx) || 26, String(title), String(text)] });
    return true;
  },
  async scroll({ direction = "down", amount = 0.8 }) {
    const t = await activeTab();
    const [r] = await chrome.scripting.executeScript({ target: { tabId: t.id }, func: scrollPage, args: [String(direction), clamp(Number(amount) || 0.8, 0.1, 3)] });
    return r ? r.result : null;
  },
  async navigate({ action }) {
    const t = await activeTab();
    if (action === "back" || action === "forward") {
      // From inside the page: Chrome's own Back skips pages the user never clicked or typed on, and someone
      // using only their voice never does, so "go back" would jump past the page they were just on.
      const [r] = await chrome.scripting.executeScript({
        target: { tabId: t.id },
        func: (dir) => {
          const nav = globalThis.navigation;
          const can = nav ? (dir === "back" ? nav.canGoBack : nav.canGoForward) : true;
          if (can) dir === "back" ? history.back() : history.forward();
          return can;
        },
        args: [action],
      }).catch(() => [null]);
      if (!r) {
        // A page extensions can't script (chrome://, the Web Store): Chrome's own buttons.
        await (action === "back" ? chrome.tabs.goBack(t.id) : chrome.tabs.goForward(t.id)).catch(() => {});
        return action;
      }
      if (r.result) return action;
      if (action === "back" && Number.isInteger(t.openerTabId)) {
        const opener = await chrome.tabs.get(t.openerTabId).catch(() => null);
        if (opener) {
          await chrome.tabs.update(opener.id, { active: true });
          await chrome.windows.update(opener.windowId, { focused: true }).catch(() => {});
          return "opener";
        }
      }
      return "none";
    }
    await chrome.tabs.reload(t.id);
    return action;
  },
  async openUrl({ url, newTab = true }) {
    if (!/^https?:\/\//i.test(String(url))) throw new Error("Only http or https addresses can be opened.");
    if (newTab) await chrome.tabs.create({ url });
    else await chrome.tabs.update((await activeTab()).id, { url });
    return url;
  },
  async openCall({ url, name, service = "meet", spacecallOrigin = "" }) {
    const parsed = new URL(url);
    const allowedOrigins = new Set(["https://meet.google.com"]);
    if (service === "spacecall") {
      if (!/^https:\/\/[^/?#:@\\\s]+$/i.test(spacecallOrigin)) throw new Error("Ally Call needs its configured HTTPS origin.");
      allowedOrigins.clear();
      allowedOrigins.add(new URL(spacecallOrigin).origin);
    } else if (service !== "meet") {
      throw new Error("Unknown Ally call service.");
    }
    if (parsed.protocol !== "https:" || !allowedOrigins.has(parsed.origin) || parsed.username || parsed.password || parsed.port) {
      throw new Error("Ally calls need an allowed HTTPS address.");
    }
    // Info requests and load events wait until the new popup's identity is saved.
    const opening = callCreation.then(async () => {
      const docked = service === "spacecall" ? await openDockedCall(url) : null;
      const win = docked || await chrome.windows.create({ url, type: "popup", state: "maximized", focused: true });
      const tab = win.tabs && win.tabs[0];
      try {
        if (!tab || typeof tab.id !== "number") throw new Error("The call tab did not open.");
        const key = service === "spacecall" ? `allySpacecall:${tab.id}` : callKey(tab.id);
        await chrome.storage.session.set({ [key]: {
          tabId: tab.id, windowId: win.id, name: String(name || "your trusted person").slice(0, 30),
          service,
          ...(docked ? docked.callLayout : {}),
        } });
      } catch (e) {
        await chrome.windows.remove(win.id).catch(() => {});
        throw e;
      }
      if (service === "meet" && tab.status === "complete") await insertCallCSS(tab.id);
      return { opened: true, windowId: win.id };
    });
    callCreation = opening.catch(() => {});
    return await opening;
  },
  async rememberSite({ styles = [] }) {
    const t = await activeTab();
    let origin;
    try {
      origin = new URL(t.url).origin;
    } catch {
      throw new Error("This page can't have saved styles.");
    }
    if (!/^https?:/.test(origin)) throw new Error("Saved styles only work on normal websites.");
    const { sites = {}, profile = {} } = await chrome.storage.local.get(["sites", "profile"]);
    if (styles.length) sites[origin] = styles;
    else delete sites[origin];
    await chrome.storage.local.set({ sites });
    const library = profile.styles || {};
    for (const name of styles) if (library[name]) await insertStyle(t.id, name, library[name]);
    return new URL(t.url).hostname;
  },
  async setProfile(p) {
    await chrome.storage.local.set({ profile: p });
    if (p.minFontPx > 0) await chrome.fontSettings.setMinimumFontSize({ pixelSize: Math.round(p.minFontPx) }).catch(() => {});
    if (p.applyNow) await applySaved(await activeTab()).catch(() => {});
    return true;
  },
  async resetPage() {
    const t = await activeTab();
    await chrome.tabs.setZoom(t.id, 0);
    if (typeof pageTeardown === "function") await withTab(t.id, () => pageTeardown(t.id)).catch(() => {});
    await clearStylesFrom(t.id).catch(() => {});
    await chrome.scripting.executeScript({ target: { tabId: t.id }, func: toggleReader, args: [false, 26] }).catch(() => {});
    const { profile = {} } = await chrome.storage.local.get("profile");
    if (profile.minFontPx > 0) await chrome.fontSettings.setMinimumFontSize({ pixelSize: Math.round(profile.minFontPx) });
    else await chrome.fontSettings.clearMinimumFontSize();
    chrome.tts.stop();
    return true;
  },
  async setTabTitle({ title }) {
    const t = await activeTab();
    const [r] = await chrome.scripting.executeScript({
      target: { tabId: t.id },
      func: (next) => {
        if (typeof globalThis.__allyOrigTitle !== "string") globalThis.__allyOrigTitle = document.title;
        document.title = String(next);
        return document.title;
      },
      args: [String(title == null ? "" : title)],
    });
    return { title: r ? r.result : String(title) };
  },
  async rewriteElement({ selector = "title", text = "" }) {
    const t = await activeTab();
    const [r] = await chrome.scripting.executeScript({
      target: { tabId: t.id },
      func: (sel, next) => {
        const el = document.querySelector(sel);
        if (!el) return { ok: true, matched: 0 };
        const edits = (globalThis.__allyTextEdits = globalThis.__allyTextEdits || {});
        if (!(sel in edits)) edits[sel] = el.textContent;
        el.textContent = String(next);
        return { ok: true, matched: 1 };
      },
      args: [String(selector || "title"), String(text == null ? "" : text)],
    });
    return r ? r.result : { ok: true, matched: 0 };
  },
  async restoreText() {
    const t = await activeTab();
    await chrome.scripting.executeScript({
      target: { tabId: t.id },
      func: () => {
        if (typeof globalThis.__allyOrigTitle === "string") {
          document.title = globalThis.__allyOrigTitle;
          delete globalThis.__allyOrigTitle;
        }
        const edits = globalThis.__allyTextEdits || {};
        for (const sel of Object.keys(edits)) {
          const el = document.querySelector(sel);
          if (el) el.textContent = edits[sel];
        }
        globalThis.__allyTextEdits = {};
      },
    });
    return { restored: true };
  },
  async speak({ text, rate = 1 }) {
    chrome.tts.speak(String(text || "").slice(0, 30000), { rate: clamp(Number(rate) || 1, 0.1, 10) });
    return true;
  },
  async stopSpeaking() {
    chrome.tts.stop();
    return true;
  },

  // ---- page steps (docs/page_steps.md). Every page-changing action runs in the tab's queue.
  async pageInfo(args = {}) {
    return await pageInfo(await resolveTab(args));
  },
  async pageBaseline(args = {}) {
    const tab = await resolveTab(args);
    const zoom = await chrome.tabs.getZoom(tab.id);
    return await withTab(tab.id, async () => {
      const b = await inPage(tab.id, "baseline", { zoom, force: !!args.force });
      return { tabId: tab.id, href: b.href, zoom: b.zoom, metrics: b.metrics };
    });
  },
  async auditAtZooms(args = {}) {
    if (typeof auditAtZooms !== "function") throw new Error("The enlarge step isn't loaded.");
    const tab = await resolveTab(args);
    return await withTab(tab.id, () => auditAtZooms(tab, args));
  },
  async setZoomSettled(args = {}) {
    const tab = await resolveTab(args);
    return await withTab(tab.id, () => setZoomSettled(tab.id, Number(args.zoom) || 1));
  },
  async stepOn(args = {}) {
    const tab = await resolveTab(args);
    const step = STEPS[args.step];
    if (!step) throw new Error(`Unknown page step: ${args.step}`);
    return await withTab(tab.id, async () => ({ tabId: tab.id, step: args.step, ...(await step.on(tab.id, args.opts || {})) }));
  },
  async stepOff(args = {}) {
    const tab = await resolveTab(args);
    const step = STEPS[args.step];
    if (!step) throw new Error(`Unknown page step: ${args.step}`);
    return await withTab(tab.id, async () => ({ tabId: tab.id, step: args.step, ...(await step.off(tab.id, args.opts || {})) }));
  },
  async pageVerify(args = {}) {
    const tab = await resolveTab(args);
    const zoom = await chrome.tabs.getZoom(tab.id);
    return await withTab(tab.id, async () => {
      const v = await inPage(tab.id, "verify", { zoom, stickyBudget: args.stickyBudget });
      return { tabId: tab.id, zoom, ok: v.ok, checks: v.checks };
    });
  },
  async pageTeardown(args = {}) {
    const tab = await resolveTab(args);
    return await withTab(tab.id, () => pageTeardown(tab.id));
  },
  // Picture viewer (steps/picture.js, docs/picture_viewer.md).
  async pictureList(args = {}) {
    const tab = await resolveTab(args);
    return await withTab(tab.id, async () => {
      const out = await inPage(tab.id, "picture.list", Number(args.minPx) > 0 ? { minPx: Number(args.minPx) } : {});
      out.win = { ...(out.win || {}), zoom: await chrome.tabs.getZoom(tab.id) };
      return { tabId: tab.id, ...out };
    });
  },
  async pictureStep(args = {}) {
    const tab = await resolveTab(args);
    return await withTab(tab.id, () => inPage(tab.id, "picture.step", { back: !!args.back }));
  },
  async pictureZoom(args = {}) {
    const tab = await resolveTab(args);
    const direction = args.direction === "out" ? "out" : "in";
    return await withTab(tab.id, () => inPage(tab.id, "picture.zoom", { direction, closeAtFit: !!args.closeAtFit }));
  },
  async pictureOutline(args = {}) {
    const tab = await resolveTab(args);
    return await withTab(tab.id, () => inPage(tab.id, "picture.outline", { ids: Array.isArray(args.ids) ? args.ids.map(String) : [] }));
  },
  async pictureRemember(args = {}) {
    const tab = await resolveTab(args);
    return await withTab(tab.id, () => inPage(tab.id, "picture.remember", { descriptions: args.descriptions || {} }));
  },
  async pictureState(args = {}) {
    try {
      const tab = await resolveTab(args);
      return await pictureProbe(tab.id);
    } catch {
      return { open: false, id: null, scale: 1 };
    }
  },
  // Click by voice / by number (steps/clicks.js, docs/click_by_voice.md).
  async clickList(args = {}) {
    const tab = await resolveTab(args);
    const opts = Number(args.max) > 0 ? { max: Number(args.max) } : {};
    return await withTab(tab.id, async () => ({ tabId: tab.id, ...(await inPage(tab.id, "clicks.list", opts)) }));
  },
  async clickShow(args = {}) {
    const tab = await resolveTab(args);
    return await withTab(tab.id, async () => ({ tabId: tab.id, step: "clicks", ...(await STEPS.clicks.on(tab.id, args)) }));
  },
  async clickDo(args = {}) {
    const tab = await resolveTab(args);
    const target = args.n !== undefined && args.n !== null ? { n: Number(args.n) } : { id: String(args.id || ""), gen: Number(args.gen) || 0 };
    const out = await withTab(tab.id, () => inPage(tab.id, "clicks.click", target));
    if (out && out.open && /^https?:/i.test(out.open)) {
      if (out.newTab) await chrome.tabs.create({ url: out.open, openerTabId: tab.id, index: tab.index + 1 });
      else await chrome.tabs.update(tab.id, { url: out.open });
    }
    return out;
  },
  async clickState(args = {}) {
    try {
      const tab = await resolveTab(args);
      return await clickProbe(tab.id);
    } catch {
      return { shown: false, count: 0, items: [] };
    }
  },
  // For the AI checker and fixer (steps/fix.js): what the user sees, and what's on the page.
  async pageScreenshot(args = {}) {
    const tab = await resolveTab(args);
    return { tabId: tab.id, mediaType: "image/jpeg", data: await captureTab(tab, args.quality || 60) };
  },
  async pageOutline(args = {}) {
    const tab = await resolveTab(args);
    return await withTab(tab.id, () => inPage(tab.id, "fix.outline", {}));
  },
  // Simplified mode's remembered sites (steps/simple.js keeps them under the "simpleSites" key).
  async simpleRemember(args = {}) {
    return await simpleRemember(args.origin || (await resolveTab(args)).url, args.steps || []);
  },
  async simpleForget(args = {}) {
    return await simpleForget(args.origin || (await resolveTab(args)).url);
  },
  async simpleForgetAll() {
    return await simpleForgetAll();
  },
  async simpleList() {
    return await simpleList();
  },
};

// ---------------------------------------------------------------- events for the agent

chrome.windows.onFocusChanged.addListener(async (windowId) => {
  const focused = windowId !== chrome.windows.WINDOW_ID_NONE;
  send({ type: "event", name: "focus", focused, tab: focused ? await tabInfo() : undefined });
});

chrome.tabs.onActivated.addListener(async () => {
  send({ type: "event", name: "tab", tab: await tabInfo() });
});

chrome.tabs.onZoomChange.addListener(async () => {
  send({ type: "event", name: "zoom", tab: await tabInfo() });
});

chrome.tabs.onUpdated.addListener(async (tabId, change, tab) => {
  if (change.status === "complete") {
    await callCreation;
    await insertCallCSS(tabId);
    await applySaved(tab).catch(() => {});
    if (tab.active) send({ type: "event", name: "tab", tab: await tabInfo() });
  }
});

chrome.tabs.onUpdated.addListener(async (tabId, change, tab) => {
  if (typeof change.title !== "string") return;
  // Serialize with opening and other title events so a call can expand only once.
  const expanding = callCreation.then(async () => {
    const key = `allySpacecall:${tabId}`;
    const saved = (await chrome.storage.session.get(key))[key];
    if (!saved || saved.expanded || !saved.workArea || saved.windowId !== tab.windowId) return;
    const [page] = await chrome.scripting.executeScript({
      target: { tabId }, func: () => document.documentElement.dataset.allyCall,
    });
    if (page?.result !== "connected") return;
    const panel = callWindowGeometry(saved.workArea, true);
    await chrome.windows.update(saved.windowId, { state: "normal", ...panel });
    saved.expanded = true;
    await chrome.storage.session.set({ [key]: saved });
    try {
      await chrome.windows.update(saved.browserWindowId, {
        state: "normal", left: saved.workArea.left, top: saved.workArea.top,
        width: panel.left - saved.workArea.left, height: saved.workArea.height,
      });
      saved.browserShrunk = true;
      await chrome.storage.session.set({ [key]: saved });
    } catch { /* The expanded call is usable even if Chrome could not be resized. */ }
  });
  callCreation = expanding.catch(() => {});
  await callCreation;
});

async function insertCallCSS(tabId) {
  try {
    const saved = (await chrome.storage.session.get(callKey(tabId)))[callKey(tabId)];
    if (saved) await chrome.scripting.insertCSS({ target: { tabId }, files: ["call.css"] });
  } catch { /* a closed or navigated tab must leave plain Meet usable */ }
}

chrome.windows.onRemoved?.addListener(async (windowId) => {
  try {
    const closing = chrome.storage.session.get(null); // capture records before the End call handler removes them
    await callCreation;
    const saved = { ...await closing, ...await chrome.storage.session.get(null) };
    const keys = Object.keys(saved).filter((key) => (key.startsWith("allyCall:") || key.startsWith("allySpacecall:")) && saved[key].windowId === windowId);
    if (keys.length) {
      for (const key of keys) {
        const call = saved[key];
        if (!call.browserShrunk || !call.browserBounds) continue;
        try {
          const { state, ...bounds } = call.browserBounds;
          await chrome.windows.update(call.browserWindowId, { state: "normal", ...bounds });
          if (state !== "normal") await chrome.windows.update(call.browserWindowId, { state });
        } catch { /* The original browser may have been closed as well. */ }
      }
      await chrome.storage.session.remove(keys);
      send({ type: "event", name: "risk_call_ended", service: saved[keys[0]].service === "spacecall" ? "spacecall" : "meet" });
    }
  } catch { /* the browser may be shutting down */ }
});

// ---------------------------------------------------------------- shortcuts (work without the agent)

async function readPageLocally() {
  const info = await ACTIONS.getText({ maxChars: 30000 });
  if (info && info.text) chrome.tts.speak(info.text, { rate: 1 });
}

chrome.commands.onCommand.addListener(async (command) => {
  try {
    if (command === "zoom-in") await ACTIONS.zoomStep({ direction: "in" });
    else if (command === "zoom-out") await ACTIONS.zoomStep({ direction: "out" });
    else if (command === "reset-page") await ACTIONS.resetPage({});
    else if (command === "read-page") {
      if (!send({ type: "command", id: nextCommandId++, text: "read this page" })) await readPageLocally();
    }
  } catch (e) {
    console.warn("Ally shortcut failed:", friendlyError(e));
  }
});

// ---------------------------------------------------------------- popup

async function sendCommand(text) {
  const id = nextCommandId++;
  if (!send({ type: "command", id, text })) return { ok: false, error: "The Ally agent isn't running. Start it with: python run.py" };
  return await new Promise((resolve) => {
    pendingReplies.set(id, (m) => resolve({ ok: !!m.ok, text: m.text || "" }));
    setTimeout(() => {
      if (pendingReplies.delete(id)) resolve({ ok: false, error: "Ally didn't answer in time." });
    }, 60000);
  });
}

async function handlePopup(msg) {
  switch (msg.op) {
    case "status":
      return { ok: true, connected: isOpen(), port: await getPort() };
    case "setPort": {
      const port = clamp(Math.round(Number(msg.port) || DEFAULT_PORT), 1024, 65535);
      await chrome.storage.local.set({ port });
      if (ws) ws.close();
      else connect();
      return { ok: true, port };
    }
    case "command":
      return await sendCommand(String(msg.text || ""));
    case "action": {
      const fn = ACTIONS[msg.action];
      if (!fn) return { ok: false, error: "Unknown action" };
      return { ok: true, result: await fn(msg.args || {}) };
    }
    case "readLocal":
      await readPageLocally();
      return { ok: true };
    default:
      return { ok: false, error: "Unknown request" };
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && (msg.kind === "allyCallInfo" || msg.kind === "allyCallEnd")) {
    (async () => {
      await callCreation;
      const tabId = sender.tab && sender.tab.id;
      const saved = (await chrome.storage.session.get(callKey(tabId)))[callKey(tabId)];
      if (!saved || saved.windowId !== sender.tab.windowId) return { isCall: false };
      if (msg.kind === "allyCallEnd") {
        await chrome.windows.remove(saved.windowId);
        await chrome.storage.session.remove(callKey(tabId));
      }
      return { isCall: true, name: saved.name };
    })().then(sendResponse, () => sendResponse({ isCall: false }));
    return true;
  }
  if (msg && msg.kind === "pageRisk") {
    // From extension/risk.js: pass the page to the agent over the existing bridge.
    const { url, title, text, fields, overlay_text, fullscreen, has_tel_link, signals } = msg;
    sendResponse({ ok: send({ type: "event", name: "page", url, title, text, fields, overlay_text, fullscreen, has_tel_link, signals, tab_id: sender.tab && sender.tab.id }) });
    return false;
  }
  if (!msg || msg.kind !== "popup") return false;
  handlePopup(msg).then(sendResponse, (e) => sendResponse({ ok: false, error: friendlyError(e) }));
  return true; // answer asynchronously
});

// ---------------------------------------------------------------- start up

chrome.alarms.get("ally-reconnect").then((alarm) => {
  if (!alarm) chrome.alarms.create("ally-reconnect", { periodInMinutes: 0.5 }); // wakes the worker to reconnect
});
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "ally-reconnect") connect();
});
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
connect();
