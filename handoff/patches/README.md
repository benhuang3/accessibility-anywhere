# Scratch-branch code as patches

These contain the code from the older scratch branches, so the whole history of the work is in one branch. They
are **not** needed to use the rules in `ally/risk/` (those are already on this branch). They are the
glue code that depends on the foundation patch: bridge hook, adapter, extension script, dashboard panel.

Apply in this order on top of the original base commit `fef8cf4` (VERIFIED: applying 01 to 04 in order on a clean
checkout of `fef8cf4` reproduces the `ganesh/dash` tree exactly):

| Patch | What it adds |
|---|---|
| `01-foundation-resolved.patch` | The proactive foundation (`contracts.py`, `proactive.py`, `habits.py`, `senses/`, `overlay/`, `voice_confirm.py`, starter risk rules, `/api/event`, `/api/habits`) with the conflicts in `agent.py` and `requirements-optional.txt` resolved by hand. 96 tests passed on this state when it was built. This is a scratch resolution, not Ben H's integration. |
| `02-v1-risk-rules.patch` | The first (v1) rule set and its tests (`ally/risk/*`, `tests/test_risk.py`). Superseded by the pure modules on this branch. |
| `03-extension-bridge-hook-adapter.patch` | Extension risk script and banner, the `pageRisk` bridge event, `Bridge.on_page`, and `ally/risk/wire.py` (adapter on the v1 API). |
| `04-dashboard-panel.patch` | The dashboard "Proactive help" panel (`dashboard/` only; 17 browser checks passed). |
| `05-extension-only.patch` | The extension part alone (apply after 01 instead of 02 to 04). |

Apply with `git apply --whitespace=nowarn <patch>` from the repo root. The original, unresolved patch is in
`handoff/foundation/`.
