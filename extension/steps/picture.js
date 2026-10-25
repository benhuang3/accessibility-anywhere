// Picture viewer step (docs/picture_viewer.md): on opens one listed picture large, off closes the viewer.
// The pictureList / pictureZoom / pictureStep / pictureState actions live in background.js.

PAGE_FILES.push("page/picture.js");

STEPS.picture = {
  async on(tabId, opts = {}) {
    return await inPage(tabId, "picture.open", {
      id: String(opts.id || ""),
      order: Array.isArray(opts.order) ? opts.order.map(String) : [],
      gen: Number(opts.gen) || 0,
      description: String(opts.description || ""),
    });
  },
  async off(tabId) {
    return await inPage(tabId, "picture.close");
  },
};

// Whether the viewer is open, without injecting anything: false on chrome://, PDFs, the Web Store and pages
// Ally never touched.
async function pictureProbe(tabId) {
  const closed = { open: false, id: null, scale: 1 };
  try {
    const [r] = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => {
        const m = globalThis.AllyPage && globalThis.AllyPage.modules && globalThis.AllyPage.modules.picture;
        return m ? m.state() : null;
      },
    });
    const s = r && r.result;
    return s && s.open ? s : closed;
  } catch {
    return closed;
  }
}
