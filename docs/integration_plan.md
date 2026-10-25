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

## Change log (everyone: add an entry here with every push)

Newest first. Each entry: date and time, who, branch and commit, what changed, and what others need
to know or do. Agents: read the newest entries before starting work.

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
