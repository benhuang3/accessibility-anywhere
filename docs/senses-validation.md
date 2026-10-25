# Validating face and struggle sensing

These commands measure the real detectors. They do not invent a second set of thresholds. On Windows use `python`.

Stop Ally first, every time a command below opens the camera:

```
python run.py stop
```

The face model has to be on disk already at `state/models/face_landmarker.task`. Start Ally once so it can download that file, then stop Ally again. That download is the only network use this feature should need.

Recordings store numbers only: face scores, landmarks, blendshapes, the face matrix, mouse and keyboard events, and labels. They do not store pictures.

## Self-check (about 60 seconds)

One person, in front of the camera:

```
python -m ally.senses.matrix selfcheck
```

A card walks through the steps: sit still (this also reads the light), close only the left eye, close only the right eye, smile, blink, turn your head, and lean in. Each line prints PASS or FAIL with the measured number and, on a FAIL, what to do next.

The left-eye and right-eye lines are the mirroring check. Closing the left eye should make `squint_l` higher than `squint_r`. Closing the right eye should do the opposite. If those two are swapped, left and right are reversed in the camera image.

The other lines use the `[senses]` thresholds that are in effect: smile against `smile_gate`, blink against `blink_gate`, head turn against `pose_gate` (the turn has to be above that angle), lean against `lean_ratio` (face width compared with the resting face), and light against `light_floor`.

## Record a session

Record five people if you are filling the matrix. Each person does four sessions: glasses on and off, in bright light and in dim light. That is 20 files. An alias is letters, digits, `_` or `-`.

One session, full script, once:

```
python -m ally.senses.matrix record --person ada --glasses no --light bright
```

The same person in the other three conditions:

```
python -m ally.senses.matrix record --person ada --glasses no --light dim
python -m ally.senses.matrix record --person ada --glasses yes --light bright
python -m ally.senses.matrix record --person ada --glasses yes --light dim
```

A full session is about 5 minutes (4 minutes 38 seconds of script, plus a moment while the camera starts). The card on screen and the terminal walk through the same parts.

`--camera N` picks the camera. `--run NAME` groups files under one folder. The default folder name is the time in UTC.

`--repeat N` runs the scripted block that many times after the one calibration, so one person can give more squint examples. Each extra pass adds a bit over 4 minutes. `--parts NAME,NAME` runs only those parts, in the order you name them, still after one calibration. The default is the full script once.

Optional free use, for false positives during ordinary work. Minutes of normal computer use, labeled as no struggle:

```
python -m ally.senses.matrix record --person ada --glasses no --light bright --free 5
```

Optional, and only if the person wants to say so: `--eyes monolid|double|hooded|unsure` and `--age under40|40to64|65plus`. Glasses is already on the command. Eye shape, age, and glasses are the physical things that change how eyes look to the camera, and the benchmark reports errors for each group. The recorder does not ask for race, ethnicity, or skin color.

```
python -m ally.senses.matrix record --person ada --glasses no --light bright --eyes hooded --age 40to64 --repeat 2
```

## What is stored

Each session writes `state/senses_runs/<run>/<alias>-<glasses>-<light>.jsonl`. One JSON object per line. Numbers and short text only. No picture, no pixels, and no video. Ctrl+C keeps what was already written. A partial label is not scored as a miss.

The first line has `"rec": "meta"`:

- `alias`, `glasses` (`yes` or `no`), `light` (`bright` or `dim`)
- `eyes` (`monolid`, `double`, `hooded`, `unsure`, or null)
- `age` (`under40`, `40to64`, `65plus`, or null)
- `camera`, `screen` (width and height), `senses` (the settings in effect), `commit`, `started`

Later lines:

- `"rec": "helper"` from the camera helper, about 15 times a second while a face is visible. `t` is the time. `f` is the summary scores: `squint_l`, `squint_r`, `smile`, `blink`, `blink_l`, `blink_r`, `yaw`, `pitch`, `width`, `light`. The same line also has `lm` (478 landmarks, each `[x, y, z]`, 4 decimals), `bs` (every blendshape by name, 4 decimals), `mat` (the 16 numbers of the face matrix, or null), and `w` and `h` (frame width and height). Live Ally does not ask for `lm`, `bs`, `mat`, `w`, or `h`.
- `"rec": "input"` for mouse and keyboard events (counts and times, not the keys).
- `"rec": "label"` with `name`, `start`, `end`, and `expect` for each part. A part cut off with Ctrl+C has `"partial": true`.

If a second face is at least 70 percent as wide as the largest face, that frame is stored as "no face", so two people are not mixed into one set of numbers.

## The parts

The parts run in this order. A 3 second rest sits between them. Rests are not scored. The rest before "Lean and squint" is longer (32 seconds with the default settings) so an earlier detection can finish. Calibration is not repeated. Squint parts are not next to each other.

1. Read normally, 15 s. Builds the baseline. Not scored.
2. Normal text. Read a sentence. Do not squint or lean. Expect no struggle. 8 s.
3. Bottom. Read a line at the bottom of the screen. The card sits on that edge. Expect no struggle. 8 s.
4. Soft squint. Squint a little, the way you would at text that is slightly too small. Expect a squint. 8 s.
5. Smile. Expect no struggle. 8 s.
6. Small text. Read it naturally. Do not act. Scored on its own, not as a false positive or a miss. 8 s.
7. Top. Read a line at the top of the screen. The card sits on that edge. Expect no struggle. 8 s.
8. Squint. Hold a squint as if the text were too small. Expect a squint. 8 s.
9. Fast blinks. Blink quickly, several times. Expect no struggle. 8 s.
10. Lean a little, as you would to read. Do not squint. Expect a lean. 8 s.
11. Wink with either eye, a few times. Expect no struggle. 8 s.
12. Small text again, for 20 s. Read it naturally. Do not act. Scored on its own.
13. Laugh. Expect no struggle. 8 s.
14. Tired. Let your eyelids feel heavy and keep reading. Expect no struggle. 8 s.
15. Lean in close. Do not squint. Expect a lean. 8 s.
16. Phone. Look down at a phone in your lap, then look back. Expect no struggle. 8 s.
17. Left eye only. Expect no struggle. 8 s. This is also the mirroring check.
18. Sit back. Lean back in the chair and keep reading. Expect no struggle. 8 s.
19. Right eye only. Expect no struggle. 8 s. This is also the mirroring check.
20. Turn the head left, then right. Expect no struggle. 8 s.
21. Lean and squint. Expect a struggle. 10 s.
22. If `--free` was set, that many minutes of normal use. Expect no struggle.

## Score the recordings

```
python -m ally.senses.evaluate report state/senses_runs
```

`PATHS` can be JSONL files or a folder of them. `--out DIR` chooses where the report goes. The default is the current folder: `senses-report.md` and `senses-report.csv`.

Replay runs the real `Senses` code on the recorded timestamps, with a temporary home directory, so it does not touch the saved baseline or `state/`. The baseline for each file comes from that file's calibration segment.

## Tune thresholds

```
python -m ally.senses.evaluate tune state/senses_runs
```

This searches the `[senses]` knobs that exist for tuning: `squint_delta`, `squint_weak`, `squint_hold`, `wink_max`, `smile_gate`, `blink_gate`, `pose_gate`, `light_floor`, `lean_ratio`, `lean_weak`, `lean_release`, `lean_hold`. It prints a `[senses]` snippet and the margin to the nearest false positive. It never edits `config.toml`. Copy the snippet by hand if you want those values.

It refuses to recommend when the files come from fewer than two people (two files from one alias still count as one person).

The search prefers, in order: zero struggle-level false positives, then the fewest misses, then the fewest stray signals, then the shortest detection latency. The snippet is the fit on everyone. The held-out false positives and false negatives are leave-one-person-out: each person's files are scored with thresholds fit on the others. Trust the held-out counts.

## Privacy check

With Ally stopped and the face model already present:

```
python scripts/privacy_check.py --seconds 60
```

For one minute (the default) it runs the camera helper with signals on and gaze off, and the input helper. Once a second it lists their network endpoints, including child processes. It also checks that no new image or video file appeared under the repo or under `state/`. A timestamped report is written to `state/privacy_checks/<UTC time>.txt`. Exit code 0 is PASS, 1 is FAIL, 2 means Ally is running, the model is missing, or the arguments are bad.

Any TCP or UDP endpoint on those processes is a failure. A new picture or video file is a failure.

The script also prints two checks to do by hand:

1. Turn Wi-Fi off and run the same command again. It should still pass.
2. Open Resource Monitor (`resmon`), open the Network tab, and confirm the camera helper and the input helper show no network use. Save a screenshot with the report.

While the camera is open, a small window sits in a corner: "Camera on - click to turn off". It stays on top, does not take focus, and has no taskbar button. Clicking it turns off eye tracking and face sensing and closes the camera. If that window cannot be created, the helper logs that once and keeps the camera on.

## How to read the numbers

A struggle is counted in the segment that contains its timestamp.

- **Struggle false positive.** A struggle during a segment labeled no struggle, including free use. This is help offered when the person is fine. The pitch table is false positives per minute of that time, split by glasses and light. Zero is the goal.
- **Miss (false negative).** A segment that expected a squint, a lean, or a struggle, and did not get the matching signal or a struggle. A squint segment counts if a squint signal or a struggle that covers squint appears. A lean segment works the same way. A struggle segment counts if any struggle appears.
- **Stray signal.** A squint or lean signal during a segment that expected none. Reported per kind (`squint`, `lean`) and tier (`strong`, `weak`).
- **Signal miss.** An expected kind that never produced that signal. A strong miss is an expected kind with no strong signal. A weak miss is an expected kind with neither a strong nor a weak signal.
- **Latency.** Seconds from the start of the segment to the first matching signal or struggle. The run table shows the mean.
- **Natural reading.** The two tiny-text parts (8 s and 20 s). They are listed on their own. A struggle there is not a false positive and a quiet face there is not a miss. Use them to see what unprompted small text looks like.
- **Rests and calibration.** Not scored. If calibration cannot build a baseline, that file is reported as a baseline failure and is not treated as a perfect zero.
- **Held-out counts.** From tune. These are the honest false positives and misses on people the search did not fit. The snippet by itself is the fit on the whole set.
- **Margin.** How far the nearest tuned knob can move toward more sensitivity before a new struggle false positive appears.

For the pitch, read the false-positives-per-minute table by condition (glasses × light) together with the miss counts. A setting that is quiet in bright light and noisy with glasses in dim light will show up there.
