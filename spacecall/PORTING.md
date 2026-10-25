# Ally Call port and verification handoff

All source changes are inside `spacecall/`. No commit, push, branch switch, SpacetimeDB publish or login was performed. No `.env` or repository `config.toml` was opened. Other agents' changes outside this directory were left alone.

## Files and upstream changes

The upstream source is [SpaceChatDB by Lethalchip](https://github.com/Lethalchip/SpaceChatDB), commit `37b110d49b0d13b0449f4f4015d65c2f6b082686`.

- Added `server/src/index.ts`, `server/package.json`, `server/package-lock.json`, `server/tsconfig.json`: TypeScript SpacetimeDB 2.13 module and dependencies. Removed the entire `spacetimedb/` Rust directory after the port passed its tests.
- Replaced `src/lib/stdb.ts` and `src/lib/callRuntime.ts`: typed room subscriptions, incident state, automatic elder media, helper audio activation, PCM audio, JPEG video, device cleanup, mute, camera flip and slow-connection reporting.
- Added `src/lib/config.ts`, `src/lib/room.ts`, `src/lib/types.ts`: public connection defaults, hash validation, plain reason labels, and types inferred from the bindings.
- Replaced `src/routes/+page.svelte`; added `src/lib/app.css`: large elder view, phone-first helper view, glass bars, high contrast, amber notices, 80px End call button, system fonts and visible upstream credit. No demo chat, nickname picker, user list, lobby or room browser remains.
- Added `src/routes/+layout.ts`; updated `src/routes/+layout.svelte`, `src/app.html`, `src/lib/assets/favicon.svg`, `svelte.config.js`: static prerendering, build-time base path, global styles, Ally favicon and safe viewport sizing.
- Deleted all upstream files in `src/module_bindings/`; the sole replacement is **`src/module_bindings/index.ts`**, a hand-written, functional SDK schema stub marked **replace with spacetime generate**. The client uses the generated-style camelCase accessors and reducer arguments. No fake successful reducers are used.
- Removed the obsolete upstream `src/lib/mediaSettings.ts` singleton. Media defaults are local constants validated by server reducers; the UI has no media settings screen.
- Updated `package.json`, added `package-lock.json`, updated `.gitignore` and `spacetime.json`; added `static/.nojekyll`, `tests/module-loader.mjs` and `server/tests/server.test.ts`.
- Replaced `README.md` with attribution, setup, publish/generate/build/deploy instructions, link contract and privacy limits. `UPSTREAM_COMMIT` remains unchanged.

The port retains the AudioWorklet microphone capture and the original PCM16/JPEG reducer-to-event-table transport. It replaces UUID sessions and direct user-to-user calling with a fresh room capability and two fixed roles. Chat, nicknames, voice-only sessions, incoming-call dialogs and the global online-user browser were deliberately removed for the requested two-person flow.

## Schema

| Table/view | Visibility | Fields |
| --- | --- | --- |
| `incident` | Private | `room_id: string` PK, `kind: string`, `status: string`, `created_at: Timestamp`, `updated_at: Timestamp`, `helper_name: string` |
| `presence` | Private | `slot: string` PK (`room:role`), indexed `room_id: string`, indexed `identity: Identity`, `connection_id: string`, `role: string`, `joined: bool`, `updated_at: Timestamp` |
| `call_incident` | Public sender-filtered view | Same columns as incident, with room primary key; only rooms claimed by the requesting identity |
| `audio_frame_event` | Public event table with recipient RLS | `room_id`, `sender`, `recipient`, `seq: u32`, `sample_rate: u32`, `channels: u8`, `rms: f32`, `pcm16_le: bytes` |
| `video_frame_event` | Public event table with recipient RLS | `room_id`, `sender`, `recipient`, `seq: u32`, `width: u16`, `height: u16`, `jpeg: bytes` |

Status is one of `ringing`, `responding`, `connected`, `ended`, assigned only by reducers. Role is validated as `elder` or `helper`. Room IDs must match `^[A-Za-z0-9_-]{16,64}$`. Incident kinds use a category allowlist with `other` for unknown categories, rejecting URLs and page text. Names are limited to 64 characters without control characters.

## Reducers and lifecycle

| Reducer | Behavior |
| --- | --- |
| `open_incident(room_id, kind, helper_name)` | Elder creates ringing incident and claims elder slot. Existing room cannot be taken over, revived or reset. |
| `helper_responding(room_id)` | Claims helper slot using the capability and sets responding; late arrivals can see ended state. |
| `join_call(room_id, role)` | Joins the caller's claimed role; marks connected only when both roles have joined. |
| `end_call(room_id)` | Either participant permanently ends the incident. |
| `heartbeat(room_id)` | Refreshes presence and enforces lazy expiry. |
| `send_audio_frame(room_id, seq, sample_rate, channels, rms, pcm16_le)` | Validates exactly 50ms of 16kHz mono PCM16LE (1600 bytes), derives the same-room peer and emits one recipient event. |
| `send_video_frame(room_id, seq, width, height, jpeg)` | Accepts 320x180 or 480x270 JPEG frames up to 128KB, derives peer and emits one recipient event. |
| `client_disconnected` lifecycle | Ends that connection's rooms and marks its presence unjoined. A stale disconnect cannot end a newer connection's room. |

Every room operation checks the two-hour age cutoff. Expired calls commit ended state and emit no media. This is lazy expiry, triggered by operations and the client's 15-second heartbeat; there is no scheduled data purge. End call stops the camera, microphone, worklet, audio playback, frame timers and object URLs immediately. Disconnect also stops local media and reports a plain error. Reopening a closed tab starts a new browser identity, so a new incident link is required.

Default video is 320x180 at 5fps and JPEG quality 0.55. Thirty healthy sends allow 480x270 at 8fps. Slow acknowledgments lower quality; a watchdog also detects missing incoming media and shows Connection is slow. Video encoding/sending is serialized. Audio is resampled to 16kHz, sent in 50ms blocks, capped at eight in-flight sends, and played with a bounded queue. Late/duplicate frames are dropped. A helper's tap resumes its AudioContext before any asynchronous reducer call; both video elements use playsinline. Elder autoplay restrictions expose a plain Tap to hear recovery button.

## Room isolation and privacy

The static host never receives the URL fragment. The browser sends the room capability to SpacetimeDB over WSS. The integration must generate at least 128 random bits per incident; validation of length and alphabet cannot establish entropy. There is no public room enumeration table. Incident/presence remain private and the view is filtered by sender identity after a room role is claimed. One identity cannot claim both roles, and another identity cannot take an occupied role.

The [SDK event-table documentation](https://spacetimedb.com/docs/tables/event-tables/) says private-table views cannot currently read event tables. Accordingly, audio/video events are public tables protected by exported recipient-only SQL visibility filters. The server derives each recipient from the same room. The client additionally filters subscriptions by exact room and recipient. A modified client subscribing to all events should still receive only events addressed to its own identity. **Actual subscription-level RLS behavior must be verified after publishing**; reducer/view isolation and schema validation were tested locally. RLS is an [experimental SDK feature](https://spacetimedb.com/docs/how-to/rls/); do not deploy with these filters removed.

This is capability access, not proof of a person's real-world identity. Anyone with an unused helper link can claim that helper slot. SpacetimeDB and its operator can access media. Event rows disappear from live tables, but inserts remain in the provider commit log. This implementation does not provide end-to-end encryption or a retention-deletion guarantee. No URL/page content, analytics, browser-persisted token or local media recording is added.

## Verification results

The final command output is recorded below after running the checks. Browser appearance, camera/microphone behavior, Maincloud latency, real generated bindings, GitHub Pages deployment, Android Chrome and iOS Safari remain unverified. Browser inspection could not proceed because the computer-use tool did not approve access to Google Chrome.

```text
BASE_PATH=/ally-call npm run check
COMPLETED 336 FILES 0 ERRORS 0 WARNINGS 0 FILES_WITH_PROBLEMS

npx tsc --noEmit -p server
Exit code 0 (no diagnostics)

npm test
tests 10; pass 10; fail 0

BASE_PATH=/ally-call npm run build
vite v7.3.7
155 SSR modules transformed; 175 client modules transformed
client built in 3.71s; complete build in 17.60s
Using @sveltejs/adapter-static
Wrote site to "build"
done
```

The build emits the AudioWorklet as a separate 0.24KB JavaScript asset. The largest call page chunk is 134.99KB (38.36KB gzip); global CSS is 4.83KB (1.79KB gzip). A local preview at `/ally-call/` returned HTTP 200 for the HTML page and all 13 referenced entry assets. `build/.nojekyll` is included. These checks do not run browser JavaScript or establish a live media call.

The machine has Node 24.12.0, TypeScript 5.9.x and SpacetimeDB SDK 2.13.0. Both client and server dependencies were installed with npm. The root install reported three low-severity development dependency advisories; the server install reported zero vulnerabilities.

**Local tooling workaround:** npm's default cache is outside the writable workspace, so installation used `--cache .npm-cache` (server: `--cache ../.npm-cache`). Vite 7's esbuild config loader tried to enumerate a denied ancestor directory. For verification only, the installed, ignored `node_modules/vite/dist/node/chunks/config.js` default was changed from `configLoader = "bundle"` to `configLoader = "native"`, using Node 24's native TypeScript loading. No application source or lockfile depends on that patch. It applies to SvelteKit's nested client build too. An unrestricted workstation can use the normal build commands; this sandbox needs the same local loader workaround after a fresh npm install.

The repository-mandated Python command was run from `tests/` with bytecode writing disabled:

```text
python -m unittest test_config test_registry test_brain test_network test_runtime test_handsfree
Ran 50 tests in 11.342s
FAILED (errors=8)
```

Missing NumPy prevented the brain, network and hands-free imports and hotkey agent setup; the resulting hotkey teardown errors and a Windows speech worker failure account for the remaining errors. These are outside the authorized `spacecall/` scope. Ally itself and `e2e_chrome.py` were not started.

## Commands for the next operator

With the 2.13.x SpacetimeDB CLI installed, start in `spacecall/`:

```sh
npm install
cd server
npm install
spacetime publish ally-call --server maincloud
cd ..
spacetime generate --lang typescript --out-dir src/module_bindings --module-path server
npm run check
npx tsc --noEmit -p server
npm test
BASE_PATH=/ally-call npm run build
npx gh-pages --dist build --nojekyll --repo https://github.com/OWNER/ally-call.git
```

Replace OWNER with the GitHub account that owns the destination ally-call repository, enable Pages for gh-pages at repository root, and use HTTPS. PowerShell build equivalent: `$env:BASE_PATH='/ally-call'`, then `npm.cmd run build`. The [official code-generation guide](https://spacetimedb.com/docs/clients/codegen/) documents the generate command; the `spacetime.json` target also permits bare `spacetime generate` from this directory.

No publish or deploy command above has been run. After replacing the binding stub, confirm that generated accessors are `db.callIncident`, `db.audioFrameEvent`, `db.videoFrameEvent` and camelCase reducers, then repeat the checks and test a real two-device call plus an unrelated all-events subscriber. Phone permission flows, iOS audio resumption, mute, camera flipping and loss of network need device validation.
