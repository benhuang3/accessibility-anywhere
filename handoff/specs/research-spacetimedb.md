# Deep research brief: should Ally use SpacetimeDB, especially for the video call?

You are a research agent: read-only, with web research allowed. Do not edit any file and do not run Ally. Never read `.env` or `config.toml`.
Your final message is the full markdown report.

## Situation (Cal Hacks hackathon, Saturday night; submission and a live judge demo on Sunday morning)
- **Ally** is a Windows accessibility assistant for older adults: a Python agent, a Chrome MV3 extension and a Tk overlay. Read
  `README.md` and `docs/RISK_HANDOFF.md` for the full picture.
- One feature is **scam protection.**
  - When a scam is likely, for example a remote-control tool running while a bank page is open, the panel offers **"Call Priya"**, a
    trusted family member.
  - **Today, the call works like this:**
    - Ally opens the helper's *standing Google Meet room* in a borderless Chrome popup.
    - A content script (`extension/call.js`, `call.css`) hides Meet's UI, auto-joins, turns the camera and mic on, and adds Ally's own
      frame.
    - The helper's phone gets an **ntfy.sh** push with a Join button (`ally/risk/contact.py`, `ally/risk/wire.py` `_tell`).
  - **Known weak points:**
    - Meet's page structure can change.
    - A guest who isn't signed in has to wait to be admitted.
    - The standing link is visible to a scammer watching the screen.
    - ntfy gives no "the helper answered" acknowledgement.
- **A SpacetimeDB representative visited our table and said:**
  - SpacetimeDB can be used as a full backend, more than a database.
  - It can **host the video call**, and someone built a **video-call demo project** with it. The rep was emphatic about this.
  - It could also store user memory, eye-tracking data and so on.
  - It would make a cool sponsor integration.

## Questions to answer (cite sources with links; mark anything you couldn't verify as UNVERIFIED)
1. **What SpacetimeDB is today.**
   - The architecture (modules and reducers, tables, subscriptions).
   - The module languages (Rust, C#, TypeScript?) and the client SDKs: TypeScript/browser, C#, Rust, and is there a **Python** client?
   - The hosting (Maincloud): free tier, sign-up friction, limits.
   - The current version and how stable it is.
2. **The video-call demo.**
   - Find it: the repo, the author, the blog post or video.
   - How does it actually work? Does SpacetimeDB do only **WebRTC signaling** (offers, answers and ICE stored in tables) with P2P media,
     or does it relay media or frames through the database?
   - What does it need: TURN/STUN servers, browser support, mobile browser support?
   - License, and whether it can be reused or forked.
3. **Fit for Ally's call by Sunday morning (about 8–10 working hours, and the coding is done by AI agents).**
   - What would a SpacetimeDB-backed call replace? The Meet popup and/or ntfy?
   - **The PC side:**
     - Can a Chrome MV3 extension page or popup run the SpacetimeDB TS SDK and WebRTC? MV3 forbids remotely hosted code and limits
       `connect-src`, so is a bundled SDK allowed?
     - Or should the call page be served by the local Python agent at 127.0.0.1? Note our dashboard CSP: `script-src 'self'`.
   - **The helper's phone side:**
     - A web page hosted where?
     - Does it work in mobile Safari and Chrome?
     - How does the helper get notified? Keep ntfy, or use SpacetimeDB subscriptions?
   - **What it would fix:**
     - a call UI we fully own, with no Meet DOM hacks;
     - no waiting to be admitted;
     - "helper answered" acknowledgement and presence through a subscription;
     - a per-incident room instead of a standing link, which fixes the leak of the link to the scammer.
   - **What it would break or risk:**
     - NAT traversal without TURN on venue wifi;
     - a new dependency the night before the demo;
     - building a Rust or C# module from scratch.
4. **Other uses the rep mentioned** (user memory, eye-tracking data, helper dashboard). Which of these are credible for the demo, and what
   are the privacy implications of storing eye-tracking or behavior data in a cloud database for older adults?
5. **The sponsor angle:** is SpacetimeDB a Cal Hacks sponsor with a prize track this year, and what do they judge on? UNVERIFIED if you
   can't find it.
6. **Alternatives for the same goal:** Daily.co, LiveKit Cloud, PeerJS, Jitsi as a JaaS embed, and Whereby Embedded. A one-line
   comparison of each on setup time, a custom UI, mobile support and cost.

## Output (markdown, under 1500 words)
- **The verdict:** one of
  - (a) integrate the SpacetimeDB call for Sunday;
  - (b) make a small SpacetimeDB integration (for example signaling/presence or incident log only) while keeping Meet as the fallback;
  - (c) not now, and mention it as future work.

  Give one paragraph of reasoning.
- **What it is in five bullets.**
- **The video-call demo:** link, how it works, and how reusable it is.
- **The integration plan for your verdict:** the components, which files would change in our repo (see `extension/`, `ally/risk/`),
  the steps in order with a time estimate for each, and the riskiest step plus its fallback.
- **The risks, ranked,** and the demo-day failure plan.
- **The alternatives table.**
- **Sources.**
