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
