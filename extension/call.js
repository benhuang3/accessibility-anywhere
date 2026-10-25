// Only an Ally-created popup gets this frame; ordinary Meet tabs are untouched.
(async () => {
  let observer, poll, deadline, mutationTimer, bar, end;
  let failed = false;
  const hidden = new Set();

  function restoreMeet() {
    failed = true;
    clearInterval(poll);
    clearTimeout(deadline);
    clearTimeout(mutationTimer);
    if (observer) observer.disconnect();
    document.documentElement.classList.remove("ally-call");
    for (const element of hidden) element.classList.remove("ally-hidden");
    if (bar) bar.remove();
    // The End button's independent fallback styles keep it usable with plain Meet.
    if (end) end.classList.add("ally-call-fallback");
  }

  function leaveButton() {
    return document.querySelector('[aria-label^="Leave call"]');
  }

  try {
    const info = await chrome.runtime.sendMessage({ kind: "allyCallInfo" });
    if (!info || !info.isCall) return;

    bar = document.createElement("header");
    bar.id = "ally-call-bar";
    bar.setAttribute("role", "status");
    bar.textContent = `Connecting to ${info.name}\u2026`;
    end = document.createElement("button");
    end.id = "ally-call-end";
    end.type = "button";
    end.textContent = "End call";
    end.addEventListener("click", async () => {
      try {
        const leave = leaveButton();
        if (leave) leave.click();
      } catch {
        restoreMeet();
      } finally {
        try {
          await chrome.runtime.sendMessage({ kind: "allyCallEnd" });
        } catch {
          restoreMeet();
        }
      }
    });
    document.body.append(bar, end);
    document.documentElement.classList.add("ally-call");

    let joinClicked = false;
    let cameraClicked = false;
    let microphoneClicked = false;
    let mediaChangedAt = null;
    let foundControl = false;
    let controlsHidden = false;
    const started = Date.now();

    function status(text) {
      if (bar.textContent !== text) bar.textContent = text;
    }

    function mediaButton(label) {
      return Array.from(document.querySelectorAll(`[aria-label^="${label}"]`)).find(visible);
    }

    function scan() {
      try {
        if (failed) return;
        const leave = leaveButton();
        if (leave) {
          foundControl = true;
          status(`Talking with ${info.name}`);
          if (controlsHidden && !Array.from(hidden).some((element) => element.isConnected)) controlsHidden = false;
          if (!controlsHidden) {
            for (let parent = leave.parentElement; parent && parent !== document.body; parent = parent.parentElement) {
              const box = parent.getBoundingClientRect();
              if (box.width >= window.innerWidth * 0.6 && box.height > 0 && box.height < 200) {
                hidden.add(parent);
                parent.classList.add("ally-hidden");
                controlsHidden = true;
                break;
              }
            }
          }
        }
        if (!joinClicked && !leave) {
          if (!cameraClicked) {
            const camera = mediaButton("Turn on camera");
            if (camera) {
              cameraClicked = true;
              camera.click();
              mediaChangedAt = Date.now();
            }
          }
          if (!microphoneClicked) {
            const microphone = mediaButton("Turn on microphone");
            if (microphone) {
              microphoneClicked = true;
              microphone.click();
              mediaChangedAt = Date.now();
            }
          }
          const join = Array.from(document.querySelectorAll('button, [role="button"]')).find((element) =>
            visible(element) && ["Join now", "Ask to join", "Join"].includes(element.innerText.trim()));
          if (join) {
            foundControl = true;
            const cameraReady = cameraClicked || mediaButton("Turn off camera");
            const microphoneReady = microphoneClicked || mediaButton("Turn off microphone");
            const mediaReady = (cameraReady && microphoneReady) || Date.now() - started >= 5000;
            if (mediaReady && (mediaChangedAt === null || Date.now() - mediaChangedAt >= 500)) {
              join.click();
              joinClicked = true;
            }
          }
        }
        if (joinClicked && !leave) status(`Waiting for ${info.name} to let you in\u2026`);
      } catch {
        restoreMeet();
      }
    }

    function visible(element) {
      const style = getComputedStyle(element);
      return element.getClientRects().length > 0 && style.visibility !== "hidden" && style.display !== "none";
    }

    observer = new MutationObserver(() => {
      if (mutationTimer || failed) return;
      mutationTimer = setTimeout(() => {
        mutationTimer = null;
        scan();
      }, 200);
    });
    observer.observe(document.body, { childList: true, subtree: true, attributes: true, characterData: true });
    poll = setInterval(scan, 500);
    deadline = setTimeout(() => {
      try {
        scan();
        if (!foundControl) restoreMeet();
      } catch {
        restoreMeet();
      }
    }, 30000);
    scan();
  } catch {
    restoreMeet();
  }
})();
