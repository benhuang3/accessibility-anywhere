"use strict";

const TOKEN = document.querySelector('meta[name="ally-token"]').content;
const $ = (id) => document.getElementById(id);
let lastEvent = 0;
let savedConfig = "";

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: { "X-Ally-Token": TOKEN, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = {};
  try {
    data = await res.json();
  } catch {
    /* empty body */
  }
  if (res.status === 401 || res.status === 403) throw new Error("Not allowed. Reload this page.");
  return data;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v);
  }
  for (const c of children) node.append(c);
  return node;
}

function showReply(text, kind = "") {
  const box = $("reply");
  box.textContent = text || "";
  box.className = "reply " + kind;
}

// ---------------------------------------------------------------- ask

async function sendCommand(text) {
  if (!text.trim()) return;
  showReply("Working…", "busy");
  try {
    const r = await api("POST", "/api/command", { text, source: "dashboard" });
    const extra = r.mode === "offline" && r.error ? " (offline mode)" : "";
    showReply((r.text || "Done.") + extra, r.actions && r.actions.some((a) => !a.ok) ? "warn" : "");
  } catch (e) {
    showReply("Couldn't reach Ally: " + e.message, "error");
  }
  pollEvents();
}

$("ask").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = $("ask-input");
  const text = input.value;
  input.value = "";
  sendCommand(text);
});

$("stop").addEventListener("click", () => api("POST", "/api/stop", {}));

for (const btn of document.querySelectorAll(".quick button")) {
  btn.addEventListener("click", async () => {
    if (btn.dataset.command) return sendCommand(btn.dataset.command);
    showReply("Working…", "busy");
    try {
      const args = btn.dataset.args ? JSON.parse(btn.dataset.args) : {};
      const r = await api("POST", "/api/tool", { name: btn.dataset.tool, args, speak: true });
      showReply(r.text || r.error || "Done.", r.ok ? "" : "error");
    } catch (e) {
      showReply(e.message, "error");
    }
    pollEvents();
  });
}

// Microphone (Chrome's Web Speech API; works without installing anything).
(function setupMic() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) return;
  const mic = $("mic");
  mic.hidden = false;
  let rec = null;
  mic.addEventListener("click", () => {
    if (rec) {
      rec.stop();
      return;
    }
    rec = new SR();
    rec.lang = navigator.language || "en-US";
    rec.interimResults = true;
    let finalText = "";
    rec.onresult = (ev) => {
      let interim = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const r = ev.results[i];
        if (r.isFinal) finalText += r[0].transcript;
        else interim += r[0].transcript;
      }
      $("ask-input").value = finalText + interim;
    };
    rec.onerror = (ev) => showReply("Microphone: " + ev.error, "error");
    rec.onend = () => {
      mic.setAttribute("aria-pressed", "false");
      mic.textContent = "Speak";
      rec = null;
      if (finalText.trim()) {
        $("ask-input").value = "";
        sendCommand(finalText);
      }
    };
    mic.setAttribute("aria-pressed", "true");
    mic.textContent = "Listening… (click to stop)";
    rec.start();
  });
})();

// ---------------------------------------------------------------- status

function chip(label, value, state) {
  return el("li", { class: "chip " + state }, el("span", { class: "chip-label", text: label + ": " }), value);
}

async function pollStatus() {
  let s;
  try {
    s = await api("GET", "/api/status");
  } catch (e) {
    $("chips").replaceChildren(chip("Agent", "not responding", "bad"));
    return;
  }
  const brain = s.brain.mode === "claude"
    ? (s.brain.online === false ? ["Claude (offline right now)", "warn"] : [`Claude ${s.brain.model}`, "good"])
    : [s.brain.has_key ? "offline mode" : "offline (no API key)", "warn"];
  const chrome = s.browser.connected ? ["connected", "good"] : ["not connected", "warn"];
  const keys = { on: ["on", "good"], starting: ["starting", "warn"], off: ["off", "warn"] }[s.hotkeys.status] || ["error", "bad"];
  const voice = s.voice.listening ? ["listening", "good"] : s.voice.available ? ["ready", "good"] : ["mic button only", "warn"];
  const wake = s.listener.status === "listening"
    ? [s.listener.awake ? "listening now" : `say "${(s.listener.wake_words[0] || "ally")}"`, "good"]
    : s.listener.status === "off" ? ["off", "warn"] : [s.listener.status === "starting" ? "starting" : "error", s.listener.status === "starting" ? "warn" : "bad"];
  const eyes = { tracking: ["tracking", "good"], off: ["off", "warn"], starting: ["starting", "warn"],
    "needs calibration": ["say \"calibrate my eyes\"", "warn"], "can't see you": ["can't see you", "warn"] }[s.gaze.status] || ["error", "bad"];
  const items = [
    chip("Brain", brain[0], brain[1]),
    chip("Chrome", chrome[0], chrome[1]),
    chip("Hotkeys", keys[0], keys[1]),
    chip("Hands-free", wake[0], wake[1]),
    chip("Eyes", eyes[0], eyes[1]),
    chip("Voice", voice[0], voice[1]),
    chip("Speech", s.speech.engine + (s.speech.speaking ? " (talking)" : ""), s.speech.engine === "off" ? "warn" : "good"),
  ];
  if (s.config.error) items.push(chip("Settings", "error, see below", "bad"));
  $("chips").replaceChildren(...items);
  $("version").textContent = `${s.name} ${s.version} on ${s.platform} · up ${Math.round(s.uptime / 60)} min`;

  const list = s.hotkeys.keys.map((k) => el("li", {}, el("kbd", { text: k.keys }), " " + k.action.replaceAll("_", " ")));
  $("hotkeys").replaceChildren(...list);
  const herr = $("hotkey-error");
  const problems = [["Hotkeys", s.hotkeys.error], ["Hands-free", s.listener.error], ["Eyes", s.gaze.error]]
    .filter(([, e]) => e).map(([name, e]) => `${name}: ${e}`);
  herr.hidden = !problems.length;
  herr.textContent = problems.join(" · ");
}

async function pollEvents() {
  try {
    const { events } = await api("GET", `/api/events?after=${lastEvent}`);
    const log = $("log");
    for (const ev of events) {
      lastEvent = Math.max(lastEvent, ev.id);
      const time = new Date(ev.ts * 1000).toLocaleTimeString();
      log.prepend(el("li", { class: "ev ev-" + ev.kind }, el("time", { text: time }), el("b", { text: ev.kind }), " " + ev.text));
    }
    while (log.children.length > 150) log.lastChild.remove();
  } catch {
    /* status chip shows the problem */
  }
}

// ---------------------------------------------------------------- tools

async function loadTools() {
  const { tools } = await api("GET", "/api/tools");
  $("tool-count").textContent = `(${tools.length})`;
  const items = tools.map((t) => {
    const tags = el("span", { class: "tags" });
    tags.append(el("span", { class: "tag", text: t.source === "builtin" ? "built-in" : t.source === "config" ? "command" : "plug-in" }));
    if (t.ai) tags.append(el("span", { class: "tag ai", text: "AI" }));
    if (t.disabled) tags.append(el("span", { class: "tag off", text: "off" }));
    const li = el("li", {}, el("div", { class: "tool-head" }, el("code", { text: t.name }), tags),
      el("p", { text: t.description.split("\n")[0] }));
    if (!t.required.length && !t.disabled) {
      const b = el("button", { class: "small", text: "Run" });
      b.addEventListener("click", async () => {
        const r = await api("POST", "/api/tool", { name: t.name, args: {}, speak: true });
        showReply(r.text || r.error, r.ok ? "" : "error");
        pollEvents();
      });
      li.append(b);
    }
    return li;
  });
  $("tools").replaceChildren(...items);
}

// ---------------------------------------------------------------- config

async function loadConfig() {
  const c = await api("GET", "/api/config");
  savedConfig = c.text;
  $("config").value = c.text;
  showConfigMessages(c.errors || [], c.warnings || [], c.error ? "The file on disk has errors; the last good settings are active." : "");
}

function showConfigMessages(errors, warnings, headline) {
  const box = $("config-msg");
  const parts = [];
  if (headline) parts.push(el("p", { class: errors.length ? "error" : "ok", text: headline }));
  if (errors.length) parts.push(el("ul", { class: "error" }, ...errors.map((e) => el("li", { text: e }))));
  if (warnings.length) parts.push(el("ul", { class: "warn" }, ...warnings.map((w) => el("li", { text: "Warning: " + w }))));
  box.replaceChildren(...parts);
}

$("save").addEventListener("click", async () => {
  const r = await api("POST", "/api/config", { text: $("config").value });
  if (r.ok) {
    savedConfig = $("config").value;
    showConfigMessages([], r.warnings || [], "Saved. New settings are on.");
    loadTools();
  } else {
    showConfigMessages(r.errors || [r.error || "Unknown error"], r.warnings || [], "Not saved. Fix these first:");
  }
});

$("revert").addEventListener("click", () => {
  $("config").value = savedConfig;
  showConfigMessages([], [], "Edits undone.");
});

$("restart").addEventListener("click", async () => {
  await api("POST", "/api/restart", {});
  showReply("Restarting… this page reconnects by itself.", "busy");
});

$("shutdown").addEventListener("click", async () => {
  if (!confirm("Stop Ally? Hotkeys and voice will stop working until you start it again.")) return;
  await api("POST", "/api/shutdown", {});
  showReply("Ally stopped. Start it again with: python run.py", "warn");
});

// ---------------------------------------------------------------- start

pollStatus();
pollEvents();
loadTools().catch(() => {});
loadConfig().catch(() => {});
setInterval(pollStatus, 3000);
setInterval(pollEvents, 1500);
$("ask-input").focus();
