# Codex spec: demo-hardening fixes for the risk feature

Worktree `wt-risk-all`, branch `risk-branch`. Do not commit, push, merge or switch branches. Never read `.env`. Do not start `run.py agent`.
Read first: `ally/risk/wire.py`, `ally/risk/actions.py`, `ally/risk/guardian.py`, `ally/risk/__init__.py`, `ally/risk/copy.toml`, `ally/risk/copy.py`,
`ally/claude.py` (the `Claude(...)` constructor: `timeout`, `retries`), `extension/background.js` (`ACTIONS.closeTab`, the `pageRisk` handler),
`extension/risk.js`, and the tests `tests/test_risk_wire.py`, `test_risk_actions.py`, `test_risk_second_opinion.py`, `test_risk_guardian.py`, `test_risk_copy.py`.

## Allowed files
`ally/risk/wire.py`, `ally/risk/actions.py`, `ally/risk/guardian.py`, `ally/risk/__init__.py`, `ally/risk/copy.toml`, `extension/background.js`,
and the risk test files above (plus a new `tests/test_risk_demo_fixes.py`). Nothing else. Keep every existing public name working.

## Fixes (each found by a reviewer or a live test)
1. **Explanation cut mid-word.** `wire._evidence` truncates the model's explanation at 200 chars, so the panel and the voice say "...and do n".
   Trim at the last sentence end (". ", "! ", "? " or final punctuation) within 240 chars; if there is none, cut at the last space and add "…".
   Never cut inside a word. Keep the existing redaction (phone numbers, URLs).
2. **"Your bank is usually netflix.com."** In `copy.toml`, the `untrusted_site` body assumes every brand is a bank. Make it brand-neutral:
   "You're on {host}. The real site is {usual_site}." (and keep the title/spoken rules the copy tests enforce). Check every copy section
   for the same assumption.
3. **Wrong wording for page-trick warnings.** A `Risk` with `source == "risk.behavior"` currently renders the `scam_popup` copy ("asks you to
   call a number"), which is false for those pages. Add a `[page_tricks]` copy section (title like "This page is trying to trap you.", body
   saying it is stopping you from leaving or taking over the screen, spoken line without a question, actions close_page / explain) and make
   `warning_for` use it for `source == "risk.behavior"`. Update `copy.copy_key` only if needed, keeping its current behavior for other kinds.
4. **Slow warnings on bad wifi.** The default model call in `guardian.Guardian._ask_model` uses `Claude()` with its default timeout and retries,
   and high warnings wait for it. Use `Claude(timeout=8.0, retries=0)` (check the constructor's real parameter names), and add a
   `review_timeout_s` (default 10.0) so `RiskWire._second_opinion` never waits longer: run the review in a worker thread and, if it does
   not finish in time, return the rule's risk unchanged (for weak-sign-only pages, return None). Test with a fake guardian that sleeps.
5. **Yes fails to close the tab.** `closeTab` matches the exact URL, and pages change their `#hash`. Make it robust end to end:
   - `background.js` `pageRisk` handler: include `tab_id: sender.tab && sender.tab.id` in the page event it forwards.
   - `wire.py`: remember the tab id of the page that produced a risk (key: host + kind), and pass it to the action.
   - `actions.close_page(call, url, tab_id=None)`: send `{"url": url, "tabId": tab_id}`.
   - `background.js` `closeTab({ url, tabId })`: if `tabId` is a number and that tab still exists and its URL has the same origin as `url`,
     close it; otherwise close tabs whose URL equals `url` ignoring the `#hash`. Return `{ closed: n }`.
6. **A rehearsal disables the AI.** Add `RiskWire.reset()` that clears the deduper, the pending marks/tab ids, the guardian cache, and the
   budget's per-page cooldowns (add `Budget.clear_pages()` that also persists). Do not reset the daily or hourly counters (they cap spend).
   Expose it on the bus: `RiskWire.start()` subscribes to topic `"risk_reset"` and calls `reset()`; `stop()` unsubscribes.

## Tests (about 15 new, keep all existing passing; update an existing assertion only where the behavior change is intended, and say which)
Sentence trimming (long explanation, no punctuation, exact boundary, redaction kept); brand-neutral copy for netflix/microsoft lookalikes
(no "bank" in the text unless the brand is a bank — check `joined.BANK_BRANDS` for the list); page-tricks wording; review timeout returns the
rule's risk within ~timeout and the weak-sign page returns None; tab id travels from the page event to the closeTab call; closeTab arguments
include tabId; reset clears dedupe (the same page warns again), cache and page cooldown, but not the daily counter; the bus topic triggers reset.
`node --check extension/background.js`. Run: `cd tests && python -m unittest test_risk_wire test_risk_actions test_risk_second_opinion
test_risk_guardian test_risk_copy test_risk_demo_fixes test_proactive`.

## Report
Files changed, test results, anything not done or assumed.
