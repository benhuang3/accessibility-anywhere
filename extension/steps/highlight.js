// "Show me where to click" and "highlight the menu" (docs/tabs_and_pointing.md): on shows a box or spotlight
// around elements found by highlight.find, off clears it. The highlightFind / highlightShow / highlightClear
// actions live in background.js.

PAGE_FILES.push("page/highlight.js");

STEPS.highlight = {
  async on(tabId, opts = {}) {
    return await inPage(tabId, "highlight.show", {
      ids: Array.isArray(opts.ids) ? opts.ids.map(String) : [],
      gen: Number(opts.gen) || 0,
      mode: opts.mode === "spotlight" ? "spotlight" : "point",
      note: String(opts.note || ""),
    });
  },
  async off(tabId) {
    return await inPage(tabId, "highlight.clear");
  },
};

// Tabs in most-recently-used order, for "go to the other tab". In session storage: the service worker sleeps.
async function rememberTab(tabId) {
  const { recentTabs = [] } = await chrome.storage.session.get("recentTabs");
  await chrome.storage.session.set({ recentTabs: [tabId, ...recentTabs.filter((id) => id !== tabId)].slice(0, 30) });
}

chrome.tabs.onActivated.addListener(({ tabId }) => {
  rememberTab(tabId).catch(() => {});
});

chrome.tabs.onRemoved.addListener(async (tabId) => {
  const { recentTabs = [] } = await chrome.storage.session.get("recentTabs");
  await chrome.storage.session.set({ recentTabs: recentTabs.filter((id) => id !== tabId) });
});
