# Ally Call

A two-person call between an older adult's PC and a trusted helper's phone. Ally creates a fresh incident link; this app handles the call. Audio and video travel through SpacetimeDB reducers and event subscriptions, preserving SpaceChatDB's media design rather than using WebRTC.

Adapted from [SpaceChatDB by Lethalchip](https://github.com/Lethalchip/SpaceChatDB), upstream commit **37b110d49b0d13b0449f4f4015d65c2f6b082686** (also in `UPSTREAM_COMMIT`). The call footer keeps the credit visible: **Built on SpaceChatDB**. Upstream's [SpacetimeDB referral link](https://spacetimedb.com/?referral=Lethalchip) is preserved here too.

The helper now sees the shared PC screen first, with a small camera tile that swaps the main view when tapped. Tap the shared screen to zoom; scroll it or use browser pinch zoom. A freshness hint shows the last frame age.

We replaced the Rust module with TypeScript, replaced the chat lobby and user browser with elder/helper call screens, added private incident/presence state, room capabilities, two-hour expiry, recipient security, adaptive JPEG video and mobile audio activation. See [PORTING.md](PORTING.md) for the schema, files, validation results and limitations.

## Run, publish, build and host

Use Node.js **24 or newer**. Install the SpacetimeDB **2.13.x CLI** separately before steps 3 and 4; there is no Rust dependency. No `.env` is needed. Public database connection settings live in `src/lib/config.ts`: `STDB_URI` defaults to `wss://maincloud.spacetimedb.com`, `DB_NAME` to `ally-call`.

1. In `spacecall/`, install client dependencies:

   ```sh
   npm install
   ```

2. Install the TypeScript module dependencies:

   ```sh
   cd server
   npm install
   ```

3. Still in `server/`, publish the module to Maincloud when you are ready:

   ```sh
   spacetime publish ally-call --server maincloud
   ```

4. Return to `spacecall/` and replace the hand-written SDK bindings with generated ones:

   ```sh
   cd ..
   spacetime generate --lang typescript --out-dir src/module_bindings --module-path server
   ```

   `spacetime.json` also contains this generate target, so `spacetime generate` is the short equivalent. The checked-in bindings were locally updated to mirror this schema for type-checking. Regenerate the whole directory before deploying this client and run the checks again. The CLI was unavailable in this sandbox; these edits are not a substitute for code generation.

5. Check and build for a GitHub Pages project named `ally-call`:

   ```sh
   npm run check
   npx tsc --noEmit -p server
   npm test
   BASE_PATH=/ally-call npm run build
   ```

   PowerShell uses `$env:BASE_PATH='/ally-call'` followed by `npm run build` on a new line. If script execution is disabled, use `npm.cmd` and `npx.cmd`. Omit `BASE_PATH` for a domain-root deployment; it is a URL path, never a full domain.

6. Deploy **the contents of `build/`** to GitHub Pages. For a separate `ally-call` GitHub repository, replace `OWNER` below, then run this from `spacecall/` after reviewing the output:

   ```sh
   npx gh-pages --dist build --nojekyll --repo https://github.com/OWNER/ally-call.git
   ```

   Enable Pages for the destination repository's `gh-pages` branch, root folder, in GitHub Settings > Pages. The site is `https://OWNER.github.io/ally-call/`. The included `.nojekyll` keeps the `_app/` assets accessible. Use HTTPS for camera and microphone access. The deploy command publishes to the destination repository; it has not been run here.

For local development after publishing, run `npm run dev`. For the production preview, use `npm run preview`. Localhost is a secure context for camera access. A phone visiting a LAN HTTP address is not; use the HTTPS deployment for phone testing. Do not start Ally or use port 8765 for this app.

## Call links

Ally supplies the fragment, which the web host does not receive:

```text
https://OWNER.github.io/ally-call/#room=<secret>&role=elder&name=Priya&kind=remote_access
https://OWNER.github.io/ally-call/#room=<same-secret>&role=helper&name=Priya&kind=remote_access
```

Generate a fresh **128-bit random** room for every incident, for example `crypto.randomBytes(16).toString('hex')` in Node, and encode the name with `URLSearchParams`. IDs must contain 16-64 characters from `A-Z`, `a-z`, `0-9`, `_`, `-`. Length validation cannot verify randomness; the caller is responsible for generating the secret. Treat the full link as a capability and share it only with the intended helper.

Supported kinds: `remote_access`, `scam_popup`, `lookalike_domain`, `payment_form`, `remote_banking`, `browser_trap`, `fake_scan`, `suspicious_page`, `other`. Use `other` for an unknown category. URLs and page text are rejected as kinds.

The elder page opens the incident automatically and requests camera/microphone permission. The helper taps Join call, which resumes audio, reports responding, obtains media and joins. If a browser blocks automatic audio on the elder PC, a plain Tap to hear button unlocks it. End call stops local devices immediately; a peer disconnect ends the room too. A refresh or closed tab ends the incident, so start a new incident from Ally rather than reusing the link.

## Ringing popup

With the Ally extension, an elder call starts in a quiet 360 x 340 popup, 16 pixels from the work area's bottom-right corner. This leaves room for Chrome's title and address bars; the ringing card fits both the no-answer and permission notes in a 360 x 270 content area. Chrome stays in place until the helper connects. Then the same popup grows into the docked call panel, Chrome moves beside it, and one quiet two-note chime plays if browser audio policy permits. Closing the call window restores Chrome's saved bounds and window state only if Ally resized it. These details survive an extension worker restart in session storage. If the extension cannot determine the work area, the call still opens maximized.

The ringing card shows the helper's initial, Calling status and End call. After 60 seconds without an answer it adds a calm message and keeps ringing. Camera/microphone permission notes and real errors remain visible; other call controls and the sharing banner appear on connection. The gentle portrait pulse respects reduced motion. The elder page exposes `data-ally-call` (`ringing`, `connected`, `ended`) and updates its title on each status change so only the saved call tab can trigger expansion.

## Privacy and deployment checks

`incident` and `presence` are private. A sender-filtered `call_incident` view reveals only rooms the caller has claimed using a valid capability. Media reducers derive the recipient from that room's two roles; callers cannot supply an arbitrary recipient. Clients subscribe to their exact room and recipient identity.

Event tables cannot currently be read through private-table views. Public audio/video/screen event tables therefore have **server-enforced recipient row-level security**. RLS is experimental in this SDK; deployment must verify that an unrelated identity subscribing to all events receives none. Do not remove the visibility filters to make a publish succeed. See the official [event table limitations](https://spacetimedb.com/docs/tables/event-tables/) and [RLS documentation](https://spacetimedb.com/docs/how-to/rls/).

Room IDs and media are sent to SpacetimeDB over WSS; the hash only keeps them out of the static host's HTTP request. This is not end-to-end encryption. Event rows leave the live tables after their transaction, but SpacetimeDB records event inserts in its commit log. The two-hour cutoff stops calls; it does not erase provider logs or retained incident/presence records. There is no analytics, local media recording, room browser or stored browser auth token in this client.

Before a real demonstration, generate bindings and test two separate devices, all-event subscriptions from an outsider, expiry, End call, denied permissions, mute, camera switching, interrupted connectivity and iOS Safari audio. Maincloud, real phone media and iOS behavior remain unverified here.


## Screen sharing update (2026-10-25)

An explicit **Call Priya** tap in Ally opens the elder page and starts Python screen sharing, only for `call_service = "spacecall"` with `[risk] screen_share = true` (the default). Automatic alerts alone never start sharing. Meet and Tell retain their existing behavior. The elder sees a persistent **Priya can see your screen** banner with **Stop sharing**; after stopping it becomes **Share my screen again**. With `screen_share = false`, the URL includes `screen=off`, the incident is switched off immediately after opening, and the Python sender is not started. To enable sharing later in that case, change the config and start a new call.

**The helper sees everything captured on the screen, including bank pages, private messages and other open apps.** This is the purpose of the call. Capture uses the same `agent.platform.screenshot_png()` function as the risk guardian, and therefore the same platform/monitor coverage. It begins only after the explicit Call tap; it does not require `screen_review` or send images to Anthropic. Stop sharing blocks new screen events on the server and clears the helper's displayed screenshot. An already delivered image cannot be recalled from another person's device.

`ally/risk/screenshare.py` uses stdlib HTTP with five-second timeouts and optional **Pillow** for in-memory resizing/JPEG encoding. Install Pillow in Ally's Python environment for screen sharing; if it is missing or encoding/capture/network fails, the sender stops quietly and the helper keeps a waiting/stale freshness hint. No PNG fallback is uploaded. Frames are captured up to four times a second while changing, at most 1280 pixels wide, JPEG quality 60, reducing dimensions further until at most 150,000 bytes; the reducer hard cap is 200,000 bytes. No frames, room capabilities or identity tokens are written to disk or logged by this sender. A new call, call-window close, `RiskWire.stop()`, ended room, permanent rejection, or the 30-minute sender deadline stops sharing.

To make **Share my screen again** work, the capture/send loop stops while paused but a lightweight HTTP readiness watcher remains alive for the remainder of the same 30-minute deadline. It captures nothing while paused. The extra capability-only `screen_share_ready(room_id)` reducer rejects paused/ended rooms without returning private incident rows. Initial room creation may take up to 30 seconds; readiness checks wait for it before capturing. Broken HTTP connections reconnect once; other HTTP failures stop the sender. The browser's sharing flag is consent state, not confirmation that the Python sender is healthy; the helper's freshness hint exposes waiting or stale frames.

HTTP endpoints, checked against the current official docs:

- [`POST /v1/identity`](https://spacetimedb.com/docs/http/identity/) obtains one token per sender.
- [`POST /v1/database/<db>/call/<reducer>`](https://spacetimedb.com/docs/http/database/#post-v1databasename_or_identitycallreducer) calls `screen_share_ready` with `[room_id]`, then `send_screen_frame` with `[room_id, seq, jpeg_byte_array, width, height]`, using `Authorization: Bearer <token>` and `Content-Type: application/json`. [SATS-JSON arrays](https://spacetimedb.com/docs/sats-json/) encode the JPEG bytes.

The Python integration uses `https://maincloud.spacetimedb.com` / `ally-call`, matching `src/lib/config.ts`. Update both locations together for a different deployment.

### Schema and republishing

**This breaks the published database/client schema.** `incident` and its `call_incident` view gain `screen_sharing: bool`, initially true. Audio/video events gain `server_at: Timestamp`. The new transient `screen_frame_event` has `room_id`, `recipient`, `seq`, `jpeg`, `width`, `height`, `server_at`; it uses the same recipient-only visibility policy as camera/audio, always deriving the helper from that exact room. `presence`, existing reducer arguments, and previous column types are unchanged. `set_screen_sharing(room_id, on)` is elder-only; the Python sender uses the room capability with a separate HTTP identity.

Screen events are not stored snapshot tables, but **event inserts remain in the provider commit log** as explained above. RLS subscription isolation still needs a real published outsider-subscription test; the local harness verifies recipient derivation and event transience.

Audio now sends **640 samples / 40 ms / 1280 bytes** at 16 kHz mono. The server also accepts legacy **800 samples / 50 ms / 1600 bytes**. Other lengths reject. The debug overlay reads `server_at` from received audio/video rows, even when the SDK provides no reducer metadata; it measures server-to-browser age, with clock skew, excluding capture/uplink and playback buffering. It is not end-to-end delay.

Claude should run these exact PowerShell commands from the repository root (the publish command clears the disposable database data):

```powershell
Set-Location spacecall
spacetime publish ally-call --server maincloud --module-path server --delete-data always
spacetime generate --lang typescript --out-dir src/module_bindings --module-path server
npm.cmd run check
npm.cmd run check:server
npm.cmd test
$env:BASE_PATH='/ally-call'
npm.cmd run build
```

The [official CLI reference](https://spacetimedb.com/docs/cli-reference/) documents `--delete-data always`. Publish the module before deploying the new static client, then test a real elder/helper call, stop/resume, bank-page visibility, window close, iOS zoom/audio, and an outsider subscribing to all screen events. No publish, deployment, commit or push was performed here. The pre-existing untracked `server/dist/` was left untouched. If Vite hits the ancestor-directory sandbox restriction after a fresh dependency install, use the local native-loader workaround in [PORTING.md](PORTING.md).

### Local validation

```text
npm run check: 429 files, 0 errors, 0 warnings
npm run check:server: exit 0
npm test: 38 tests, 38 pass, 0 fail
BASE_PATH=/ally-call npm run build (PowerShell): static site written to build/
python -m unittest test_risk_screenshare test_risk_spacecall test_risk_contact test_risk_wire test_config:
  100 tests, OK
python -m unittest test_config test_registry test_brain test_network test_runtime test_handsfree:
  50 tests, FAILED (errors=8)
python -m unittest discover -p "test_*.py":
  674 tests, FAILED (failures=1, errors=17, skipped=11)
```

Python screenshot tests used Pillow 13 installed only into a temporary test dependency directory via `PYTHONPATH`; the repo environment was not changed. Broader Python errors involve missing NumPy and dependent hotkey teardown failures, Windows speech, and a sandbox socket privacy test. No failures occurred in the new sender, risk integration or config suites. The production build succeeds with the existing ignored native-loader workaround. Live device rendering/media, real generated bindings, HTTP transport against Maincloud, and subscription-level RLS remain unverified.


## Higher frame rates and bandwidth

Camera sending targets **12 fps at 320×180**, with at most three captures/reducers in flight.
A full pipeline drops capture ticks instead of keeping old frames. Sustained good mediaPolicy
health permits **15 fps at 480×270**. Falling receive fps, a pipeline full for over one second,
bad health or bandwidth pressure steps down promptly, through 12 fps to 6 fps / JPEG quality 45.
Recovery requires sustained good health again. Capture uses a deadline timer independent of reducer
acknowledgments, with OffscreenCanvas JPEG encoding where supported and a DOM canvas fallback for
Safari. Receivers decode one frame at a time, replace their single waiting frame, and discard stale
sequences or decodes invalidated by newer arrivals, pause or call teardown.

The Python screen sender reuses one HTTPS connection for readiness checks and reducer calls.
One capture/encode thread overlaps posting; its single waiting slot always keeps the latest frame.
A 64×36 grayscale thumbnail is compared against the last successfully sent frame (mean absolute
difference threshold 1.5). Unchanged captures are skipped, with a keyframe every five seconds.
Changing screens target **up to 4 fps**, versus about 1 fps previously. Posts over 400 ms lower
JPEG quality from 60 to 45, then width from 1280 to 960, before lowering capture fps.
Pause clears the waiting slot and suspends capture until readiness succeeds. Stop, permanent
rejection and the 30-minute deadline still end the sender; all screenshots remain in memory.

The **soft call budget is 1.2 MB/s averaged over ten seconds**, using decimal MB.
The helper can observe both camera directions plus the incoming screen and lowers its camera
quality when their combined JPEG payload exceeds that budget. Each camera also has a conservative
150 KB/s allowance, while Python reserves the remaining 900 KB/s for its screen POST bodies
(including the larger SATS-JSON byte arrays). These independent allowances let senders react
without a new server schema; they are soft quality controls, not a hard traffic limit.
The elder and Python cannot directly observe all other senders. A future server feedback signal
for per-room byte rates would allow fully coordinated adaptation across all three senders.

Add &debug=1 to the call fragment to see actual sent/received camera fps, screen receive fps
and kbps, combined media kbps, the ten-second observed call JPEG rate, camera target fps and
pending reducers. These count media payloads, excluding headers and transport overhead; the
helper has the complete camera-plus-screen view. Received rates include frames later dropped
during decoding. The existing delay estimate remains server-to-browser age, not end-to-end delay.

For planning with the spec's **about 12.5 GB/month free Maincloud egress allowance**:
two 6 KB camera JPEGs at 12 fps plus 40 KB screen JPEGs at 4 fps total
2 × 6 × 12 + 40 × 4 = 304 KB/s, or **18.24 MB/minute** of images.
Bidirectional 16 kHz / 16-bit mono audio adds 64 KB/s, giving **about 22.08 MB/minute**,
before protocol overhead (roughly 566 minutes / 9.4 hours per 12.5 GB).
An unchanged screen sends only its five-second keyframes, reducing that example to about
12.96 MB/minute including audio. At the 1.2 MB/s image budget, images alone cost
72 MB/minute: about 174 minutes / 2.9 hours per allowance. JPEG sizes vary substantially;
these are estimates, not measured call performance or a guarantee of free-tier eligibility.
No server changes are required for these frame-rate changes; live two-device and iOS checks
remain necessary before publishing.
