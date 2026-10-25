// Simplify menus step for the service worker (see docs/page_steps.md).
// mode "panel" (simplify_menus tool): open the big Ally menu panel now.
// mode "strip" (Simplified mode): a docked "Ally menu" bar at the top that opens the panel.
// Both turn on the big <select> chooser and the ally_menus layer (hover menus close after a grace delay).

PAGE_FILES.push("page/menus.js");

const MENUS_LAYER = "ally_menus";
const MENUS_PAUSE = 'html:not([data-ally-pause~="menus"])';
const MENUS_STRIP_PX = 64;

// Closing is delayed only in the closed state; opening (hover or focus) has no delay. While focus is inside
// a submenu its closing is held off, so the site's own "open" display value stays as it was.
function menusCss(basePad) {
  const sub = '[data-ally-menus~="sub"]';
  const pad = Math.max(0, Math.round(Number(basePad) || 0));
  return `
${MENUS_PAUSE} [data-ally-menus~="menu"] ${sub} {
  transition: visibility 0s linear .6s, display 0s linear .6s allow-discrete !important;
}
${MENUS_PAUSE} [data-ally-menus~="menu"] :is(:hover, :focus-within) > ${sub} {
  transition-delay: 0s !important;
}
${MENUS_PAUSE} [data-ally-menus~="menu"] ${sub}:focus-within {
  transition: visibility 0s linear 86400s, display 0s linear 86400s allow-discrete !important;
  visibility: visible !important;
  opacity: 1 !important;
  pointer-events: auto !important;
}
${MENUS_PAUSE}[data-ally-menus-strip] body {
  padding-top: ${MENUS_STRIP_PX + pad}px !important;
}
${MENUS_PAUSE}[data-ally-menus-strip] [data-ally-menus~="shift"] {
  top: ${MENUS_STRIP_PX}px !important;
}
`;
}

STEPS.menus = {
  async on(tabId, opts = {}) {
    const mode = opts.mode === "strip" ? "strip" : "panel";
    const r = await inPage(tabId, "menus.on", { mode });
    await layerOn(tabId, MENUS_LAYER, menusCss(r.basePad));
    return { said: r.said, groups: r.groups, links: r.links, more: r.more, mode };
  },

  async off(tabId) {
    const r = await inPage(tabId, "menus.off", {}).catch(() => ({ said: "" }));
    const removed = await layerOff(tabId, MENUS_LAYER);
    return { said: r.said || (removed ? "closed the Ally menu" : ""), off: true };
  },
};
