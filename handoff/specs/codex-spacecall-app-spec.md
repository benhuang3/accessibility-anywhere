# Codex spec A: turn SpaceChatDB into "Ally Call" (SpacetimeDB video call for a trusted helper)

Worktree `wt-risk-all`, branch `risk-branch`.
- Work ONLY inside `spacecall/`.
- Do not commit, push or switch branches.
- Never read `.env` or `config.toml`, nor any `.env` in `spacecall/`, and don't create one with secrets.
- Do not run `spacetime publish` or log in.

Another Codex job is editing `ally/risk/` and `extension/` at the same time; don't touch them.

## Context
- **Ally** is a Windows accessibility assistant for older adults. When it detects a likely scam, for example a remote-control tool
  running while a bank page is open, its panel offers **"Call Priya"**, a trusted family member.
- **Today** that opens Google Meet. We're replacing it with our own call running over **SpacetimeDB**, a sponsor of this hackathon
  (prize: "Best Use of Spacetime").
- `spacecall/` holds the source of **SpaceChatDB** (github.com/Lethalchip/SpaceChatDB; commit in `spacecall/UPSTREAM_COMMIT`):
  - a SvelteKit client;
  - a Rust SpacetimeDB module (`spacetimedb/src/lib.rs`);
  - media sent *through* the database: AudioWorklet PCM16 audio and JPEG canvas frames, via reducers, delivered through event-table
    subscriptions.
- Read `spacecall/README.md`, `spacetimedb/src/lib.rs`, `src/lib/stdb.ts`, `src/lib/callRuntime.ts`, `src/routes/+page.svelte` and
  `src/module_bindings/`.

## Goal
A purpose-built, older-adult-friendly call between the older adult's PC (role **elder**) and the helper's phone (role **helper**), with a
fresh room per incident.

## Build
1. **Module: port to TypeScript.**
   - Rewrite `spacetimedb/src/lib.rs` as a TypeScript SpacetimeDB module in `spacecall/server/`. There's no Rust toolchain here. Use the
     official TypeScript module API for the `spacetimedb` 2.x package. Check `node_modules/spacetimedb` and its docs once installed.
   - Keep the media design: event tables for audio and video frames, scoped to a room.
   - Add:
     - **`incident` table:** `room_id` (string PK), `kind` (string: remote_access, scam_popup and so on; never a URL or page text),
       `status` (`ringing` | `responding` | `connected` | `ended`), `created_at`, `updated_at`, `helper_name`.
     - **Reducers:**
       - `open_incident(room_id, kind, helper_name)`, called by the elder page on load;
       - `helper_responding(room_id)`;
       - `join_call(room_id, role)`, which sets connected when both roles are present;
       - `end_call(room_id)`;
       - plus the existing media and presence reducers, rewritten to take `room_id`.
     - **Validation:** `room_id` must be 16–64 URL-safe characters; reject anything else.
     - **Expiry:** ignore or end incidents older than 2 hours.
   - Media must only reach subscribers of the same `room_id`. The room id is a 128-bit secret, so the link is the capability. If the
     SDK supports private tables plus filtered views, use them; otherwise say so in the report.
   - Delete the Rust folder once the TS module covers everything. Keep a note of what was ported.
2. **Client: rework the UI and roles.** SvelteKit with `@sveltejs/adapter-static`, so it can be hosted on GitHub Pages.
   - Make the base path configurable through a `BASE_PATH` env at build time.
   - Read the room from the URL **hash**, which is never sent to servers: `#room=<id>&role=elder|helper&name=<helper name>&kind=<kind>`.
   - Put `STDB_URI` (default `wss://maincloud.spacetimedb.com`) and `DB_NAME` (default `ally-call`) in `src/lib/config.ts`.
   - **Elder view (PC, large screen)**, built for an anxious older adult:
     - No setup screens and no device pickers. Camera and mic are on automatically; if the browser asks permission, show one big plain
       sentence explaining why.
     - The helper's video large, the self view small.
     - A top status line, in plain words:
       - "Calling Priya…"
       - "Priya is on her way" (responding)
       - "Priya is here" (connected)
       - "Call ended"
     - One big red **End call** button (at least 72px tall) and a mute toggle. Nothing else.
     - Text at least 22px, high contrast, and no flashing.
   - **Helper view (phone, portrait first)**, for the family member opening it from a push notification:
     - On load, show "Ally stopped a possible scam on <kind in plain words>. They're waiting for you."
     - One big **"Join call"** button. It calls `helper_responding` and then `join_call`. The tap also satisfies iOS's need for a user
       gesture before audio and camera.
     - Then the elder's video full-bleed, plus mute, flip camera and End call.
     - It must work in iOS Safari and Android Chrome:
       - resume the `AudioContext` on the tap;
       - `playsinline`;
       - the HTTPS requirement is met by GitHub Pages.
   - **Look:** calm, premium, Apple "Liquid Glass" inspired. Frosted translucent bars (`backdrop-filter`) floating over the video, soft
     shadows, 20–28px radii, and the system font stack (`-apple-system, "Segoe UI Variable", "Segoe UI", system-ui`). Amber for caution;
     red only on End call. Light and dark both work over video.
   - **Quality:** keep the 320×180 at 5 fps default, but add an adaptive step up to 480×270 at 8 fps when sends keep up. Keep 16 kHz
     audio. Show a small "connection is slow" chip instead of failing silently.
   - Remove the upstream demo's lobby, room browser and anything unrelated to this two-person flow.
3. **Attribution.** `spacecall/README.md` must say:
   - this is adapted from SpaceChatDB by Lethalchip, with the link and the upstream commit;
   - what we changed;
   - how to run, build, publish and host it, as a step list: `npm install`; `spacetime publish ally-call --server maincloud` from
     `server/`; `spacetime generate`; `BASE_PATH=/ally-call npm run build`; deploy `build/` to GitHub Pages.

   Keep upstream credit visible in the UI footer too: "Built on SpaceChatDB".

## Verify
- Run `npm install` (network is enabled for this job), then `npm run check` and `npm run build` with `BASE_PATH=/ally-call`. Both must
  pass. Report the output.
- The TS module must type-check (`npx tsc --noEmit -p server` or the SDK's equivalent).
- `spacetime generate` needs the CLI, which isn't installed. Write the client against the bindings the generator will produce from your
  module. If you must hand-write binding stubs so the client compiles now, put them in `src/module_bindings/`, mark them
  "replace with spacetime generate", and list them in the report.
- Delete the copied upstream `src/module_bindings/` once yours replace them.

## Report
- Files.
- What changed from upstream.
- The schema and reducers.
- How the room-scoping and privacy work.
- The build output.
- The exact publish, generate and deploy commands Claude should run after the CLI is installed.
- Anything unverified, especially iOS.
