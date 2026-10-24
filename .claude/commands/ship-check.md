---
description: Pre-merge check: tests, inline-script/handler grep, secret grep, run.py check
---

Run these from the repo root and print a PASS/FAIL summary at the end. Run all of them even if one fails.

1. Tests: `cd tests && python -m unittest test_config test_registry test_brain test_network test_runtime test_proactive` (add any new test_*.py modules). Report the pass count.
2. Inline scripts and handlers in `dashboard/` and `extension/` (CSP is `script-src 'self'`). Use Grep for each:
   - `<script(?![^>]*\bsrc=)[^>]*>` in `*.html` (a `<script>` with no `src`)
   - `\son[a-z]+\s*=` in `*.html` and in JS that builds HTML strings (e.g. `onclick=`)
   - `javascript:` URLs
3. API-key patterns across the repo (skip `.git`, `.env`): `sk-[A-Za-z0-9_-]{16,}`, `AIza[0-9A-Za-z_-]{20,}`, `CARTESIA_API_KEY\s*=\s*\S+`, `ANTHROPIC_API_KEY\s*=\s*\S+`. Confirm `.env` is not tracked: `git ls-files .env`.
4. `python run.py check`.

Summary format, one line per item:
`PASS|FAIL  tests (N passed)`, `inline-scripts`, `secrets`, `run.py check`, then `OVERALL: PASS|FAIL`. For each FAIL, list file:line.
