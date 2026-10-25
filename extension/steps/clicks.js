// Click by number step (docs/click_by_voice.md): on shows numbers on the clickable things on screen, off hides them.
// The clickList / clickShow / clickDo / clickState actions live in background.js.

PAGE_FILES.push("page/clicks.js");

STEPS.clicks = {
  async on(tabId, opts = {}) {
    const args = {};
    if (Array.isArray(opts.only)) args.only = opts.only.map(String);
    if (Number(opts.max) > 0) args.max = Number(opts.max);
    return await inPage(tabId, "clicks.show", args);
  },
  async off(tabId) {
    return await inPage(tabId, "clicks.hide");
  },
};

// The numbers showing, without injecting anything: none on chrome://, PDFs, the Web Store and pages Ally
// never touched.
async function clickProbe(tabId) {
  const none = { shown: false, count: 0, items: [] };
  try {
    const [r] = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => {
        const m = globalThis.AllyPage && globalThis.AllyPage.modules && globalThis.AllyPage.modules.clicks;
        return m ? m.state() : null;
      },
    });
    const s = r && r.result;
    return s && s.shown ? s : none;
  } catch {
    return none;
  }
}
