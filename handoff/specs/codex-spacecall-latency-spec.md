# Codex spec: Ally Call — fix the "Connection is slow" chip and reduce lag

Worktree `wt-risk-all`, branch `risk-branch`.
- Work only in `spacecall/src/` and `spacecall/tests/`.
- Don't change the module schema in `spacecall/server/`. It's already published, and any schema change needs a republish, which
  Claude does. If a server change is clearly worth it, describe it in the report instead.
- Do not commit, push or publish.
- Never read `.env` or `config.toml`.

## Live findings (Ganesh, real devices: Windows PC Chrome as elder, iPhone Safari as helper, over Maincloud)
- The call works both ways on the iPhone.
- **"Connection is slow" is ALWAYS shown**, even when the call is fine. It also showed in a headless two-window test on one machine.
- Lag is noticeable: "a little slow, not terrible".

## Read first
`src/lib/callRuntime.ts` (capture, send, playback and the adaptive quality logic), `src/lib/stdb.ts`, `src/lib/mediaSettings.ts`,
`src/lib/pcm-capture-worklet.ts`, `src/routes/+page.svelte`, `server/src/index.ts` (read only), and `PORTING.md`.

## 1. A correct slow indicator
- Find why the chip is always on: the threshold, a unit mix-up (ms vs s), a metric that never updates, or the chip state never clearing.
- Base it on real receive-side health, over a rolling window of about 5 s:
  - video fps received (slow if under 2 fps for 5 s);
  - the audio playback buffer underrunning repeatedly;
  - the send queue backing up (frames dropped or pending more than N).
- Show the chip only after 5 s of sustained trouble, and hide it after 5 s of health. No flicker.
- **Add a debug overlay.** When the hash contains `&debug=1`, show a small panel with:
  - sent and received fps;
  - audio chunks per second;
  - kbps up and down;
  - the playback buffer in ms;
  - pending reducer calls;
  - an estimated one-way delay, using the server timestamp on received events if the event carries one; if not, say so.

## 2. Less lag (keep it simple and robust; no WebRTC)
Measure first by reasoning from the code, then apply the safe wins:
- **Audio:**
  - Chunk duration: aim for about 40 ms chunks if it's larger now.
  - Playback jitter buffer: target about 120 ms, cap about 300 ms. When the buffer grows past the cap, drop the oldest audio rather
    than letting the delay grow forever. This is the usual cause of "slow".
  - Resume the AudioContext as today.
- **Video:**
  - Never queue frames. If the previous frame's reducer call hasn't finished, skip the capture (latest-frame-wins).
  - JPEG quality about 0.6.
  - Use `createImageBitmap` for decode-and-draw if that's cheaper than `<img>`.
- The adaptive step-up (320×180 at 5 fps → 480×270 at 8 fps) should only happen while health is good. Step back down quickly when it
  isn't.
- Keep everything working in iOS Safari: no APIs Safari lacks without a fallback, and AudioWorklet resume on the tap.

## Tests and checks
- Unit-test the pure logic in `spacecall/tests/`:
  - the health and chip state machine, given synthetic metric streams;
  - the jitter-buffer trimming policy;
  - latest-frame-wins.
- Run `npm run check`, `npm test`, and the build. In this sandbox, use the Vite loader workaround from `PORTING.md` if needed, but don't
  commit it. Note: run the build from PowerShell, or set `MSYS_NO_PATHCONV=1`, because Git Bash rewrites `BASE_PATH=/ally-call`.

## Report
- The root cause of the always-on chip.
- Each latency change and its expected effect in ms.
- The debug overlay fields.
- The test output.
- Any server change you'd recommend.
