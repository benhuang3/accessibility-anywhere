# Validating the eye tracker

These steps are for a real person in front of the camera. On Windows use `python`.

Saying "set up my eyes" or "check my eye tracker" needs Ally running. Stop Ally first when you record,
because that command opens the camera itself:

```
python run.py stop
```

The face model has to be on disk already at `state/models/face_landmarker.task`. Start Ally once so it can download that file, then stop Ally again.

Recordings store numbers only (landmarks, blendshapes, the face matrix, dot times, clicks). They do not store pictures. The files stay in `state/`.

## Set up

With Ally running, say "Ally, set up my eyes" (or "calibrate my eyes"). This takes about 45 seconds. `[gaze] calibration_poses = 1` is the 9 dots only, about 30 seconds.

## What you can say

- "What am I looking at", and "this", "that", or "here" when you mean a spot: the area of the screen.
  On Windows, also the role and name of the control there (a button, a link, some text). That name is
  read only when you ask, and it goes to Claude only as part of your request.
  A Chrome page, an Electron window, or the Windows 11 taskbar often comes back as only a pane or a
  client with no name. Ally then says the region only, and does not call it a control. Naming the
  thing inside Chrome needs the extension: a content script that asks `document.elementFromPoint`.
  That is the extension owner's area.
- "Magnify what I'm looking at", or "magnify here": zoom around that spot. The view then follows your eyes.
  Looking near an edge pans that way. The pan is calm (default speed 0.35, about a third of a view
  width per second at the very edge) and waits about 0.3 seconds so a small wobble does not move the
  page. Say "follow my eyes slower" or "follow my eyes faster". That is saved as `[gaze] pan_speed`.
- A yes or no by looking: Ally shows the choices as large cards. Two choices use the left and right
  halves of the screen. Look at one for about a second. Up to four choices. If you don't settle, Ally says none.
  That look also teaches the tracker: the features from the dwell, and the centre of the chosen card.
- "Check my eye tracker": only the 5 check dots, about 10 seconds. Ally re-tunes the saved model
  from those dots (a shift, not a new fit) and says how far off it was before and after.
- "Set up my eyes" runs the full setup again.
- "Show where I am looking" draws a soft circle at the gaze point. "Hide the eye dot" removes it.
  The circle is click-through and does not take focus. `[gaze] show_dot` (default false) remembers it.
  While zoomed in, the circle is placed in the magnified view so it stays on the glass where you look.

If the tracker isn't set up, Ally can't see your face, your eyes don't stay on one spot, or the
uncertainty is above `max_radius_px` (default 250), Ally uses the mouse pointer and says why.
If that uncertainty stays high for about 10 seconds while your face is still visible, Ally says,
once: "Your position changed. Say check my eye tracker to re-tune it." It waits 5 minutes before
saying that again.

Clicks can improve the estimate after setup (`click_learning`, on by default). The click is two
numbers used on this computer. It is not saved as a picture and it is not sent anywhere. What the
tracker has learned from those clicks is written next to the calibration (feature numbers, screen
points, and times only), at most once a minute and when the camera helper exits, and read back
when Ally starts. A new setup throws that away and starts fresh. Set
`click_learning = false` to turn that off.

`features` defaults to `v5`. v5 is the v4 head model (where the head is, plus an eye angle learned at
setup) plus MobileGaze yaw and pitch in degrees from `mobileone_s0`. Download that model once. A file
already in `state/models` is skipped:

```
python scripts/get_gaze_models.py
```

v5 also needs the ONNX runner: `pip install onnxruntime`. If `state/models/mobileone_s0_gaze.onnx` or
onnxruntime is missing, calibration and tracking use v4, and the calibration result says so. A new
setup uses the feature set in config (`v1`, `v2`, `v3`, `v4`, or `v5`). An older calibration keeps
the set it was fit with.

`python run.py doctor` prints how old the calibration is, the check (pixels and areas), the feature
set, whether `gaze.camera` still matches, and whether `mobileone_s0_gaze.onnx` is present and
onnxruntime imports. The fix it names is `python scripts/get_gaze_models.py` and `pip install onnxruntime`.

## Calibrate

With Ally running, say "Ally, set up my eyes".

The screen waits until the setup looks right, then holds for about 3 seconds. Your face width and head pose also have to stay steady for about 1 second, so the first dots are not taken while you are still sitting down. The screen says: move only your eyes, keep your head still. If something is off, it says what to fix and waits:

- "I can't see your face. Look at the screen."
- "I see two faces. Only one person, please."
- "move a little closer" or "move a little farther back" (from how wide the face is)
- "turn on a light in front of you"
- "Center your head and look at the screen."

Then 9 dots: the 3x3 grid, center first. These are pose `normal`. A ring shrinks onto each dot for 0.8 seconds. The next 1.5 seconds are kept, skipping blinks. A dot with fewer than 10 good frames is shown once more at the end. The same happens, once, when the head turns more than about 3 degrees during a dot, or sits that far from the pose at the first dot of that round. That repeat says: keep your head still, look with your eyes only.

With `calibration_poses = 3` (the default), two short rounds follow. Ally says "Sit back a little", waits until that position holds still, and collects 5 dots tagged pose `back`. Then "Lean in a little", and 5 dots tagged pose `close`. Each round judges a turned head against its own first dot. `calibration_poses = 1` skips those rounds.

Then 5 new dots, each in a different area of the screen. Each one settles, then 1.5 seconds are kept. Those dots are not used to fit the model. They are the check.

Esc cancels. A saved calibration is ignored if the screen size or the camera index changes.

## What the result words mean

The words use the 5 check dots, not the dots the model was fit on. "Areas" means the 3x3 grid (top left, top, top right, left, middle, right, bottom left, bottom, bottom right). 150 pixels is the bar on a 1920x1080 screen at a normal sitting distance.

Good: about 150 pixels or less, and the check dots landed in the right areas. With 5 dots, that means all 5. The line looks like: "Good. About 140 pixels off, and 5 of 5 areas right."

Okay: the screen says "Okay for areas of the screen, not exact spots." The area is right often enough to trust left, right, or the 3x3. With 5 dots, 4 right is enough for this word. The error can be worse than 150 pixels.

Poor: too many areas were wrong. The second line is the likely cause.

- The light on your face was too dim. Turn on a light in front of you.
- You were a bit far away, or very close.
- Glasses glare may be covering your eyes. Move the light off to the side.
- Otherwise: sit still, with light on your face, and try again.

Ally still saves the fit when the dots could be fit. The saved file includes `validation` (`error_px`, `region_hits`, `region_total`), the camera index, and `screen_mm` (the screen's width and height in millimetres). The on-screen words are the check. The `error_px` next to `dots` is the fit's own leave-one-out error.

## Record

Record at least two people. Each person does four sessions: glasses on and off, in bright light and in dim light. An alias is letters, digits, `_` or `-`.

```
python -m ally.gaze_record --person ada --glasses no --light bright
python -m ally.gaze_record --person ada --glasses no --light dim
python -m ally.gaze_record --person ada --glasses yes --light bright
python -m ally.gaze_record --person ada --glasses yes --light dim
```

`--camera N` picks the camera. `--run NAME` groups files under one folder. The default folder name is the time in UTC.

`--cnn mobileone_s0,resnet18` also runs those gaze models on every frame, in memory. Each frame line then has `cnn` (the model name, then yaw and pitch in degrees) and `cnn_ms` (how many milliseconds that model took). Without `--cnn` the file is unchanged. The picture is still not saved. If a model file is missing, the command says to run `python scripts/get_gaze_models.py` and does not open the camera.

Each session writes `state/gaze_runs/<run>/<alias>-<glasses>-<light>.jsonl`. One JSON object per line:

- `meta`: alias, glasses, light, screen size, `screen_mm` (the screen's width and height in millimetres), camera, git commit, created time
- `frame`: time, image width and height, 478 landmarks, all 52 blendshapes, and the 16-number face matrix or null. With `--cnn`, also `cnn` (model name to yaw and pitch in degrees) and `cnn_ms` (milliseconds for that model)
- `target`: phase `calibration`, `validation`, or `drift`, plus `shown`, `start`, and `end`. Calibration targets also have `pose`: `normal`, `back`, or `close`
- `click`: time and screen point, only if you click during the reading

`shown` is when the dot appeared. `start` to `end` is the collection window after it settled. Ctrl+C keeps what was already written.

The session on screen:

1. The same readiness, dots, and 5 check dots as calibration, including the sit-back and lean-in rounds when `calibration_poses` is 3. The result words are shown. This command does not replace `state/gaze_calibration.json`.
2. About 2 minutes of reading a page of text. Click if you want; the click is two numbers.
3. Five check dots again, phase `drift`, in different areas from the calibration dots.

Ally has to be stopped. If it is still running, the command refuses and tells you to run `python run.py stop`.

## Compare with MobileGaze

MobileGaze is an open-source gaze model (MIT license). It can be run on the same session, in memory, and scored against our tracker. The picture is still not saved.

Download the model files once. They go in `state/models`, and a file that is already there is skipped:

```
python scripts/get_gaze_models.py
```

That fetches `mobileone_s0_gaze.onnx` and `resnet18_gaze.onnx`.

Record with Ally stopped. `--cnn` names the models. Without it, nothing about the file changes.

```
python -m ally.gaze_record --person ada --glasses no --light bright --cnn mobileone_s0,resnet18
```

Score the recordings. Folders are searched recursively. A recording with no `cnn` numbers is skipped. The fit uses the calibration dots. The check dots (`validation`) and the later dots (`drift`) are scored, not used to fit.

```
python -m ally.gaze_cnn evaluate state/gaze_runs
```

Each model gets three rows:

- `cnn`: the model's yaw and pitch, plus where the head is (from the face matrix), mapped onto the screen the way v4 maps an eye angle. A gain and an offset per axis are fit on the calibration dots.
- `hybrid`: v4's eye terms and the model's two angles, in one fit of the eye angle the head would need. The live tracker is this fit, as feature set `v5`, using `mobileone_s0` only.
- `v4`: our tracker, for reference.

`python -m ally.gaze_eval report` adds a `v5` row, with the same columns as the other feature sets (including the per-frame and smoother numbers), when frames stored `mobileone_s0` angles. Older recordings skip `v5`, and the report says so.

For each of those, on validation and on drift: how far the median of a dot is from that dot, and whether it fell in the right 3x3 area; the median and 90th percentile of the error on single frames; and the median and 90th percentile of the jump from one frame to the next. `ms` is the median time that model took on a frame. A CSV is written in the current folder as `gaze_cnn.csv`, or where `--out` says (a `.csv` path, or a folder).
