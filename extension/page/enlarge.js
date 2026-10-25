// Ally enlarge step, page side: measures the page with the fit layer on (the text step always adds it) and
// the buttons layer paused, so a zoom audit sees what the text step will really do (docs/page_steps.md).

(() => {
  const P = globalThis.AllyPage;
  if (!P || P.modules.enlarge) return;

  // The buttons step marks each button (and link styled as one) with its own label size, so its layer can
  // make the label a little bigger than itself; CSS alone can't (em is relative to the parent).
  function markButtons({ selector }) {
    const sizes = new Set();
    let marked = 0;
    P.withPaused(["buttons", "text"], () => {
      P.unmark("buttons");
      const els = Array.from(document.querySelectorAll(selector)).filter((el) => !P.isAlly(el) && (P.visible(el) || P.allyHidden(el)));
      const measured = els.map((el) => [el, Math.round(parseFloat(getComputedStyle(el).fontSize) || 0)]);
      for (const [el, px] of measured) {
        if (px <= 0) continue;
        el.setAttribute("data-ally-buttons", String(px));
        sizes.add(px);
        marked++;
      }
    });
    return { sizes: [...sizes], marked };
  }

  function clearButtons() {
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
