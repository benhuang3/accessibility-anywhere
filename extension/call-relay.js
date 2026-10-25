// Page messages are untrusted; only the worker's saved call-tab record authorizes actions.
(() => {
  window.addEventListener("message", async (event) => {
    const data = event.data;
    if (event.source !== window || event.origin !== location.origin ||
        !data || data.source !== "ally-call" || data.type !== "helperAction") return;
    if (!["close_tab", "hl_start", "hl_move", "hl_end", "touch_start", "touch_move", "touch_end"].includes(data.action)) return;
    const message = { kind: "allyHelperAction", seq: data.seq, action: data.action, x: data.x, y: data.y };
    if (data.action !== "close_tab") {
      try { void chrome.runtime.sendMessage(message).catch(() => {}); } catch { /* stream continues */ }
      return;
    }
    let ok = false;
    try {
      const reply = await chrome.runtime.sendMessage(message);
      ok = reply?.ok === true;
    } catch { /* a missing or reloaded extension reports failure */ }
    window.postMessage({ source: "ally-ext", type: "helperActionResult", seq: data.seq, ok }, location.origin);
  });
})();
