// Page sorting (excluded / article / form / general) and the reader view. See docs/page_steps.md.
// pageInfo() in core.js merges STEPS.classify.info() into its result.

PAGE_FILES.push("page/classify.js", "page/reader.js");

STEPS.classify = {
  // Read-only, so it doesn't wait in the tab's queue.
  async info(tabId) {
    try {
      return await inPage(tabId, "classify.info");
    } catch (e) {
      return { type: "excluded", reasons: ["Ally can't read this page"], articleScore: 0, mainVideo: null, formControls: 0 };
    }
  },
  async on() {
    return { said: "" };
  },
  async off() {
    return { said: "" };
  },
};

STEPS.reader = {
  async on(tabId, opts = {}) {
    return await inPage(tabId, "reader.open", {
      fontPx: Number(opts.fontPx) || 26,
      withStrip: !!opts.withStrip,
      images: opts.images !== false,
    });
  },
  async off(tabId) {
    return await inPage(tabId, "reader.close");
  },
};
