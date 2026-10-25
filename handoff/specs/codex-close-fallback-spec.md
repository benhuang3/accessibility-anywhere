# Codex spec: when a remote tool can't be closed, protect the page and say so

Worktree `wt-risk-all`, branch `risk-branch`.
- Do not commit, push, merge or switch branches.
- Never read `.env` or `config.toml`.
- Do not start `run.py`.
- Do not kill any real process; tests use fakes.

## Bug (verified live)
- `taskkill /IM QuickAssist.exe /F` returns "Access is denied", because Quick Assist runs elevated.
- So Yes on "Want me to close Quick Assist?" (and "Close it" on the confirm step) does nothing.
- The person gets no feedback at all.

Read first:
- `ally/risk/actions.py` (`close_tool`, `_kill`, `perform`, `close_page`)
- `ally/risk/wire.py` (`on_risk_warning`'s ask_and_act, `attach`, `_tab_ids`, how a joined risk carries the bank page URL)
- `ally/proactive.py` (`Coordinator`: `ui`, `show_card`, `say`/voice; find the cleanest way to show a short message)
- `ally/overlay/overlay.py` (`show_card`)
- `tests/test_risk_actions.py` and `tests/test_risk_cooldown.py`

## Allowed files
- `ally/risk/actions.py`, `ally/risk/wire.py`, `ally/risk/copy.toml`
- `tests/test_risk_actions.py`, `tests/test_risk_cooldown.py`
- `docs/RISK_HANDOFF.md` (a limitation line)

If the only way to show a message needs a change to `ally/proactive.py` or `ally/agent.py`, stop and say so instead.

## Changes
1. **`_kill` reports why it failed.** Keep the current return value working for existing callers. Expose whether the failure was "access
   denied", which taskkill reports as exit code 1 with "Access is denied" in stdout or stderr, for example through a small result object or
   a second function.
2. **Fallback for remote-access risks.**
   - When the person said yes (to the first question or to the confirm step) and no matching process could be closed:
     - If the risk is the joined one and a bank or card page is known (its URL and/or tab id, the same data `close_page` uses), close that
       page with `actions.close_page(call, url, tab_id)`.
     - Then show a short message with whatever result path the wire can reach without changing other teams' files. Look at what
       `attach(agent)` can pass in, such as `agent.proactive.ui.show_card` or a `notify` callable injected into `RiskWire` and defaulting to
       a no-op. Use new `copy.toml` text:
       - bank page closed: "I closed the bank page. I can't close {tool} myself. Please click the X on its window."
       - nothing closed: "I can't close {tool} myself. Please click the X on its window, then hang up the phone."
     - Also speak it, if the existing path has a voice line.
   - Publish `risk_action` with `ok` set to the real result, plus `"fallback": "closed_page"` or `"fallback": "none"`.
   - For cooldown purposes, a failed close counts like Yes (forget the key), so the person is warned again if the tool is still running.
     Check whether that would re-warn every 10 s from the poller; if so, give the key a 2-minute hold instead, and say which you chose.
3. **A successful close also confirms**, using the same message path: "Closed {tool}." or "Closed the page."

## Tests (about 8)
- An access-denied kill closes the bank page through `close_page` with the remembered tab id, and shows the bank-page message.
- With no page, it shows the "click the X" message.
- A successful kill shows "Closed {tool}".
- `risk_action` carries the fallback field.
- The confirm-step "Close it" uses the same path.
- No crash when the notify path raises.
- Run:
  - `cd tests && python -m unittest test_risk_actions test_risk_cooldown test_risk_wire test_risk_contact test_proactive`
  - then full discovery (sandbox-only failures in speech and senses-lab are known).

## Report
Files changed, the message path you chose, and the test output.
