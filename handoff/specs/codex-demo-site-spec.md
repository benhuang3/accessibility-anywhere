# Codex spec: "Scam Lab" demo site for the judges

Worktree `wt-risk-all`, branch `risk-branch`.
- Work only inside a NEW folder `demo/`.
- Do not commit, push or switch branches.
- Never read `.env` or `config.toml`.
- Do not start `python run.py`.

Another Codex job is editing `handoff/tools/guardian_eval.py`; don't touch it.

## Context
Ally is a Windows accessibility assistant for older adults, with a Chrome extension and a Python agent. Its scam protection warns when a
page looks like a scam. At the live Cal Hacks demo, the presenter visits these pages and the judges watch Ally react.

## Read first, to know what triggers Ally
- `ally/risk/wire.py`: `scan_page`, `on_page`, and the page dict fields (url, text, fields, overlay_text, fullscreen, has_tel_link,
  signals).
- `ally/risk/text.py`: scam-text scoring. Medium needs score ≥ 4, at least 2 signals and "alarm"; check the high rule.
- `ally/risk/fields.py` and `ally/risk/joined.py`: card fields, and bank pages combined with remote tools.
- `ally/risk/behavior.py` and `extension/risk-main.js`: page tricks.
- `ally/risk/fakescan.py`.
- `ally/risk/domains.py`: localhost can't be a lookalike, so skip that scenario.
- `extension/risk.js`: how the page dict is collected.
- The existing demos, to reuse what works: `extension/demo/risk-demo.html`, `extension/demo/trap-demo.html`, `trap-demo.js`.

## Build
- **`demo/index.html`, the "Scam Lab" landing page:**
  - One line on what Ally protects against.
  - A card per scenario, each with "what to watch for" in one sentence.
  - A "how Ally decides" strip: local rules first, then an optional AI second look, then a calm warning, and you can call a trusted
    person.
  - Numbers only if quoted exactly from `README.md`'s Scam protection section.
- **Scenario pages.** All brands and phone numbers are fictional (555-01xx numbers, invented brands, no real logos).
  1. A fake virus alert, the tech-support popup: `scam_popup` HIGH.
  2. A fake bank "account locked" page with card, PIN and security-code fields: `payment_field` plus scam text. It's the page to open
     while AnyDesk or Quick Assist runs, which gives the joined warning.
  3. A trap page that blocks the back button, demands fullscreen and loops dialogs. Port trap-demo, which is known to work.
  4. A "support session" page that tells the person to install a remote tool and open Event Viewer; the lead-in to the fake-scan beat.
  5. Two SAFE pages that must NOT warn: a small-shop checkout and a news article about scams.
- **Design:** inspired by Apple's "Liquid Glass".
  - The landing page: frosted translucent panels (`backdrop-filter: blur() saturate()`) over soft drifting gradient blobs, a specular
    top-edge highlight, 20–28px radius, layered soft shadows, and type in `-apple-system, "Segoe UI Variable", "Segoe UI", system-ui`.
  - Scam pages look like believable scams, not glass. Each gets only a small "← Scam Lab" glass pill in a corner.
  - Accessible: 18px minimum body text, WCAG AA contrast on glass (tint behind text), `prefers-reduced-motion`, a visible focus ring,
    and light and dark via `prefers-color-scheme`.
- **Technical:**
  - Plain HTML, CSS and JS with no network requests, CDNs or web fonts.
  - JS in separate files, with no inline `<script>` or `on*=` handlers.
  - It must work with `python -m http.server 8000` run from `demo/`.
  - Add `demo/README.md` with that command and the 5-beat demo order: the virus alert, then the bank page with AnyDesk and the call, then
    the trap, then the fake scan, then a safe page that stays quiet.

## Verify (required)
1. For every page, build the page dict the extension would send (copy `risk.js`'s collection logic) and run
   `ally.risk.wire.scan_page(page)`.
   - Scam pages must produce the intended kind and severity.
   - Safe pages must produce none.
   - If a page doesn't, fix the page copy, not the rules.
   - Save the verifier as `demo/verify_demo.py`.
2. If Playwright is installed, screenshot `index.html` light and dark at 1440×900 and 390×844 into `demo/shots/` (see
   `handoff/tools/guardian_eval.py` for usage).

## Report
The files, a `scan_page` result table per page, the screenshot paths, and anything not done.
