# Codex spec A: screen share at maximum speed (Python sender)

Worktree `wt-risk-all`, branch `risk-branch`.
- Do not commit.
- Never read `.env` or `config.toml`.

An earlier job may have left partial edits in `ally/risk/screenshare.py`. Read the current file and build on it.

**Allowed files:** `ally/risk/screenshare.py`; `ally/risk/wire.py` (only the `screen_capture` default near
`screen_capture=lambda: agent.platform.screenshot_png()`); `tests/test_risk_screenshare.py`.
Another job is editing `spacecall/`; don't touch it.

## Measured on the target laptop
- `agent.platform.screenshot_png()` takes about **650 ms** per frame, and it's the bottleneck.
- `PIL.ImageGrab.grab()` plus `thumbnail(1280)` plus JPEG q60 takes about **103 ms** total, about 74 KB.
- Maincloud HTTPS round trips are 70–85 ms on a kept-alive connection; the first is about 330 ms.
- The live user report: **the helper's view only updated about every 5 seconds.** The change detection or the bandwidth guard is
  suppressing almost every frame.

## Ganesh's decision: maximum smoothness, energy cost doesn't matter
1. **Remove the change detection, the 5 s keyframe logic and the 1.2 MB/s bandwidth guard** from the sender entirely. Every frame is
   sent.
2. **A fast capture path:**
   - Use `PIL.ImageGrab.grab()` on the primary screen, returning a PIL image directly (no PNG round trip).
   - Downscale to a max width of 1280 and JPEG q60. Use `Image.reduce` or BILINEAR, whichever is faster.
   - Fall back to the injected capture callable if ImageGrab is unavailable or raises. The existing `capture` parameter must keep
     working for tests.
3. **Throughput:**
   - Capture in a dedicated thread, at as high a rate as it can manage, up to **15 fps** (the cap).
   - Send through **3 kept-alive HTTPS connections** in parallel (3 in flight).
   - Latest-frame-wins: never queue stale frames; drop one if all 3 senders are busy.
   - Per-room `seq` increases monotonically; the receiver already drops older seq.
4. **Adaptivity, the only one kept:** if posts start failing or take more than 1 s, temporarily lower the JPEG quality to 0.45 and the
   width to 960. Never drop below 5 fps for bandwidth reasons.
5. Keep the safety rules:
   - never raise;
   - no token or room id in logs;
   - stop on reducer rejection (sharing off or call ended), on `stop()`, or at 30 min;
   - no disk writes.

## Tests
- The fast path is used when ImageGrab works; the fallback is used when it raises.
- No frame is skipped as "unchanged", and the fps cap is respected with a fake clock.
- At most 3 in flight; seq is monotonic.
- The quality step-down happens on slow posts.
- `stop()` and rejection work.

Run: `cd tests && python -m unittest test_risk_screenshare test_risk_spacecall`.

## Report
Files changed, and the expected fps given the measured timings.
