// Snapshot logic copied from extension/risk.js. Values entered in forms are never read.
(() => {
  const MAX_TEXT = 20000;
  const MAX_FIELDS = 40;

  function describeFields() {
    const out = [];
    for (const el of document.querySelectorAll("input, select, textarea")) {
      if (out.length >= MAX_FIELDS) break;
      const label = (el.labels && el.labels[0] && el.labels[0].innerText) || el.getAttribute("aria-label") || "";
      out.push({
        name: el.name || "", id: el.id || "", placeholder: el.placeholder || "",
        label: label.slice(0, 80), autocomplete: el.getAttribute("autocomplete") || "", type: el.type || "",
      });
    }
    return out;
  }

  function overlayText() {
    const w = window.innerWidth;
    const h = window.innerHeight;
    const parts = [];
    for (const el of document.querySelectorAll("body *")) {
      const cs = getComputedStyle(el);
      if (cs.position !== "fixed" && cs.position !== "absolute") continue;
      if (cs.visibility === "hidden" || cs.display === "none") continue;
      const r = el.getBoundingClientRect();
      if (r.width * r.height < w * h * 0.25) continue;
      const t = (el.innerText || "").trim();
      if (t) parts.push(t.slice(0, 2000));
      if (parts.length >= 3) break;
    }
    return parts.join("\n");
  }

  const text = ((document.body && document.body.innerText) || "").replace(/\n{3,}/g, "\n\n").slice(0, MAX_TEXT);
  return {
    kind: "pageRisk", url: location.href, title: document.title || "", text,
    overlay_text: overlayText(), fullscreen: !!document.fullscreenElement,
    has_tel_link: !!document.querySelector('a[href^="tel:"]'), fields: describeFields(),
    signals: { ...(window.__demoSignals || {}) },
  };
})()
