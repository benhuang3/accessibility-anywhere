// Hide clutter step (docs/page_steps.md): cookie banners, promo pop-ups, chat buttons, ads, share bars and
// motion. The page side (page/clutter.js) marks elements; the ally_clutter layer hides them. Motion is its
// own layer, ally_motion, so it can be turned off on its own.

PAGE_FILES.push("page/clutter.js");

const CLUTTER_KINDS = ["cookie", "promo", "chat", "ads", "share", "motion"];
const CLUTTER_ON = 'html:not([data-ally-pause~="clutter"])';
const MOTION_ON = 'html:not([data-ally-pause~="motion"])';

const CLUTTER_CSS = `${CLUTTER_ON} [data-ally-clutter] { display: none !important; }
${CLUTTER_ON}[data-ally-clutter-unlock] { overflow-y: auto !important; }
${CLUTTER_ON}[data-ally-clutter-unlock] body { overflow: visible !important; position: static !important; }`;

// Animations jump to their end state. Videos are left alone: removing CSS couldn't start them again.
const MOTION_CSS = `${MOTION_ON}, ${MOTION_ON} *, ${MOTION_ON} *::before, ${MOTION_ON} *::after {
  animation-duration: 0.001ms !important; animation-delay: 0s !important; animation-iteration-count: 1 !important;
  transition-duration: 0.001ms !important; transition-delay: 0s !important; scroll-behavior: auto !important; }`;

// Spoken names: [one, many].
const CLUTTER_NAMES = {
  cookie: ["a cookie banner", "cookie banners"],
  promo: ["a pop-up", "pop-ups and banners"],
  chat: ["a chat button", "chat buttons"],
  ads: ["an ad", "ads"],
  share: ["a share bar", "share bars"],
};

function clutterKinds(kinds) {
  if (kinds === undefined || kinds === null || (Array.isArray(kinds) && !kinds.length)) return CLUTTER_KINDS.slice();
  const list = Array.isArray(kinds) ? kinds : [kinds];
  for (const k of list) {
    if (!CLUTTER_KINDS.includes(k)) throw new Error(`Unknown clutter kind: ${k}. Use ${CLUTTER_KINDS.join(", ")}.`);
  }
  return CLUTTER_KINDS.filter((k) => list.includes(k));
}

function joinWords(parts) {
  if (parts.length <= 1) return parts.join("");
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

function clutterPhrase(counts) {
  return Object.keys(CLUTTER_NAMES).filter((k) => counts[k]).map((k) => {
    const n = counts[k];
    return n === 1 ? CLUTTER_NAMES[k][0] : `${n} ${CLUTTER_NAMES[k][1]}`;
  });
}

function sumCounts(counts) {
  return Object.values(counts || {}).reduce((a, b) => a + b, 0);
}

async function motionOn(tabId) {
  const applied = await docStyles(tabId);
  return !!applied.ally_motion;
}

async function clutterOn(tabId, opts = {}) {
  const kinds = clutterKinds(opts.kinds);
  const pageKinds = kinds.filter((k) => k !== "motion");
  const out = { said: "", hidden: {}, total: {}, unlocked: false };

  if (pageKinds.length) {
    // The layer goes in first so the page can see what is hidden when it decides to free scrolling.
    await layerOn(tabId, "ally_clutter", CLUTTER_CSS);
    let r = null;
    try {
      r = await inPage(tabId, "clutter.hide", { kinds: pageKinds });
    } finally {
      const left = r ? r.total : (await inPage(tabId, "clutter.state", {}).catch(() => ({ total: {} }))).total;
      if (!sumCounts(left)) await layerOff(tabId, "ally_clutter");
    }
    Object.assign(out.hidden, r.hidden);
    Object.assign(out.total, r.total);
    out.unlocked = r.unlocked;
  }

  if (kinds.includes("motion")) {
    if (await motionOn(tabId)) {
      out.total.motion = 1;
    } else if (await inPage(tabId, "clutter.videoMain", {})) {
      out.motion = "skipped: the page is mainly a video";
    } else {
      await layerOn(tabId, "ally_motion", MOTION_CSS);
      out.hidden.motion = 1;
      out.total.motion = 1;
    }
  }

  const parts = clutterPhrase(out.hidden);
  const said = [];
  if (parts.length) said.push(`hid ${joinWords(parts)}`);
  if (out.hidden.motion) said.push("stopped moving animations");
  out.said = said.join(" and ");
  return out;
}

async function clutterOff(tabId, opts = {}) {
  const kinds = clutterKinds(opts.kinds);
  const pageKinds = kinds.filter((k) => k !== "motion");
  const out = { said: "", shown: {}, total: {} };

  if (pageKinds.length) {
    const r = await inPage(tabId, "clutter.show", { kinds: pageKinds });
    Object.assign(out.shown, r.shown);
    Object.assign(out.total, r.total);
    if (!sumCounts(r.total)) await layerOff(tabId, "ally_clutter");
  } else {
    Object.assign(out.total, (await inPage(tabId, "clutter.state", {})).total);
  }
  if (kinds.includes("motion")) {
    if (await layerOff(tabId, "ally_motion")) out.shown.motion = 1;
  } else if (await motionOn(tabId)) {
    out.total.motion = 1;
  }

  const said = [];
  const parts = clutterPhrase(out.shown);
  if (parts.length) said.push(`showed ${joinWords(parts)} again`);
  if (out.shown.motion) said.push("let animations move again");
  out.said = said.join(" and ");
  return out;
}

STEPS.clutter = {
  on: clutterOn,
  off: clutterOff,
  // Simple mode's on-load path: the same as on, without speaking.
  async redetect(tabId, kinds) {
    const r = await clutterOn(tabId, { kinds });
    return { ...r, said: "" };
  },
};
