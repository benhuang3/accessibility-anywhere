# Spec: the call is a small mini-player in Ally's corner, and "Call Priya" never triggers "Are you sure?"

About 75 minutes of build time. A parallel builder (branch ganesh/call-touch) is changing the helper's
action buttons, the `helper_action` handling in `ally/risk/wire.py`, a new highlighter in the overlay, and the
helper section of the call page. Stay out of those parts; keep your edits small and in the places named here so
the branches merge cleanly.

## Product intent (from Ganesh, the owner)

"Calling Priya summons Ally to life": the call takes the same corner and roughly the same space as Ally's bear
and card, on top of the risky site, like a Zoom / Google Meet mini-player where you mainly see the other person's
face. No side bar, Chrome is never resized, and no extra pop-ups add clutter.

## 1. No "Are you sure?" after tapping Call Priya

Today: a risky page shows Ally's warning ("Want me to close this page?" with Yes, close it / Keep it open and a
"Call Priya" link). After tapping **Call Priya**, a second panel appears: "Are you sure? Scammers often say it's
safe to continue." That follow-up must appear **only** after the person explicitly chooses **Keep it open** (a
real "no" from a button or voice).

- First find out what answers the first offer with "no" (or otherwise reaches the `confirm_close` branch in
  `RiskWire` ~line 242) after the extra action runs. Suspects: the overlay's extra-action path
  (`_start_extra` / `extra_done` / button margin-click handlers in `ally/overlay/overlay.py`), focus moving to the
  new call popup, a timeout path. Write a failing test that reproduces it, then fix the cause.
- New behaviour once the call has actually opened (the `_tell` path returns after `_open_call` succeeded): Ally's
  warning card and its question close quietly. Resolve the offer as `"dismissed"` with source `"call"` so no
  follow-up question runs and nothing is closed, and snooze that page's warning for `HOLD_SECONDS` (Priya now
  handles it). If the call could not open, keep today's behaviour (the panel shows the "couldn't reach" note).
- Tests in `tests/test_risk_wire.py` or a new `tests/test_call_mini.py`, plus `tests/test_overlay.py` if the cause
  is in the overlay.

## 2. The call window stays a mini-player (no docking)

`extension/background.js` today opens a 360x340 ringing popup in the bottom-right corner and, when the page
reports `connected`, grows it into a docked side panel and shrinks Chrome. Change:

- Never dock and never resize or move Chrome. Remove the expand-on-connected behaviour and the browser-bounds
  restore (nothing to restore any more). Keep the `tabs.onUpdated` title listener only if something still needs it.
- The window stays in the bottom-right corner: 380 x 360 DIPs (outer size), 16 px from the work-area edges, both
  while ringing and while connected.
- Update `tests/test_risk_spacecall.py` and `tests/test_call_ring.py` to the new behaviour.

Elder page (`spacecall/src/routes/+page.svelte` elder parts, `spacecall/src/lib/app.css` elder styles,
`spacecall/src/lib/ringing.ts`):

- Ringing state stays as it is.
- Connected state in that small window: Priya's live video fills the window (object-fit: cover), like a Meet
  mini-player, with her name in a small label. Overlaid along the bottom: compact **Mute** and **End call** icon
  buttons with text labels (at least 48 px tall), and one small line "Priya can see your screen" with a **Stop**
  link (the existing sharing toggle). No self-view tile, no top bar, no big sharing banner, no footer.
  Before her video arrives: her initial in the circle with "Priya is here".
- The connected layout must fit a 380 x 290 viewport with no scrolling. Take screenshots of ringing and
  connected at 380 x 290 (Playwright preview setup in `spacecall/tests/`), save as
  `spacecall/tests/shots/mini-*.png`, and look at them.
- Keep the existing title/dataset status (`ringing` / `connected` / `ended`) and the join chime.

## 3. Keep the call window on top of the risky site (Windows)

A Chrome popup is not always-on-top, so clicking the scam page would hide the call. Ally keeps it on top:

- New `ally/risk/callwindow.py` (stdlib `ctypes`, Windows only, a no-op elsewhere): find visible top-level
  windows of class `Chrome_WidgetWin_1` whose title contains "Ally Call" (the page title ends in "· Ally Call";
  Chrome may append " - Google Chrome"), set them `HWND_TOPMOST` with `SWP_NOMOVE|SWP_NOSIZE|SWP_NOACTIVATE`,
  and return the window rect (physical pixels). Never steal focus.
- `RiskWire`: after a spacecall call opens, run a daemon loop every 1 s that applies topmost, until
  `on_call_ended` or 2 hours or `stop()`. Expose the latest call-window rect (or None).
- Unit-test the window filter and the loop's start/stop with fakes (no real Win32 calls in tests).

## 4. Ally's own panels sit above the call window

While a call window is up, Ally's cards and done-notes (e.g. "Priya closed that page for you.") must not cover it.
They stack directly above it in the same corner, 12 px gap, right edges aligned (or right-aligned to the work area
if that is simpler), and the bear sits on top of the call window rather than beside it.

- Add a small hook on the overlay, e.g. `Overlay.set_reserved(rect_or_None)` (thread-safe through its queue),
  that the wire calls with the call window rect from step 3 (and None when the call ends). `ally/overlay/layout.py`
  `corner_origin` gains the reserved area as an input; pure tests for it in `tests/test_overlay.py`.
- `NullUI` and other UIs may lack the method: guard with `getattr`.

## Allowed files

`extension/background.js` (call window open/expand/restore code only), `spacecall/src/routes/+page.svelte` (elder
parts only), `spacecall/src/lib/app.css` (elder styles only), `spacecall/src/lib/ringing.ts`,
`spacecall/tests/ringing.test.ts`, `ally/risk/wire.py` (the `_tell` / call-open path, `on_call_ended`, `stop`,
`attach`, and the follow-up-question fix only), new `ally/risk/callwindow.py`, `ally/overlay/overlay.py`,
`ally/overlay/layout.py`, `tests/test_overlay.py`, `tests/test_risk_wire.py`, `tests/test_risk_spacecall.py`,
`tests/test_call_ring.py`, new `tests/test_call_mini.py`, `spacecall/README.md` (update the ringing section).
Nothing else. Never touch `.env`, `config.toml`, `state/`, `logs/`. Do not start Ally or use port 8765.

## Acceptance

- `cd tests && python -m unittest test_overlay test_risk_wire test_risk_spacecall test_call_ring test_call_mini test_risk_bridge test_risk_helper_action test_risk_cooldown`
  plus `test_config test_registry test_brain test_network test_runtime test_handsfree` pass (note any failure that
  also fails on the base commit).
- `cd spacecall && npm.cmd run check && npm.cmd test` pass; `$env:BASE_PATH='/ally-call'; npm.cmd run build` succeeds.
- Screenshots above, inspected.
- Files are CRLF; preserve line endings. Git is not writable from your sandbox: do not commit. End with a short
  report: files changed, what caused the spurious "Are you sure?", test results, screenshots, what is unverified.
