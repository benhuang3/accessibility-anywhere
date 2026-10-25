// Page steps foundation for the service worker (see docs/page_steps.md). Loaded by background.js with
// importScripts before the step files; step files register STEPS.<name> = { on(tabId, opts), off(tabId, opts) }.

const STEPS = {};
// Page-side files injected into every page that uses a step, in order. Step files append theirs.
const PAGE_FILES = ["page/runtime.js"];
// CSS layer names Ally's steps own; reset removes all of them, "style none" removes none of them.
const ALLY_LAYER = /^ally_/;
// Must match VERSION in page/runtime.js: a tab still running an older runtime gets every file again.
const RUNTIME_VERSION = 2;

// ---------------------------------------------------------------- one queue per tab
const tabQueues = new Map();

function withTab(tabId, fn) {
  const prev = tabQueues.get(tabId) || Promise.resolve();
  const next = prev.catch(() => {}).then(fn);
  tabQueues.set(tabId, next);
  next.finally(() => {
    if (tabQueues.get(tabId) === next) tabQueues.delete(tabId);
  }).catch(() => {});
  return next;
}

async function resolveTab(args) {
  if (args && Number.isInteger(args.tabId)) {
    const tab = await chrome.tabs.get(args.tabId);
    return tab;
  }
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tab) throw new Error("No tab is open.");
  return tab;
}

function scriptable(tab) {
  return /^https?:|^file:/i.test((tab && tab.url) || "");
}

// ---------------------------------------------------------------- page runtime
async function ensureRuntime(tabId) {
  const [probe] = await chrome.scripting.executeScript({
    target: { tabId },
    func: (files, version) => {
      const p = globalThis.AllyPage;
      if (!p || p.version !== version) return files;
      return files.filter((f) => !(p.loaded || []).includes(f));
    },
    args: [PAGE_FILES, RUNTIME_VERSION],
  });
  const missing = (probe && probe.result) || PAGE_FILES;
  if (!missing.length) return;
  await chrome.scripting.executeScript({ target: { tabId }, files: missing });
  await chrome.scripting.executeScript({
    target: { tabId },
    func: (files) => {
      const p = globalThis.AllyPage;
      if (p) p.loaded = Array.from(new Set([...(p.loaded || []), ...files]));
    },
    args: [missing],
  });
}

// Calls AllyPage.run(path, args) in the page (isolated world) and returns its (awaited) result.
async function inPage(tabId, path, args) {
  await ensureRuntime(tabId);
  const [r] = await chrome.scripting.executeScript({
    target: { tabId },
    func: async (p, a) => {
      try {
        return { ok: true, value: await globalThis.AllyPage.run(p, a) };
      } catch (e) {
        return { ok: false, error: String((e && e.message) || e) };
      }
    },
    args: [String(path), args || {}],
  });
  const out = r && r.result;
  if (!out) throw new Error("The page didn't answer.");
  if (!out.ok) throw new Error(out.error);
  return out.value;
}

// ---------------------------------------------------------------- CSS layers (named, tracked by insertStyle)
async function layerOn(tabId, name, css) {
  if (!ALLY_LAYER.test(name)) throw new Error(`Ally layer names start with ally_: ${name}`);
  await insertStyle(tabId, name, css);
}

async function layerOff(tabId, name) {
  return await removeStyleFrom(tabId, name);
}

async function allyLayersOff(tabId) {
  const applied = await docStyles(tabId);
  for (const name of Object.keys(applied)) if (ALLY_LAYER.test(name)) await removeStyleFrom(tabId, name);
}

// ---------------------------------------------------------------- zoom that waits for the page to settle
async function zoomDefaults(tabId) {
  const s = await chrome.tabs.getZoomSettings(tabId).catch(() => ({}));
  return (s && s.defaultZoomFactor) || 1;
}

// Waits for the page's layout to follow a zoom change: a resize (or innerWidth change), two frames and
// a short pause for the site's own debounced resize handlers. Never hangs: a hidden window may not paint.
async function waitSettled(tabId, prevWidth, ms = 150) {
  await chrome.scripting.executeScript({
    target: { tabId },
    func: (prev, wait) => new Promise((resolve) => {
      const start = Date.now();
      const done = () => setTimeout(resolve, wait);
      const frames = () => requestAnimationFrame(() => requestAnimationFrame(done));
      const check = () => {
        if (innerWidth !== prev || Date.now() - start > 600) frames();
        else setTimeout(check, 30);
      };
      setTimeout(resolve, wait + 1200); // fallback when frames never come
      check();
    }),
    args: [prevWidth, ms],
  }).catch(() => {});
}

async function viewportWidth(tabId) {
  const [r] = await chrome.scripting.executeScript({ target: { tabId }, func: () => innerWidth }).catch(() => [null]);
  return (r && r.result) || 0;
}

async function setZoomSettled(tabId, zoom) {
  const current = await chrome.tabs.getZoom(tabId);
  if (Math.abs(current - zoom) < 0.001) return current;
  const before = await viewportWidth(tabId);
  const deflt = await zoomDefaults(tabId);
  // Zoom equal to Chrome's default: 0 removes the per-site entry instead of saving one.
  await chrome.tabs.setZoom(tabId, Math.abs(zoom - deflt) < 0.001 ? 0 : clamp(zoom, 0.25, 5));
  await waitSettled(tabId, before);
  return await chrome.tabs.getZoom(tabId);
}

// ---------------------------------------------------------------- teardown (reset)
async function pageTeardown(tabId) {
  await chrome.scripting.executeScript({
    target: { tabId },
    func: () => (globalThis.AllyPage ? globalThis.AllyPage.teardown() : false),
  }).catch(() => {});
  for (const step of Object.values(STEPS)) {
    if (typeof step.teardown === "function") await step.teardown(tabId).catch(() => {});
  }
  await allyLayersOff(tabId).catch(() => {});
  return true;
}

// ---------------------------------------------------------------- page type (filled in by steps/classify.js)
async function pageInfo(tab) {
  const tabId = tab.id;
  const zoom = await chrome.tabs.getZoom(tabId);
  const defaultZoom = await zoomDefaults(tabId);
  const base = { tabId, url: tab.url || "", zoom, defaultZoom, type: "general", reasons: [], articleScore: 0, simple: false };
  if (!scriptable(tab)) return { ...base, type: "excluded", reasons: ["Chrome doesn't let extensions change this page"] };
  if (STEPS.classify && typeof STEPS.classify.info === "function") {
    Object.assign(base, await STEPS.classify.info(tabId));
  }
  if (typeof isSimpleSite === "function") base.simple = await isSimpleSite(tab.url);
  if (STEPS.text && typeof STEPS.text.scaleOf === "function") base.textScale = await STEPS.text.scaleOf(tabId);
  return base;
}
