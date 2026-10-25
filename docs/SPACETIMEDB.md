# How Ally uses SpacetimeDB

SpacetimeDB is the real-time backbone of **Ally Call**: when Ally catches a scam in progress, an older adult can
tap **Call Priya** and a trusted helper joins from their phone, sees the elder's screen live, talks to them, and can
point at, tap, scroll or close what's on that screen. Every byte of that call (audio, camera video, screen frames,
the helper's gestures and their results) and all of the call's state flow through one SpacetimeDB module that we
wrote in TypeScript. The same module also stores each user's private **face profile** for the eye tracker.

There is no separate application server, media server, WebRTC signalling server or database: the module *is* the
backend. Clients call reducers; reducers validate and write rows; subscribed clients receive those rows in real time.

| | |
|---|---|
| Module | `spacecall/server/src/index.ts` (+ `profile.ts`), TypeScript, `spacetimedb` 2.13, published to Maincloud as `ally-call` |
| Clients | the call web app (`spacecall/src/`, SvelteKit, GitHub Pages) on the elder's PC and the helper's phone; the Python agent over SpacetimeDB's HTTP API (`ally/risk/screenshare.py`, `ally/profile_store.py`) |
| Generated bindings | `spacecall/src/module_bindings/` (`spacetime generate`) |
| Schema | 8 tables (2 private state, 5 event tables, 1 private profile), 14 reducers, 1 lifecycle reducer, 2 views, 5 row-level visibility filters |
| Tests | 79 TypeScript tests (15 against the module itself, 64 for the call app) and 75 Python tests on the SpacetimeDB paths (see Testing) |

---

## 1. Why SpacetimeDB

The first version of "Call Priya" opened the helper's standing Google Meet room and scripted Meet's page. It broke
whenever Meet's page changed, made unsigned guests wait to be admitted, and exposed a standing link to anyone
watching the screen (the research brief: `handoff/specs/research-spacetimedb.md`). We needed:

- **a fresh, private room per incident** that expires on its own,
- **two fixed roles** (elder, helper) enforced by the server, not the client,
- **low-latency fan-out of binary media** to exactly one recipient,
- **custom real-time messages** (the helper's highlight, tap, scroll and close-tab gestures, and their results),
- **no server to run** at a hackathon, and one place for all the rules.

SpacetimeDB gives all of that in one module: reducers are the API, tables are the state, event tables are the
real-time message bus, visibility filters are the privacy rules, and Maincloud hosts it. Google Meet stays as the
fallback (`[risk] call_service = "meet"`).

## 2. Architecture

```
 Elder's PC                                         SpacetimeDB (Maincloud, module "ally-call")        Helper's phone
 ───────────                                        ─────────────────────────────────────────          ──────────────
 Ally agent (Python)                                tables                                             call web app
   risk wire: scam detected ──"Call Priya"──┐         incident   (private)  room state, expiry          (SvelteKit)
   ScreenShare: JPEG frames ─HTTP reducer───┼──►      presence   (private)  roles, identities, joined
     send_screen_frame / screen_share_ready │         face_profile (private) one row per identity
   remote touch: SendInput ◄────────────────┼──┐    event tables (fan-out, never stored)
   overlay marker: highlight ◄──────────────┤  │      audio_frame_event   ──► recipient only ─────────► plays audio
 Chrome                                     │  │      video_frame_event   ──► recipient only ─────────► camera tile
   call web app (elder side)                │  │      screen_frame_event  ──► helper only  ─────────► shared screen
     open_incident / join_call / heartbeat ─┘  │      helper_action_event ──► elder only   ◄──────────── tap / drag /
     send_audio_frame / send_video_frame ────► │      action_result_event ──► helper only  ─────────►   highlight /
     helper_action_event ──► call-relay.js ────┘    views: call_incident, own_face_profile               close tab
       (extension) ──► Ally agent                   lifecycle: client_disconnected ends the call
     report_action_result ──►
```

## 3. Schema

All in `spacecall/server/src/index.ts` unless noted.

| Table | Kind | Purpose |
|---|---|---|
| `incident` | private | One row per call: `room_id` (primary key), incident `kind`, `status` (`ringing` → `responding` → `connected` → `ended`), timestamps, helper name, `screen_sharing` |
| `presence` | private | One row per role slot (`<room>:elder`, `<room>:helper`): the SpacetimeDB `identity`, connection id, `joined`, last heartbeat. Indexed by room and identity (btree) |
| `audio_frame_event` | public **event** table | 40-50 ms of 16 kHz mono PCM16 per row, sender, recipient, sequence number, level |
| `video_frame_event` | public **event** table | One 320x180 or 480x270 JPEG camera frame per row |
| `screen_frame_event` | public **event** table | One JPEG frame of the elder's screen (up to 1280 px wide, 200 KB) for the helper |
| `helper_action_event` | public **event** table | A helper gesture: `close_tab`, `hl_start/move/end` (highlight), `touch_start/move/end` (tap and drag), normalised x/y |
| `action_result_event` | public **event** table | Whether the elder's PC carried out an action, back to the helper |
| `face_profile` | private (`profile.ts`) | The eye tracker's face profile, one row per identity, JSON up to 64 KB |

Event tables carry the call's media and gestures: rows are delivered to subscribers and never persisted, so
SpacetimeDB acts as a typed, authenticated, per-recipient real-time bus.

## 4. Reducers

| Reducer | Who calls it | What it enforces |
|---|---|---|
| `open_incident(room_id, kind, helper_name)` | elder | Room id format (16-64 url-safe chars, a capability), `kind` from an allow-list (never page text or a URL), helper name sanity; creates the incident and claims the elder slot |
| `helper_responding(room_id)` | helper | Claims the helper slot; `ringing` → `responding` |
| `join_call(room_id, role)` | either | Must already hold that role; both joined → `connected` |
| `heartbeat(room_id)` | either | Every 15 s; refreshes presence and runs lazy expiry |
| `end_call(room_id)` | either | Members only |
| `client_disconnected` (lifecycle) | SpacetimeDB | A dropped connection ends the call and marks the slot not joined |
| `send_audio_frame(...)` | either | Connected and joined; 16 kHz mono, 1280 or 1600 bytes, finite level; writes one event row for the *peer* only |
| `send_video_frame(...)` | either | Only 320x180 or 480x270, real JPEG magic bytes, under 128 KB; peer only |
| `set_screen_sharing(room_id, on)` | elder only | The elder can pause or resume sharing at any time |
| `send_screen_frame(...)` | Ally agent (HTTP) | Sharing on, JPEG, size limits; delivered to the joined helper only |
| `screen_share_ready(room_id)` | Ally agent (HTTP) | Capability preflight so the agent waits without taking screenshots |
| `send_helper_action(...)` | helper only | Joined helper, sharing on, action allow-list, coordinates in [0, 1] |
| `report_action_result(room_id, seq, ok)` | elder only | Closes the loop so the helper sees "done" |
| `save_face_profile(profile_json)` / `delete_face_profile()` | the agent (HTTP) | Under 64 KB, valid versioned JSON; owner = caller's identity |

Shared guards: `validateRoom`, `liveIncident` (two-hour expiry, committed by returning normally rather than
throwing), `claimRole` (one identity per role, and the elder and helper must be different devices), `member`,
`recipient` and `actionPeer` (only the joined peer of a connected call).

## 5. Privacy and security, enforced by the server

- **Per-incident capability rooms**: a fresh random room id per call; nothing guessable or standing.
- **Roles are server state**, not client claims: each reducer checks the caller's `identity` against `presence`.
- **Row-level visibility filters** on all five event tables (`WHERE recipient = :sender`), so even a modified client
  that subscribes to everything only ever receives frames and actions addressed to it.
- **Views** (`call_incident`, `own_face_profile`) are the only read path to the private tables, filtered by the
  caller's identity. The face profile is never readable by anyone else.
- **Validation everywhere**: frame sizes, formats and JPEG magic bytes, action allow-lists, coordinate bounds, name
  length and control characters; `SenderError` gives the client a plain reason.
- **Automatic expiry**: calls end after two hours (lazy, on the next operation or heartbeat), and on disconnect.
- **The elder stays in control**: only the elder can pause screen sharing, the helper can never type, and the agent
  re-validates every helper action before acting (`ally/risk/wire.py` `on_helper_action`).
- **No page content leaves the PC** through the incident: only the allow-listed incident kind and the helper's name.

## 6. Data flows in detail

**Starting a call.** The risk wire (`ally/risk/wire.py`) builds a fresh room link with a random room id
(`secrets.token_urlsafe(16)`) and opens the
call page in a docked mini-player; the page calls `open_incident` and the helper's phone gets a push with the link.
The helper opens it, `helper_responding` then `join_call`; when both are joined the incident becomes `connected` and
both sides' subscriptions (`spacecall/src/lib/stdb.ts`, `onInsert` handlers) start receiving media.

**Audio and camera.** An AudioWorklet (`pcm-capture-worklet.ts`) cuts the microphone into 16 kHz PCM frames and
`send_audio_frame`s them; the camera is encoded to small JPEGs (`jpeg.ts`, `mediaPolicy.ts`) and sent with
`send_video_frame`. The peer's `audio_frame_event` / `video_frame_event` handlers play and draw them.

**Screen sharing.** The Python agent captures the screen (`ally/risk/screenshare.py`), JPEG-encodes it (quality 60,
up to 1280 px, up to 15 frames a second, stepping quality and size down when frames are rejected or slow), and calls
`send_screen_frame` over SpacetimeDB's HTTP API (`POST /v1/database/ally-call/call/send_screen_frame`) with its own
identity token. `screen_share_ready` lets it wait for the helper without capturing anything. The helper sees the
screen first, with the camera as a small tile, and can zoom and pan (`SharedScreen.svelte`).

**Helper actions.** A tap, drag or highlight on the phone becomes `send_helper_action`. On the elder's PC the call
page receives the `helper_action_event`, hands it to the Ally extension's content script (`extension/call-relay.js`),
which forwards it to the agent (`ally/bridge.py` → `wire.on_helper_action`). The agent draws the yellow highlight
(`ally/overlay/marker.py`), turns taps and drags into real mouse input (`ally/risk/remote_touch.py`, `SendInput`), or
closes the tab through the extension, then the page calls `report_action_result` so the helper sees it worked.

**Face profile sync.** Optional (`[profile] sync = "spacetimedb"`, off by default): `ally/profile_store.py` saves, loads and deletes
the user's face profile through `save_face_profile`, the `own_face_profile` view and `delete_face_profile`, so a new
PC can skip most of the face setup (`docs/face-profile.md`).

## 7. Testing

| Suite | Tests | Covers |
|---|---|---|
| `spacecall/server/tests/server.test.ts` | 15 | The module itself: room ids and kinds rejected transactionally, role transitions and no role hijacking, private views, per-room media isolation between simultaneous rooms, two-hour expiry, final end-of-call, stale disconnects, face-profile privacy, helper-action roles and bounds |
| `spacecall/tests/media.test.ts` | 28 | Audio and video framing, sizes, pacing |
| `spacecall/tests/helperActions.test.ts` | 14 | Highlight, tap, drag and close-tab gestures and results |
| `spacecall/tests/room.test.ts`, `ringing.test.ts`, `screenshare.test.ts`, `layout.test.ts` | 22 | Room lifecycle, ringing, screen view, layout |
| `tests/test_risk_screenshare.py` | 32 | The agent's screen sender: reducer calls, readiness, back-off, pause |
| `tests/test_risk_spacecall.py` | 18 | Opening calls, links, fallbacks to Meet |
| `tests/test_risk_helper_action.py`, `tests/test_remote_touch.py` | 18 | Validating and carrying out helper actions |
| `tests/test_face_profile.py` | 7 | Profile save, load, delete over the HTTP API |

Run: `cd spacecall && npm test`, and `cd tests && python -m unittest test_risk_screenshare test_risk_spacecall
test_risk_helper_action test_remote_touch test_face_profile`.

## 8. Limits (honest)

- Media goes through reducers and event tables rather than WebRTC: simple and private, but sized for a two-person
  call at modest resolution (camera 320x180 or 480x270, screen up to about 15 fps), not HD video.
- Expiry is lazy (on the next operation or heartbeat); there is no scheduled purge job.
- Reopening a closed call tab gives the browser a new identity, so a new incident link is needed.
- The call app is adapted from [SpaceChatDB by Lethalchip](https://github.com/Lethalchip/SpaceChatDB) with the
  author's permission; we replaced its Rust module with this TypeScript module and its chat UI with the elder and
  helper screens (`spacecall/PORTING.md`, `spacecall/UPSTREAM_COMMIT`).

More: `spacecall/README.md` (run, publish, build, host), `docs/RISK_HANDOFF.md` (how calls fit scam protection).
