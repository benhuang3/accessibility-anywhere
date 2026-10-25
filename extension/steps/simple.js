// Simplified mode, extension side (see docs/page_steps.md). Python's runner applies the steps and calls
// stepOn({step: "simple"}) last; this file keeps per-tab state, shows the card and chip, turns everything
// off on Undo, remembers sites the user said yes to, and re-applies them on page load in JS only.

PAGE_FILES.push("page/simple.js");

// Off runs in this order (the reverse of the order steps are applied in).
const SIMPLE_OFF_ORDER = ["buttons", "text", "menus", "clutter", "reader"];
const SIMPLE_DEFAULT_STEPS = ["clutter", "menus", "text", "buttons"];
const SIMPLE_HYDRATE_MS = 800;
const SIMPLE_SPA_MS = 1000;
const simpleSpaTimers = new Map(); // tabId -> timer for single-page navigation refresh

// ---------------------------------------------------------------- remembered sites (storage.local "simpleSites")
function simpleOrigin(urlOrOrigin) {
  try {
    const u = new URL(String(urlOrOrigin || ""));
    return /^https?:$/.test(u.protocol) ? u.origin : "";
  } catch {
    return "";
  }
}

async function simpleSites() {
  const { simpleSites = {} } = await chrome.storage.local.get("simpleSites");
  return simpleSites;
}

async function isSimpleSite(url) {
  const origin = simpleOrigin(url);
  return !!origin && !!(await simpleSites())[origin];
}

// Bumped whenever simple mode is turned on or off in a tab; a re-apply waiting for a page to load checks it,
// so a "simple mode off" said during the wait isn't undone a moment later.
const simpleGen = new Map();
const bumpSimple = (tabId) => simpleGen.set(tabId, (simpleGen.get(tabId) || 0) + 1);

async function simpleRemember(origin, steps, scale) {
  const o = simpleOrigin(origin);
  if (!o) throw new Error("Simple mode can only be remembered for normal websites.");
  const sites = await simpleSites();
  const list = Array.isArray(steps) && steps.length ? steps.map(String) : SIMPLE_DEFAULT_STEPS;
  sites[o] = { steps: list, scale: Number(scale) > 1 ? Number(scale) : undefined, at: Date.now() };
  await chrome.storage.local.set({ simpleSites: sites });
  return { origin: o, steps: list };
}

async function simpleForget(origin) {
  const o = simpleOrigin(origin);
  const sites = await simpleSites();
  if (!o || !sites[o]) return false;
  delete sites[o];
  await chrome.storage.local.set({ simpleSites: sites });
  return true;
}

async function simpleForgetAll() {
  const n = Object.keys(await simpleSites()).length;
  await chrome.storage.local.set({ simpleSites: {} });
  return n;
}

async function simpleList() {
  return Object.entries(await simpleSites()).map(([origin, v]) => ({ origin, steps: v.steps || [], at: v.at || 0 }));
}

// ---------------------------------------------------------------- per-tab state (storage.session survives a sleeping worker)
async function simpleTabs() {
  const { simpleTabs = {} } = await chrome.storage.session.get("simpleTabs").catch(() => ({}));
  return simpleTabs;
}

async function simpleTab(tabId) {
  return (await simpleTabs())[tabId] || null;
}

async function setSimpleTab(tabId, rec) {
  const all = await simpleTabs();
  if (rec) all[tabId] = rec;
  else delete all[tabId];
  await chrome.storage.session.set({ simpleTabs: all }).catch(() => {});
}

async function simpleTabUrl(tabId) {
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  return (tab && tab.url) || "";
}

async function simpleShowChip(tabId, text) {
  await inPage(tabId, "simple.chip", { text }).catch(() => {});
}

function simpleChipText(remembered) {
  return remembered ? "Simple mode on for this site" : "Simple mode on";
}

// ---------------------------------------------------------------- STEPS.simple
STEPS.simple = {
  // opts.working true/false only shows or hides the "Simplifying this page…" card.
  // Otherwise: {steps, remember} records what the runner applied and shows the chip.
  async on(tabId, opts = {}) {
    if (typeof opts.working === "boolean") return await STEPS.simple.showWorking(tabId, opts.working);
    const url = await simpleTabUrl(tabId);
    const origin = simpleOrigin(url);
    const prev = (await simpleTab(tabId)) || {};
    const steps = Array.isArray(opts.steps) ? opts.steps.map(String) : prev.steps || [];
    let remembered = origin ? await isSimpleSite(url) : false;
    const scale = Number(opts.scale) > 1 ? Number(opts.scale) : prev.scale;
    if (opts.remember && origin) {
      await simpleRemember(origin, steps.length ? steps : undefined, scale);
      remembered = true;
    }
    const zoomBefore = Number(opts.zoomBefore) > 0 ? Number(opts.zoomBefore) : prev.zoomBefore;
    if (Array.isArray(opts.steps)) bumpSimple(tabId);
    await setSimpleTab(tabId, { origin, steps, zoomBefore, scale, at: Date.now() });
    await simpleMarkDoc(tabId);
    await inPage(tabId, "simple.working", { on: false }).catch(() => {});
    await simpleShowChip(tabId, simpleChipText(remembered));
    return { steps, remembered, said: remembered && opts.remember ? "will keep this site simple" : "" };
  },

  async off(tabId, opts = {}) {
    bumpSimple(tabId);
    const rec = (await simpleTab(tabId)) || {};
    const applied = Array.isArray(rec.steps) && rec.steps.length ? rec.steps : Array.isArray(opts.steps) ? opts.steps : SIMPLE_OFF_ORDER;
    const said = [];
    const errors = [];
    for (const name of SIMPLE_OFF_ORDER) {
      const step = STEPS[name];
      if (!applied.includes(name) || !step || typeof step.off !== "function") continue;
      const stepOpts = {};
      if (name === "text") {
        const restore = Number(opts.restoreZoom) > 0 ? Number(opts.restoreZoom) : rec.zoomBefore;
        if (restore > 0) stepOpts.restoreZoom = restore;
      }
      try {
        const r = await step.off(tabId, stepOpts);
        if (r && r.said) said.push(r.said);
      } catch (e) {
        errors.push(`${name}: ${(e && e.message) || e}`);
      }
    }
    if (STEPS.fix) await STEPS.fix.off(tabId).catch(() => {}); // the AI fixes were made for these steps
    await inPage(tabId, "simple.hide").catch(() => {});
    await simpleMarkDoc(tabId);
    await setSimpleTab(tabId, null);
    let forgot = false;
    if (opts.forget) forgot = await simpleForget(rec.origin || (await simpleTabUrl(tabId)));
    const out = { steps: applied.filter((s) => SIMPLE_OFF_ORDER.includes(s)), forgot, said: "turned simple mode off" };
    if (errors.length) out.errors = errors;
    return out;
  },

  async showWorking(tabId, on) {
    if (on) {
      // Keep the zoom from before Ally measures, so Undo can put it back.
      const rec = (await simpleTab(tabId)) || {};
      if (!rec.steps || !rec.steps.length) {
        const zoomBefore = await chrome.tabs.getZoom(tabId).catch(() => 0);
        await setSimpleTab(tabId, { ...rec, origin: simpleOrigin(await simpleTabUrl(tabId)), steps: [], zoomBefore });
      }
    }
    await simpleMarkDoc(tabId);
    const r = await inPage(tabId, "simple.working", { on: !!on });
    return { working: !!r.working, said: "" };
  },

  async teardown(tabId) {
    bumpSimple(tabId);
    clearTimeout(simpleSpaTimers.get(tabId));
    simpleSpaTimers.delete(tabId);
    await setSimpleTab(tabId, null);
  },
};

// ---------------------------------------------------------------- re-apply in JS only (page load, popup toggle)
async function simpleExcluded(tabId) {
  if (!STEPS.classify || typeof STEPS.classify.info !== "function") return "";
  const info = await STEPS.classify.info(tabId).catch(() => null);
  return info && info.type === "excluded" ? (info.reasons || [])[0] || "this page" : "";
}

async function simpleCallStep(name, fn, tabId, opts) {
  const step = STEPS[name];
  if (!step || typeof step[fn] !== "function") return null;
  try {
    return await step[fn](tabId, opts || {});
  } catch (e) {
    console.warn(`Ally simple mode: ${name}.${fn} failed:`, e);
    return null;
  }
}

// Never measures or changes zoom: Chrome already remembers the zoom per site.
// `fresh` (the popup toggle) turns clutter on instead of only re-detecting remembered kinds.
async function simpleReapply(tabId, steps, { fresh = false, remembered = true, scale } = {}) {
  if (STEPS.clutter && steps.includes("clutter")) {
    if (!fresh && typeof STEPS.clutter.redetect === "function") await simpleCallStep("clutter", "redetect", tabId);
    else await simpleCallStep("clutter", "on", tabId);
  }
  if (steps.includes("menus")) await simpleCallStep("menus", "on", tabId, { mode: "strip" });
  if (steps.includes("buttons")) await simpleCallStep("buttons", "on", tabId);
  const prev = (await simpleTab(tabId)) || {};
  const textScale = Number(scale) > 1 ? Number(scale) : prev.scale || 1.25;
  if (steps.includes("text")) await simpleCallStep("text", "on", tabId, { scale: textScale });
  await setSimpleTab(tabId, { origin: simpleOrigin(await simpleTabUrl(tabId)), steps, zoomBefore: prev.zoomBefore,
    scale: textScale, at: Date.now() });
  await simpleMarkDoc(tabId);
  await simpleShowChip(tabId, simpleChipText(remembered));
}

async function simpleOnLoad(tabId, url) {
  const origin = simpleOrigin(url);
  if (!origin) return;
  const sites = await simpleSites();
  const rec = await simpleTab(tabId);
  // A remembered site, or a link followed inside a site where simple mode is on in this tab.
  const steps = sites[origin] ? sites[origin].steps : rec && rec.origin === origin && rec.steps.length ? rec.steps : null;
  if (!steps) {
    if (rec && rec.origin !== origin) await setSimpleTab(tabId, null); // left the site
    return;
  }
  const scale = (sites[origin] && sites[origin].scale) || (rec && rec.scale);
  const gen = simpleGen.get(tabId) || 0;
  await new Promise((r) => setTimeout(r, SIMPLE_HYDRATE_MS));
  await withTab(tabId, async () => {
    if ((simpleGen.get(tabId) || 0) !== gen) return; // simple mode was turned on or off during the wait
    if (simpleOrigin(await simpleTabUrl(tabId)) !== origin) return; // moved on during the delay
    if (await simpleExcluded(tabId)) {
      await setSimpleTab(tabId, null);
      return;
    }
    await simpleReapply(tabId, steps, { remembered: !!sites[origin], scale });
  });
}

// Single-page navigation: content changed without a new document. The baseline drops itself on URL change.
function simpleOnSpa(tabId) {
  clearTimeout(simpleSpaTimers.get(tabId));
  simpleSpaTimers.set(tabId, setTimeout(async () => {
    simpleSpaTimers.delete(tabId);
    const rec = await simpleTab(tabId);
    if (!rec || !rec.steps.length) return;
    await withTab(tabId, async () => {
      if (rec.steps.includes("clutter")) {
        if (STEPS.clutter && typeof STEPS.clutter.redetect === "function") await simpleCallStep("clutter", "redetect", tabId);
        else await simpleCallStep("clutter", "on", tabId);
      }
      if (rec.steps.includes("menus")) {
        if (STEPS.menus && typeof STEPS.menus.redetect === "function") await simpleCallStep("menus", "redetect", tabId, { mode: "strip" });
        else await simpleCallStep("menus", "on", tabId, { mode: "strip" });
      }
    }).catch(() => {});
  }, SIMPLE_SPA_MS));
}

// Marks the document as one simple mode has already handled. It lives in the isolated world, so it goes away
// with the document: a "complete" event in a marked document is a single-page navigation, not a new page.
async function simpleMarkDoc(tabId) {
  await chrome.scripting.executeScript({ target: { tabId }, func: () => { globalThis.__allySimpleDoc = true; } }).catch(() => {});
}

async function simpleSameDoc(tabId) {
  const [r] = await chrome.scripting.executeScript({ target: { tabId }, func: () => globalThis.__allySimpleDoc === true })
    .catch(() => [null]);
  return !!(r && r.result);
}

async function simpleOnComplete(tabId, url) {
  const origin = simpleOrigin(url);
  if (!origin) return;
  const rec = await simpleTab(tabId);
  if (!rec && !(await simpleSites())[origin]) return;
  if (await simpleSameDoc(tabId)) simpleOnSpa(tabId); // history.pushState also ends with "complete"
  else await simpleOnLoad(tabId, url);
}

chrome.tabs.onUpdated.addListener((tabId, change, tab) => {
  if (change.status === "complete") {
    simpleOnComplete(tabId, tab.url).catch((e) => console.warn("Ally simple mode on load:", e));
  } else if (change.url && !change.status) {
    simpleOnSpa(tabId);
  }
});

chrome.tabs.onRemoved.addListener((tabId) => {
  clearTimeout(simpleSpaTimers.get(tabId));
  simpleSpaTimers.delete(tabId);
  setSimpleTab(tabId, null).catch(() => {});
});

// ---------------------------------------------------------------- messages from the chip and the popup
// The chip's messages come from its page; the popup's from no tab (or ?tab=<id> when opened in a tab).
async function simpleTabFor(msg, sender) {
  if (Number.isInteger(msg.tabId)) return await resolveTab({ tabId: msg.tabId });
  const fromPage = sender && sender.tab && !String(sender.url || "").startsWith("chrome-extension:");
  return fromPage ? sender.tab : await resolveTab({});
}

async function simpleHandleMessage(msg, sender) {
  const tab = await simpleTabFor(msg, sender);
  const tabId = tab.id;
  const origin = simpleOrigin(tab.url);
  switch (msg.op) {
    case "undo": {
      const remembered = await isSimpleSite(tab.url);
      await withTab(tabId, () => STEPS.simple.off(tabId, {}));
      if (remembered) await inPage(tabId, "simple.chip", { ask: true }).catch(() => {});
      return { ok: true, remembered };
    }
    case "forget":
      return { ok: true, forgot: await simpleForget(origin) };
    case "status": {
      const rec = await simpleTab(tabId);
      const sites = await simpleSites();
      return {
        ok: true,
        origin,
        host: origin ? new URL(origin).hostname : "",
        allowed: !!origin,
        remembered: !!sites[origin],
        active: !!(rec && rec.steps && rec.steps.length),
        count: Object.keys(sites).length,
      };
    }
    case "toggle": {
      if (!origin) return { ok: false, error: "Simple mode only works on normal websites." };
      if (msg.on) {
        const why = await simpleExcluded(tabId);
        if (why) return { ok: false, error: `Simple mode stays off here: ${why}.` };
        const known = (await simpleSites())[origin];
        let steps = known ? known.steps : SIMPLE_DEFAULT_STEPS;
        if (!known && STEPS.classify && typeof STEPS.classify.info === "function") {
          const info = await STEPS.classify.info(tabId).catch(() => null);
          if (info && info.type === "form") steps = ["clutter", "text", "buttons"];
        }
        await simpleRemember(origin, steps);
        await withTab(tabId, () => simpleReapply(tabId, steps, { fresh: true }));
        return { ok: true, on: true, steps };
      }
      await withTab(tabId, () => STEPS.simple.off(tabId, { forget: true }));
      return { ok: true, on: false };
    }
    case "forgetAll":
      return { ok: true, forgot: await simpleForgetAll() };
    default:
      return { ok: false, error: "Unknown request" };
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || msg.kind !== "ally-simple") return false;
  simpleHandleMessage(msg, sender).then(sendResponse, (e) => sendResponse({ ok: false, error: String((e && e.message) || e) }));
  return true;
});
