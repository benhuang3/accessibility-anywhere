const $ = (id) => document.getElementById(id);
const ask = (msg) => chrome.runtime.sendMessage({ kind: "popup", ...msg });
let connected = false;
let port = 8765;

function say(text, isError = false) {
  const el = $("reply");
  el.textContent = text || "";
  el.classList.toggle("error", isError);
}

async function refresh() {
  try {
    const s = await ask({ op: "status" });
    connected = !!s.connected;
    port = s.port;
    $("port").value = s.port;
    $("status").textContent = connected ? "Connected to the Ally agent." : "Agent not running. Basic buttons still work.";
    $("status").className = connected ? "ok" : "warn";
  } catch (e) {
    $("status").textContent = "Extension error: " + e.message;
  }
}

$("ask").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = $("text").value.trim();
  if (!text) return;
  say("Working…");
  const r = await ask({ op: "command", text });
  say(r.ok ? r.text : r.error || r.text, !r.ok);
});

for (const btn of document.querySelectorAll(".grid button")) {
  btn.addEventListener("click", async () => {
    say("");
    try {
      if (btn.dataset.command && connected) {
        const r = await ask({ op: "command", text: btn.dataset.command });
        say(r.ok ? r.text : r.error || r.text, !r.ok);
        return;
      }
      if (btn.dataset.fallback) {
        const r = await ask({ op: btn.dataset.fallback });
        if (!r.ok) say(r.error, true);
        return;
      }
      if (btn.dataset.command && !connected) {
        say("This needs the Ally agent. Start it with: python run.py", true);
        return;
      }
      const args = btn.dataset.args ? JSON.parse(btn.dataset.args) : {};
      const r = await ask({ op: "action", action: btn.dataset.action, args });
      if (!r.ok) say(r.error, true);
      else if (btn.dataset.action === "zoomStep") say(`Zoom ${Math.round(r.result * 100)}%`);
    } catch (e) {
      say(e.message, true);
    }
  });
}

$("save").addEventListener("click", async () => {
  const r = await ask({ op: "setPort", port: $("port").value });
  say(r.ok ? `Saved. Connecting on port ${r.port}…` : r.error, !r.ok);
  setTimeout(refresh, 1500);
});

$("dashboard").addEventListener("click", (e) => {
  e.preventDefault();
  chrome.tabs.create({ url: `http://127.0.0.1:${port}/` });
});

refresh();
$("text").focus();

// ---- Simple mode (extension/steps/simple.js). ?tab=<id> picks a tab when popup.html is opened in a tab.
const simpleTabId = Number(new URLSearchParams(location.search).get("tab")) || undefined;
const askSimple = (msg) => chrome.runtime.sendMessage({ kind: "ally-simple", tabId: simpleTabId, ...msg });

async function refreshSimple() {
  const s = await askSimple({ op: "status" }).catch(() => null);
  if (!s || !s.ok) return;
  $("simple").hidden = false;
  $("simple-on").checked = !!(s.remembered || s.active);
  $("simple-on").disabled = !s.allowed;
  $("simple-host").textContent = s.host ? ` (${s.host})` : "";
  if (!s.allowed) $("simple-msg").textContent = "Simple mode works on normal websites only.";
  $("simple-forget").hidden = !s.count;
}

$("simple-on").addEventListener("change", async () => {
  const on = $("simple-on").checked;
  $("simple-on").disabled = true;
  $("simple-msg").textContent = on ? "Simplifying…" : "Turning off…";
  const r = await askSimple({ op: "toggle", on }).catch((e) => ({ ok: false, error: e.message }));
  $("simple-on").disabled = false;
  await refreshSimple();
  $("simple-msg").textContent = r.ok ? (on ? "Simple mode is on. Ally will keep this site simple." : "Simple mode is off.") : r.error;
});

$("simple-forget").addEventListener("click", async (e) => {
  e.preventDefault();
  const r = await askSimple({ op: "forgetAll" }).catch(() => null);
  await refreshSimple();
  if (r && r.ok) $("simple-msg").textContent = "Ally forgot all simple sites.";
});

refreshSimple();
// ---- /Simple mode
