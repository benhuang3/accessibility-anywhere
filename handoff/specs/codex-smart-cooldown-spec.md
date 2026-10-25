# Codex spec: the cooldown follows the person's answer, and "No" gets an "Are you sure?" step

Worktree `wt-risk-all`, branch `risk-branch`.
- Do not commit, push, merge or switch branches.
- Never read `.env` or `config.toml`.
- Do not start `run.py`.

Read first:
- `ally/risk/wire.py` (`Deduper`, `on_page`, `on_risk_warning`, `_tell`)
- `ally/risk/actions.py` (`ask_for`, `perform`)
- `ally/contracts.py` (`Offer`: its fields and how `options` / labels work)
- `ally/overlay/layout.py` (`button_labels`, `no_label`) and `ally/overlay/overlay.py` (how an offer's labels and pause are drawn)
- `ally/risk/copy.toml`
- `tests/test_risk_wire.py` and `tests/test_risk_contact.py`

## Allowed files
- `ally/risk/wire.py`, `ally/risk/actions.py`, `ally/risk/copy.toml`
- `ally/overlay/overlay.py` and `ally/overlay/layout.py`, only if custom button labels aren't already supported
- `tests/test_risk_wire.py`, `tests/test_risk_contact.py`, and a new `tests/test_risk_cooldown.py`
- `docs/RISK_HANDOFF.md` (one short paragraph)

## Behavior
Today `Deduper` silences a (host, kind) for 30 min as soon as the warning is published, whatever the person answers. Change it so the
silence depends on the answer:

| Answer to the first question | Result |
|---|---|
| **Yes** | Run the action as today, then **forget** the key. Coming back to the page warns again. |
| **No** | Ask a second question at once (below). |
| Timeout or no answer (`ask` returns anything other than "yes" or "no") | Silence the key for **2 min** only. |

Second question (only after a first "No"):
- Text from a new `copy.toml` entry: "Are you sure? Scammers often say it's safe to continue."
- Yes button label "Close it", the safe primary choice; No button label "Keep it open". No pause countdown and no extra contact button
  on this one.
- Timeout 20 s. The panel must show it as a fresh question; check how the overlay replaces an answered offer.

| Answer to the second question | Result |
|---|---|
| **Close it** | Run the same action as the first Yes, then forget the key. |
| **Keep it open** | Silence the key for **30 min**. |
| Timeout | Silence for 2 min. |

- While a question is on screen, the same (host, kind) must not raise another warning. Mark it "in progress" with a short hold, which
  the answer above then replaces.
- `Deduper` gains `snooze(key, seconds)` and keeps `forget`. Every time comes from the injectable clock.
- `RiskWire.reset()` still clears everything.
- The 30 s pause (remote tool plus bank) still applies to the first question only.
- Auto-alert and the contact button are unchanged.
- Publish a `risk_decision` bus event with the key, the final answer and the chosen snooze seconds, so the dashboard can show it later.

## Tests (new `test_risk_cooldown.py`, about 10; keep all existing passing; update an existing assertion only where the behavior change is intended, and say which)
- Yes means the same page warns again at once.
- No, then Keep it open: silent at 29 min, warns again after 30.
- No, then Close it: the action runs and it warns again.
- A first-question timeout: silent at 1 min, warns after 2.
- A second-question timeout gives 2 min.
- The second question has the right labels, no pause and no extra_action.
- No duplicate warning while a question is pending.
- `risk_decision` is published.
- `reset` clears snoozes.

Run:
- `cd tests && python -m unittest test_risk_cooldown test_risk_wire test_risk_contact test_risk_actions test_overlay test_proactive`
- then full discovery. Sandbox-only failures in Windows speech and senses-lab socket are known; report the rest.

## Report
Files changed, test output, and any assertion you changed with the reason.
