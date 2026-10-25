# Hayden: senses, eye tracker, hands-free voice — integration notes

Owner: Hayden (`ally/senses/`, the eye tracker, face and input signals). Written Oct 24, 2026, about
16:00 PT, for teammates and their coding agents. Read with the other `docs/integration/*.md` files.

Everything here was built on Hayden's Windows 11 laptop (Python 3.12, 64-bit). Labels follow the
team's convention: **VERIFIED** (tests or live evidence on this laptop), **REPORTED** (built and unit
tested, not checked live), **OPEN** (not done or not decided).

---

## 1. Where the code is

| Branch | State | What's on it |
| --- | --- | --- |
| `main` (GitHub) | `4702a36` | Windows fixes, hands-free voice, eye tracker v1 (via `fef8cf4`), `AGENTS.md`, `CLAUDE.md`, `scripts/dispatch.ps1`, a test fix so unit tests never zoom the real screen (`7974a9e`) |
| `hayden/senses` (GitHub, PR #1 into `main`) | pushed, including `b1d4759` and this file | Everything below in sections 3–5. **Not merged.** |
| `hayden/eye-tracker` (local) | `6d19471` | `hayden/senses` + the eye tracker plan (`docs/eye-tracker-plan.md`). Phase 1 work is on `agent/codex` and `agent/cursor`, being reviewed. |

Tests: `cd tests` then `python -m unittest` (it must be run from `tests/`). 200 tests pass on
`hayden/senses` at `b1d4759`. **VERIFIED**

The **foundation patch** (`0001-proactive-foundation.patch`: `contracts.py`, event bus, brain, habits,
its own `ally/senses/`) was never in this checkout. Everything here is built against the contract
**as documented** on the Code Foundation page. See section 6 for what has to be reconciled.

---

## 2. Earlier work already on `main`

- **Windows platform** (`ally/platforms/windows.py`, also `speech.py`, `supervisor.py`, `cli.py`):
  - Speech uses one long-lived PowerShell voice process: about 0.2 s to start speaking instead of
    about 3 s.
  - Read-selection (Ctrl+Alt+R) waits for the hotkey's keys to be released and talks to the clipboard
    directly, then restores your clipboard.
  - The magnifier takes over from the Windows Magnifier app, so zoom levels are exact.
  - Win-key shortcuts wait until the hotkey's modifiers are released.
  - **VERIFIED** live.
- **Hands-free voice** (`ally/listener.py`):
  - Always listening for "Ally, …", transcribed locally with faster-whisper.
  - A 6 s follow-up window after a reply; only "Ally, stop" is accepted while Ally is talking.
  - **VERIFIED** with a real voice ("Ally, what time is it?", "Ally, zoom in").
- **Eye tracker v1** (`ally/gaze.py`, `gaze_helper.py`, `gaze_model.py`, `tools/gaze_tools.py`):
  - 9-dot calibration, "magnify what I'm looking at" (Ctrl+Alt+G), and a magnifier that follows
    the eyes.
  - Accuracy was poor live: about 200 px held-out error, and the vertical estimate was bad.
    Being rebuilt (section 7).

---

## 3. Struggle sensing (`ally/senses/`, branch `hayden/senses`)

It notices when the user is struggling and emits one **Struggle** event.

### Signals

- **Face (webcam):**
  - **Squint and lean**, each measured as a change from the person's own resting face. Calibration
    is "Ally, set up face sensing" (15 s), or an automatic baseline from the first 20 s.
  - **Gates** (frames that don't count): blinks shorter than 0.4 s, eyes shut, smiling or laughing,
    head turned more than 20°, too dark, and winks. A wink is judged against the person's own
    usual left/right eye difference, with hysteresis.
  - **While leaning in, only a strong squint counts.** Leaning alone raised one person's squint
    score by +0.16.
- **Mouse and keyboard** (separate process, pynput; it never records which key was pressed):
  - **Rage click:** 4 clicks in 2 s within 30 px; double- and triple-clicks don't count.
  - **Retype:** a burst of backspaces, or a backspace rate above 2× the person's own rate.
  - **Hesitation:** the pointer rests on something **clickable**, checked with the Windows hand
    cursor or the accessibility role. Resting while reading doesn't count.
- **Fusion:**
  - An offer needs **two different signals that agree within 4 s**. A signal that is still going
    on, like a held lean, stays eligible to pair.
  - `[senses] solo` can let one kind fire alone; it's empty by default.
  - After a struggle there's a 30 s cooldown. After a "no", that signal kind is snoozed for 90 s.

### The Struggle event (matches the Code Foundation contract)

```python
{"kind": "squint" | "lean" | "rage_click" | "hesitation" | "retype" | "fused",
 "confidence": 0.0-1.0,
 "target": {"app", "site", "window", "x", "y", "box"},
 "source": "senses" | "senses.vision" | "senses.input" | "simulated",
 "detail": {"signals": [...], "simulated": true if simulated},
 "ts": epoch_seconds}
```

`ally/senses/contract.py` provides `KINDS`, `validate_struggle(d) -> list[str]` and
`to_event(d)`, which adds `type: "struggle"` for `POST /api/event`. Every struggle is validated
before delivery; an invalid one is logged and dropped.

### How a struggle reaches the brain (pick one; all exist)

1. **`agent.on_struggle(struggle_dict)`**: if the agent object defines it, it's called on a separate
   thread for every struggle, so a slow or failing handler never blocks sensing.
2. **`agent.senses.subscribe(callback)`**: any number of listeners.
3. **`POST /api/event`**: set `[senses] post_url` (empty by default, meaning off). Sends
   `to_event(struggle)` with `X-Ally-Token`, never blocks, 2 s timeout (`ally/struggle_sink.py`).
4. It is also written to the agent's event log as kind `struggle` (the dashboard can show it).

### What the brain should call back

- **`agent.senses.answer(struggle, "yes" | "no")`**: after "no", every signal kind in that struggle
  is snoozed for `decline_seconds` (default 90). Call it when an offer is answered.
- **`agent.senses.simulate(kind)`**: `kind` is `demo` (a timed weak squint, then a weak lean, then
  fused) or a single signal kind. Tagged `simulated`. Also available as the tool `simulate_struggle`.
- **`agent.senses.info()`**: status, counts, the last signal and struggle, and snoozes (for a
  dashboard panel).
- **`agent.senses.set_vision(on)`** and **`set_input(on)`**: the off switches.

### Tools (registered in `ally/tools/senses_tools.py`)

`calibrate_face_sensing`, `struggle_sensing(on, what)`, `senses_hud(on)` (live debug window),
`simulate_struggle(kind)`. Hotkey **Ctrl+Alt+C** recalibrates face sensing.

### Settings (`[senses]` in `config.toml`)

- **On/off and timing:** `vision`, `input`, `fps`, `calibrate_seconds`.
- **Fusion:** `solo`, `window_seconds`, `cooldown_seconds`, `decline_seconds`, `min_confidence`,
  `post_url`.
- **Thresholds:** `squint_delta 0.20`, `squint_weak 0.12`, `squint_hold 1.5`, `wink_max 0.12`,
  `smile_gate 0.35`, `blink_gate 0.5`, `blink_max 0.4`, `blink_closed 0.8`, `pose_gate 20`,
  `light_floor 40`, `lean_ratio 1.15`, `lean_weak 1.10`, `lean_release 1.08`, `lean_hold 2.0`,
  `rage_*`, `retype_*`, `hesitation_seconds`, `rearm_seconds`.
- **Defaults:** face and input sensing are **on**. That's a demo choice; the privacy research
  suggests opt-in.

---

## 4. Measurement and privacy tools

- **Self-check:** `python -m ally.senses.matrix selfcheck` (Ally stopped). A guided 60 s check of
  the live numbers, including left/right mirroring.
- **Recorder:** `python -m ally.senses.matrix record --person ALIAS --glasses yes|no --light bright|dim [--free MIN]`.
  - A scripted session: read normally, read tiny text, act a squint, smile, laugh, look down at a
    phone, wink each eye, turn the head, lean in, lean in and squint.
  - **Numbers only**, saved to `state/senses_runs/`.
- **Report and tuner:**
  - `python -m ally.senses.evaluate report state/senses_runs` replays recordings through the real
    detection code: false offers per minute, misses, stray signals.
  - `… tune` searches thresholds, tested leave-one-person-out.
- **Privacy check:** `python scripts/privacy_check.py` watches the camera and input helpers'
  network connections and fails if any appear.
- **Camera indicator:** a camera-on window, top-most, that turns the camera off when clicked.
- **Privacy guarantee:** a test fails the build if any file in `ally/senses/` or the camera helper
  imports networking or writes images or video.
- Guide: `docs/senses-validation.md`.

### Evidence

**VERIFIED** on this laptop, at `8988c33`:
- **Self-check** passed on Hayden.
- **Recorded sessions:** 3 sessions with 2 people (glasses off, bright light), 2.8 minutes scored:
  - **0 false offers** and 0 stray signals.
  - "Lean and squint" was caught in both sessions recorded with the current script (3.5 s and
    4.0 s).
- **The tuner** did worse on the person it hadn't seen (1 false offer, 2 misses) than the current
  defaults, so **no thresholds were changed from the research values.**

**OPEN:**
- Glasses, dim light, the 5-person matrix and free-use false offers per minute.
- A live `privacy_check`, the Wi-Fi-off run, and the Resource Monitor screenshot.
- Live behaviour of the camera-on window and the two-face guard.
- Clickable detection in real apps.
- `/api/event` against the real endpoint.

---

## 5. Files outside `ally/senses/` that `hayden/senses` changes (merge hotspots)

| File | Change |
| --- | --- |
| `ally/agent.py` | Creates `Senses`, starts and stops it, adds it to `status()`, ticks it from housekeeping |
| `ally/gaze.py`, `ally/gaze_helper.py` | **One process owns the webcam**: it serves the eye tracker and the face numbers; adds cards, the camera-on window, the two-face guard and the HUD |
| `ally/config.py`, `ally/default_config.toml` | The `[senses]` section; hotkey Ctrl+Alt+C |
| `ally/doctor.py` | Face and input sensing checks |
| `ally/tools/__init__.py` | Registers `senses_tools` |
| `ally/tools/gaze_tools.py` | One reply text (the camera may still be in use for senses) |
| `ally/struggle_sink.py` (new) | Optional `POST /api/event` |
| `scripts/privacy_check.py`, `docs/` | Tools and guides |
| `tests/helpers.py` | Test homes turn senses off; `fake_screen()` so tests never zoom the real screen |
| README | The senses section |

---

## 6. How to integrate with each part

**Ben H (brain, contracts, merges):**
- **Duplicate code from the foundation patch:**
  - The patch also has `ally/senses/` and `contracts.py`, with the same idea and names
    (`SquintDetector`, `LeanDetector`, `senses_vision`).
  - Keep **one** `ally/senses/`. This one has live fixes from real recordings, so prefer it and
    point `contracts.Struggle` at the same field names (they match the documented contract).
  - The config switch here is `[senses] vision`, not `senses_vision`.
- **Wiring:** the brain gets struggles through `agent.on_struggle` (simplest) or `subscribe`. When
  an offer is answered, call `agent.senses.answer(struggle, "yes"|"no")` so declined signals go
  quiet for 90 s.
- **If the brain consumes `/api/event`** instead: set `post_url` to the agent's own URL, or call
  `to_event()` in-process.
- **Policy is still Ben H's decision:**
  - The two-signal rule is the default.
  - Rage-click-alone is possible with `solo = ["rage_click"]`.
  - The offer cooldown is 30 s; it may be better aligned with the offer UI's timer.
- **`/api/event`:** this branch doesn't add a server handler for it. That's in the foundation
  patch; add it there, not twice.

**Ganesh (overlay, dashboard, extension, risk, consent screen):**
- **Proactive panel:** read `agent.status()["senses"]` (`senses.info()`) and the event log entries
  of kind `sense` (signals) and `struggle`.
- **Camera indicator:** the camera helper already shows a camera-on window that turns the camera
  off when clicked. The consent screen can reuse that, or replace it, but keep exactly one.
- **Cards:** the camera helper draws simple cards (`card {json}` on its stdin). The overlay can
  replace them later.
- **Answering offers by looking:** the eye tracker's planned `choose_by_looking(options)` (section 7)
  needs on-screen option areas. The overlay's offer bubble should put Yes and No in clearly separate
  halves, or expose their screen boxes.
- **Risk code:** no overlap. The extension or risk code can send its own events to `/api/event`.

**Ben W (voice):**
- **Microphone:** the hands-free listener (`ally/listener.py`) already keeps the mic open.
  `ask_yes_no()` should go through the listener, for example its follow-up window. Opening a second
  mic stream from voice-confirm risks the mic contention the foundation notes warn about.
- **After an answer:** call `agent.senses.answer(struggle, "yes"|"no")`.

**Everyone:**
- **Simulate a struggle:**
  - `python run.py tool simulate_struggle '{"kind": "demo"}'` (the tool route).
  - `agent.senses.simulate("demo")` (in-process).
  - No webcam needed either way.
- **Tests:** run from `tests/` (`python -m unittest`). Use `helpers.fake_screen(agent.platform)` for
  any test that builds a real `Agent`.

---

## 7. Eye tracker v2 (in progress, branch `hayden/eye-tracker`)

The full spec is `docs/eye-tracker-plan.md`. The eye tracker becomes another way to talk to Ally:
1. **"This" and "here":** it resolves them to a screen place.
2. **Answering offers by looking:** at Yes or No.
3. **Steering the magnifier.**

It never clicks or acts on its own.

- **Accuracy bar,** measured on targets the model wasn't fit on: median error ≤ 150 px, and 8 of 9
  screen regions correct, still holding after 2 minutes. Below the bar, it uses regions only, or
  falls back to the mouse pointer and says so.
- **Phase 1** (`agent/codex` and `agent/cursor`, under review):
  - **Model and evaluator** (Codex; reviewed, 209 tests pass):
    - **v2 features:** per-eye measurements, eyelids and head pose.
    - **New functions:** `validate`, `region`, a linear-time `fixation`, `Corrector` (clicks act as
      free calibration samples) and `radius_px` (uncertainty).
    - **Evaluator:** `python -m ally.gaze_eval report`.
    - **Default:** stays on v1 features until recordings show v2 is better.
  - **Calibration v2 and recording mode** (Cursor; 2 of 7 items committed, resumed after an
    interruption):
    - **Calibration:** a readiness check, 13 targets, 5 validation targets and a plain-words result.
    - **Recording mode:** `python -m ally.gaze_record`.
- **Phase 2** (after merge): connecting it to the agent.
  - **`GazeManager.target()`** returns `x`, `y`, `radius_px`, `confidence`, `region` and
    `source: eyes|mouse`.
  - **Clicks** are forwarded to the camera helper for correction.
  - **New tools:**
    - `where_am_i_looking()`: the region, plus the role and name of the thing there.
    - `check_eye_tracker()`.
    - `choose_by_looking(options, timeout)`.
    - `magnify_where_i_look` switched to `target()`.
  - **Also:** offline phrases, doctor checks and settings.
- **For the brain:** "read this", "what is this?" and "click this" style requests should call
  `where_am_i_looking()`. It returns the mouse pointer, with `source = "mouse"`, when the eyes aren't
  reliable.

---

## 8. To-do list (Hayden)

Highest value first:
1. **Finish eye tracker phase 1:** review Cursor's branch, merge both into `hayden/eye-tracker`, and
   run the full suite plus the privacy test.
2. **Record gaze sessions** (2 or more people, with and without glasses), then run `gaze_eval` to
   choose v1 or v2 on the data.
3. **Eye tracker phase 2:** `target()`, click correction, the new tools, and the offer-by-looking
   hook. Coordinate with the overlay for the Yes/No areas.
4. **Senses matrix:** a glasses session, a dim-light session, 3 or more people, and a `--free` run
   for false offers per minute.
5. **Privacy proof:** run `scripts/privacy_check.py` live, then a Wi-Fi-off run and a Resource
   Monitor screenshot.
6. **Merge:** merge `hayden/senses` after Ben H reconciles it with the foundation patch.
7. **Known gap:** strong squints are rarely reported, because the weak level fires first. Offers are
   unaffected; revisit with more data.
8. **Gated by the research page:**
   - **Presage spike:** needs an API key, 90-minute cap.
   - **Gaze accuracy spike:** the bar is 7/9 regions for 2 people; phase 2 covers it.
9. **Housekeeping:**
   - Remove the uncommitted copies of `scripts/dispatch.ps1` and `AGENTS.md` from the
     `hayden/eye-tracker` checkout once agent work ends.
   - Update Notion's Current State with the eye tracker status.

---

## 9. How work is done here (for agents)

- **Agents:** Claude Code leads. Codex and Cursor run headless in worktrees `../ally-codex` and
  `../ally-cursor` (branches `agent/codex` and `agent/cursor`) through `scripts/dispatch.ps1`.
  - Codex needs `-NoSandbox` on this laptop: its sandbox accounts can't read the per-user Python
    install.
  - Resume a stopped run instead of restarting: `codex exec resume --last`,
    `cursor-agent --continue`.
- **Rules:** in `AGENTS.md`. Edit only the files you were given, commit on your own branch, never
  push or merge, don't open the webcam or start Ally in tests.
- **Time stamps:** all evidence above has a commit and a test count. Re-run before relying on it.
