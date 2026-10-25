# Codex spec B: Ally Call — helper phone layout, landscape, and max camera fps (client)

Worktree `wt-risk-all`, branch `risk-branch`.
- Work only in `spacecall/src/` and `spacecall/tests/`.
- Do not commit, push or publish.
- Never read `.env` or `config.toml`.

Another job is editing `ally/risk/screenshare.py`; don't touch Python.

## Live report from a real iPhone (Safari), the helper side
1. **The shared screen is zoomed far into one part.** You can't see the whole screen.
2. **Landscape breaks the layout.**
3. **There's no natural place for the elder's face** (the computer user's camera).
4. Screen updates only arrived about every 5 s. The sender is being fixed separately, but make sure the client does no throttling of its
   own: render every received screen frame immediately.

Ganesh's decision: **maximum smoothness; ignore energy and bandwidth cost.**

## Helper view: redesign for the iPhone
**Shared screen:**
- Always show the **whole screen**, letterboxed (`object-fit: contain` on the image or canvas, sized to the available area).
- Never crop. Never use CSS transforms that start zoomed.
- The default is "fit".
- Pinch-to-zoom and pan are optional extras. Double-tap toggles between fit and 2×, and there's a visible "Fit screen" button whenever
  it's zoomed.

**Portrait layout:**
- A top status pill.
- The screen fills the middle, at the screen's own aspect ratio (16:10 / 16:9), as large as possible.
- Below it, a row with the **elder's face tile** (about 120×120, rounded, labelled with the name, for example "Mom") and the controls
  (mute, end).
- Their face always has this fixed home, and never overlaps the screen.

**Landscape layout:**
- The screen takes the full height on the left, about 75% of the width, fitted.
- A right-hand rail holds the elder's face tile at the top, the helper's own self view small below it, and the controls at the bottom.
- The status pill floats over the top of the screen.
- Use CSS grid with `@media (orientation: landscape)`. Respect `env(safe-area-inset-*)` for the notch and the home bar.
- No horizontal scrolling. Use `100dvh` heights, so Safari's toolbar doesn't break the layout.

**Other details:**
- The "Show face" swap is replaced by the fixed tile. Tapping the tile can enlarge the face temporarily.
- The helper's own self view: a small corner tile, optional to hide.
- Keep "Screen updated N s ago", but show it only when the last frame is more than 2 s old.

## Elder view (PC)
Keep it. Make sure the screen-share banner and controls don't cover the helper's face.

## Camera video: maximum
- Raise the defaults to **15 fps at 480×270**, stepping up to **24 fps at 640×360** while healthy.
- Up to **4 frames in flight**, latest-frame-wins.
- Remove any client-side bandwidth guard that limits fps for cost reasons. Keep only the congestion step-down, when frames are actually
  piling up.
- The receiver draws every newest frame, using `requestAnimationFrame`, and drops stale ones.

## Checks
- Unit-test the pure layout and zoom helpers (fit math for portrait and landscape, the double-tap toggle) and the fps policy.
- Run `npm run check`, `npm test`, and the build with `BASE_PATH=/ally-call`, from PowerShell or with `MSYS_NO_PATHCONV=1`.

## Report
Files changed, a short description of both layouts, and anything that needs checking on the real iPhone.
