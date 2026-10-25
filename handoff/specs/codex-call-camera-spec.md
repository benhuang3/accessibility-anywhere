# Codex spec: turn the camera and mic on in Ally calls, with honest call status

Worktree `wt-risk-all`, branch `risk-branch`.
- Do not commit, push, merge or switch branches.
- Never read `.env` or `config.toml`.
- Do not start `run.py`.

Read `extension/call.js` and `extension/call.css`.

**Allowed files:** `extension/call.js` and `extension/call.css` only.

## Why
The person tapped "Call Priya", so they asked for this call. In that Ally-opened popup only, the camera and microphone should be on by
default. Ordinary Meet tabs stay untouched (the existing `allyCallInfo` check).

## Changes
1. **Camera and mic on before joining.**
   - In `scan()`, before clicking the join button, look for visible elements whose `aria-label` starts with "Turn on camera" and
     "Turn on microphone".
   - Click each one once; track that with flags, so they are never toggled back off.
   - Don't click anything whose label starts with "Turn off". That means it's already on.
   - Then wait one scan tick (500 ms) before clicking join, so Meet applies the change.
   - If the labels aren't found within 5 s, join anyway.
   - Chrome's own camera permission prompt is out of scope. It's granted once, by hand.
2. **Honest status in the bar.**
   - "Connecting to <name>…" until the join button is clicked.
   - After that, and while there's no "Leave call" element: "Waiting for <name> to let you in…". Meet shows "Asking to join" while the
     host decides.
   - "Talking with <name>" only once an element with `aria-label^="Leave call"` exists.
   - Today, the join button disappearing switches straight to "Talking", which is wrong while someone is waiting to be admitted.
3. Keep every fail-safe as it is, along with the textContent-only DOM building and the 30 s fallback. Also extend the deadline logic so a
   person still waiting to be admitted at 30 s keeps the frame; only "no join and no leave control ever found" restores plain Meet.

## Acceptance
- `node --check extension/call.js`.
- The diff touches only the two allowed files.
- Report every aria-label and text you rely on.
