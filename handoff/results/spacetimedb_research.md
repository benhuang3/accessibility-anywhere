# Should Ally use SpacetimeDB for Sunday?

*Research checked October 24, 2026. Estimates below are engineering judgments.*

## Verdict: (b) small integration, retain Meet

Add **incident status and helper acknowledgement**, while retaining Meet for calls and ntfy for notifications. SpacetimeDB can credibly power the shared backend, and the representative’s video demonstration exists. However, that demonstration implements its own media transport through database events; adopting it introduces browser audio, privacy, reliability and licensing work. With 8–10 hours remaining, even AI-written code needs real-device verification. A small integration offers a useful sponsor demonstration without putting Sunday’s call on a new media stack.

## What it is in five bullets

- **Architecture:** a relational database and application server together. Uploaded modules define tables and business logic; reducers execute atomic transactions; subscriptions synchronize committed changes into client caches. A separate API server is often unnecessary. [Architecture](https://spacetimedb.com/docs/intro/key-architecture/)
- **Module languages:** Rust, C#, TypeScript and C++. Rust/C#/C++ compile to WebAssembly; TypeScript runs on V8. Building a Rust or C# module is therefore optional. [Language support](https://spacetimedb.com/docs/intro/language-support/)
- **Clients:** official current SDKs cover TypeScript/browser, Rust, C# including Unity, and C++/Unreal. **A historical official Python SDK exists**, but its README explicitly says it is not actively maintained; compatibility with current releases is **UNVERIFIED**. Use browser TypeScript for this integration. [SDK list](https://spacetimedb.com/docs/intro/language-support/), [Python SDK](https://github.com/clockworklabs/spacetimedb-python-sdk)
- **Version/stability:** GitHub marks **2.13.0**, released October 21, as latest; documentation still displays “Version: 2.0.0.” The backend has production use in BitCraft, but that does not establish calling reliability. Recent TypeScript serialization fixes justify pinning matching CLI, module, SDK and generated bindings. [Release](https://github.com/clockworklabs/SpacetimeDB/releases/tag/v2.13.0), [repository](https://github.com/clockworklabs/SpacetimeDB)
- **Maincloud:** managed hosting; CLI login uses GitHub/Google, followed by publication. Free includes **2,500 TeV/month**—approximately 3 million simple calls **or** 12.5 GB egress **or** 1 GB storage, consuming one shared budget. Pro is **$25/month**, Team **$250/month**. Free databases suspend when idle and typically resume within one second. Credit-card requirements and hard concurrency ceilings are **UNVERIFIED**. [Pricing](https://spacetimedb.com/pricing), [deployment](https://spacetimedb.com/docs/how-to/deploy/maincloud/)

## The video-call demo

**Found:** [Lethalchip/SpaceChatDB](https://github.com/Lethalchip/SpaceChatDB), owned by Lethalchip. Alessandro Asoni’s September 8 [official write-up](https://spacetimedb.com/blog/video-conferencing-over-a-database-with-spacetimedb) links a YouTube demonstration. An [official X post](https://x.com/spacetime_db/status/2027187510950994255) was located, but its video was inaccessible here.

This is **media through SpacetimeDB**, rather than SDP/ICE signaling for peer-to-peer WebRTC:

- AudioWorklet captures audio, converts it to PCM16LE, and calls a reducer.
- Canvas captures JPEG video frames and calls another reducer.
- Event-table subscriptions deliver frames for playback without accumulating ordinary table rows.

The inspected backend defaults to **320×180 at 5 fps**, with 16 kHz audio. The media implementation uses no `RTCPeerConnection`, STUN or TURN; it needs a reachable database connection, camera/microphone permission, Web Audio and canvas support. [Backend](https://raw.githubusercontent.com/Lethalchip/SpaceChatDB/main/spacetimedb/src/lib.rs), [media runtime](https://raw.githubusercontent.com/Lethalchip/SpaceChatDB/main/src/lib/callRuntime.ts)

Actual mobile Safari/Chrome reliability is **UNVERIFIED**. The vendor explicitly recommends WebRTC for production calling.

**Reuse:** no license was found in the repository listing, README or package metadata. Reuse permission is **UNVERIFIED**. GitHub permits viewing/forking public repositories, but broader modification/distribution rights require licensing or permission. [GitHub licensing guidance](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository)

## Fit for Ally

A future WebRTC implementation could replace Meet’s popup and DOM automation, provide an owned UI, and admit authenticated family members directly. SpacetimeDB could carry offers, answers, ICE candidates and call state; media would travel peer-to-peer or through TURN. **Signaling alone supplies no NAT relay.** [TURN guidance](https://webrtc.org/getting-started/turn-server)

A bundled TypeScript SDK is compatible with MV3’s code-packaging rules. Use a persistent extension window for media/status rather than the toolbar popup or service worker. Explicitly allow required HTTPS/WSS destinations if adding `connect-src`; remote executable code remains forbidden. [MV3 rules](https://developer.chrome.com/docs/extensions/develop/migrate/remote-hosted-code), [CSP](https://developer.chrome.com/docs/extensions/reference/manifest/content-security-policy)

Alternatively, `127.0.0.1` can support camera access as a trustworthy local origin. Ally’s existing `connect-src 'self'` would need narrowly scoped Maincloud allowances in `ally/server.py`; preserve external scripts/styles and `X-Ally-Token` protection. [Secure contexts](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Secure_Contexts)

Host the helper page separately over HTTPS, for example on [GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages). Mobile WebRTC is supported in Safari/Chrome, but permissions, user interaction and background suspension require testing. Keep ntfy: an ordinary database subscription does not wake a closed phone page.

## Integration plan

**Proposed changes only; no implementation performed. Total: approximately 7–8 hours.**

| Order | Work and proposed files | Estimate |
|---|---|---:|
| 1 | Install/pin matching tooling, authenticate Maincloud, publish a minimal TypeScript module under new `spacetimedb/`. | 0.5 h |
| 2 | Add private incident/status tables, pre-paired laptop/helper identities, authorized reducers, expiry and identity-scoped views. Store incident ID, coarse risk kind and timestamps only. | 1.5 h |
| 3 | Add persistent `extension/incident.html`, `.js`, `.css`, bundled SDK and generated bindings. Update `extension/background.js` to coordinate incident creation/status through the existing bridge. | 1.5 h |
| 4 | Add HTTPS helper page with “I’m responding” and Meet join controls. Update `ally/risk/wire.py` and `contact.py` for a separate, explicitly allowed helper-action URL; retain direct Meet fallback. | 1 h |
| 5 | Verify authorization, expiry, duplicate alerts, outages and phone acknowledgement; run mandated unit tests plus relevant risk/contact tests. Rehearse on venue Wi-Fi and cellular. | 1.5 h |
| 6 | Integration buffer and demo freeze. | 1–2 h |

Use [private tables with caller-specific views](https://spacetimedb.com/docs/tables/access-permissions/). A phone acknowledgement should display **“Priya is responding”**; it must not claim the call was answered.

**Riskiest step:** pairing and authorization across two browsers. If unresolved after four hours, reduce to a synthetic incident-log demonstration and keep the existing call flow.

This scope adds acknowledgement and shared status. Meet admission, DOM fragility and the standing call link remain. Expiring incident links reduce exposure; they cannot protect a computer already under remote control.

## Ranked risks and demo-day failure plan

1. **Unauthorized disclosure:** the inspected demo declares media event tables public. Client subscription filters are not access control, and the server receives readable media.
2. **Deadline regression:** AI implementation speed does not replace device testing. Freeze the call path early.
3. **Media failure:** a WebRTC fork needs tested TURN; either approach risks permissions, autoplay and contention with Ally’s webcam/microphone use.
4. **Misleading presence:** disconnected or backgrounded phones can leave stale state. Use expiry/heartbeats and honest labels.
5. **Cloud dependency:** outages, exhausted credits or version mismatches require graceful failure.

Rehearse signed-in Meet users and admission beforehand. If SpacetimeDB fails, continue with Meet/ntfy; if Meet framing fails, use plain Meet. If connectivity fails, show a clearly identified prerecorded demonstration.

## Other uses and privacy

A helper dashboard and minimal incident history are credible now. Synchronizing selected accessibility preferences is plausible later. Raw gaze, camera frames, browsing behavior and detailed user memory should remain local for Sunday: uploading them changes Ally’s existing privacy promise. Any later cloud feature needs explicit consent, access controls, limited retention, deletion and a clear explanation of who can see it.

## Sponsor and prize track

**VERIFIED:** Spacetime/Clockwork Labs sponsors Cal Hacks 13, October 23–25, 2026. [Sponsor announcement](https://spacetimedb.com/blog/spacetime-at-calhacks)

Devpost lists **Best Use of Spacetime**: **$2,048 / $1,024 / $512**, plus a separate Scrabblebot tournament. General judging covers application, intentionality, functionality, creativity and technical complexity. Sponsor-specific scoring weights are **UNVERIFIED**; the sponsor describes rewarding the most impressive use. [Event and prizes](https://cal-hacks-13-0.devpost.com/)

## Alternatives

*Setup estimates cover a basic prototype; participant-minutes count each participant separately.*

| Option | Setup/custom UI | Mobile | Current cost |
|---|---|---|---|
| **Daily** | 1–2 h prebuilt; 3–6 h custom SDK UI | Supported | 10,000 free participant-min/month, then $0.004/min. [Pricing](https://www.daily.co/pricing/video-sdk/), [SDK examples](https://docs.daily.co/guides/additional-resources/demos) |
| **LiveKit Cloud** | 3–6 h; full custom UI, token service | Safari/iOS, Chrome/Android | Free Build: 5,000 WebRTC min and 50 GB downstream; paid Ship starts $50/month. [Pricing](https://livekit.com/pricing), [browser support](https://docs.livekit.io/reference/client-sdk-js/) |
| **PeerJS** | 2–4 h; own UI and authorization; arrange TURN | Browser WebRTC; test iOS | MIT/free signaling service; TURN/hosting additional. [Project](https://peerjs.com/), [ICE configuration](https://peerjs.com/client/api/peer) |
| **Jitsi/JaaS** | 1–3 h iframe/JWT; configurable UI | Safari/iOS, Chrome/Android | Free 25 monthly active users; $99/month for 300. [Pricing](https://cpaas.8x8.com/en/pricing/jitsi-as-a-service-pricing/), [support](https://developer.8x8.com/jaas/docs/jaas-browser-support/) |
| **Whereby Embedded** | 1–2 h embed; 3–6 h custom SDK UI | Safari/iOS, Chrome/Android | Free 2,000 min/month, 30-minute meetings; Build $10.99/month plus overage. [Pricing](https://whereby.com/information/embedded/pricing), [custom UI](https://docs.whereby.com/whereby-for-web-browser/react-based-browser-sdk/quick-start) |

## Sources and research limits

Primary sources are linked inline. GitHub, official docs/blog, X, YouTube and Discord-linked public pages were searched; no additional verifiable Discord demo was found.

Local context: `README.md`, `docs/RISK_HANDOFF.md`, and relevant call/server code. **No files edited; Ally and tests were not run.**