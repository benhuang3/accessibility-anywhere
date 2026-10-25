# Codex spec: the auto-alert must never open the call on the PC

Worktree `wt-risk-all`, branch `risk-branch`.
- Do not commit, push or switch branches.
- Never read `.env` or `config.toml`.

Another Codex job is editing `spacecall/src/`; don't touch `spacecall/`.

**Allowed files:** `ally/risk/wire.py`, `ally/risk/contact.py`, `tests/test_risk_spacecall.py`, `tests/test_risk_contact.py`,
`docs/RISK_HANDOFF.md` (one line).

## Bug (seen live by Ganesh)
- The setup: local config with `auto_alert = true`, `call_service = "spacecall"` and a valid `spacecall_url`.
- On a sketchy site, the Ally Call window (the GitHub Pages call page) **pops up on the PC by itself**, before the person taps
  "Call Priya".
- It must open only when they tap the Call button. The person decides whether to be on camera.

## Required behavior
1. **The auto-alert (high warning) only sends the push to the helper.**
   - It never calls `openCall` or `openUrl`, never opens a browser, and never creates a call window.
   - Find the code path that opens it (likely the shared room/URL helper or `_send_contact` being reused for auto-alert in the
     spacecall branch) and separate "notify" from "open".
2. **The auto-alert push must NOT carry a join link,** because nobody is in the call yet.
   - Plain text, for example: "Ally safety alert: <what>. Ally asked them to stop and wait. If they tap Call, you'll get a second alert
     with a link to join."
   - No `Click` or `Actions` headers.
3. **When the person taps Call afterwards:**
   - open the elder call page;
   - send a **second** push with the helper join link: "<name> is calling you through Ally. Tap to join."
   - It's still one room per incident. Generate the room at tap time if the auto-alert didn't need one.
   - The panel note stays honest, for example: "Opening a call with Priya. Hang up on the other caller first."
4. **Meet mode gets the same rule:** the auto-alert never opens Meet, and its push has no join link. The join link goes out only when
   the person taps Call.
5. Keep the existing rate limit (one auto-alert per 5 min) and every fallback.

## Tests
- With auto-alert on, in both spacecall and Meet mode: a high warning sends exactly one push, with no join headers, and makes zero
  `openCall`/`openUrl` calls (assert with a fake `call`).
- A later Call tap makes exactly one open call and sends one more push carrying the helper URL.
- Auto-alert off: unchanged.
- Run:
  - `cd tests && python -m unittest test_risk_spacecall test_risk_contact test_risk_wire test_risk_cooldown test_risk_actions test_proactive`
  - then full discovery (sandbox-only speech and socket failures are known).

## Report
The root cause (file and function), the changes, and the test output.
