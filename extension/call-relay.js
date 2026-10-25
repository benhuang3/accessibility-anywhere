// Page messages are untrusted; only the worker's saved call-tab record authorizes actions.
(() => {
  window.addEventListener("message", async (event) => {
    const data = event.data;
    if (event.source !== window || event.origin !== location.origin ||
        !data || data.source !== "ally-call" || data.type !== "helperAction") return;
    let ok = false;
    try {
      const reply = await chrome.runtime.sendMessage({ kind: "allyHelperAction",
        seq: data.seq, action: data.action, x: data.x, y: data.y });
      ok = reply?.ok === true;
    } catch { /* a missing or reloaded extension reports failure */ }
    window.postMessage({ source: "ally-ext", type: "helperActionResult", seq: data.seq, ok }, location.origin);
  });
})();
