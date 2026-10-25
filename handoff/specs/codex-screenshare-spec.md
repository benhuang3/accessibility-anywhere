# Codex spec: screen sharing in Ally Call (the helper sees the screen and judges the site)

Worktree `wt-risk-all`, branch `risk-branch`.
- Do not commit, push or publish.
- Never read `.env` or `config.toml`.
- Do not start `run.py`.

**Allowed files:**
- `spacecall/server/src/` (module)
- `spacecall/src/` (client) and `spacecall/tests/`
- `ally/risk/` (a new `screenshare.py`, plus the hook in `wire.py`)
- `tests/test_risk_screenshare.py` (new)
- `docs/RISK_HANDOFF.md` and `spacecall/README.md`

## Idea
- When the older adult taps **"Call Priya"**, the helper should automatically see their **screen**, not only their face. Then Priya
  can say "that site is fake, close it".
- Tapping Call is the consent, so there's no separate setup step.
- The elder's call page must always show, clearly, **"Priya can see your screen"**, with a big **"Stop sharing"** button.

## Why not the browser's `getDisplayMedia`
It always shows a picker and needs a gesture, so it isn't automatic, and it's awkward for an older adult.

Ally's Python agent already captures the screen. Find the function `_make_guardian` uses for screenshots in `ally/risk/wire.py`, and
reuse it, along with the existing JPEG helper `ally.platforms.base.png_to_jpeg_if_possible`, or Pillow if installed.

## Design
1. **Module** (`spacecall/server`):
   - A new event table, `screen_frame_event` (`room_id`, `seq`, `jpeg` bytes, `width`, `height`).
   - A new reducer, `send_screen_frame(room_id, seq, jpeg, width, height)`. The room id is the capability, the same as the other
     reducers. Validate the room id and the room's status. Reject when the incident is ended or `screen_sharing` is false. Cap the frame
     size at 200 KB.
   - Add `screen_sharing: bool` to the incident (default true when the elder opens it), and a reducer
     `set_screen_sharing(room_id, on)` callable by the room's elder.
   - Keep the existing tables compatible. If adding the column is a breaking schema change for the already-published database, say so;
     Claude will republish with data cleared, since there's no real data yet.
2. **Python sender** (`ally/risk/screenshare.py`):
   - A small class, `ScreenShare(db_url, db_name, room_id, capture)`.
   - On `start()`, a daemon thread:
     - gets an identity token once via SpacetimeDB's HTTP API (`POST /v1/identity`; check the current HTTP docs, and if the endpoint
       differs, use what the docs say);
     - then at about **1 fps** captures the screen, downscales it to a max width of 1280, encodes JPEG at quality about 0.6 (target under
       150 KB), and calls the reducer over HTTP
       (`POST /v1/database/<db>/call/send_screen_frame` with a JSON args array and `Authorization: Bearer <token>`).
   - It stops on `stop()`, when the reducer rejects (sharing off or call ended), or after 30 min.
   - stdlib only (`urllib`), with timeouts. It never raises into the caller, never logs the token or the room id, and never writes
     frames to disk.
   - **`wire._tell`** (spacecall mode only): after opening the elder page, start a `ScreenShare` for that room. Keep at most one running;
     a new call stops the old one. Stop it when the call window closes (the existing call-ended path) and on `RiskWire.stop()`.
   - **Config:** `[risk] screen_share` (bool, default true when `call_service = "spacecall"`). Add it to `ally/config.py` and
     `ally/default_config.toml` (Ben H's files, one key; flag it in the report).
3. **Client:**
   - **Helper view (iPhone first):**
     - The elder's screen is the main view: fit to width, pinch-zoom or tap-to-zoom.
     - The elder's face is a small tile; tapping the tile swaps the two.
     - Show a "Screen updated 1 s ago" freshness hint.
     - Keep audio and the controls as they are.
   - **Elder view:**
     - A persistent glass banner, "Priya can see your screen", with a big "Stop sharing" button. It calls `set_screen_sharing(false)`
       and becomes "Share my screen again".
     - The person must always be able to tell sharing is on.
4. **Privacy:**
   - Screen frames go only to subscribers of that room (same scoping as video).
   - They're event rows, never stored tables.
   - Document honestly that the helper sees everything on screen, including bank pages, which is the point. Sharing starts only on an
     explicit Call tap, and the elder can stop it at any time.

## Tests and checks
- **Python:** `ScreenShare` with a fake HTTP opener and a fake capture:
  - frames are sent at the right rate and under the size cap;
  - it stops on rejection or on `stop()`;
  - no token or room id appears in logs;
  - `_tell` starts it only in spacecall mode with `screen_share` on;
  - a second call stops the first share.
- **Client:** unit-test the pure helpers (the freshness text, the tile-swap state).
- Run `npm run check`, `npm test`, the TS module type-check and the build. In this sandbox, use the `PORTING.md` loader workaround if
  needed. Build from PowerShell or with `MSYS_NO_PATHCONV=1`.
- Python:
  - `cd tests && python -m unittest test_risk_screenshare test_risk_spacecall test_risk_contact test_risk_wire test_config`
  - then full discovery (known sandbox-only failures).

## Report
- Files changed.
- The schema change, and whether it breaks the published database.
- The HTTP endpoints used, with a docs link.
- The test output.
- The exact republish, generate and build commands for Claude.
