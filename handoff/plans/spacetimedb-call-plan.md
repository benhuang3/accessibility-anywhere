# Plan: Ally's own call over SpacetimeDB (proposed Sat Oct 24, ~11 PM; not started)

Research: `handoff/results/spacetimedb_research.md` (Codex + web).

## Why
- SpacetimeDB is a confirmed Cal Hacks 13 sponsor, with a prize track: "Best Use of Spacetime", $2,048 / $1,024 / $512.
- The rep's demo is SpaceChatDB (github.com/Lethalchip/SpaceChatDB), featured on spacetimedb.com/blog/video-conferencing-over-a-database-with-spacetimedb.
- How it works: media goes *through* the database. AudioWorklet PCM16 and canvas JPEG frames (320×180 at 5 fps) are sent by reducers and
  delivered through event-table subscriptions. There's no WebRTC and no STUN/TURN.

## The licence problem
SpaceChatDB has no licence, which means all rights reserved. We may view it and click Fork, but not copy, modify or submit its code.

- **A. Ask for permission.** Ask the SpacetimeDB rep to connect us with Lethalchip, or ask the author directly for an MIT licence. If
  granted, fork and iterate.
- **B. A clean-room rebuild (start in parallel).**
  - Codex builds from the public blog description only and is told not to open the repo's source.
  - The technique isn't copyrightable.

## What makes ours unique
- A mobile helper app. The ntfy push opens the phone page for this incident's room: one tap and she's in. No Meet, no waiting to be
  admitted.
- A custom PC call interface for older adults: big faces, one big End call button, nothing to configure. Glass-style UI.
- A fresh room per incident instead of the standing Meet link the scammer can see. Rooms expire.
- Live status in Ally's panel: "Priya is responding", then "Priya is here".
- Triggered by scam detection, so the database carries the rescue ("intentional use").
- Data: the incident stores only risk kind and timestamps, never the URL or page text. Media tables are private and scoped to the room.
  SpaceChatDB's are public, and we should fix that.

## Steps (estimate ~6–7 h, with Meet + ntfy kept as the fallback)
| Step | Time |
|---|---|
| TypeScript SpacetimeDB module: rooms, frames, audio, incident status, room-scoped access, expiry | ~1 h |
| One responsive call web app, PC and phone layouts | ~3 h |
| Ally hook: "Call Priya" opens our call page; the ntfy Join opens the phone page with the room id; the panel shows the status | ~1 h |
| Real-phone testing and fixes | ~1.5 h |

## Risks
- iPhone Safari audio (AudioWorklet, autoplay). It's the riskiest part.
- 5 fps looks choppy; raise it if bandwidth allows.
- The phone needs an HTTPS page for the camera: GitHub Pages (~10 min) or a Cloudflare quick tunnel.
- A cloud dependency the night before the demo. Meet stays as the fallback, so the downside is lost time, not a broken demo.

## Blocked on Ganesh
1. Go / no-go.
2. Install the SpacetimeDB CLI and run `spacetime login` (opens a browser).
3. Ask the rep about the author's permission, and whether this kind of use fits "Best Use of Spacetime".
4. Which phone plays Priya: iPhone or Android?
