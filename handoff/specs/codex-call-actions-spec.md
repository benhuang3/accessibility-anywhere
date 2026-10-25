# Spec: the helper can help on the older adult's screen (close the page, point, make it bigger) through SpacetimeDB

Deadline: about 2.5 hours of build time. A parallel builder is changing the elder's ringing popup in another
worktree (elder parts of `+page.svelte`/`app.css`, `openDockedCall` and a `tabs.onUpdated` listener in
`extension/background.js`). Keep your edits to those two files in the helper section / new handlers so the
branches merge cleanly, and put new logic in new files where you can.

## Goal (demo story)

Grandma is on a gift-card scam page. She taps **Call Priya**. Priya joins on her phone and sees grandma's shared
screen. Priya taps **Close this page**; the scam tab closes on grandma's PC and Ally's bear says
"Priya closed that page for you." Priya can also tap **Point** and then tap a spot on the shared screen; a blue
ring appears at that spot on grandma's real screen with "Priya is pointing here." And **Make it bigger** zooms
grandma's current page one step.

Only these three actions. **No remote clicking, typing or scrolling** — that is what scammers do with remote
desktop tools, and Ally warns about those. Every action is announced on grandma's screen.

## Data path

helper phone tap
→ SpacetimeDB reducer `send_helper_action` (server checks: caller is the joined helper of a live, connected room
  with screen sharing on)
→ event row to the elder's identity only
→ elder call page (already subscribed to its room) posts it to the page window
→ new extension content script `extension/call-relay.js` forwards it to the service worker
→ `background.js` checks the sender tab is the saved `allySpacecall:<tabId>` call tab (session storage) — the only
  trust anchor; reject everything else
→ close tab / zoom in the extension; point (and the done notes) go to Ally over the existing bridge as an
  `{"type":"event","name":"helper_action", ...}` message
→ result goes back the same way, and the elder page calls `report_action_result` so the helper sees ✓ or a short
  failure note.

## Server (`spacecall/server/src/index.ts`, `spacecall/server/tests/server.test.ts`)

- New public **event** table `helper_action_event`: `room_id`, `recipient` (identity), `seq` (u32), `action`
  (string), `x` (f32), `y` (f32), `server_at` (timestamp). Visibility filter `recipient = :sender`, same as the
  other event tables.
- New public event table `action_result_event`: `room_id`, `recipient`, `seq`, `ok` (bool), `server_at`. Same filter.
- Reducer `send_helper_action(room_id, seq, action, x, y)`: live incident, status `connected`, caller is the
  joined **helper**, `screen_sharing` is true (otherwise SenderError "Screen sharing is off"), action in
  `close_tab | point | bigger`, x and y finite in [0, 1] (only meaningful for `point`; send 0,0 otherwise).
  Inserts the event for the joined elder's identity.
- Reducer `report_action_result(room_id, seq, ok)`: caller is the joined **elder**; inserts for the joined helper.
- Tests in the existing harness: helper-only, elder-only, rejected when not connected / sharing off / bad action /
  out-of-range point, recipient derivation (the event goes to the peer, never chosen by the caller).
- Update `spacecall/src/module_bindings/**` to match (the checked-in bindings are hand-mirrored; follow the existing
  files). I will run `spacetime generate` and publish myself afterwards; you do not publish.

## Call page (`spacecall/src`)

- `stdb.ts`: subscribe to the two new tables filtered to the exact room and recipient, like the screen/video events.
- New `spacecall/src/lib/helperActions.ts`: pure helpers + small stores (pending action by seq, last result,
  tap-to-normalized-point maths for the shared screen image, accounting for its displayed rect/zoom).
- **Helper UI** (helper section of `+page.svelte`, `SharedScreen.svelte`, `app.css`): a bar of three large
  buttons on the shared screen view — **Close this page**, **Point**, **Make it bigger** — at least 56 px tall,
  thumb-reachable on a phone in portrait and usable on desktop. Disabled with a short reason when sharing is off
  or the call is not connected. While waiting: "Closing…"; on result: "Closed ✓" / "Couldn't close it" for a
  few seconds. **Point** toggles a point mode ("Tap where you want to point"); the next tap on the shared screen
  sends the point and shows a ring at that spot on the helper's own view for ~3 s; in point mode the existing
  tap-to-zoom does not fire. One action in flight per kind; ignore taps while pending.
- **Elder page**: on a `helper_action_event`, `window.postMessage({source: "ally-call", type: "helperAction", seq,
  action, x, y}, location.origin)`, then wait up to 5 s for `{source: "ally-ext", type: "helperActionResult", seq,
  ok}` from the same window; call `report_action_result` with ok (false on timeout). No visible UI needed on the
  elder page beyond what Ally's bear shows.

## Extension

- New `extension/call-relay.js` content script (add to `manifest.json`, `https://*/*`, document_idle, top frame
  only). It only accepts `message` events where `event.source === window`, `event.origin === location.origin`,
  `data.source === "ally-call"`, and a known type; forwards `{kind: "allyHelperAction", seq, action, x, y}` with
  `chrome.runtime.sendMessage`, and posts the reply back as `ally-ext` `helperActionResult`. Small and inert on
  every other page.
- `background.js`: a `chrome.runtime.onMessage` handler for `allyHelperAction` (add to the existing listener if
  there is one). Reject unless `sender.tab.id` has an `allySpacecall:<id>` session entry. Validate fields.
  - Target tab = the active tab of the most recently focused **normal** window that is not the call window. If
    none, or it is a `chrome://` / extension page, `ok: false`.
  - `close_tab`: remove that tab. `bigger`: one zoom step on that tab (reuse the existing zoom-step logic, but
    on the target tab, not `activeTab()`, which may be the call popup).
  - `point`: no browser change.
  - Then send Ally `{"type":"event","name":"helper_action","action", "ok", "x", "y", "name": <saved helper name>}`
    with the existing `send()`. For `point`, ok = whether `send()` succeeded.

## Ally (Python)

- `ally/bridge.py` (Ben H's file: keep it to a few lines, mirror the existing `on_page` dispatch, run hooks on a
  daemon thread, swallow hook errors): dispatch event `helper_action` → `self.on_helper_action(msg)` and event
  `risk_call_ended` → `self.on_call_ended(msg)` when those attributes are set. `wire.attach` already sets
  `bridge.on_call_ended`, but the bridge never calls it today; this fixes that. Extend `tests/test_risk_bridge.py`.
- `ally/risk/wire.py`: `RiskWire.on_helper_action(msg)`, wired in `attach()` (`bridge.on_helper_action = ...`):
  - `close_tab` ok → the bear shows a done note "Priya closed that page for you." (helper name from the message,
    sanitized, max 30 chars) and Ally says it. Any open risk card / risk question about that page goes away and
    **no follow-up question** ("Are you sure?" etc.) appears for it afterwards. Find how the coordinator/offers
    work and do the smallest correct thing; test the outcome.
  - `bigger` ok → done note "Priya made the page bigger."
  - `point` → the overlay highlight (`ui.highlight(box)`, box = left, top, width, height in screen pixels) as a
    ~140 px square centred on the point, mapped from the normalized x, y onto the same screen area that the screen
    share captures (find how `ScreenShare.capture_screen` / `agent.platform.screenshot_png()` and the overlay
    measure the screen; DPI matters on this 200% laptop). Plus done note "Priya is pointing here." Clear the
    highlight after 8 s unless a newer point replaced it.
  - Ignore malformed messages; never raise.
- **Done note in the overlay** (`ally/overlay/layout.py`, `ally/overlay/overlay.py`, `tests/test_overlay.py`):
  add card kind `"done"`. `card_tone` maps it to the existing toast tone (check icon, happy bear, self-dismiss),
  whatever the text. A done card may replace a risk card (the risk-beats-info rule stays for `"info"` only).

## Allowed files

`spacecall/server/src/index.ts`, `spacecall/server/tests/server.test.ts`, `spacecall/src/module_bindings/**`,
`spacecall/src/lib/stdb.ts`, `spacecall/src/lib/helperActions.ts` (new), `spacecall/src/lib/SharedScreen.svelte`,
`spacecall/src/routes/+page.svelte` (helper section + elder relay logic only), `spacecall/src/lib/app.css`
(helper action styles only), `spacecall/tests/helperActions.test.ts` (new), `extension/call-relay.js` (new),
`extension/manifest.json`, `extension/background.js` (new handler only), `ally/bridge.py`, `ally/risk/wire.py`,
`ally/overlay/layout.py`, `ally/overlay/overlay.py`, `tests/test_risk_bridge.py`, `tests/test_risk_spacecall.py`,
new `tests/test_risk_helper_action.py`, `tests/test_overlay.py`, `spacecall/README.md` (short section).
Nothing else. Never touch `.env`, `config.toml`, `state/`, `logs/`.

## Acceptance

- `cd spacecall && npm.cmd run check && npm.cmd run check:server && npm.cmd test` pass.
- `cd tests && python -m unittest test_overlay test_risk_bridge test_risk_spacecall test_risk_helper_action test_risk_wire test_risk_cooldown`
  pass, plus the AGENTS.md list `test_config test_registry test_brain test_network test_runtime test_handsfree`
  (note any failures that also fail on the base commit).
- `BASE_PATH=/ally-call npm run build` succeeds.
- Screenshot the helper view with the action bar at phone size (390x844) and desktop (1280x800) using the
  Playwright phone-preview setup in `spacecall/tests/`, save as `spacecall/tests/shots/actions-*.png`, look at them.
- Do not start Ally (`run.py`) or use port 8765.

## Report

Commit on this branch. Do not push, merge, rebase or publish. End with: files changed, test results, screenshots,
and what is unverified (live Maincloud, real phone, real Chrome tab close).
