// Runs in the page's MAIN world: counts scam-like behaviors without changing outcomes.
(() => {
  try {
    if (window.__allyRiskMain) return;
    window.__allyRiskMain = true;

    const MAX = 99;
    const signals = {
      pushState: 0,
      replaceState: 0,
      backTrap: 0,
      beforeunload: false,
      fullscreenRequests: 0,
      keyboardLock: false,
      contextMenuBlocked: false,
      dialogs: 0,
      popups: 0,
      pointerLock: false,
    };

    function bump(key) {
      if (typeof signals[key] === "number" && signals[key] < MAX) signals[key] += 1;
      maybePublish();
    }

    function setTrue(key) {
      if (typeof signals[key] === "boolean") signals[key] = true;
      maybePublish();
    }

    let lastPublish = 0;
    function publish(force) {
      const now = Date.now();
      if (!force && now - lastPublish < 1000) return;
      lastPublish = now;
      document.dispatchEvent(new CustomEvent("ally-risk-signals", {
        detail: JSON.stringify(signals),
      }));
    }

    let trailing = null;
    function maybePublish() {
      publish(false);
      // A burst inside the throttle window would drop its last counts; send them once things settle.
      if (trailing === null) {
        trailing = setTimeout(() => { trailing = null; publish(true); }, 1100);
      }
    }

    const origPush = history.pushState.bind(history);
    const origReplace = history.replaceState.bind(history);
    let popstateAt = 0;

    history.pushState = function (...args) {
      bump("pushState");
      if (popstateAt && Date.now() - popstateAt <= 250) bump("backTrap");
      return origPush(...args);
    };
    history.replaceState = function (...args) {
      bump("replaceState");
      return origReplace(...args);
    };

    window.addEventListener("popstate", () => {
      popstateAt = Date.now();
    });

    const origAddEventListener = EventTarget.prototype.addEventListener;
    EventTarget.prototype.addEventListener = function (type, listener, options) {
      if (type === "beforeunload") setTrue("beforeunload");
      return origAddEventListener.call(this, type, listener, options);
    };

    const onBeforeDesc =
      Object.getOwnPropertyDescriptor(Window.prototype, "onbeforeunload") ||
      Object.getOwnPropertyDescriptor(window, "onbeforeunload");
    if (onBeforeDesc && onBeforeDesc.set) {
      Object.defineProperty(window, "onbeforeunload", {
        get: onBeforeDesc.get,
        set(fn) {
          if (fn) setTrue("beforeunload");
          return onBeforeDesc.set.call(this, fn);
        },
        configurable: true,
        enumerable: onBeforeDesc.enumerable,
      });
    }

    function wrapFullscreen(proto, name) {
      const orig = proto[name];
      if (!orig) return;
      proto[name] = function (...args) {
        bump("fullscreenRequests");
        return orig.apply(this, args);
      };
    }
    wrapFullscreen(Element.prototype, "requestFullscreen");
    wrapFullscreen(Element.prototype, "webkitRequestFullscreen");

    if (navigator.keyboard && typeof navigator.keyboard.lock === "function") {
      const origLock = navigator.keyboard.lock.bind(navigator.keyboard);
      navigator.keyboard.lock = function (...args) {
        setTrue("keyboardLock");
        return origLock(...args);
      };
    }

    document.addEventListener("contextmenu", (e) => {
      queueMicrotask(() => {
        if (e.defaultPrevented) setTrue("contextMenuBlocked");
      });
    }, true);

    for (const name of ["alert", "confirm", "prompt"]) {
      const orig = window[name];
      window[name] = function (...args) {
        bump("dialogs");
        return orig.apply(this, args);
      };
    }

    const origOpen = window.open;
    window.open = function (...args) {
      bump("popups");
      return origOpen.apply(this, args);
    };

    const origPointerLock = Element.prototype.requestPointerLock;
    if (origPointerLock) {
      Element.prototype.requestPointerLock = function (...args) {
        setTrue("pointerLock");
        return origPointerLock.apply(this, args);
      };
    }

    document.addEventListener("DOMContentLoaded", () => {
      setTimeout(() => publish(true), 1500);
    });
  } catch {
    /* never throw into the page */
  }
})();
