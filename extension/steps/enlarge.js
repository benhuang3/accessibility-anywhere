// Enlarge steps for the service worker (see docs/page_steps.md).
// text: Chrome zoom plus a safe "fit" layer. buttons: bigger buttons and form fields; never changes zoom.
// auditAtZooms measures the page at several zoom levels so Python (ally/pagekit/zoom.py) can pick one.

PAGE_FILES.push("page/enlarge.js");

const AUDIT_LEVELS = [1.1, 1.25, 1.5, 1.75, 2, 2.5];
const FIT_PAUSE = 'html:not([data-ally-pause~="fit"])';
const BUTTONS_PAUSE = 'html:not([data-ally-pause~="buttons"])';

const enlargeBusy = new Set(); // tab ids with a zoom audit running
const textZoom = new Map(); // tabId -> the zoom the text step set

function prefixed(pause, selectors) {
  return selectors.map((s) => `${pause} ${s}`).join(",\n");
}

const FIT_CSS = `
${prefixed(FIT_PAUSE, ["img", "video"])} {
  max-width: 100% !important;
  height: auto !important;
}
${prefixed(FIT_PAUSE, ["p", "li", "td", "th", "a", "h1", "h2", "h3", "h4", "h5", "h6", "dd", "blockquote"])} {
  overflow-wrap: break-word !important;
}
${prefixed(FIT_PAUSE, [":focus-visible"])} {
  outline: 4px solid #ffbf00 !important;
  outline-offset: 2px !important;
}
`;

// Buttons inside list items, trees and menu bars (expand arrows in a table of contents, menu toggles) are
// compact on purpose: making them 44px tall pushes their rows apart and the list stops lining up.
const COMPACT = ":not(li *, [role=tree] *, [role=treeitem] *, [role=menubar] *, [role=menu] *)";
const BUTTON_LIKE = ["button", "[role=button]", "input[type=submit]", "input[type=button]", "input[type=reset]"]
  .map((b) => `${b}${COMPACT}`);
const FIELDS = [
  "select", "textarea", "input:not([type])", "input[type=text]", "input[type=search]", "input[type=email]",
  "input[type=tel]", "input[type=url]", "input[type=number]", "input[type=password]", "input[type=date]",
];

const BUTTONS_CSS = `
${prefixed(BUTTONS_PAUSE, [...BUTTON_LIKE, ...FIELDS])} {
  min-height: 44px !important;
}
${prefixed(BUTTONS_PAUSE, BUTTON_LIKE)} {
  padding-block: max(0.5em, 6px) !important;
  padding-inline: max(0.9em, 12px) !important;
  outline: 2px solid currentColor !important;
  outline-offset: 1px !important;
}
${prefixed(BUTTONS_PAUSE, FIELDS)} {
  font-size: max(1em, 18px) !important;
}
${prefixed(BUTTONS_PAUSE, BUTTON_LIKE.map((b) => `${b} + :is(${BUTTON_LIKE.join(", ")})`))} {
  margin-inline-start: 0.25em !important;
}
${prefixed(BUTTONS_PAUSE, BUTTON_LIKE.map((b) => `:is([style*="flex"], [class*="flex"], [class*="group"], [role=group], [role=toolbar]) > ${b}`))} {
  margin: 0.25em !important;
}
`;

function assertNotAuditing(tabId) {
  if (enlargeBusy.has(tabId)) throw new Error("Ally is still checking zoom levels on this page. Try again in a moment.");
}

// Default levels: Chrome's zoom steps above the current zoom, up to maxZoom.
function auditLevels(original, args) {
  const maxZoom = Number(args.maxZoom || args.max_zoom) || 2.5;
  const asked = Array.isArray(args.levels) && args.levels.length ? args.levels.map(Number) : AUDIT_LEVELS;
  const levels = asked.filter((z) => z > 0 && z <= maxZoom + 0.001 && Math.abs(z - original) > 0.001);
  if (!Array.isArray(args.levels) || !args.levels.length) return levels.filter((z) => z > original);
  return Array.from(new Set(levels)).sort((a, b) => a - b);
}

async function auditAtZooms(tab, args = {}) {
  const tabId = tab.id; // locked: the whole run works on this tab, whatever becomes active meanwhile
  if (!scriptable(tab)) throw new Error("Chrome doesn't let Ally check this page.");
  assertNotAuditing(tabId);
  enlargeBusy.add(tabId);
  const original = await chrome.tabs.getZoom(tabId);
  const defaultZoom = await zoomDefaults(tabId);
  const audits = [];
  // The text step always adds the fit layer, so measure with it on: a page whose only problem at a bigger
  // zoom is a wide image would otherwise look broken at every level. The baseline (taken first) still
  // measures the page with every Ally layer paused, so verify compares against the real page.
  const fitWasOn = !!(await docStyles(tabId))["ally_fit"];
  try {
    const base = await inPage(tabId, "baseline", { zoom: original });
    if (!fitWasOn) await layerOn(tabId, "ally_fit", FIT_CSS);
    audits.push({ zoom: original, ...(await inPage(tabId, "enlarge.measure")) });
    for (const level of auditLevels(original, args)) {
      const zoom = await setZoomSettled(tabId, level);
      const metrics = await inPage(tabId, "enlarge.measure");
      if (metrics.href !== base.href) throw new Error("The page changed while Ally was checking it.");
      audits.push({ zoom: Math.round(zoom * 1000) / 1000, ...metrics });
    }
  } finally {
    await setZoomSettled(tabId, original).catch(() => {});
    if (!fitWasOn) await layerOff(tabId, "ally_fit").catch(() => {});
    enlargeBusy.delete(tabId);
  }
  return { tabId, url: tab.url || "", original, defaultZoom, audits };
}

STEPS.text = {
  async on(tabId, opts = {}) {
    assertNotAuditing(tabId);
    const before = await chrome.tabs.getZoom(tabId);
    let zoom = before;
    if (Number(opts.zoom) > 0) {
      zoom = await setZoomSettled(tabId, Number(opts.zoom));
      textZoom.set(tabId, zoom);
    }
    await layerOn(tabId, "ally_fit", FIT_CSS);
    const pct = Math.round(zoom * 100);
    return { zoom, before, said: zoom !== before ? `made the page ${pct}% size` : "made the page fit better" };
  },
  async off(tabId, opts = {}) {
    assertNotAuditing(tabId);
    const removed = await layerOff(tabId, "ally_fit");
    const set = textZoom.get(tabId);
    textZoom.delete(tabId);
    let zoom = await chrome.tabs.getZoom(tabId);
    let restored = false;
    const restoreTo = Number(opts.restoreZoom);
    if (restoreTo > 0 && set !== undefined && Math.abs(zoom - set) < 0.001) {
      zoom = await setZoomSettled(tabId, restoreTo);
      restored = true;
    }
    return { zoom, restored, said: restored ? "put the page size back" : removed ? "stopped fitting the page" : "" };
  },
  async teardown(tabId) {
    textZoom.delete(tabId);
  },
};

STEPS.buttons = {
  async on(tabId) {
    await layerOn(tabId, "ally_buttons", BUTTONS_CSS);
    return { said: "made buttons and boxes bigger" };
  },
  async off(tabId) {
    const removed = await layerOff(tabId, "ally_buttons");
    return { said: removed ? "put buttons back to normal" : "" };
  },
};

chrome.tabs.onRemoved.addListener((tabId) => textZoom.delete(tabId));
