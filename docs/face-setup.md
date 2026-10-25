# Face setup

One setup learns the eyes, the lean, and the squint. Say **set up my face** (or **set up my eyes**,
**calibrate my eyes**, **scan my face**). It takes about 90 seconds the first time. The pose rounds
and the check dots are the same length as the old eye-tracker setup, so a full run with no repeated
dots is a little over a minute and a half. Esc cancels.

Quick calibrate is unchanged: **quick calibrate** or **check my eye tracker** is five dots, about
six seconds, and only re-tunes a setup you already have.

## What you see

The camera helper opens a full-screen window. The top line is `Step N of 6` and a progress bar.
Ally says one short sentence at each step and does not cut that sentence off. The next step does
not start until the sentence has had time to be said. While this is on screen, Ally's other voices
stay quiet.

1. **Face scan** (about 10 seconds). An oval face guide and a ring of 16 segments. Turn your head
   slowly in a circle. A segment fills when your head points that way. The same readiness checks
   as the old setup watch the light, the distance, and glasses glare, and the screen says how to
   fix them. Turning your head is the task, so a turn is not treated as a problem.
2. **Eyes.** The 9 grid dots. Move only your eyes.
3. **Sit back a little.** 5 dots. This is the far end of your lean.
4. **Lean in a little.** 5 dots. This is the near end. Ally saves your lean ratio: how much bigger
   your face got, and how your head distance changed, near divided by your normal sitting pose.
5. **Reading.** A short paragraph in the middle (8 seconds), a line at the top (4 seconds), a line
   at the bottom (4 seconds), a smile (3 seconds), then a squint at tiny text (3 seconds, a short
   rest, then 3 seconds again). These labelled frames are numbers only. They train the senses
   baseline and the personal squint model.
6. **Check.** The 5 check dots, then the result: how accurate the eyes are, and whether squint
   sensing is on for you. If the squint was not distinct, Ally says so, and lean and the other
   signals still work.

Frames stay in memory. The helper does not write a picture or a video, and it does not use the
network. The saved files are numbers: the gaze model, the senses baseline (including the personal
squint model and the lean ratio), and, when the profile store is present, one face profile.

## Afterward

If the personal squint model is good, squint sensing uses it. If it turns itself off, squint stays
off and lean still uses your ratio.

On a later start, if there is no local gaze calibration and no senses baseline, but a saved face
profile exists, Ally restores it and says: "Welcome back. I restored your face setup."

If nothing is set up, Ally suggests the face setup once. If tracking drifts after that, Ally still
offers quick calibrate, as before.

## What to check with a camera

The unit tests do not open a camera. On a real webcam, check:

- The oval sits over your face and all 16 ring segments fill when you turn in a circle.
- Dim light, sitting too far or too close, and glasses glare each show one short fix.
- The 9 dots, the sit-back dots, and the lean-in dots land where you look, and the spoken lines
  are not cut off.
- The reading text is in the middle, then the top, then the bottom, then the smile, then the tiny
  squint, twice.
- The check result names the eye accuracy and whether squint sensing is on.
- Esc closes the window and Ally says the setup was cancelled.
- A second "quick calibrate" is still only the short check.
