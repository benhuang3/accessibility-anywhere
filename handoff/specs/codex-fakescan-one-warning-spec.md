# Codex follow-up: one fake-scan warning per trick, not one per process

Worktree `wt-risk-all`, branch `risk-branch`. The uncommitted fake-scan work is already in the tree; build on it.
- Do not commit, push, merge or switch branches.
- Never read `.env` or `config.toml`.

**Allowed files:** `ally/risk/fakescan.py`, `ally/risk/wire.py`, `tests/test_risk_fakescan.py`.

## Problem
Opening Event Viewer starts `eventvwr.exe`, which launches `mmc.exe`. Both are in `SCARE_TOOLS`, so one action produces two high warnings,
one after the other.

## Change
1. `FakeScanWatcher.observe` keeps returning every newly appeared scare tool; other callers may want that.
2. `wire.poll_once` publishes **at most one** fake-scan Risk per poll, choosing the most specific tool.
   - Priority order: eventvwr.exe, tree.com, netstat.exe, msconfig.exe, msinfo32.exe, regedit.exe, mmc.exe (mmc last, because it's
     generic).
   - When eventvwr.exe and mmc.exe appear together, the evidence says "Event Viewer".
3. While a fake-scan warning for this remote session is in progress (asked and not yet answered), don't raise another fake-scan
   warning.
4. If mmc.exe appears in a later poll right after eventvwr.exe has already warned in this remote session, treat it as the same trick and
   don't warn again.

## Tests
- eventvwr and mmc in the same poll: one warning, saying "Event Viewer".
- eventvwr in one poll, then mmc in the next: still one warning.
- netstat later in the same session: a second warning, as today.
- The existing fake-scan tests still pass.
- Run:
  - `cd tests && python -m unittest test_risk_fakescan test_risk_wire test_risk_cooldown test_risk_actions`
  - then full discovery. Sandbox-only failures are known.

## Report
Files changed and the test output.
