# Spec: Priya's tools become Highlight, Use their screen, and Close tab (live, drag-able, through SpacetimeDB)

About 90 minutes of build time. A parallel builder (branch ganesh/call-mini) is changing the elder's call window
(mini-player, elder parts of the call page, `extension/background.js` window open/expand code, the `_tell` /
call-open path and `on_call_ended` in `ally/risk/wire.py`, and overlay placement via a new `set_reserved` hook).
Stay out of those parts so the branches merge cleanly.

## Current state (read the code first)

`ea81d99` added three helper actions: `close_tab`, `point`, `bigger`. Path: helper phone -> SpacetimeDB reducer
`send_helper_action(room_id, seq, action, x, y)` -> `helper_action_event` for the elder only -> elder call page
`createElderRelay` (`spacecall/src/lib/helperActions.ts`) -> `window.postMessage` -> `extension/call-relay.js` ->
`background.js handleHelperAction` (checks the saved `allySpacecall:<tabId>` entry) -> Ally bridge event
`helper_action` -> `RiskWire.on_helper_action` -> overlay. The elder page reports results back with
`report_action_result`.

## What Ganesh wants instead

Three buttons on the helper's shared-screen view:

1. **Highlight** (a mode toggle). Press and drag on the shared screen: a live highlighter follows the finger on the
   older adult's real screen, like a translucent yellow marker pen (about 45% opacity, about 28 DIP wide stroke,
   round caps, plus a small solid dot at the current finger position). It updates live while dragging. The
   stroke fades out about 2.5 s after the finger lifts. A single tap draws a dot that fades the same way. The
   helper's own view shows the same stroke locally, immediately.
2. **Use their screen** (a mode toggle). The helper's finger acts like a touchscreen on the older adult's PC:
   a tap = a left click at that point; a drag = scrolling, like a phone (vertical and horizontal mouse-wheel
   steps proportional to the drag distance). No right-click, no typing, no keyboard at all. Each click shows a
   brief ripple on the older adult's screen at that spot, so they can see what happened.
3. **Close tab** (one tap, as today).

Remove **Make it bigger** and the old single-tap **Point** everywhere (server allowlist, UI, extension, Python,
tests, README). When neither mode is on, the shared screen keeps its existing tap-to-zoom / scroll behaviour;
when a mode is on, gestures go to the mode instead (use pointer events and `touch-action: none` on the image in a
mode). Exactly one mode at a time; tapping the active mode button turns it off. Buttons at least 56 px tall,
labelled with text, current mode clearly shown as pressed (`aria-pressed`). Same disabled rules as today (call
connected and screen sharing on).

## Transport (no schema change)

Keep the tables. Change only the reducer's action allowlist in `spacecall/server/src/index.ts`:
`close_tab`, `hl_start`, `hl_move`, `hl_end`, `touch_start`, `touch_move`, `touch_end`. Points are normalized
x, y in [0, 1] of the shared screen image, which is the older adult's whole primary screen.

- The helper throttles `*_move` to at most 20 per second (send the latest point, drop older ones; never queue).
  Always send `*_start` and `*_end`. If a reducer call fails, keep going.
- Only `close_tab` uses results (`report_action_result`) and shows "Closing…" / "Closed ✓". Streaming events
  get no per-event results.
- Elder page relay: stream events are forwarded immediately with no waiting. `background.js` validates each one
  (keep the existing call-tab check; cache the session lookup briefly so 20 msgs/s stay cheap) and forwards to
  Ally as bridge event `helper_action` with `action`, `x`, `y`, `helper_name`. `close_tab` keeps its current
  behaviour.
- Update `spacecall/src/module_bindings` only if generated types change (they should not; the reducer signature
  is the same). I will republish the module and regenerate bindings myself.

## Ally side

- `RiskWire.on_helper_action` handles the new actions. Ignore malformed messages; never raise. Map normalized
  points to the primary screen the same way the existing point code does (physical pixels, verified correct on
  this 200% laptop).
- **Highlighter drawing** in the overlay (Tk thread, through the overlay queue): a full-screen, borderless,
  topmost, **click-through** window (layered + `WS_EX_TRANSPARENT` via ctypes so the older adult's mouse and
  Priya's touches pass through), transparent background, the stroke drawn on a Canvas; translucency via the
  window `-alpha` with a transparent-colour key. Put drawing logic in a new `ally/overlay/marker.py` plus a
  small `Overlay.marker(phase, x, y)` method; scale widths with the overlay's DPI scale. `NullUI` may lack it:
  guard with `getattr`. Remove the old 140 px square point code.
- **Touch input**: new `ally/risk/remote_touch.py` (stdlib ctypes `SendInput`, Windows only, a no-op elsewhere).
  Use absolute normalized coordinates (`MOUSEEVENTF_ABSOLUTE`, 0..65535 on the primary monitor) so DPI does not
  matter. `touch_start` remembers the point; `touch_move` beyond a small threshold (about 12 DIP) switches to
  scrolling and sends wheel steps for the delta since the last move (vertical `MOUSEEVENTF_WHEEL`, horizontal
  `MOUSEEVENTF_HWHEEL`; dragging the finger up scrolls the page down, like a phone); `touch_end` without
  scrolling = click (move, left down, left up) and a ripple via the marker. Moves the real pointer only for
  clicks; restore nothing. A stuck state is impossible: no button is ever held between messages.
- Announce once per call, on the first `touch_*` or `hl_*` event: done-note "Priya is helping on your screen."
  (and say it). `close_tab` keeps "Priya closed that page for you." Do not announce every stroke or tap.
- Safety: act only while a call is open (the wire already knows when the call opened and gets `on_call_ended`);
  ignore all touch and highlight events after the call ends.

## Allowed files

`spacecall/server/src/index.ts`, `spacecall/server/tests/server.test.ts`, `spacecall/src/lib/helperActions.ts`,
`spacecall/src/lib/SharedScreen.svelte`, `spacecall/src/routes/+page.svelte` (helper section and the elder relay
wiring only), `spacecall/src/lib/app.css` (helper action styles only), `spacecall/tests/helperActions.test.ts`,
`extension/call-relay.js`, `extension/background.js` (`handleHelperAction` only), `ally/risk/wire.py`
(`on_helper_action` and its helpers, `attach` wiring only), new `ally/risk/remote_touch.py`, new
`ally/overlay/marker.py`, `ally/overlay/overlay.py` (marker method + queue command only), `tests/test_overlay.py`,
`tests/test_risk_helper_action.py`, `tests/test_risk_spacecall.py`, new `tests/test_remote_touch.py`,
`spacecall/README.md` (helper actions section). Nothing else. Never touch `.env`, `config.toml`, `state/`, `logs/`.
Do not start Ally or use port 8765. Do not publish.

## Acceptance

- `cd spacecall && npm.cmd run check && npm.cmd run check:server && npm.cmd test` pass.
- `cd tests && python -m unittest test_overlay test_risk_helper_action test_risk_spacecall test_remote_touch test_risk_bridge test_risk_wire test_risk_cooldown`
  plus `test_config test_registry test_brain test_network test_runtime test_handsfree` pass (note failures that
  also fail on the base commit). Tests must not move the real mouse or open real windows (fake `SendInput` and Tk).
- `$env:BASE_PATH='/ally-call'; npm.cmd run build` succeeds.
- Screenshots of the helper view at iPad portrait (820x1180), iPad landscape (1180x820) and phone (390x844), with
  Highlight mode on and a stroke drawn, saved as `spacecall/tests/shots/touch-*.png`, inspected.
- Files are CRLF; preserve line endings. Git is not writable from your sandbox: do not commit. End with a short
  report: files changed, test results, screenshots, what is unverified.
