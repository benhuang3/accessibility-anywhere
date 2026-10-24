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
