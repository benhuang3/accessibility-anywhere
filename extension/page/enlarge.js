// Ally enlarge step, page side: measures the page with the fit layer on (the text step always adds it) and
// the buttons layer paused, so a zoom audit sees what the text step will really do (docs/page_steps.md).

(() => {
  const P = globalThis.AllyPage;
  if (!P || P.modules.enlarge) return;

  P.modules.enlarge = {
    measure() {
      return P.withPaused(["buttons", "text"], P.measure);
    },
  };
})();
