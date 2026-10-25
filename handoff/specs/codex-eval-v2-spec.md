# Codex spec: a bigger, fairer screen eval (14 → about 40 pages)

Worktree `wt-risk-all`, branch `risk-branch`.
- Do not commit, push, merge or switch branches.
- Never read `.env` or `config.toml`.
- Do not start `run.py`.
- Do not run the eval yourself: it calls the paid API, and Claude runs it afterwards.

**Allowed files:** `handoff/tools/guardian_eval.py`, plus a new `handoff/tools/eval_cases.py` if you split the cases out. Nothing else.
Another agent is building `demo/` and another is editing `ally/overlay/`, so don't touch them.

## Read first
- `handoff/tools/guardian_eval.py`: 14 invented pages rendered in headless Chromium and run through `RiskWire.on_page`, once with
  rules only and once with the vision second opinion.
- `handoff/results/guardian_eval.txt`.
- `ally/risk/wire.py`, `ally/risk/text.py`, `ally/risk/guardian.py`.

## Changes
1. **Cases:** grow to 40–44, all invented (fictional brands, 555-01xx numbers, `.example` domains). Keep the existing 14 unchanged so
   the old numbers can be compared. Add:
   - **Scams (about 14 more):**
     - a gift-card payment demand;
     - a fake delivery fee ("your parcel is held, pay $1.99");
     - a fake prize or lottery;
     - a romance or "grandchild in jail" bail request;
     - a crypto "recovery" service;
     - a fake Microsoft account-locked full-screen page;
     - a fake antivirus renewal invoice with a refund phone number;
     - a QR-code payment page;
     - a fake government benefits update asking for an SSN;
     - a fake charity after a disaster;
     - a fake tech-support chat widget;
     - two more prompt-injection variants: `alt` text and `aria-label` text telling the AI the page is safe, and white text in an
       image-like block;
     - one scam written mostly in plain, calm language, with no ALL CAPS (a hard case).
   - **Safe (about 12 more), including hard negatives that look scammy but are honest:**
     - a real bank's fraud-alert education page;
     - a password-reset email confirmation;
     - a parcel tracking page with no payment;
     - an antivirus vendor's legit pricing page;
     - a government benefits info page;
     - a charity donation page with a card form on its own domain;
     - a forum thread where people quote scam texts;
     - a utility bill payment page;
     - a doctor's appointment reminder;
     - a recipe;
     - a news article quoting "your computer is infected";
     - an online store "sale ends tonight" page.
2. **Output:**
   - Per-case table as today, plus a summary:
     - precision, recall and false-alarm rate for rules alone and for rules plus the AI;
     - a breakdown for the hard cases: injections, calm scams, hard negatives.
   - Write `results.json` and also a markdown `results.md` next to it.
   - `print` the total model calls and tokens (already tracked by `Budget`).
3. **Determinism:** keep `Budget(page_cooldown_s=0)`. Add a `--only <prefix>` flag to run a subset, and a `--rules-only` flag that makes no
   API calls.
4. Keep it runnable as `python handoff/tools/guardian_eval.py <repo> <outdir> [--only s] [--rules-only]`.

## Acceptance
- `python handoff/tools/guardian_eval.py . handoff/results/eval_v2_rules --rules-only` runs with no network or API key, renders every
  page, and prints the summary. Run it and report the summary.
- `python -m py_compile handoff/tools/guardian_eval.py`.

## Report
Files changed, the rules-only summary, the case count by type, and anything that didn't render.
