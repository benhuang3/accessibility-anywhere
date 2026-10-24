# Eye tracker plan

Branch `hayden/eye-tracker` (from `hayden/senses`). This is the spec the agents build to; the
research numbers come from the Research: Vision & Sensing page in Notion.

## What it is for

The eye tracker is one more way to talk to Ally. It answers "where is the user looking?" so
Ally can:

1. **Resolve "this", "here" and "that"** in requests: "magnify this", "read this", "what is
   this?". The agent calls a tool that returns where the user is looking and what is there.
2. **Take a yes or no without speaking or clicking**: when Ally offers help, the user can answer
   by looking at Yes or No for a moment.
3. **Steer the magnifier** (already built): while zoomed in, the view follows the eyes.

The eye tracker never clicks, types or acts on its own. It only tells the agent where the user
is looking; the agent decides what to do.

## How accurate it has to be

Webcam gaze is about 2 to 4 degrees at best (100 to 200 px on a laptop), worse in dim light and
with glasses. So the design works with **regions and on-screen things, not exact pixels**, and
always states its uncertainty.

The bar, measured on targets the model was not fit on:

| Measure | Bar |
| --- | --- |
| Median error | 150 px or less on a 1920x1080 screen at normal sitting distance |
| 3x3 regions | 8 of 9 correct (the research bar is 7 of 9 for two people without glasses) |
| Drift | still meets both 2 minutes after calibration |

When the bar isn't met, Ally says so in plain words, uses gaze only for coarse regions (left or
right half, 3x3), and otherwise falls back to the mouse pointer.

Today's tracker (first session) measured about 200 px held-out error, and one live request landed
at the top edge, far from where the user looked. Known weaknesses: the vertical estimate (iris
offset alone barely moves up and down), only 9 dots of about 1.2 s each, head movement after
calibration, and nothing that corrects drift.

## Design

### 1. Model (`ally/gaze_model.py`, pure numpy)

Keep these working with the same signatures, because the camera helper calls them:
`features(landmarks, blendshapes, matrix, width, height) -> (vector, openness) | None`,
`fit(samples, screen) -> model`, `predict(model, f) -> (x, y)`, `save`, `load`, `OneEuro`.
The feature vector's length may change; bump `MODEL_VERSION` when it does (old calibrations are
then ignored and the user recalibrates).

Improvements, each kept only if it helps on recordings (section 5):

- **Per-eye features** instead of averaging both eyes: iris offset across and up/down for each eye,
  normalized by that eye's width.
- **Vertical cues**: the eyelids follow vertical gaze, so add each eye's lid opening, plus the
  `eyeLookUp`/`eyeLookDown` blendshapes per eye.
- **Head pose**: yaw, pitch, roll and position from the facial transformation matrix, plus
  iris-by-head interaction terms, so small head moves after calibration don't shift the estimate.
- **Cleaner calibration frames**: drop blinks and frames during eye jumps (fast feature change),
  and use each target's settled frames only.
- Ridge regression with the penalty chosen by leaving one target out (keep this).

New functions (exact names; the camera helper and agent will call them):

- `validate(model, samples) -> dict`: samples are `(features, (x, y))` from targets not used in
  `fit`. Returns `error_px` (median over targets of the distance from the target to the median
  prediction for that target), `region_hits` and `region_total` (3x3), and `per_target` details.
- `region(x, y, screen) -> str`: "top left", "top", "top right", "left", "middle", "right",
  "bottom left", "bottom", "bottom right".
- `fixation(points, t0, t1, radius_px) -> dict | None`: `points` are `(t, x, y)`. Finds the longest
  fixation (dispersion-threshold method: points staying within `radius_px` for at least 0.2 s)
  between `t0` and `t1`. Returns `{"x", "y", "start", "end", "samples", "spread_px"}`.
- `class Corrector`: online correction from mouse clicks. People look at what they click, so each
  click is a free calibration sample. `Corrector(model)`; `add(features, (x, y), t)` stores the
  features from about 0.1 s before the click with the click position, but only when the current
  prediction is within 300 px of the click (outlier rejection: muscle-memory clicks); keeps the last
  50; after at least 8, refits with calibration plus click samples, or fits a small affine
  correction if that validates better. `predict(features) -> (x, y)`; `info() -> dict` (samples
  used, current estimated error).
- Each prediction comes with an uncertainty: `radius_px`, from the validation error, made larger
  when the head has moved away from its calibration pose.

### 2. Calibration and setup (`ally/gaze_helper.py`)

Said as "Ally, set up my eyes". About 35 seconds, voice and full-screen:

1. **Readiness check, about 3 s**: face found, distance (face width), light on the face, head
   roughly centered. If something is off, say exactly what to fix ("move a little closer", "turn
   on a light in front of you") and wait for it.
2. **Calibration, 13 targets**: the 3x3 grid plus 4 between them. A ring shrinks onto each target
   (0.8 s settle), then 1.0 s of frames are collected, skipping blinks and eye jumps. A target with
   fewer than 10 good frames is shown once more at the end.
3. **Validation, 5 new targets** at random places inside different grid cells, 1.5 s each. They
   are not used for fitting. `validate()` gives the honest error and region score.
4. **Result**, shown and spoken in plain words: good (meets the bar), okay (regions only), or poor
   (with the likely cause: light, distance, glasses glare).

Esc or "stop" cancels. The model is saved with its validation numbers, the screen size and the
camera; it is ignored if the screen size or camera changes.

**Recording mode** (`python -m ally.gaze_record --person ALIAS --glasses yes|no --light bright|dim`,
Ally stopped): runs the same readiness, calibration and validation, then 2 minutes of normal reading,
then validation again (drift). It writes raw numbers to
`state/gaze_runs/<run>/<alias>-<glasses>-<light>.jsonl` so models can be compared offline.

### 3. Recording format (the contract between the recorder and the evaluator)

One JSON object per line. Numbers only; frames never leave the camera process and are never saved.

```
{"rec": "meta", "alias": "...", "glasses": "no", "light": "bright", "screen": [1920, 1080],
 "camera": 0, "commit": "...", "created": 1730000000.0}
{"rec": "frame", "t": 1730000000.12, "w": 640, "h": 480,
 "lm": [[x, y, z], ... 478 points, 5 decimals],
 "bs": {"eyeLookUpLeft": 0.12, ... all 52 blendshapes, 4 decimals},
 "mat": [16 numbers] or null}
{"rec": "target", "phase": "calibration" | "validation" | "drift", "id": 3, "x": 960, "y": 540,
 "shown": 1730000001.0, "start": 1730000001.8, "end": 1730000002.8}
{"rec": "click", "t": 1730000100.5, "x": 812, "y": 403}
```

`shown` is when the target appeared; `start`..`end` is the collection window after it settled.
Clicks are optional (the reading part).

### 4. Agent side (`ally/gaze.py`) and tools (`ally/tools/gaze_tools.py`)

- `GazeManager.target(t0=None, t1=None) -> dict`: where the user is looking, from the fixation in the
  request window (the spoken request's start and end, as today) or the last 1.5 s.
  `{"x", "y", "radius_px", "confidence", "region", "source": "eyes" | "mouse", "why"}`. When the
  tracker isn't calibrated, isn't seeing the face, or the uncertainty is too large, it returns the
  mouse pointer with `source = "mouse"` and a plain-words `why`.
- Clicks: the senses input helper already reports clicks to the agent; the agent forwards each to
  the camera helper (`click <t> <x> <y>` on its stdin), which feeds `Corrector`.
- Tools (the agent's way to "hear" the eyes):
  - `where_am_i_looking()`: region plus, on Windows, the role and name of the thing there (button,
    link, text) from the accessibility API. This is what the brain uses for "read this" or "what is
    this?". The name goes to Claude only as part of the user's own request.
  - `magnify_where_i_look` (exists): switch to `target()`.
  - `calibrate_eye_tracker` (exists): the new flow.
  - `check_eye_tracker()`: the 5-target validation only (about 8 s), to check accuracy without a full
    setup.
  - `choose_by_looking(options, timeout)`: shows each option as a large card in its own area of the
    screen (2 options: left and right halves), returns the one the user looks at for 0.8 s, or none.
    Region-level, so it works even when accuracy is only okay. This is how an offer gets answered by
    eye; the overlay owner can later draw the cards instead.
- Offline phrases in `ally/rules.py` for "what am I looking at", "read this" (with gaze), "check my
  eye tracker".
- `doctor` reports calibration age, validated error and region score.

### 5. How accuracy gets decided

1. Record people with the recording mode (with and without glasses, bright and dim).
2. `python -m ally.gaze_eval report PATHS` fits each model variant on the calibration targets and
   scores it on the validation and drift targets: median error, 3x3 region score, and the same after
   simulated click correction. Leave-one-session-out, so nothing is scored on the data it was fit on.
3. Keep the variant that meets the bar on the most sessions; the default only changes with that
   evidence.

### Privacy

Same rules as the senses: frames stay in the camera process and are never written; recordings hold
numbers only and stay in `state/`; nothing under `ally/senses/` or the camera helper imports
networking (the existing privacy test). Face landmarks are biometric-like numbers, so recordings
stay local and are never sent.

## Who builds what

Agents work in parallel only on separate files; the integration step comes after the first two merge.

| Phase | Agent | Builds | Files |
| --- | --- | --- | --- |
| 1 | Codex | Model v2, `validate`, `region`, `fixation`, `Corrector`, uncertainty; the offline evaluator | `ally/gaze_model.py`, new `ally/gaze_eval.py`, new `tests/test_gaze_model.py` |
| 1 | Cursor | Calibration v2 (readiness, 13 + 5 targets, result), recording mode, recording format | `ally/gaze_helper.py`, new `ally/gaze_record.py`, new `tests/test_gaze_record.py`, `docs/` |
| 2 | Codex | Agent side and tools: `target()`, click forwarding, `Corrector` in the helper, the new tools, rules, doctor, config | `ally/gaze.py`, `ally/gaze_helper.py`, `ally/tools/gaze_tools.py`, `ally/rules.py`, `ally/doctor.py`, `ally/config.py`, `ally/default_config.toml`, `tests/test_handsfree.py` |

Between phases: record at least 2 people, run the evaluator, pick the model. Claude reviews every
branch, runs the tests and the evaluator, and reports before anything merges.
