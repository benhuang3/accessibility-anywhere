Connection lost, reconnecting to https://agentn.global.api5.cursor.sh (attempt 1)...
Retry attempt 1...
The live docs could not be opened in this session. Web search was rejected, and fetches of `spacetimedb.com`, GitHub, `calhacks.io`, Daily, and LiveKit were blocked. Anything not read from this repo is marked **UNVERIFIED**. No version numbers or prices are quoted.

## The verdict

**(c) Not now. Mention SpacetimeDB as future work.**

Sunday’s call stays the Google Meet popup plus the ntfy.sh push. The rep’s claim that SpacetimeDB hosts the video call is **UNVERIFIED**: the demo repo was not found. SpacetimeDB’s published model replicates rows to connected clients. A browser call still needs WebRTC for audio and video, a public page for the helper’s phone, and a TURN server on venue Wi-Fi and cellular. Maincloud does not replace those, and a locked phone will not hear a subscription. Ally’s agent is Python, and an official Python client is **UNVERIFIED**. The Meet path is already wired, and `docs/RISK_HANDOFF.md` still marks its DOM assumptions **NOT LIVE-TESTED**. That is the failure that can sink a judge demo. A Cal Hacks prize track for SpacetimeDB or Clockwork Labs is **UNVERIFIED** (`https://calhacks.io/` did not load), so a sponsor logo does not buy a new media stack tonight.

## What it is in five bullets

These are the long-published product model. They are **UNVERIFIED** against the site on 24 Oct 2026.

- A module is the backend: relational tables and reducers (functions that run inside a transaction and roll back on failure). Clients subscribe; they do not talk to a separate app server you host.
- A subscription is a SQL query. The client keeps a cache and applies row changes as transactions commit. That is the realtime channel.
- Rust and C# are the long-standing module languages. The browser client people actually ship is the TypeScript SDK, bundled in your own page. TypeScript as a server-module language is **UNVERIFIED** today. An official Python client was not part of that public SDK set; whether one exists now is **UNVERIFIED**.
- Hosting is Maincloud: a GitHub login and a CLI publish. Free-tier quotas were not loaded, so none are stated. Signup plus a Rust or .NET toolchain on Windows is the friction.
- Only a connected client receives updates. Maincloud is a database API. Serving the helper’s HTML from Maincloud is **UNVERIFIED** and should not be assumed.

## The video-call demo

**Not found.** Searches for a SpacetimeDB WebRTC or video-call repo, blog, or video returned nothing because search and GitHub were blocked. No author, license, STUN/TURN setup, or mobile test is cited. Do not fork a project you have not opened.

The architecture implies the usual demo, and this is inference, not a reading of their code: tables hold session rows and signaling (SDP offers, answers, ICE candidates); each browser runs `RTCPeerConnection` and sends media peer to peer. Row replication is a bad place for video frames, so “the database relays the call” should be treated as **UNVERIFIED** and unlikely. Public STUN (often Google’s) gets through easy NATs. Hackathon Wi-Fi and a phone on LTE usually need TURN, which you would rent from someone else. Mobile Safari can do WebRTC and often fails until the page waits for a tap before `getUserMedia`, and until the codec is one Safari accepts. License and reuse rights are **UNVERIFIED**.

## The integration plan

**Tonight: no SpacetimeDB, no new call stack.** Files left as they are: `extension/call.js`, `extension/background.js` (`openCall` accepts only `https://meet.google.com`), `extension/manifest.json`, `ally/risk/contact.py`, `ally/risk/wire.py`.

Spend the night on the path you will actually demo:

1. **1.5 h.** On the demo laptop, live-test Call: popup, join, camera, mic, Ally frame, End call. The handoff’s assumed labels are English `Join now`, `Ask to join`, or `Join`, and an `aria-label` starting with `Leave call`.
2. **0.5 h.** Put ntfy on the helper’s phone, subscribe to `contact_topic`, and send one real alert with the Join action.
3. **0.5 h.** Use a Chrome profile that is already in the Google account that owns the room, so a guest is not waiting to be admitted while a judge watches.
4. **0.5 h.** Rehearse once on the venue network. Keep a backup `contact_call_url` on `meet.jit.si` or `whereby.com` (both already in `CALL_HOSTS`). Those open as a normal tab; only Meet gets the Ally frame.
5. **Rest of the night.** Script the spoken demo. Leave the module unwritten.

**Future work, after Sunday**, if a written prize brief appears. Smallest credible slice: an incident row (kind and time only, never page URL or page text) written when `_tell` runs, Meet and ntfy unchanged.

- About **1 h** to publish an empty module. This is the riskiest step (account, CLI, Windows toolchain). If publish fails, stop. Meet is untouched.
- About **1 h** for one table and one reducer.
- About **1 h** for a fire-and-forget HTTP call from `ally/risk/wire.py` `_tell`, errors swallowed, using `urllib` only if you have confirmed the HTTP call path in current docs. That path is **UNVERIFIED** tonight. New keys in `ally/config.py` and `ally/default_config.toml` sit on Ben H’s files (handoff §6).
- Skip a live helper dashboard. `ally/server.py` sends `connect-src 'self'`, so the dashboard cannot open a WebSocket to Maincloud. A bundled SDK in an extension page is allowed MV3 code; a script tag from a CDN is not. Ally’s manifest already ships local scripts only. The Chrome policy page was not re-fetched: **UNVERIFIED**.

A full SpacetimeDB call would also add a public HTTPS page for the phone (localhost is useless to them), keep ntfy as the ring, and add TURN. Budget that at the whole 8–10 h, with the venue call as the step that fails. Fallback is the Meet popup you already have.

User memory and a helper status view fit the product later. Eye-tracking and struggle data do not belong in Maincloud for this demo. The README says webcam video is processed on the machine and not saved; only calibration numbers stay in `state/`. `senses.post_url` is empty by default. Gaze and “having a hard time” signals for an older adult are sensitive, and a cloud copy is a third party the demo does not need.

## The risks, ranked, and the demo-day failure plan

1. **Peer-to-peer media on venue NAT, with no TURN.** The call connects in a hotel room and fails in the hall. Fallback: stay on Meet, which already relays media.
2. **Meet’s page or the admit lobby during the demo.** Fallback already in `extension/call.js`: if join/leave controls do not show within 30 seconds, restore plain Meet and keep End call. The helper admits from the ntfy Join button. A signed-in room owner is the prevention.
3. **A new cloud account on the critical path.** A failed publish or a hung client must not block the warning. The risk wire already attaches in a try/except. Do not call Maincloud from startup.
4. **No “helper answered” signal.** A subscription can report that only after the helper’s page is open. It does not ring the phone. Keep ntfy. The ack gap remains for Sunday.
5. **The standing Meet link is on the scammer’s screen once the popup opens.** A per-incident room shrinks the window; it does not close it, because `openCall` shows a real Meet URL and a remote-control session can read the screen. Meet’s admit step, which is awkward for the demo, is also what stops a leaked link from joining silently.
6. **Shipping gaze or behavior to a cloud database.** Skip it. Local sensing is the privacy story you already tell.
7. **Sponsor judging criteria.** **UNVERIFIED.** Do not trade the demo for a track you have not read.

**Demo-day failure plan.** If the framed Meet UI breaks, use the plain Meet window and let the helper admit. If ntfy does not arrive, the helper is already in the standing room because you texted them before the slot. If the building network blocks Google, switch `contact_call_url` to the backup `meet.jit.si` or Whereby URL and accept a normal tab. If anyone started a SpacetimeDB client and the laptop misbehaves, quit that client; Ally’s call code does not depend on it.

## The alternatives table

Costs and quotas were not loaded. Setup times are planning estimates.

| Service | Setup | Custom UI | Mobile | Cost |
|---|---|---|---|---|
| Daily.co | About an hour for a prebuilt room URL. `openCall` still allows only `meet.google.com`, so the Ally frame needs a code change. | Prebuilt embed, or their JS SDK if you want your own controls. | A main supported target. | Free tier exists. 2026 quota **UNVERIFIED**. |
| LiveKit Cloud | About half a day: a token endpoint plus a small page. Their SFU carries media, which is what venue NAT needs. | Yours, on their web client. | Yes. | Free cloud tier. Numbers **UNVERIFIED**. |
| PeerJS | A few hours for a 1:1 page. | Yours. | WebRTC in mobile Chrome and Safari; iOS is the fragile one. | Public broker is free and includes no TURN. Broker still up: **UNVERIFIED**. |
| Jitsi / JaaS | Fastest backup with no new code: `meet.jit.si` is already allowed. The Ally frame stays Meet-only. | Iframe API. The pixels are still Jitsi. | Usable; iOS Safari is the weak end. | Public servers are free and often show a lobby. JaaS quotas **UNVERIFIED**. |
| Whereby Embedded | An account, then a `whereby.com` URL, already an allowed host. | Their embed, with limited control. | Yes. | Commercial. Current free allotment **UNVERIFIED**. |

Daily, LiveKit, and JaaS are the services that relay media. PeerJS has the same NAT problem as a SpacetimeDB signaling demo. None of them should replace a working Meet rehearsal tonight.

## Sources

**Read in this repo:** `README.md` (local eye tracking, offline mode, empty struggle upload); `docs/RISK_HANDOFF.md` (Call Priya, ntfy, standing room, Meet DOM **NOT LIVE-TESTED**, other call hosts fall back to a normal tab); `extension/manifest.json` (local scripts, `host_permissions` `<all_urls>`, content script only on `meet.google.com`); `extension/background.js` `openCall`; `extension/call.js`; `ally/risk/contact.py` (`CALL_HOSTS`, ntfy Join action, no page URL in the push); `ally/risk/wire.py` `_tell`; `ally/server.py` dashboard CSP (`script-src 'self'`; `connect-src 'self'`).

**Requested and not retrieved:** `https://spacetimedb.com/docs`, `https://spacetimedb.com/pricing`, `https://github.com/clockworklabs/SpacetimeDB`, GitHub search for SpacetimeDB plus WebRTC, `https://calhacks.io/`, `https://www.daily.co/pricing/`, LiveKit cloud pricing docs. Chrome’s MV3 remote-code policy page was not re-fetched.
