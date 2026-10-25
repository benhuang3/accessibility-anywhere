# Codex spec: must-fixes for "Call / Tell a trusted person"

Worktree `wt-risk-all`, branch `risk-branch`.
- Do not commit, push, merge or switch branches.
- Never read `.env` or `config.toml`.
- Do not start `run.py`.

The review that found these is in `handoff/results/call_eval_sol.md`.

## Allowed files
- `ally/overlay/overlay.py`
- `ally/risk/wire.py` (`_tell` only)
- `ally/risk/contact.py`
- `README.md` and `docs/RISK_HANDOFF.md` (the pause sentences only)
- `tests/test_risk_contact.py` and `tests/test_overlay.py`

## Fixes
1. **The extra button freezes the panel and never confirms** (`overlay.py` `_extra_row.run`).
   - Today the callback runs on the Tk thread and can block for about 12 s.
   - `_render()` skips the rebuild because `card_key` is unchanged, so the confirmation never shows.
   - Fix:
     - On click, record "Contacting <label target>…" (for example "Contacting Priya…") in `_extra_done[offer_id]` and force a rebuild,
       so the button disappears at once and can't be pressed twice.
     - Run the callback in a daemon thread.
     - When it finishes, marshal back to the Tk thread with the overlay's existing queue or `root.after` (never touch Tk from the worker),
       set the final note, and force a rebuild.
   - Add a small helper for the forced rebuild, for example by clearing `self._card_key` / `self._bubble_key` before `_render()`.
   - The answered/closed path must not crash if the offer is gone by the time the worker finishes.
2. **Honest wording** (`contact.message_for`, `wire._tell`):
   - Push text:
     - Drop "nothing has been paid", because Ally cannot know that.
     - Use: "Ally safety alert: <what>. Ally asked them to stop and wait. Please contact them now."
     - When there is a call link, keep the existing "Tap to join the video call" sentence.
   - On-screen results, in plain words, with no "submitted" or other jargon:
     - Call, sent:
       - for `remote_access`: "Opening a call with Priya. Hang up on the other caller first."
       - otherwise: "Opening a call with Priya. I've asked them to join."
     - Tell, sent: "I've sent Priya a message."
     - Failure: keep the existing "couldn't reach" wording.
   - Update the test assertions that check these strings.
3. **Link validation** (`contact.valid_call_url`):
   - Also reject a URL that has user info (`user:pass@`), an explicit port, whitespace, or control characters.
   - In `ntfy_sender.send`, build the `Request` inside the `try`, so it truly never raises.
4. **Docs:** the pause blocks **No** (continue). **Yes** (close) works at once. Fix the sentences in `README.md` and `docs/RISK_HANDOFF.md`
   that say Yes waits.

## Tests (keep all passing)
- The new bad URLs are rejected.
- `send` never raises when building the request fails (patch `urllib.request.Request` to raise).
- The message contains no "paid".
- The `remote_access` call note says to hang up.
- Overlay, without a display: unit-test whatever pure helper you add. If the threading can't be tested headless, say so in the report.
- Run:
  - `cd tests && python -m unittest test_risk_contact test_overlay test_risk_wire test_proactive`
  - `python -m unittest discover -p "test_*.py"` (sandbox-only failures in Windows speech and the senses-lab socket test are known; report the rest).

## Report
Files changed, test output, and anything not done.
