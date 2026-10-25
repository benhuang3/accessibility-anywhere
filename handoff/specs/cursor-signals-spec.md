# Cursor spec: scam-behavior signals in the Chrome extension

Worktree `wt-overlay` (branch `ganesh/overlay`, already synced to the latest risk work). Do not commit, push, merge or switch branches.

## Goal
Ally's risk pipeline flags pages with cheap local checks and then asks a vision model for a second opinion. Add page-BEHAVIOR signals that scam
pages show and normal sites almost never do. The extension counts them and sends the counts to the agent; the agent decides what they mean.
Chrome Manifest V3. Plain JavaScript, no libraries, no build step.

## Allowed files
- `extension/risk-main.js` (new): runs in the page's own JavaScript world and records behavior
- `extension/risk.js` (edit): the existing isolated content script; merge the signals into the page report it already sends
- `extension/manifest.json` (edit): register `risk-main.js` as a content script with `"world": "MAIN"` and `"run_at": "document_start"`
- `extension/demo/trap-demo.html` (new): an invented scam-behavior demo page (see below)
Do not edit `background.js` or anything else. Read `extension/risk.js` and `extension/manifest.json` first.

## `risk-main.js` (page world, must never throw into the page and must never change what the page does)
Wrap, do not replace behavior (call the original with the same arguments and return its result). Count:
- `pushState`, `replaceState`: wrap `history.pushState` / `history.replaceState`; count calls.
- `backTrap`: true when `popstate` fires and the page calls pushState within 250 ms after it (a page that re-adds itself when the user presses Back);
  count how many times.
- `beforeunload`: true when the page registers a beforeunload handler (wrap `window.addEventListener` for "beforeunload" and the `onbeforeunload` setter).
- `fullscreenRequests`: wrap `Element.prototype.requestFullscreen` (and `webkitRequestFullscreen`); count calls.
- `keyboardLock`: true when `navigator.keyboard.lock` is called (if the API exists).
- `contextMenuBlocked`: true when a `contextmenu` event on the document is default-prevented by the page (listen in capture phase on document and check
  `defaultPrevented` after a microtask).
- `dialogs`: count calls to `window.alert`, `window.confirm`, `window.prompt`.
- `popups`: count calls to `window.open`.
- `pointerLock`: true when `requestPointerLock` is called.
Keep memory tiny: only numbers/booleans. Cap every counter at 99.
Publish the signals to the isolated script with `document.dispatchEvent(new CustomEvent("ally-risk-signals", { detail: JSON.stringify(signals) }))`,
throttled to at most once per second and also once at `DOMContentLoaded` + 1.5 s. `detail` is a JSON STRING of a flat object with only those keys. Never
include page text, URLs, field values or any data besides the counters above. Wrap the whole file in a try/catch IIFE; guard against being injected
twice (`window.__allyRiskMain`).

## `risk.js` edit
- Listen for `ally-risk-signals` on `document` (isolated worlds see DOM events), `JSON.parse` the string inside try/catch, accept only the known keys with
  number/boolean values (ignore anything else, a hostile page can fire this event), clamp numbers to 0..99.
- Add the latest signals to the object it already sends in `snapshot()` as `signals`, and include a compact signature of the signals in `sig` so a change in
  behavior triggers a resend (keep the existing resend limits).
- Do not change anything else (field descriptors, overlay text, banner, resend logic).

## `manifest.json` edit
Add a second `content_scripts` entry: `{ "matches": ["http://*/*", "https://*/*"], "js": ["risk-main.js"], "run_at": "document_start", "world": "MAIN", "all_frames": false }`.
Keep the existing entry. Keep the file valid JSON, 2-space indent.

## `extension/demo/trap-demo.html`
An invented brand ("Pinecrest Savings" is already used on the other demo, use "Harbor Light Bank"). Plain page that behaves like a scam page so the signals fire,
without doing anything harmful:
- on load: `history.pushState` twice, add a `beforeunload` handler (returns a string), calls `alert` once after 3 s? NO: do not call alert on load (it blocks).
  Use a button "Run the scam tricks" that, when clicked, does: pushState x3, adds a popstate handler that re-pushes the state, requests fullscreen on the
  document element, calls `window.open("about:blank")` once, prevents the context menu, and shows a big fake "SECURITY ALERT" box (text only, invented number
  555-0100 style, no real brands).
- A visible note: "Ally demo page. Invented brand. Nothing here is real."
- No inline `<script>` (CSP rule for this repo applies to the dashboard; for this page put JS in `trap-demo.js` next to it) and no `on*=` attributes. Create `extension/demo/trap-demo.js`.
  (Allowed files include `extension/demo/trap-demo.js`.)

## Constraints
- No network calls. No reading input values. No `eval`, no `innerHTML` with page-derived strings. Match the style of `risk.js` (2-space indent, small functions, comments only where the reason isn't obvious).
- Check syntax with `node --check extension/risk-main.js extension/risk.js extension/demo/trap-demo.js` and validate manifest.json with `python -c "import json;json.load(open('extension/manifest.json'))"`.
- You cannot run Chrome. State clearly in the report what you could not verify.
Final report: files changed, checks run, what is unverified.
