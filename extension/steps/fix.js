// Ally fix step (service worker): screenshots and a page outline for the AI checker/fixer, and the
// "ally_fix" CSS layer holding the fixer's rules (docs/page_steps.md, "AI check and fix").

PAGE_FILES.push("page/fix.js");

// The only CSS the fixer may use: layout and size properties, never anything that loads or runs things.
const FIX_PROPERTIES = new Set([
  "font-size", "line-height", "letter-spacing", "word-spacing", "max-width", "min-width", "width", "height",
  "min-height", "max-height", "padding", "padding-top", "padding-bottom", "padding-left", "padding-right",
  "margin", "margin-top", "margin-bottom", "margin-left", "margin-right", "display", "flex-wrap",
  "flex-direction", "flex-basis", "flex-shrink", "flex-grow", "grid-template-columns", "gap", "position",
  "overflow", "overflow-x", "overflow-y", "white-space", "word-break", "overflow-wrap", "float", "text-align",
  "box-sizing", "columns",
]);
const FIX_VALUE = /^[a-z0-9.%#(),\s-]{1,40}$/i;
const FIX_ENUMS = {
  display: ["block", "inline-block", "flex", "grid", "none", "inline-flex"],
  position: ["static", "relative"],
  overflow: ["visible", "auto"], "overflow-x": ["visible", "auto"], "overflow-y": ["visible", "auto"],
  float: ["none"],
};
const fixRules = new Map(); // tabId -> rules currently in the layer

function validFixRule(r) {
  if (!r || typeof r !== "object") throw new Error("a fix rule must be {selector, property, value}");
  const selector = String(r.selector || "").trim();
  const property = String(r.property || "").trim().toLowerCase();
  const value = String(r.value || "").trim().toLowerCase();
  if (!selector || selector.length > 200 || /[{};@<>\\]|ally-/i.test(selector)) throw new Error(`selector not allowed: ${selector}`);
  if (!FIX_PROPERTIES.has(property)) throw new Error(`property not allowed: ${property}`);
  if (!FIX_VALUE.test(value) || /url|expression|import|!/.test(value)) throw new Error(`value not allowed: ${value}`);
  if (FIX_ENUMS[property] && !FIX_ENUMS[property].includes(value)) throw new Error(`${property}: ${value} not allowed`);
  return { selector, property, value };
}

function fixCss(rules) {
  return rules.map((r) => `html:not([data-ally-pause~="fix"]) ${r.selector} { ${r.property}: ${r.value} !important; }`).join("\n");
}

STEPS.fix = {
  // opts.rules: [{selector, property, value}]. Adds to the rules already applied on this tab.
  async on(tabId, opts = {}) {
    const rules = (opts.rules || []).map(validFixRule);
    if (!rules.length) return { said: "", rules: 0 };
    const counts = await inPage(tabId, "fix.check", rules);
    const all = [...(fixRules.get(tabId) || []), ...rules];
    await layerOn(tabId, "ally_fix", fixCss(all));
    fixRules.set(tabId, all);
    return { said: "", rules: all.length, matched: counts };
  },
  async off(tabId) {
    fixRules.delete(tabId);
    const removed = await layerOff(tabId, "ally_fix");
    return { said: removed ? "removed the extra fixes" : "" };
  },
  async teardown(tabId) {
    fixRules.delete(tabId);
  },
};

// Chrome allows about 2 screenshots per second per extension; keep them apart and retry once if refused.
const CAPTURE_GAP_MS = 600;
let lastCapture = 0;

async function captureVisible(windowId, quality) {
  for (let attempt = 0; ; attempt++) {
    const wait = lastCapture + CAPTURE_GAP_MS - Date.now();
    if (wait > 0) await new Promise((r) => setTimeout(r, wait));
    lastCapture = Date.now();
    try {
      return await chrome.tabs.captureVisibleTab(windowId, { format: "jpeg", quality });
    } catch (e) {
      if (attempt || !/MAX_CAPTURE_VISIBLE_TAB_CALLS_PER_SECOND|quota/i.test(String((e && e.message) || e))) throw e;
    }
  }
}

// A JPEG of what the user sees in the tab (Ally's own "Simplifying..." card hidden for the picture).
async function captureTab(tab, quality = 60) {
  const hide = (on) => chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: (h) => {
      const card = document.getElementById("ally-simplifying");
      if (card) card.style.setProperty("visibility", h ? "hidden" : "visible", "important");
    },
    args: [on],
  }).catch(() => {});
  await hide(true);
  try {
    await new Promise((r) => setTimeout(r, 120)); // let the hidden card leave the screen
    const url = await captureVisible(tab.windowId, 85);
    return await shrinkJpeg(url, SHOT_WIDTH, clamp(Math.round(quality), 20, 90) / 100);
  } finally {
    await hide(false);
  }
}

// Screens can be 3000+ px wide; 800 px is plenty to judge a layout and makes Claude's answer much faster.
const SHOT_WIDTH = 800;

async function shrinkJpeg(dataUrl, width, quality) {
  try {
    const bitmap = await createImageBitmap(await (await fetch(dataUrl)).blob());
    const scale = Math.min(1, width / bitmap.width);
    const canvas = new OffscreenCanvas(Math.round(bitmap.width * scale), Math.round(bitmap.height * scale));
    canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const bytes = new Uint8Array(await (await canvas.convertToBlob({ type: "image/jpeg", quality })).arrayBuffer());
    let bin = "";
    for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    return btoa(bin);
  } catch (e) {
    console.warn("Ally: couldn't shrink the screenshot:", e);
    return dataUrl.slice(dataUrl.indexOf(",") + 1);
  }
}
