(function () {
  history.pushState({ step: 1 }, "", "#step1");
  history.pushState({ step: 2 }, "", "#step2");

  window.addEventListener("beforeunload", (e) => {
    e.preventDefault();
    e.returnValue = "Your session will be lost.";
    return e.returnValue;
  });

  const overlay = document.getElementById("trap-overlay");
  document.getElementById("close-overlay").addEventListener("click", () => {
    overlay.classList.add("hidden");
  });

  document.getElementById("run-tricks").addEventListener("click", () => {
    history.pushState({ trick: 1 }, "", "#t1");
    history.pushState({ trick: 2 }, "", "#t2");
    history.pushState({ trick: 3 }, "", "#t3");

    window.addEventListener("popstate", () => {
      history.pushState({ trapped: true }, "", "#trapped");
    });

    const root = document.documentElement;
    if (root.requestFullscreen) {
      root.requestFullscreen().catch(() => {});
    } else if (root.webkitRequestFullscreen) {
      root.webkitRequestFullscreen();
    }

    window.open("about:blank");

    document.addEventListener("contextmenu", (e) => {
      e.preventDefault();
    });

    overlay.classList.remove("hidden");
  });
})();
