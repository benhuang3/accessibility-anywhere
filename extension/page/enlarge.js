// Ally enlarge step, page side: measures the page with the fit layer on (the text step always adds it) and
// the buttons layer paused, so a zoom audit sees what the text step will really do (docs/page_steps.md).

(() => {
  const P = globalThis.AllyPage;
  if (!P || P.modules.enlarge) return;

  // The buttons step marks each button (and link styled as one) with its own label size, so its layer can
  // make the label a little bigger than itself; CSS alone can't (em is relative to the parent).
  // ...and with its own box size (data-ally-buttons-box="96x44"), so every button grows by a clear amount
  // even when it's already tall (a fixed "min-height: 44px" wouldn't change those).
  function markButtons({ selector }) {
    const sizes = new Set();
    const boxes = new Set();
    let marked = 0;
    // Every Ally layer paused: the page's own sizes, whatever other steps already did (hidden clutter shows,
    // the fit layer is off). Synchronous, so nothing flickers.
    P.withPaused(["buttons", "text", "fit", "clutter", "menus", "motion", "fix", "simple"], () => {
      for (const el of document.querySelectorAll("[data-ally-buttons-box]")) el.removeAttribute("data-ally-buttons-box");
      P.unmark("buttons");
      const els = Array.from(document.querySelectorAll(selector)).filter((el) => !P.isAlly(el) && (P.visible(el) || P.allyHidden(el)));
      const measured = els.map((el) => {
        const r = el.getBoundingClientRect();
        return [el, Math.round(parseFloat(getComputedStyle(el).fontSize) || 0), Math.round(r.width), Math.round(r.height)];
      });
      for (const [el, px, w, h] of measured) {
        if (px <= 0) continue;
        el.setAttribute("data-ally-buttons", String(px));
        sizes.add(px);
        if (w > 0 && h > 0) {
          el.setAttribute("data-ally-buttons-box", `${w}x${h}`);
          boxes.add(`${w}x${h}`);
        }
        marked++;
      }
    });
    return { sizes: [...sizes], boxes: [...boxes], marked };
  }

  function clearButtons() {
    for (const el of document.querySelectorAll("[data-ally-buttons-box]")) el.removeAttribute("data-ally-buttons-box");
    P.unmark("buttons");
    return true;
  }

  P.registerOff("buttons", clearButtons);
  P.modules.enlarge = {
    measure() {
      return P.withPaused(["buttons", "text"], P.measure);
    },
    markButtons,
    clearButtons,
  };
})();
