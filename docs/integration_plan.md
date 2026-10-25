# Ally integration plan & per-person next steps

Written after merging all feature branches into `integration`. Source of truth for tasks is the
Notion HQ; this file reconciles that board with what the merge actually produced.

## Integration status (branch `integration`)

Merged, in order, on top of `main`:

1. **Ben H — text enlargement** (`enlarge_text`, `win_uia.py`, extension edits). Committed first.
2. **Hayden — senses** (`ally/senses/`, face + movement signals). Clean merge.
3. **Ben W — voice** (`voice_confirm.py`, `voice_tools.py`, Cartesia say/ask_yes_no). Two trivial
   union conflicts resolved (both `agent.py` wiring and `tools/__init__.py` keep senses + voice).
4. **Ganesh — risk** (`ally/risk/`, eval set, plus the `handoff/` folder with the foundation patch).
   Clean merge.

**226 tests pass**, `config.toml` validates, `run.py doctor` loads every tool. Nothing pushed; this
is a local branch for review before it goes to `main`.

What is present now: real, independently built **senses**, **voice**, **risk**, and **text
enlargement**. What is NOT present: the connective tissue that makes them one product.

## The one open design decision (Ben H to settle)

The foundation patch (`handoff/foundation/0001-proactive-foundation.patch`) is in the tree as a file
but **not applied**. It contains two different kinds of thing:

- **Glue nobody else built** — `ally/contracts.py` (the Struggle/Risk/Offer types + event bus),
  `ally/proactive.py` (fuse → pick fix → offer), `ally/habits.py` (auto-learn), `ally/overlay/`
  (highlight, Yes/No bubble, reading card), and the `simulate` CLI command. **This is wanted.**
- **Skeleton versions of already-built parts** — `senses/behavior.py`, `senses/vision.py`,
  `risk/__init__.py`, `voice_confirm.py`. The team built better, live-tested versions of these
  independently, so applying the whole patch would collide with real code. **This is not wanted.**

**Decision needed:** how to take the glue without the skeletons.

- **Option A (recommended):** cherry-pick only the glue — `contracts.py`, `proactive.py`,
  `habits.py`, `overlay/`, the `simulate` command, and the `agent.py`/`server.py` hooks — then adapt
  the hand-built senses/voice/risk to publish onto `contracts.py`'s bus. Keeps all tested work;
  builds only the missing middle.
- **Option B:** apply the whole patch on a fresh base and re-port everyone's real work onto it. More
  disruptive; discards the clean integration above.

This is deferred to Ben H because it defines the contract shape and how every module connects. No
glue has been added yet.

## Current ownership (after the Oct 24 role swaps)

| Person | Owns | Folders |
|---|---|---|
| **Hayden** | Senses: vision (squint, lean, head pose) + movement (clicks, hesitation, retype) | `ally/senses/` |
| **Ben H** (you) | Brain & OS, **integration owner** (merges), proactive routing, habits, contracts | `ally/` core, `ally/platforms/`, `ally/tools/` |
| **Ganesh** | UI/frontend/demo + risk logic + pitch/submission | `ally/overlay/`, `dashboard/`, `extension/`, `ally/risk/` |
| **Ben W** | Voice (Cartesia say/ask_yes_no, spoken offers/warnings) + admin (credits, domain) | `ally/voice_confirm.py`, voice files |

Build until feature freeze (Sun midnight, owner: Ben H). Demo/script/submission are a shared pool
delegated at freeze.

## Per-person next steps

### Ben H (you) — unblock everyone, then integrate
1. **Settle the design decision above.** Until the bus exists, nothing connects end to end.
2. **Add the glue (Option A):** bring in `contracts.py`, `proactive.py`, `habits.py`, and the
   `simulate` command from the patch; wire `agent.py` to start the proactive loop.
3. **Adapt senses/voice/risk to the bus:** senses publishes `Struggle`, risk publishes `Risk`,
   `proactive` turns them into an `Offer`, voice/overlay present it, `agent.call_tool` applies the
   fix (zoom / `enlarge_text` / magnify).
4. **Wire `enlarge_text` as an accommodation** so an accepted offer can call it (Tier A/C already
   work; the accommodation's `{tool, args}` should target `enlarge_text`).
5. **Demo OS + doctor:** run `python run.py doctor` on the Windows laptop/VM; log the OS decision.
6. **Validate the Windows text path** (`win_uia.py` `read_focused_box`, UIA read/write) on the VM —
   confirm `tagPOINT` resolves and the COM call-thread model holds.
7. **M3 auto-learn:** once `habits.py` is in, confirm two-yes → auto-apply works end to end.
8. **GitHub repo + README** public by submission; **fallback audit**; **feature freeze** owner.

### Hayden — senses on real faces
1. **Live test matrix** (the P0 that's still open): 5 people, glasses, smiling/laughing, dim light;
   set the squint/lean thresholds from real data, not synthetic.
2. **Publish onto the bus** once Ben H lands `contracts.py`: emit the documented `Struggle` shape
   (`kind`, `confidence`, `target{app,site,window,x,y,box}`, `source`, `detail`, `ts`).
3. Confirm the two-signal fusion policy with Ben H (the `[senses] solo` default) before freeze.

### Ganesh — the visible half + risk
1. **Overlay** (`ally/overlay/`): the reading card, highlight box, and Yes/No offer bubble. This
   also unblocks Ben H's **Tier B** (`show_text_overlay` is a stub waiting on it) and the demo's
   "reading card" moment. Build to the UI spec (≥64px buttons, 28–32px question, ≤12-word copy).
2. **Extension risk content script:** scan page text + URL, POST to `/api/event` as a `Risk`,
   show the in-page warning banner.
3. **Risk logic** (`ally/risk/` is merged): wire `check_text`/`check_url`/process check into the
   bus; add the remote-access check.
4. **Dashboard "Proactive" panel:** live signal meters, decision trace, habit ledger with Undo.
5. Privacy/consent screen; demo stage pages (fictional-brand bank + fake pop-up).

### Ben W — voice + admin
1. **Voice confirm is merged** (`voice_confirm.py`): confirm `say()` and `ask_yes_no()` work on
   Cartesia with the OS-voice fallback, and that it shares the listener's mic without contention.
2. **Wire to the bus** once it lands: `proactive` calls `ask_yes_no(prompt)` → `yes/no/timeout`;
   spoken risk warnings on a `Risk`.
3. Claim free credits (Cartesia, Apify, etc.); register the domain (admin tasks).

## Blockers summary

- **The bus is the critical path.** Senses/voice/risk/text all exist but can't talk until `contracts.py`
  + `proactive.py` land. That is Ben H's first move after the design decision.
- **Tier B text replacement** waits on Ganesh's overlay.
- **Windows text + magnify** waits on VM validation.

## Voice layer v2: plan (Ben W, branch `benjamin-w/voice-integration`)

**Goal:** voice works inside the proactive loop with no glue code needed from anyone else. The
coordinator's `voice=` slot gets a real object that speaks offers, listens for the answer and
resolves the `Offer`. Spoken risk warnings never ask a question Ally isn't listening for.

**Problems found on `main` `6966696` (reason for this work):**
1. `agent.py` passes `NullVoice` to the coordinator, so offers are spoken but never heard. Every
   offer times out unless someone clicks.
2. `VoiceConfirm.confirm_offer` resolves the `Offer` only for spoken answers. `run.py answer yes` and
   `/api/voice/answer` answer the voice question but leave the `Offer` open.
3. `offer_timeout_seconds = 8` starts before Ally finishes speaking. An older user gets about
   5 s to answer and no time for the re-ask.
4. Risk: `Coordinator.handle_risk` calls `voice.say(spoken)`, and `RiskWire` (on `risk-branch`) asks
   its `Offer` on another thread. The question can cut the warning off, or come before it.
5. Risk copy asks its own questions ("Did you start it yourself?"), but a yes on the `Offer`
   *closes* the tool. A spoken question must always be the one whose "yes" does the action.

**Workstreams (each helper agent in its own worktree, reviewed and merged by Ben W's lead agent):**

| Stream | Branch | Owns | Delivers |
|---|---|---|---|
| A: bus voice | `agent/voice-bus` | new `ally/voice_bus.py`, voice wiring lines in `ally/agent.py`, `confirm_offer` and mode hook in `ally/voice_confirm.py` | `BusVoice`, the coordinator's voice plug-in (fixes 1–5) |
| B: speech | `agent/voice-speech` | `ally/speech.py`, classifier/prompts/ask loop in `ally/voice_confirm.py`, `tests/fake_cartesia.py` | Cartesia API checked against current docs, gap-free read-aloud, "not now" / "stop asking" / "help" answers |
| C: tests + setup | `agent/voice-tests` | `tests/e2e_voice.py`, `tests/fakes/`, `ally/doctor.py` voice part, new `ally/voice_setup.py` | Silent tests on Windows, `doctor` voice checks, `run.py voice-check` guided setup, then a full-agent e2e test |

**Contract additions (for Ganesh's overlay and Ben H's dashboard):** bus topic `"voice"` with
`{"state": "speaking" | "listening" | "idle", "text": caption, "offer_id": int | None, "kind": str, "ts": float}`.
Show it as captions and a "Listening" light. Nothing else changes in `contracts.py`.

## Change log (everyone: add an entry here with every push)

Newest first. Each entry: date and time, who, branch and commit, what changed, and what others need
to know or do. Agents: read the newest entries before starting work.

### Oct 25, 02:10 PT — Hayden (via Claude Code) — branch `hayden/senses-v2`, merges `908ad74`, `26775c9`

**Added (opt-in, defaults unchanged, live behavior unchanged):** senses round 2. 899 tests pass.
- **Squint and lean:** a personal squint model (calibration of about 35 s: read normally, top,
  bottom, smile, squint), a hybrid squint method, and distance-based lean. They are selected with
  `[senses] squint_method` and `lean_method`, which stay `v1`.
- **LibreFace action units, off by default:** `[senses] au_model = libreface`. Weights come from
  `scripts/get_au_models.py`. The license is USC research and non-profit only.
- **Live raw numbers:** `[senses] raw_live` sends live raw face numbers for the new methods.
- **Benchmark:** `python -m ally.senses.bench`.
- **Result on the first raw recording (1 person):** the new squint methods are not ready yet.
  The personal model turned itself off for that person. `v1` stays the default.

### Oct 25, 01:03 PT — Hayden (via Claude Code) — branch `hayden/senses-v2` (from `main` `77dd943`), merge `dab3c61`

**Added, not on `main`, and live behavior unchanged:** groundwork for fairer squint and lean detection. 872 tests pass.
- **Senses recorder v2 (`ally/senses/matrix.py`, camera helper `raw` option):**
  - Saves raw face numbers (landmarks, blendshapes, face matrix), never pictures.
  - New script parts: top and bottom of the screen, fast blinks, tired eyes, wink, soft squint, long
    natural small-text reading, soft lean, sit back.
  - Optional `--eyes` and `--age`, plus `--repeat` and `--parts`.
- **`ally/senses/features.py`, `ally/senses/bench.py`, and new detector classes in `detectors.py`:**
  - Squint and lean candidate detectors and an offline benchmark that reports per person and per group.
  - `[senses] squint_method` and `lean_method` default to `v1`, today's detectors.
- **Fairness evidence so far:**
  - On the London Face Set (CC BY 4.0), a fixed squint threshold flags East Asian faces at rest
    far more often than others.
  - The open-source LibreFace AU7 reads more resting "squint" on Black faces (15% vs 3% white,
    n=13/69).
  - So the design compares each person only with their own calibration.
- **Next on this branch:** the personal squint model, LibreFace as an optional off-by-default vote
  (USC research and non-profit license only), and volunteer recordings.

**Others:** nothing to do. Ganesh and Ben H: the senses `Struggle` shape on the bus is unchanged.
### Oct 25, 01:56 PT — Hayden (via Claude Code) — branch `hayden/voice-calibration`, commit `d578f76`

**Fixed a Windows bug that affects anyone with a Cartesia key: Ally was silent and ignored voice commands.**
867 tests pass.
- **Cause:** Cartesia's `/tts/bytes` WAV is streamed, so its RIFF and data sizes are `0xFFFFFFFF`.
  - Windows `PlaySound` refuses it. `_Sound` plays asynchronously, so nothing is heard and there is
    no error.
  - Python's `wave` reads it as about 25 hours long. The Speaker then believed it was still saying
    the greeting, and the listener treated everything as Ally's own voice (only "Ally, stop"
    worked).
  - `doctor` showed this as "89478.5 s of audio".
- **Fix:** `speech.fix_wav_sizes` writes the real sizes into every Cartesia clip, and into cached
  prompt files saved before the fix. New `StreamingWavTests`.
- **Ben W and Ben H:** please merge this branch, or at least `d578f76`, into `main` soon. On Windows
  with `CARTESIA_API_KEY` set, voice is broken without it. Delete `state/voice_cache` once if you
  like; the old files are repaired on read either way.

### Oct 25, 01:31 PT — Hayden (via Claude Code) — branch `hayden/voice-calibration` (from `main` `77dd943`), commit `bc5b13f`

**Fixed:** the eye-tracker setup's spoken prompts no longer get cut off mid-word. 865 tests pass.
- **Queued prompts:** setup prompts queue behind the sentence already playing
  (`speaker.say(text, interrupt=False)`).
- **Quiet during setup:** while a calibration or check is on screen, and for 5 s after, the "position
  changed" re-tune line is held, and the senses report no lean or squint. The setup asks people to
  lean in and sit back, so no offer talks over it.
- **Pacing:** each head-position round starts only after its prompt has had 2.5 s to be said.
- **Results spoken:** calibration and quick calibrate now say their result. `agent.handle_text`
  skips the reply once a tool has spoken, so voice requests never heard it.

**Others:**
- **Ben H and Ben W:** `GazeManager.busy()` says when the setup is on screen, if voice or offers should
  hold too.
- **Ben W:** there are no changes to `speech.py`. It already queues with `interrupt=False`.

### Oct 25, 00:11 PT — Hayden (via Claude Code) — branch `hayden/start-fixes` (from `main` `35b7608`), commit `d1dbe07`

**Fixed Ben H's open issues 7 and 8 (23:45 entry).** 858 tests pass. Merged into `main` (fast-forward); it touches
only `ally/gaze_model.py`, `tests/test_runtime.py` and `tests/test_senses.py`.
- **7. Ally starts without numpy again.**
  - `ally/gaze_model.py` imports numpy only when it is installed. Only the eye tracker's math needs
    it, and mediapipe brings it.
  - `fixation()`, which the agent process calls, is now plain Python.
  - New test (`test_runtime.NoNumpyTests`): Ally starts in a process with numpy blocked, and the eye
    tracker tools are still registered.
- **8. `test_senses` `test_calibration` is no longer timing-dependent.**
  - The fake camera now stamps frames 20 ms apart in frame time, which is what the setup window
    measures.
  - A simulated slow machine (16 real frames a second) now collects 51 frames. It used to fail with
    28.

### Oct 25, 00:10 PT — Ben H (via Claude Code) — `main` (merged from `benh/os-layer`, commit `53b75d1`)

**Merged `benh/os-layer` into `main` and pushed.** `main` now has every branch: page steps + Simplified mode +
AI check, the Windows OS layer, Hayden's senses and eye tracker, Ben W's/Ben H's voice (BusVoice wired),
Ganesh's risk protection, overlay and demo site. All 43 test modules pass on Windows 11.
**Decisions (Ben H):**
- **One Yes/No surface:** Ganesh's overlay bubble (plus voice) answers offers; Hayden's gaze cards are off by
  default (`[gaze] answer_offers = false`). Hayden: turn them back on per machine if wanted.
- **Offer timeout 20 s** (`[proactive] offer_timeout_seconds`), up from 8 s.
- **One confidence gate:** struggles from the senses (source `senses`, `senses.*`, `simulated`) skip the
  Coordinator's `min_confidence`; `run.py simulate` / API struggles are still checked.
**Everyone:** pull `main`; branch from it for new work. Reload the extension (new files). Recalibrate the eye
tracker once. Open issues are in the 23:45 entry below.

### Oct 24, 23:58 PT — Ben H (via Claude Code) — branch `benh/os-layer`, integration (merge `17f57df`)

**Integrated every branch into `benh/os-layer` and pushed. `main` not updated yet.**
- Merged: `benh/voice` (4 commits), `hayden/eye-tracker` (46 new), `risk-branch` (27). Already in before:
  `hayden/bus-fixes`, `benjamin-w/voice(-integration)`, `ganesh/risk-handoff`, `integration`, `main`.
- Conflicts resolved: `ally/platforms/base.py` + `windows.py` (Hayden's eye-pan changes kept next to the
  reading-card/text-box/DPI code), `rules.py` help text, `agent.py` shutdown, change log (every entry kept).
- **Wired:** `BusVoice` is now the Coordinator's voice (offers asked and answered out loud, "say undo" after a
  fix). Ganesh's overlay loads as the Coordinator's UI; `RiskWire` attaches on start.
- **Tests (Windows VM):** all 43 modules pass, incl. both browser suites and every risk/overlay/voice/gaze suite.
  Ally starts with overlay, risk, voice and gaze on; config valid.
- **Hayden:** recalibrate the eye tracker once (old calibrations are v1).
- Decisions still open (Ben H): how many Yes/No surfaces per offer (overlay bubble + gaze cards + voice), offer
  timeout 8 s vs ~20 s, one confidence gate vs two, when to merge into `main`.

### Oct 24, 23:45 PT — Ben H (via Claude Code) — branch `benh/os-layer`, open issues (docs only)

**Pushed `benh/os-layer` to origin. Not merged to `main`.** Open issues to fix next (measured in the Windows VM):
1. **"Bigger buttons" grows the click area, not the words.** Buttons get min 44 px tall plus padding (e.g.
   Wikipedia Search 71x32 -> 74x44), but a 12 px label stays 12 px (labels only grow with "make the text
   bigger"). Plan: button labels at least 18 px (or 1.25x), like form fields.
2. **Links styled as buttons aren't touched** (`<a class="usa-button">` "Español" on usa.gov; "Sign up",
   "Donate", "Subscribe" on many sites). Plan: include `a[role=button]` and `<a>` with button-like classes.
3. Small toggles inside lists/menus (Wikipedia's 22x22 contents arrows) are skipped on purpose (enlarging them
   broke the list); buttons already 44 px tall only get wider. Add a browser test that measures what grew.
4. CNN's "Legal Terms and Privacy" consent dialog isn't recognised as clutter, so Simplified mode refuses CNN.
5. The `<mod>+=`/`<mod>+-` hotkeys and the extension's Alt+Shift+Period/Comma shortcuts still zoom.
6. allrecipes.com hung once (before the speed and routing fixes); not re-tested since.
7. Since `hayden/eye-tracker`, Ally doesn't start without numpy (`ally/gaze_model.py` imports it at the top) - Hayden.
8. `test_senses` `test_calibration` fails now and then in the VM (timing) - Hayden.

### Oct 24, 23:43 PT — Hayden (via Claude Code) — branch `hayden/eye-tracker`, commits `ee1e039`, merge of `agent/cursor`, `d64e290`, `52782a3`

**Changed:** up-and-down accuracy, a shorter setup, and quick calibrate. 475 tests pass.
- **Eyelids for up and down (`ee1e039`, `ally/gaze_model.py`):**
  - The v4 and v5 vectors now end with the iris position between the eyelids. v4 is 10 numbers
    and v5 is 12.
  - Calibration fits a vertical-only weight set, `coef_lid`, and keeps it only when it predicts
    held-out dots better.
  - On six recordings, vertical error after the user moved fell from 95 to 66 px (median).
  - Older saved models still load and predict as before.
  - **Anyone reading feature vectors:** don't assume a length of 9 or 11. Use `gaze_model` helpers.
- **Cursor's merged work:**
  - The setup is the 9-dot grid plus the two head-position rounds, about 45 s.
  - Click learning is saved with the calibration and restored on start.
  - A look that picks a card sends the helper a `choice t0 t1 x y` line, which teaches it.
- **Calibration rows (`d64e290`):** the top and bottom rows sit 6% from the edge (was 10%). The worst
  vertical errors were beyond the old rows.
- **Quick calibrate (`52782a3`):**
  - "Quick calibrate", "recalibrate quickly" and "check my eye tracker" run 5 check dots at 0.6 s
    each, about 6 s, and re-tune the saved setup.
  - Measured: an earlier session's model went from 176 to 114 px. A full setup that day scored 91 px.

**Not merged yet:** Codex's Corrector rework (save and restore, refit). Replaying recorded clicks
through it made error worse in 4 of 6 sessions, so it is under investigation.

### Oct 24, 23:20 PT — Ben H (via Claude Code) — branch `worktree-voice`, streaming speech-to-text (unwired)

**Changed:** new `ally/ws_client.py` (stdlib WebSocket client, ws:// and wss://), new `ally/stt_stream.py`
(`StreamingSTT`, `transcribe_blocks`: Cartesia ink-2 turn detection over
`wss://api.cartesia.ai/stt/turns/websocket`, `X-API-Key` header), new `tests/fake_cartesia_ws.py` and
`tests/test_stt_stream.py` (28 tests). Built by a helper agent, reviewed here.
**For everyone:** nothing uses it yet; voice still uses the batch `/stt` call. Unverified without a real
key: how a bad key is refused, whether `close` always ends an open turn, real TLS to Cartesia.

### Oct 24, 22:55 PT — Ben H (via Claude Code) — branch `benh/os-layer`, commit `d0f8158`

**Committed (local, not pushed yet).** Product decision (Ben H): **bigger text means bigger fonts; Ally never
zooms unless the user says "zoom".**
- `enlarge_text` in Chrome now enlarges the page's own font sizes (new `ally_text` layer, one rule per original
  size; fixed-height boxes the bigger text overflows may grow). `goal="smaller"` steps down. Zoom untouched.
- Phrases: "bigger", "make the text bigger", "too small", "I can't read this" -> bigger text; "smaller", "too
  big" -> smaller text; only "zoom"/"zoom in"/"zoom out" zoom. The AI fixer can change text size, not zoom.
- `[page_enlarge] max_text_scale` (default 2.0). Tests updated (rules, pagekit, browser); all pass on Windows.
- **Known:** CNN's "Legal Terms and Privacy" consent dialog isn't recognised as clutter, so Simplified mode
  refuses CNN ("a pop-up is open"). The `<mod>+=`/`<mod>+-` hotkeys and the extension's own Alt+Shift
  shortcuts still zoom (they are zoom keys).

### Oct 24, 22:50 PT — Ben H (via Claude Code) — branch `worktree-voice`, speech queueing

**Changed:** `ally/speech.py`, `tests/test_voice_confirm.py`. `say(..., interrupt=False)` now waits for
the sound already playing instead of playing over it (read-aloud lead-in, then the text); `stop()`
stops both. No change for normal (interrupting) speech.

### Oct 24, 22:40 PT — Ben H (via Claude Code) — branch `worktree-voice`, voice in the proactive loop

**Changed:** new `ally/voice_bus.py` (`BusVoice`), `ally/voice_confirm.py`, `ally/voice.py` (one line),
new `tests/test_voice_bus.py`, new `tests/voice_fakes.py` (shared fakes), `tests/test_voice_confirm.py`,
`tests/voice_phrases.py`. Not wired in yet: `agent.py` still passes `NullVoice`.
- `BusVoice` is the Coordinator's `voice=`: it asks the Offer out loud and resolves it, says "Done. Say
  undo to put it back." after an accepted fix (or "Sorry, that didn't work."), then listens 6 s for
  "undo" (runs `reset_all`; anything else heard goes on to the listener). Lines said during a question
  wait for it to end.
- Risk Offers (`context="risk"`, as RiskWire on `risk-branch` makes them): calm, slower voice; the
  question waits up to 1 s for its warning and says both together.
- Labelled choices (RiskWire's "Are you sure?" with "Close it" / "Keep it open"): asked as "Should I close
  it, or keep it open?"; a bare "yes" / "I'm sure" is unclear, never a close.
- `offer.pause_seconds`: "no" isn't accepted before the pause ends. `created + timeout`: no re-ask that
  can't finish. "Stop asking me" is its own no (snoozes struggle offers; a plain no for risk).
- While a helper video call is open (`risk_contact` with `call_opened`) voice asks nothing for 10 min.
**For Ben H (integration):** in `agent.py`, `voice=BusVoice(self.confirm, self.bus, call_tool,
snooze=getattr(self.proactive, "snooze", None))`; do it on `benh/os-layer` (its gaze lines sit there).
Consider `offer_timeout_seconds` ~20 (gaze cards use `offer.timeout` too, so with Hayden).
**For Ganesh:** please publish a "call ended" event (voice goes quiet for a fixed 10 min until then), and
check the spoken wording for labelled choices.

### Oct 24, 22:27 PT — Hayden (via Claude Code) — branch `hayden/eye-tracker`, commit `0a248f1`

**Changed:** reading-aid tuning after Hayden tried it. 463 tests pass.
- **Edge delay:** looking near an edge waits 0.6 s before the view moves (was 0.3 s).
- **Top speed:** the new `[gaze] max_pan_speed` (default 0.35 screen widths a second) caps every
  pan. The trip back to the start of a line may peak at twice that, and takes longer when it would.
- **New defaults:**
  - `[gaze] pan_speed` defaults to 0.85. The named speeds are slow 0.4, medium 0.85 and fast 1.5.
  - "Magnify what I'm looking at" opens at 1.5x.
- **Code:** `Platform.set_gaze_source(latest, pan_speed, max_speed=None)` gained `max_speed`, and
  None keeps the saved cap. `ally/agent.py` passes it from config, on the same two lines as before.
- **Ganesh:** `risk-branch` also changes `ally/agent.py`, `ally/config.py` and
  `ally/default_config.toml`. A dry-run merge is clean.

### Oct 24, 22:20 PT — Ben H (via Claude Code) — branch `benh/os-layer`, commit `30e80b2`

**Committed (local, not pushed yet). Not merged.** Windows only from now on (Mac abandoned).

**Changed:**
- **AI check and fix after the page steps** (spec: "AI check and fix" in `docs/page_steps.md`): Claude
  compares before/after screenshots of the tab and fixes what got worse with limited, checked actions;
  3 tries, then the page is put back. Runs in the background after Ally answers; speaks only if it
  changed something. `[page_enlarge] ai_check` / `ai_background`.
- **Speed:** page steps now answer in about 0.2–4 s on real sites (Wikipedia, usa.gov, CNN), down from 9–33 s.
  Enlarge text predicts the zoom from one measurement instead of trying six levels.
- **Bugs found on real sites and fixed:** Chrome's 2-screenshots-per-second limit; a popup command's page
  actions could go to a different connected browser (now: the asking browser); two WebSockets at extension
  startup; the buttons layer broke compact list buttons (Wikipedia's table of contents).
- Tests: `tests/timed.py 75 <modules>` runs suites with a hard limit (they finish in seconds; longer =
  hang). Test browsers no longer connect to a running Ally. Real websites: `scripts/real_sites.py`.

**For others:**
- **Ben W / everyone:** `brain.ask` and `ctx.ask` take `images=[(bytes, media_type), ...]` for several pictures.
- **Ganesh:** the AI fixer can never hide `#ally-risk-banner`, dialogs or alerts (checked in the page).
- **Hayden:** `test_senses` `test_calibration` fails now and then in the Windows VM ("saw your face clearly in
  only 28 frames"): it depends on machine speed. It passes on re-run.
- Privacy: with a key, a screenshot of the visible tab goes to Claude after page changes (never on excluded
  pages such as sign-in or payments). Turn off with `[page_enlarge] ai_check = false`.

### Oct 24, 22:15 PT — Hayden (via Claude Code) — branch `hayden/eye-tracker`, commits `141222f`, `878a894`

**Changed:** the eye-following magnifier is now a reading aid, and the live eye tracker runs the v5
hybrid model. 462 tests pass.
- **Reading aid (`141222f`):**
  - In `ally/platforms/base.py`, the new `ReadingPan` notices when the eyes move forward along one
    line in small steps. It then pans right at the reader's pace, keeping the words being read a
    little left of the middle.
  - At the end of a line, a jump back to the left returns the view to where the line began, eased
    over 0.8 s.
  - Looking around without reading leaves the view still, and edges pan as before.
  - The Windows magnifier loop (`platforms/windows.py`) uses it and steps at 60 Hz while following
    the eyes.
  - "Help me read this" now starts `magnify_where_i_look` (`ally/rules.py`).
  - Tested with a simulated reader at 1.5x to 3x, including tracker bias, jitter and the real smoother.
- **v5 (`878a894`, merge of Cursor's branch):**
  - `[gaze] features` defaults to `v5`: v4 plus MobileGaze `mobileone_s0` angles, run in the camera
    helper in memory.
  - It falls back to v4, with a sentence in the calibration result, when
    `state/models/mobileone_s0_gaze.onnx` or onnxruntime is missing. `doctor` names both fixes, and
    onnxruntime is in `requirements-optional.txt`.
  - **Bug fixed along the way:** the camera helper accepted only v1 and v2, so since v4 became the
    default, live calibrations were silently fitted as v1.
  - On the benchmark session, v5 has a median error of 73 px after calibration (v4: 76) and 143 px
    after moving (v4: 169).

**Others:**
- **Ben H:** a gaze calibration saved before this push is v1. Recalibrate once to get v5.
- **Ganesh:** `README.md` and `ally/default_config.toml` also changed on `risk-branch`; a dry-run
  merge is clean.

### Oct 24, 22:01 PT — Ben H (via Claude Code) — branch `worktree-voice`, voice fixes

**Changed:** `ally/voice_confirm.py`, `ally/speech.py`, `ally/listener.py`, `tests/test_voice_confirm.py`,
one comment line in `config.toml` / `ally/default_config.toml`.
- `voice_confirm.py` loads on Python 3.11 again (an f-string needed 3.12).
- Re-asks use the last question ("Having trouble clicking? Want me to ...?").
- Risk questions: "stop them" / "get me out of here" mean yes (the protective action); a bare "stop",
  "cancel" or "go back" is unclear and re-asked, never a silent no.
- "Ally, stop" (an explicit `Speaker.stop()`) ends an open question; other speech doesn't.
- A yes/no from `run.py answer`, the dashboard or the API now resolves the Offer too.
- Voice-name lookups use the 4 s speech timeout; unknown names aren't looked up again for 60 s.
**For everyone:** `/api/stop`, `stop_speaking` and `reset_all` now end an open spoken question.
**For Ganesh:** please check the risk-answer rule above.

### Oct 24, 21:26 PT — Hayden (via Claude Code) — branch `hayden/eye-tracker`, commit `8676b99`

**Added:** a benchmark of the open-source MobileGaze model against our eye tracker. Nothing changes in
live behavior. 449 tests pass.
- **`ally/gaze_cnn.py`:** runs MobileGaze ONNX models (github.com/yakhyo/gaze-estimation, MIT) on
  MediaPipe's face box, in memory only, on the installed onnxruntime with no PyTorch.
- **Recording:** `python -m ally.gaze_record ... --cnn mobileone_s0,resnet18` stores each model's
  yaw and pitch per frame. Pictures are never saved.
- **Scoring:** `python -m ally.gaze_cnn evaluate state/gaze_runs` compares three approaches on the
  same session: MobileGaze plus head geometry, a hybrid of our features plus MobileGaze's angles,
  and our v4.
- **Model files:** `python scripts/get_gaze_models.py` downloads them into `state/models` (4.8 MB and
  45 MB). Measured here at 22 and 58 ms per frame.
- **Research summary** (a Claude helper, sources in its report):
  - Published webcam trackers reach 2–4°, and ours is about 2° per target, so the main problem was
    frame noise. That's fixed by the smoother in the next entry down.
  - MobileGaze was the only candidate with a permissive license, downloadable ONNX weights and CPU
    speed. Its published accuracy is only on the wide-angle Gaze360 dataset (11–13°), so it has to be
    measured on a laptop.
  - Rejected for licenses or PyTorch-only weights: UniGaze, OpenFace, EyeGestures, L2CS-Net,
    ETH-XGaze.

**Next:** the user records one benchmark session; the result decides between keeping our model,
the hybrid, or switching to MobileGaze.

### Oct 24, 21:05 PT — Hayden (via Claude Code) — branch `hayden/eye-tracker`, commit `01d34a0`

**Changed:** live gaze points are now steady.
- **Live testing found the gaze dot jumping.** Single frames are much noisier than the per-target
  medians the evaluator reported: median 128 px error, and 10% of frames off by more than 274 px,
  with jumps of about 60 px between frames.
- **New `gaze_model.GazeSmoother`, used for the camera helper's gaze points:** it holds a fixation
  steady, ignores single stray frames, and moves only when 3 frames agree. That feeds the gaze dot,
  the magnifier following the eyes, and `target()`.
- **Evaluator:** `ally.gaze_eval` now reports per-frame error, jitter, spread and settle time.
- **Tests:** 437 pass.

**Medians over 5 recordings, right after calibration:**

| Method | Error (typical / worst 10%) | Movement per frame | Spread while looking at one dot | Settle |
| --- | --- | --- | --- | --- |
| Old One Euro filter | 152 / 293 px | 38 px | 54 px | 192 ms |
| New smoother | 147 / 330 px | 0.1 px | 2 px | 91 ms |

**Not fixed:** accuracy. A steady wrong point stays wrong; the worst 10% got a little worse.

**In progress:** a benchmark of the open-source MobileGaze model (MIT, ONNX, 12–48 ms per frame on
this CPU) against ours, on the user's own face. Research is summarized in the next entry once the
benchmark has run.

### Oct 24, 20:30 PT — Ben H (via Claude Code) — branch `benh/os-layer`, commit `68e4150`

**Committed (local, not pushed yet). Not merged to `main`.** Before it: merged `hayden/eye-tracker` into
this branch (`f99c867`; change log conflict only). Spec: `docs/page_steps.md`.

**Built: five browser steps that work alone and in any combination, plus Simplified mode.**
- `enlarge_text` (Chrome): measures the page at Chrome's zoom steps and picks the biggest zoom that
  doesn't break the layout (then checks; undoes back to your zoom if it broke). Asking again = bigger.
- `enlarge_buttons`, `hide_clutter(kinds)`, `simplify_menus` (Ally menu panel/strip, big dropdown chooser),
  `check_page_layout` (read-only), `make_page_easier(steps)`, `open_reader` (keeps headings/links/lists).
- `simplified_mode`: sorts the page first. Passwords, payments, sign-in, banks, open dialogs, the risk
  banner and PDFs are left alone ("I won't simplify this page because..."). Articles get the reader;
  forms get clutter + text + buttons; other pages get all four. "Simplifying..." card, "Simple mode on ·
  Undo" chip, per-site memory only after an explicit yes, popup switch "Simple mode on this site".
- Phrases: "simple mode (off)", "bigger buttons", "hide the pop-ups", "declutter" (was reader view; reader
  keeps "reader mode" / "simplify this page"), "simplify the menu", "check the page".
- Proactive: squint/lean on a site -> `enlarge_text`, rage-click -> `enlarge_buttons`, both -> both;
  2+ kinds on one site within 5 min -> offers Simplified mode (once a day). Old habit keys
  (`page_zoom_150`, `min_font_20`) are skipped: learned habits reset once. A reset snoozes habits and
  offers for that site for 30 min. Page habits don't replay on every Alt+Tab.

**Tests:** macOS: every module passes (incl. 18 browser step tests + 7 end-to-end tests of the real Python
tools driving the real extension, no agent, no port 8765) except `test_senses_lab` (1 failure that was
already there before this work). Windows 11 ARM (Parallels): 211 tests OK incl. both browser suites;
`scripts/os_lab.py --auto` 8/8 (UI Automation works from a fresh thread, so the COM fix holds).
Run them: `tests/browser/README.md`, `scripts/browser_tests.ps1`.

**For others:**
- **Ganesh:** this adds `extension/steps/*.js`, `extension/page/*.js`, page-step ACTIONS at the END of
  `ACTIONS`, and a "Simple mode" block in the popup; `onMessage` untouched. `resetPage` now also removes
  Ally's page pieces but never `#ally-risk-banner`; Ally overlays sit at z-index 2147483646, below your
  banner. Clutter hiding never hides fixed boxes ≥ 25% of the screen, `tel:` links or scam words (virus,
  locked, call support), so your scanner still sees scam pop-ups. `restyle_page none` no longer removes
  `ally_*` layers.
- **Hayden:** `Target.from_dict` now turns `None` into "" (your note 1); `agent.py` calls
  `senses.answer(struggle, choice)` on every offer result (note 3); the coordinator uses your fused
  `detail.signals` to pick the fix. **Problem found:** since `hayden/eye-tracker`, `ally/gaze_model.py`
  imports numpy at the top and `agent.py` imports `gaze`, so Ally doesn't start without numpy (the README
  says no required packages). Please make that import lazy or optional.
- **Ben W:** `simplified_mode` calls `ctx.say("One moment, I'm making this page simpler.")` before working.
- `test_network`'s size test deadlocked on macOS (socket buffer); fixed by sending from a thread.

### Oct 24, 20:25 PT — Hayden (via Claude Code) — branch `hayden/eye-tracker`, commit `f4292fc`

**Fixed:** offline phrase rules for the gaze dot and the eye-follow speed. Without an API key the
phrase rules are the only way Ally understands requests, and the gaze-dot rule only matched the
exact words "show where i am looking".
- **Symptom:** "show where I'm looking at" did nothing, and "show me where I'm looking" magnified
  instead.
- **Fix:** `ally/rules.py` now accepts everyday wordings (I'm/I am, "at", "me", dot/circle/marker),
  and those rules run before `look_magnify`.
- **Tests:** 434 pass.
- **For everyone:** if you add offline phrases, test the contracted forms too ("I'm", "don't").

**Evidence:** first recording with the three-position calibration (Hayden, glasses off, bright light):

| Model | Right after calibration | After moving, no re-tune | After a 5-dot re-tune |
| --- | --- | --- | --- |
| v4 (default) | 105 px (4/4 regions) | 176 px | 71 px |
| v1 | 614 px | 625 px | 200 px |

v1 can't use calibration across several head positions; v4 is the only model that benefits from it.

### Oct 24, 20:15 PT — Hayden (via Claude Code) — branch `hayden/eye-tracker`, commit `11be275`

The eye tracker after live testing: it now copes with head movement, follows the eyes calmly, and
can show where it thinks you're looking. 433 tests pass.

1. **Head movement no longer breaks it.**
   - **New default model `v4`:** the head's position and rotation (MediaPipe's transformation matrix,
     in cm) are used as geometry; only the eye angle is learned from calibration.
   - **Error after the user moved, with no re-tune, on 4 real recordings:**

     | Model | Hayden 1 | Hayden 2 | p3 | p2 |
     | --- | --- | --- | --- | --- |
     | v1 (before) | 769 px | 999 px | 773 px | 917 px |
     | v4 | 204 px | 155 px | 159 px | 259 px |

   - **Right after calibration** it's close to v1.
   - **Codex's `v3`** (another geometric attempt) couldn't fit its own calibration and isn't used.
   - **Without a head pose,** `v4` falls back to a `v1` model fitted alongside.
   - **Settings:** `[gaze] features = "v4"` by default (also accepts `v1`, `v2`, `v3`).
2. **Calibration at three head positions.** After the 13 dots: "sit back a little" (5 dots), then
   "lean in a little" (5 dots). `[gaze] calibration_poses = 3`; set `1` for the old setup.
   Recordings now carry `pose` on target lines and `screen_mm` in the meta line.
3. **Calmer eye-following while zoomed in:**
   - **Speed:** default `pan_speed` 0.35, was 1.0.
   - **Start:** a quadratic ramp, and panning waits until the gaze has stayed in the edge band for
     0.3 s.
   - **Smoothing:** speed changes are eased.
   - **Adjusting it:** the tool `eye_follow_speed` and the phrases "follow my eyes slower/faster" save
     to `config.toml`.
   - **Files outside gaze:** the eye-follow block in `ally/platforms/windows.py` and `edge_speed`
     plus a new `ease_pan` in `ally/platforms/base.py`. Ben H: platform files, FYI.
4. **Gaze dot:** a translucent, click-through, never-focused circle where the tracker thinks the
   user is looking, sized by its uncertainty. It also works while zoomed in.
   - **Turning it on:** the tool `show_gaze` and the phrases "show where I'm looking" / "hide the eye
     dot", plus `[gaze] show_dot` (off by default).
   - **Where it lives:** `ally/gaze_dot.py`, drawn by the camera helper. It doesn't use `ally/overlay`.

**Conflict check** (fetched at 20:10): no new change-log entries from others. Ganesh's
`risk-branch` (19:44) shares only `ally/config.py` and `ally/default_config.toml` with this, in
different sections.

**Needs a person and camera:** a three-position calibration, the slower panning and voice speed
change, and the dot (no focus taken, clicks pass through, follows the zoomed view).


### Oct 24, 19:25 PT — Hayden (via Claude Code) — branch `hayden/eye-tracker`, docs only

**Changed:** this file only.
- **Merged Ben W's log into mine:** his branch `benjamin-w/voice-integration` started this same
  change-log section on its own, so the two would have conflicted. Both sections and all entries are
  kept, newest first, so Ben H can merge either branch cleanly.

**People in the recordings** (aliases differ between the two test tools):

| Person | Senses sessions (`ally.senses.matrix`) | Gaze sessions (`ally.gaze_record`) |
| --- | --- | --- |
| Hayden | `hayden` ×2 | `hayden` ×2 |
| New person | `p2` ×1 | `p3` ×1 |
| Person who turned their head (no longer available) | none | `p2` ×1 |

So "p2" means a different person in each tool. Senses has 2 people; gaze has 3. The recordings stay
local in `state/`.

**Conflict check before committing** (fetched at 19:20):
- **Ganesh's `risk-branch`:** merges into `hayden/eye-tracker` with no conflicts.
  - It touches the same `ally/agent.py`, `ally/config.py` and `ally/default_config.toml`, in
    different places.
  - **Design overlap still open:** its overlay's Yes/No bubble and the gaze Yes/No cards would both
    appear for the same offer. Ganesh: pick one (turn off `[gaze] answer_offers`, or put the
    overlay's Yes and No in the left and right screen halves and let the gaze answer them).
- **Ben W's voice plan:**
  - Changes the `Coordinator(..., voice=...)` call in `agent.py`. My `self.gaze.attach_bus(self.bus)`
    sits on the next line, so that spot may need a hand merge.
  - Behaviour: `BusVoice` and the gaze can both answer an offer, and the first answer wins.
  - **Timing:** his point 3, that the offer timeout starts before speech ends, also cuts the time to
    answer by looking; the cards use the same timeout.

### Oct 24, 19:20 PT — Hayden (via Claude Code) — branch `hayden/eye-tracker`, commit `0f78362`

**Fixed:** the eye tracker's 5-dot re-tune and click correction measured drift from predictions that
were already clamped to the screen. After a head move the estimate can sit about 1000 px off
screen, so the clamp hid most of the shift. `gaze_model.predict(..., clamp=False)` now feeds the
re-tune and the click correction; what Ally uses is still clamped. Only `ally/gaze_model.py` and its
tests changed. 410 tests pass.

**Real sessions, v1 model, error after drift and a 5-dot re-tune:**

| Session | Before the fix | After |
| --- | --- | --- |
| Hayden, session 1 | 128 px | 128 px |
| Hayden, session 2 | 638 px | **125 px** |
| New person (p3) | 607 px | **130 px** |
| Person 2 (head turned during calibration) | 814 px | 427 px |

- **Right after calibration:** p3 got 129 px with all 5 regions right, so 2 of the 4 sessions meet
  the bar.
- **Click correction:** p3's real clicks alone brought their drift from 773 px down to 349 px.

**For others:** nothing to change.

### Oct 24, 19:15 PT — Ben H (via Claude Code) — branch `benh/os-layer`, commit `dd26242`

**Committed (local, not pushed yet):** Windows OS-layer fixes, the reading card and an OS-only test harness. **Not merged.**
Branch also has `hayden/bus-fixes` and `benjamin-w/voice-integration` merged in (change log only
conflict). Tests: the AGENTS.md suite plus `test_senses`, `test_os_layer`, `test_ui_text`,
`test_win_uia` pass on macOS. **Not yet run on Windows**; that's next, in a Parallels VM.

**Changed:**
- **UI Automation threading (`ally/platforms/win_uia.py`):** every tool runs on a new thread, and
  the shared COM object was used from threads that never set up COM, so `focused_element()` likely
  returned `None` inside the agent. All UIA calls now run on one worker thread (multithreaded
  apartment) with a 2 s timeout; a hung app gets a fresh worker instead of a frozen tool.
- **No corner zoom:** an element UIA can't place, `(0, 0, 0, 0)`, is no longer treated as a box.
- **DPI (`windows.py`):** per-monitor DPI awareness when the adapter starts, not only when the
  magnifier first runs, so UIA boxes, the cursor and the magnifier agree on 150%/200% displays.
- **Reading card (Tier B):** `ally/card_helper.py` (Tk, its own process) + `ally/platforms/cards.py`.
  `show_text_overlay(box, text)` / `clear_text_overlays()` now work on Windows and macOS: a big
  card just below (or above) the box, Close button and Esc.
- **Native rewrite:** `enlarge_text(goal="...")` writes into the focused native text box via UIA
  when it's editable and holds 200 characters or less (a ValuePattern replaces the whole value,
  i.e. a whole document in an editor). The old text is kept; `restore_ui_text` and `reset_all` put it
  back. Anything else gets the reading card.
- **`scripts/os_lab.py`:** tests only the OS layer (no agent, server, port 8765, Claude, senses,
  voice or risk). `--auto` changes nothing on screen; the default asks y/n after each visible step.
  **`scripts/win_setup.ps1`:** copies the repo off the Parallels `\\Mac` share, installs Python and
  the 4 OS packages, runs the lab.

**For others:**
- **Ganesh:** `Platform.show_text_overlay` is the hook for your overlay. If `ally/overlay/` should
  draw reading cards instead, replace `self.cards` in `windows.py`/`macos.py`; the tool side
  (`enlarge_text`, `restore_ui_text`, `reset_all`) doesn't change.
- **Hayden:** the agent process is now DPI-aware on Windows from the start. Your gaze helper already
  was; the senses input helper (pynput) isn't, so its click x/y may be scaled on a 150% display.
- **Your notes for me (18:35 entry):** read, not done yet. Next on my list: the `Target.from_dict`
  guard, one confidence gate, `senses.answer()` on `offer_result`, and wiring `enlarge_text` in as an
  accommodation.

### Oct 24, 19:10 PT — Ben W (via Claude Code) — branch `benjamin-w/voice-integration`, plan only

**Changed:** this file only (the "Voice layer v2: plan" section above). No code yet.
**For everyone:** voice will plug into `Coordinator(voice=...)` as `BusVoice`. Keep calling
`voice.say(text)` and `voice.ask(offer)` as today; nothing else to change on your side.
**For Ganesh:** `RiskWire` keeps asking through `Coordinator.ask`. Voice will speak the warning
statement first, then ask *your `Offer.prompt`* as the question (the copy's own questions are
dropped from the spoken statement so a "yes" can only mean the action the `Offer` performs).

### Oct 24, 19:05 PT — Hayden (via Claude Code) — branch `hayden/eye-tracker`, commit `4d3f365`

**Pushed:** eye tracker v2, built on current `main` (includes `hayden/bus-fixes`). **Not merged.**
409 tests pass. Spec: `docs/eye-tracker-plan.md`. Guide: `docs/gaze-validation.md`.

**What it does:**
- **Calibration** ("Ally, set up my eyes", about 50 s):
  - A readiness check, then 13 dots and 5 check dots it wasn't trained on.
  - It re-shows a dot if the head moved.
  - The result in plain words: good, okay or poor.
- **Default model:** stays on `v1` features. On 3 real sessions it was 81 and 114 px for Hayden and
  355 px for a second person, whose head turned toward the dots.
- **"Ally, check my eye tracker"** (about 12 s):
  - A 5-dot re-tune.
  - It fixes drift after the user moves: 776 px down to 127 px, and 1184 px down to 125 px.
  - If gaze stays unsure for about 10 s, Ally offers it ("your position changed"), at most every
    5 minutes.
- **Click correction:** when about 4 of the user's last 6 clicks agree on the same shift, the
  estimate moves by it. Clicks stay on the laptop.
- **`GazeManager.target()`:** `{x, y, radius_px, confidence, region, source: eyes|mouse, why}`.
  It falls back to the mouse pointer, with a reason, whenever the eyes aren't reliable
  (`[gaze] max_radius_px`, 250).
- **Tools:**
  - `where_am_i_looking`: the region, plus the role and name of the thing there on Windows.
  - `check_eye_tracker`.
  - `choose_by_looking(options)`: 2–4 options; a 0.8 s look picks one.
  - `magnify_where_i_look`: now uses `target()`.
- **Brain instructions:** "this", "that", "here" and "what am I looking at" map to
  `where_am_i_looking`.
- **Privacy:** the model, recorder and evaluator are now under the camera privacy test.

**Connected to the bus:**
- **Offers can be answered by looking:** on each `offer`, if the tracker is reliable, Yes and No
  cards appear and a 0.8 s look calls `offer.resolve("yes"|"no", by="eyes")`.
  - **First answer wins:** voice, the API or a click still win if they come first; the cards close
    once it's answered another way.
  - **Off switch:** `[gaze] answer_offers = false`.
- **Gaze point in struggles:** face-signal `Struggle.target.x/y` is the gaze point when the eyes are
  reliable, otherwise the pointer.
- **Bug fixed:** a look in `choose_by_looking` almost never counted with real frame timing. It now
  does, and there's a test with irregular 15–20 fps timestamps.

**For others:**
- **Ben H:** `ally/agent.py` gets one line, `self.gaze.attach_bus(self.bus)`, right after the
  Coordinator. New `[gaze]` keys: `features`, `click_learning`, `max_radius_px`, `answer_offers`.
  `ally/doctor.py` had a conflict on merge, resolved by keeping both the voice checks and the gaze
  calibration lines.
- **Ganesh:**
  - **Two Yes/No displays:** when the overlay lands, its Yes/No bubble and the gaze cards would both
    appear. Either turn off `answer_offers`, or put the overlay's Yes and No in the left and right
    halves of the screen and have the gaze answer them. Your call; tell Hayden.
  - **Naming things in Chrome:** Windows' accessibility lookup only sees "pane" inside Chrome,
    Electron and the Windows 11 taskbar. Saying "the Send button" needs the extension: a content
    script answering `document.elementFromPoint` for a screen point.
- **Ben W:** spoken yes/no and gaze can both answer the same offer; first answer wins.
- **Needs a person and camera:** a new calibration with the second person (head-still prompts),
  real `choose_by_looking` dwell, and `where_am_i_looking` on a native Win32 app.

### Oct 24, 18:35 PT — Hayden (via Claude Code) — branch `hayden/bus-fixes`, commit `58b388b`

**Changed:** `ally/senses/manager.py` `Senses.target()`, plus a test in `tests/test_senses.py`.
- **Problem:** senses sent `None` for an unknown site, app or window, and `contracts.Target.from_dict`
  turns `None` into the text `"None"`.
- **Effect, reproduced on `main` `6966696`:** with VS Code in front, the coordinator saw
  `site="None"`, treated it as a browser and offered "make this page bigger" (`set_page_zoom`).
  Habits would have been stored under the site "None".
- **Fix:** senses now send `""` for unknown values, the program name as `app` and the window title
  as `window` (split like `Agent.proactive_context()`), so habits are per app.
- **Tests:** 359 pass on this branch.

**For Ben H (contracts, proactive, agent wiring; not changed here, your call):**
1. **Guard in `Target.from_dict`:** `str(d.get("app") or "")` (same for site and window), so any
   sender's `None` stays empty.
2. **Two confidence gates:**
   - **Senses:** already require two agreeing signals and send one `fused` struggle when the fused
     confidence is ≥ 0.5 (`[senses] min_confidence`).
   - **Coordinator:** fuses again and needs ≥ 0.6 (`[proactive] min_confidence`).
   - **Result:** the weakest real pairs (about 0.35 + 0.35, fused about 0.58) are dropped without a
     trace.
   - **Suggestion:** pick one gate, e.g. let `fused` struggles from the senses through as they are.
3. **"No" feedback to senses:** the coordinator never calls `agent.senses.answer(struggle, choice)`,
   so the senses' 90 s per-signal pause after a "no" never happens. A few lines in `agent.py` would
   fix it: subscribe to `offer_result` and, when `offer.reason` is a `Struggle`, call
   `self.senses.answer(offer.reason.to_dict(), offer.choice)`.
4. **FYI, offers can't be answered yet:** `main` has no overlay and no dashboard Proactive panel, and
   the coordinator uses `NullVoice` (speaks, can't listen). Every offer times out after 8 s until
   Ben W's `ask_yes_no` or Ganesh's overlay is wired in.

**Coming from Hayden (local, not pushed yet):**
- **Branch `hayden/eye-tracker`:** eye tracker v2 with calibration that's checked on targets it
  wasn't fit on, a 5-dot re-tune, and click correction.
- **New tools:** `where_am_i_looking`, `check_eye_tracker` and `choose_by_looking`.
- **Plan for the bus:**
  - Put the gaze point into `Struggle.target.x/y`.
  - Let `choose_by_looking` answer an `Offer` (`offer.resolve(choice, by="eyes")`).
- **Merge with `main`:** conflicts only in `ally/doctor.py`.
- **Spec:** `docs/eye-tracker-plan.md`.

