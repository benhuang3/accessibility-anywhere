# Ally Call

A two-person call between an older adult's PC and a trusted helper's phone. Ally creates a fresh incident link; this app handles the call. Audio and video travel through SpacetimeDB reducers and event subscriptions, preserving SpaceChatDB's media design rather than using WebRTC.

Adapted from [SpaceChatDB by Lethalchip](https://github.com/Lethalchip/SpaceChatDB), upstream commit **37b110d49b0d13b0449f4f4015d65c2f6b082686** (also in `UPSTREAM_COMMIT`). The call footer keeps the credit visible: **Built on SpaceChatDB**. Upstream's [SpacetimeDB referral link](https://spacetimedb.com/?referral=Lethalchip) is preserved here too.

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

   `spacetime.json` also contains this generate target, so `spacetime generate` is the short equivalent. The only hand-written binding stub is `src/module_bindings/index.ts`, marked **replace with spacetime generate**. Every copied upstream binding was removed. Run the checks again after generation.

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

## Privacy and deployment checks

`incident` and `presence` are private. A sender-filtered `call_incident` view reveals only rooms the caller has claimed using a valid capability. Media reducers derive the recipient from that room's two roles; callers cannot supply an arbitrary recipient. Clients subscribe to their exact room and recipient identity.

Event tables cannot currently be read through private-table views. Public audio/video event tables therefore have **server-enforced recipient row-level security**. RLS is experimental in this SDK; deployment must verify that an unrelated identity subscribing to all events receives none. Do not remove the visibility filters to make a publish succeed. See the official [event table limitations](https://spacetimedb.com/docs/tables/event-tables/) and [RLS documentation](https://spacetimedb.com/docs/how-to/rls/).

Room IDs and media are sent to SpacetimeDB over WSS; the hash only keeps them out of the static host's HTTP request. This is not end-to-end encryption. Event rows leave the live tables after their transaction, but SpacetimeDB records event inserts in its commit log. The two-hour cutoff stops calls; it does not erase provider logs or retained incident/presence records. There is no analytics, local media recording, room browser or stored browser auth token in this client.

Before a real demonstration, generate bindings and test two separate devices, all-event subscriptions from an outsider, expiry, End call, denied permissions, mute, camera switching, interrupted connectivity and iOS Safari audio. Maincloud, real phone media and iOS behavior remain unverified here.
