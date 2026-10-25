# Codex spec: bring the risk docs up to date, and draft two teammate notes

Worktree `wt-risk-all`, branch `risk-branch`. Do not commit, push, merge or switch branches. Never read `.env` or `config.toml`.
Do not start `run.py`. Docs only: change no `.py`, `.js` or `.toml` files.

## Read first
`docs/RISK_HANDOFF.md`, `README.md`, `ally/risk/wire.py` (PAUSE_SECONDS, `_tell`, `_make_contact`, the `extra_action` and pause logic in
`on_risk_warning`), `ally/risk/contact.py`, `ally/risk/process.py` (`_lolrmm`, `_NOT_REMOTE`), `ally/default_config.toml` `[risk]`,
`handoff/results/guardian_eval.txt`, `handoff/results/fp_sweep.txt`, `tests/test_risk_contact.py`, `tests/test_risk_eval.py`.
Every number you write must come from these files. If a number is not there, leave it out; never estimate one.

## 1. `docs/RISK_HANDOFF.md` (update in place, keep its structure)
- Add the newer features to "user experience" and "components":
  - **Pause:** when a remote tool is running and a bank page is open, the panel counts down for 30 s before Yes can be pressed.
  - **Call or Tell a trusted person:** a third button on high warnings. With `contact_call_url` set it opens the helper's standing
    Google Meet room in Chrome and sends an ntfy.sh push with a Join button. Without it, it only sends the push. The push never contains
    the address or anything from the page. The config keys are `trusted_contact`, `contact_topic` and `contact_call_url`; accepted call
    hosts come from `contact.CALL_HOSTS`.
  - **The LOLRMM catalogue** of remote tools (count from `remote_tools.json`, Apache-2.0, credit the project), and the screen-reader and
    magnifier exclusions in `_NOT_REMOTE`, with the reason for them.
  - **The hidden-text prompt-injection eval cases** (s6 to s8) and the current eval score.
- Correct any claim that the person can answer by voice. With the current `NullVoice` they answer by tapping Yes or No (or Enter / Esc).
  Voice answers are listed as open, owned by Ben W.
- Update the test count by running `cd tests && python -m unittest discover -p "test_*.py"` and reporting what it prints.
- In the Ben H table, add a row for the `config.toml` keys above (`ally/config.py` and `ally/default_config.toml`).
- Under "limitations", add: ntfy topics are only as private as their name; the Meet link is a standing room; the warning and question
  can still race when there is no overlay.

## 2. `README.md`: add one section, "Scam protection" (about 15 to 25 lines)
Plain words, for a judge or a new teammate. Cover:
- What it catches.
- How it decides: local rules first, then an optional AI look at the screen.
- What the person sees.
- The trusted-contact call.
- Honest numbers, each with its source file:
  - the eval score, rules alone and with the AI;
  - the false-alarm rate on the sweep, with the number of sites;
  - the own scam set from `test_risk_eval`.
- Privacy: screen review is off by default, there are spend caps, and alerts never include the address.

End with a link to `docs/RISK_HANDOFF.md`. Do not edit any other part of the README.

## 3. Draft notes, as new files (Ganesh sends them; do not address anyone with @)
- `handoff/notes/to-ben-h.md` (under 25 lines): which of his files `risk-branch` touches and why. Take them from the Ben H table:
  bridge `on_page` hook, agent attach/stop/status, config keys, default config.
  - What to check when merging, and that `screen_review` defaults to false.
  - Ask for a demo-reset trigger: something that publishes the bus topic `risk_reset`, such as `python run.py simulate risk_reset`
    or a dashboard button.
- `handoff/notes/to-ben-w.md` (under 20 lines): voice for risk prompts.
  - `NullVoice` can't hear "yes".
  - The spoken warning and the question can overlap.
  - Ask: support yes/no answers to `Coordinator.ask` offers. Until then, the spoken line ends with "Tap Yes or No" (no code change).

## Acceptance
- `git diff --stat` shows only `docs/RISK_HANDOFF.md`, `README.md` and the two new notes.
- Every number in the new text appears in a file listed above, or in the test run output.
- No URLs to real Meet rooms and no ntfy topic values.

## Report
The files changed, the test-run output line, and every number you used with its source.
