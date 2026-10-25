# Codex spec: higher frame rates for Ally Call (camera video and screen sharing)

Worktree `wt-risk-all`, branch `risk-branch`.
- Do not commit, push or publish.
- Never read `.env` or `config.toml`.

**Allowed files:** `spacecall/src/`, `spacecall/tests/`, `spacecall/.gitignore`, `ally/risk/screenshare.py`,
`tests/test_risk_screenshare.py`.
- Avoid module (`spacecall/server/`) changes. If one is clearly needed, describe it in the report instead; Claude republishes.
- Also remove the committed build artifact `spacecall/server/dist/` from git tracking (`git rm -r --cached`) and add it to
  `.gitignore`. That is the one git command allowed.

## Why
Ganesh wants smoother video and screen sharing. Today:
- **Camera video** is single-flight: the next frame waits until the previous reducer call is acknowledged. That's about 5 fps, adapting
  to 8.
- **Screen share** is about 1 fps, sent serially from Python with a new HTTPS connection per frame.

## Camera video (client)
- Allow up to **3 frames in flight**, with latest-frame-wins: drop a capture rather than queue it when 3 are pending.
- **Target 12 fps at 320×180**, and step up to 15 fps / 480×270 only while health is good (the existing `mediaPolicy` health signal).
- Step down fast on congestion: pending stays at the maximum for more than 1 s, or the receive fps drops.
- Use `requestVideoFrameCallback` or a timer, whichever is steadier. Use `OffscreenCanvas` / `convertToBlob` where supported; fall back
  for iOS Safari.
- The receiver should render the newest frame only and drop stale ones (seq lower than the last drawn).

## Screen share (Python, `ally/risk/screenshare.py`)
- **A persistent connection:** reuse one `http.client.HTTPSConnection` (keep-alive) for the reducer calls. Reconnect on error.
- **A pipeline:** capture and encode in one thread while the previous frame posts. Never queue more than one waiting frame (latest wins).
- **Change detection:** compare a cheap fingerprint of a 64×36 grayscale thumbnail with the last *sent* frame.
  - Skip the frame if it's unchanged (mean absolute difference below a small threshold).
  - Still send a keyframe every 5 s, so a newly joined helper gets an image.
- **Adaptive rate:** up to **4 fps** while the screen is changing. When the post takes longer than 400 ms, drop the JPEG quality
  (0.6 → 0.45) or the width (1280 → 960) before dropping fps.
- Keep every existing safety rule: never raise, never log the token or room, stop on rejection, stop at 30 min, no disk writes.

## Bandwidth guard
- Add a per-call soft budget: if the screen and camera together exceed about **1.2 MB/s** over 10 s, step quality down.
- Show the actual rates in the `&debug=1` overlay.
- Document the free-tier maths briefly in `spacecall/README.md`: about 12.5 GB a month of egress on the free Maincloud plan.

## Tests
- **JS:** in-flight cap and latest-wins, stale-frame dropping on the receiver, the step-up and step-down policy.
- **Python:**
  - unchanged frames are skipped and keyframes go out every 5 s;
  - the connection is reused (one connect for N frames with a fake connection class);
  - quality steps down when posts are slow;
  - the pipeline never queues more than one frame;
  - stop and rejection still work.
- Run:
  - `npm run check`, `npm test` and the build (from PowerShell, or with `MSYS_NO_PATHCONV=1`);
  - `cd tests && python -m unittest test_risk_screenshare test_risk_spacecall`, then full discovery (known sandbox-only failures).

## Report
Files, before and after expected fps for each stream, the bandwidth estimate per minute, the test output, and any server change you'd
recommend.
