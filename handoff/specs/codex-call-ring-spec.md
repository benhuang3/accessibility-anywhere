# Spec: Ally Call starts as a small "calling" popup in the corner, and grows when the helper joins

Deadline: about 2 hours of build time. Keep the change small and focused. A parallel builder is changing the
helper's side of the call in another worktree (helper action buttons, a new SpacetimeDB reducer, a new
extension content script). Do not touch those areas, and keep your edits to `+page.svelte` and `app.css`
in the elder-only parts so the two branches merge cleanly.

## Goal

Today, when the older adult taps **Call Priya**, `extension/background.js` `openDockedCall()` opens the Ally Call
page (`spacecall/`) as a full-height panel on the right and shrinks Chrome next to it, before anyone has answered.

New behaviour, from the older adult's point of view, should feel like a phone call being picked up:

1. **Ringing:** a small popup window (about 360 x 260 DIPs) appears in the bottom-right corner of the work area,
   16 px from the right and bottom edges. Chrome keeps its current size and position. The page shows a compact
   ringing card: the helper's initial in a circle with a slow, gentle pulse (no flashing; no animation under
   `prefers-reduced-motion`), "Calling Priya…" (helper name from the link), and one **End call** button.
   No sharing banner, no self-view, no notices other than real errors and the "Allow camera and microphone" note.
2. **Helper joins** (`incident.status` becomes `connected`): the extension grows the same window into today's
   docked panel (same width/left/top/height maths as the current `openDockedCall`) and only then moves/shrinks
   Chrome next to it, exactly as today. The page plays one soft two-note join chime through Web Audio
   (quiet, under 1 second; skip it silently if the AudioContext is not allowed to start) and shows the normal
   connected layout: "Priya is here", her video, the sharing banner, controls.
3. **No answer:** if still not connected 60 seconds after the page opened, the ringing card adds one calm line:
   "Priya hasn't answered yet. Ally sent her a message." It keeps ringing; End call still works.
4. **Call over:** when the call window closes (the existing `chrome.windows.onRemoved` path near line 830 of
   `background.js`), put Chrome back the way it was before it was shrunk (restore the saved bounds/state,
   e.g. maximized). Only if the extension shrank it.

If `openDockedCall` cannot compute a work area today, it returns null and the call opens maximized. Keep that fallback.

## How the extension learns the status

No new SpacetimeDB tables or reducers, and no new content script.

- The elder page keeps `document.documentElement.dataset.allyCall` set to `ringing` | `connected` | `ended`,
  and sets `document.title` so that it changes whenever that status changes (e.g. "Calling Priya… · Ally Call",
  "Priya is here · Ally Call", "Call ended · Ally Call").
- `background.js` adds a `chrome.tabs.onUpdated` listener. When a tab's title changes and that tab is a saved
  `allySpacecall:<tabId>` entry in `chrome.storage.session`, it reads the dataset value with
  `chrome.scripting.executeScript` (the extension already has `scripting` and `<all_urls>`). On `connected`,
  it expands the window once (idempotent: record `expanded: true` in the session entry). Ignore all other tabs.
- Save what expansion needs in the existing session entry: the work area and the browser window id and its
  bounds/state before any change. A service worker can restart between ringing and connecting, so do not keep
  this only in memory.

## Allowed files

- `extension/background.js` (only `openDockedCall`, the new onUpdated listener, the onRemoved restore, and the
  session entry fields)
- `spacecall/src/routes/+page.svelte` (elder parts only: ringing card, status dataset/title, chime, 60 s note)
- `spacecall/src/lib/app.css` (elder ringing styles only; a compact layout for small windows)
- a new `spacecall/src/lib/ringing.ts` if you want to keep logic out of the page (pure functions are easiest to test)
- new tests: `spacecall/tests/ringing.test.ts`, and a Python test file `tests/test_call_ring.py` if you test
  the extension's pure logic (follow how existing tests load extension code, if any; otherwise keep the window
  geometry maths in a small pure function and test that)
- `spacecall/README.md` (a short "Ringing popup" section)

Everything else is out of scope. Do not edit `spacecall/server/**`, `spacecall/src/module_bindings/**`,
`spacecall/src/lib/stdb.ts`, `SharedScreen.svelte`, the helper section of `+page.svelte`, `extension/manifest.json`,
or any `ally/` Python. Never touch `.env`, `config.toml`, `state/`, `logs/`.

## Design rules (older adults)

- Text at least 20 px in the compact card, name/status 24 px+, the End call button at least 56 px tall.
- Calm, warm, high contrast (WCAG AAA for text). Match the existing Ally Call look in `app.css`.
- Must not steal attention: no flashing, no sound while ringing (only the single join chime).
- The popup must still work if the camera/microphone permission prompt appears inside it.

## Acceptance

- `cd spacecall && npm.cmd run check && npm.cmd test` pass (add tests for: status → dataset/title mapping,
  the 60 s no-answer note, the ringing/expanded geometry function).
- `cd tests && python -m unittest test_risk_spacecall test_risk_bridge test_ui_text` still pass.
- `BASE_PATH=/ally-call npm run build` succeeds (PowerShell: `$env:BASE_PATH='/ally-call'; npm.cmd run build`).
- Take screenshots of the elder page at 360x260 in ringing state and at 480x900 connected (there is a
  Playwright phone-preview setup in `spacecall/tests/phone-preview.*` you can adapt), save them under
  `spacecall/tests/shots/ring-*.png`, and look at them before finishing.

## Report

Commit on this branch with a clear message. Do not push, merge or rebase. End with: files changed, test
results, screenshots taken, and anything you could not verify (e.g. the real Chrome window resize).
