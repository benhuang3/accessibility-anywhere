// Port of extension/demo/trap-demo.js; dialogs repeat until the demo is released.
(() => {
  const overlay = document.getElementById("trap-overlay");
  const start = document.getElementById("run-tricks");
  let active = false;
  let dialogTimer = null;
  let returnFocus = null;

  history.pushState({ step: 1 }, "", "#step1");
  history.pushState({ step: 2 }, "", "#step2");

  function blockExit(event) {
    if (!active) return;
    event.preventDefault();
    event.returnValue = "Your session will be lost.";
  }
  window.addEventListener("beforeunload", blockExit);

  function trapBack() {
    if (active) history.pushState({ trapped: true }, "", "#trapped");
  }
  window.addEventListener("popstate", trapBack);

  function blockMenu(event) {
    if (active) event.preventDefault();
  }
  document.addEventListener("contextmenu", blockMenu);

  function release() {
    active = false;
    clearTimeout(dialogTimer);
    overlay.hidden = true;
    window.removeEventListener("beforeunload", blockExit);
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    start.disabled = false;
  }

  function repeatDialog() {
    if (!active) return;
    window.alert("SECURITY ALERT: Your device is locked. Call support at 1-888-555-0100. Do not close this window.");
    // Space between dialogs leaves time for the real Ally warning and the escape controls.
    if (active) dialogTimer = setTimeout(repeatDialog, 3500);
  }

  start.addEventListener("click", () => {
    if (active) return;
    active = true;
    window.addEventListener("beforeunload", blockExit);
    returnFocus = document.activeElement;
    start.disabled = true;
    for (let step = 1; step <= 3; step += 1) history.pushState({ trick: step }, "", `#t${step}`);
    const root = document.documentElement;
    if (root.requestFullscreen) root.requestFullscreen().catch(() => {});
    else if (root.webkitRequestFullscreen) root.webkitRequestFullscreen();
    overlay.hidden = false;
    document.getElementById("close-overlay").focus();
    dialogTimer = setTimeout(repeatDialog, 800);
  });

  document.getElementById("close-overlay").addEventListener("click", () => {
    overlay.hidden = true;
    if (returnFocus && !returnFocus.disabled) returnFocus.focus();
    else document.querySelector(".lab-back").focus();
  });
  document.getElementById("stop-tricks").addEventListener("click", () => {
    release();
    location.href = "index.html";
  });
  document.querySelector(".lab-back").addEventListener("click", release);
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && active) {
      release();
      start.focus();
    }
    if (event.key === "Tab" && !overlay.hidden) {
      const first = document.getElementById("close-overlay");
      const last = document.getElementById("stop-tricks");
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
})();
