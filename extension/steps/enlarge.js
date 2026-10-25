// Enlarge steps for the service worker (see docs/page_steps.md).
// text: bigger fonts (the page's own text, via page/text.js and the "ally_text" layer) plus a safe "fit"
// layer; never zooms. buttons: bigger buttons and form fields; never zooms. Only the zoom tools zoom.
// auditAtZooms (measuring at several zoom levels) is kept for diagnostics; the text step doesn't use it.

PAGE_FILES.push("page/enlarge.js", "page/text.js");
const TEXT_PAUSE = 'html:not([data-ally-pause~="text"])';

// One rule per original size: 14px text becomes 21px, 32px headings 48px, and so on.
function textCss(sizes) {
  return Object.entries(sizes || {})
    .map(([px, to]) => `${TEXT_PAUSE} [data-ally-text="${px}"] { font-size: ${to}px !important; line-height: 1.4 !important; }`)
    .concat([`${TEXT_PAUSE} [data-ally-text-grow] { height: auto !important; max-height: none !important; }`])
    .join("\n");
}

const AUDIT_LEVELS = [1.1, 1.25, 1.5, 1.75, 2, 2.5];
const FIT_PAUSE = 'html:not([data-ally-pause~="fit"])';
const BUTTONS_PAUSE = 'html:not([data-ally-pause~="buttons"])';

const enlargeBusy = new Set(); // tab ids with a zoom audit running

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
// Links made to look like buttons ("Sign up", "Donate", <a class="usa-button">) get the same treatment.
const LINK_BUTTONS = ["a[role=button]", 'a[class*="btn" i]', 'a[class*="button" i]'];
const BUTTON_LIKE = ["button", "[role=button]", "input[type=submit]", "input[type=button]", "input[type=reset]",
  ...LINK_BUTTONS].map((b) => `${b}${COMPACT}`);
const FIELDS = [
  "select", "textarea", "input:not([type])", "input[type=text]", "input[type=search]", "input[type=email]",
  "input[type=tel]", "input[type=url]", "input[type=number]", "input[type=password]", "input[type=date]",
];

const BUTTONS_CSS = `
${prefixed(BUTTONS_PAUSE, [...BUTTON_LIKE, ...FIELDS])} {
  min-height: 44px !important;
  height: auto !important; /* a fixed site height would cut off the bigger label */
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
  // opts.scale: how much bigger (1.25 = a quarter bigger); goalPx: the size small body text should reach.
  async on(tabId, opts = {}) {
    const scale = Math.min(3, Math.max(1, Number(opts.scale) || 1.3));
    const r = await inPage(tabId, "text.apply", { scale, goalPx: Number(opts.goalPx) || 20, maxPx: Number(opts.maxPx) || 72 });
    await layerOn(tabId, "ally_text", textCss(r.sizes));
    await layerOn(tabId, "ally_fit", FIT_CSS);
    const grown = await inPage(tabId, "text.grow").catch(() => 0); // after the layer: needs the new layout
    return { scale, marked: r.marked, grown, said: `made the text ${Math.round((scale - 1) * 100)}% bigger` };
  },
  async off(tabId) {
    const removed = await layerOff(tabId, "ally_text");
    await layerOff(tabId, "ally_fit");
    await inPage(tabId, "text.clear").catch(() => {});
    return { scale: 1, said: removed ? "put the text back to its size" : "" };
  },
  async scaleOf(tabId) {
    try {
      return (await inPage(tabId, "text.state")).scale || 1;
    } catch {
      return 1;
    }
  },
};

// Each label clearly bigger than itself: 1.3x and at least 3px more (12px -> 16px, 16px -> 21px), never past 32px.
// Text the text step enlarges (data-ally-text) keeps that bigger size, whichever step ran first.
function labelSize(px) {
  return Math.max(px, Math.min(Math.max(Math.ceil(px * 1.3 - 0.01), px + 3), Math.max(px, 32))); // up, so >= 30%
}

function buttonLabelCss(sizes) {
  return (sizes || [])
    .map((px) => `${BUTTONS_PAUSE} [data-ally-buttons="${px}"]:not([data-ally-text]) { font-size: ${labelSize(px)}px !important; }`)
    .join("\n");
}

// Each button at least 15% wider and taller than itself (about 32% more area), and at least 44px tall.
const BOX_GROWTH = 1.15;

function buttonBoxCss(boxes) {
  return (boxes || []).map((wh) => {
    const [w, h] = wh.split("x").map(Number);
    // The repeated attribute outranks the general "min-height: 44px" rule above (its :not() lists add specificity).
    const sel = `[data-ally-buttons-box="${wh}"][data-ally-buttons-box][data-ally-buttons]`;
    return `${BUTTONS_PAUSE} ${sel} { min-width: ${Math.round(w * BOX_GROWTH)}px !important; ` +
      `min-height: ${Math.max(44, Math.round(h * BOX_GROWTH))}px !important; }`;
  }).join("\n");
}

STEPS.buttons = {
  async on(tabId) {
    const r = await inPage(tabId, "enlarge.markButtons", { selector: BUTTON_LIKE.join(", ") });
    await layerOn(tabId, "ally_buttons", `${BUTTONS_CSS}\n${buttonLabelCss(r.sizes)}\n${buttonBoxCss(r.boxes)}`);
    return { said: "made buttons and boxes bigger", marked: r.marked };
  },
  async off(tabId) {
    const removed = await layerOff(tabId, "ally_buttons");
    await inPage(tabId, "enlarge.clearButtons").catch(() => {});
    return { said: removed ? "put buttons back to normal" : "" };
  },
};

