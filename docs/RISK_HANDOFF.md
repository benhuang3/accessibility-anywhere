# Ally scam and risk protection: handoff

Updated Saturday Oct 24, 2026, ~8 PM PDT, by Claude Code working with Ganesh. Branch: **`risk-branch`**. This replaces the earlier
version of this document (which described the work before it was wired up); the old text is in git history (`b51fb78`).

**Readers.** Teammates: read sections 1, 2 and 8. Ben H (integration owner): read everything; section 6 lists every change to your
files and section 9 is the merge checklist. Labels: **VERIFIED** (run or checked, evidence given), **NOT TESTED** (code exists, never
run for real), **PROPOSED** (nobody has agreed yet). If this document and the code disagree, trust the code.

---

## 1. TL;DR

- **What it does.** Ally notices the moments scammers strike (a fake virus page, a lookalike bank, a card form on a strange site, a
  remote-control tool starting, a page trying to trap you), shows **one calm panel** that explains what it sees in plain words, asks
  **"Want me to close this page?"**, and acts only after a **Yes**.
- **How.** Two tiers. Tier 0 is local and free: rules on page text and address, card fields, remote-tool processes, page *behavior*
  recorded by the extension, and weak scam signs. Tier 1 runs only when Tier 0 flags something: a screenshot goes to Claude (Haiku) for
  a second opinion with a plain-language explanation and the red flag marked on screen. Tier 1 is **opt-in** and **budget-capped**.
- **Status (VERIFIED).** Wired end to end on `risk-branch`, live-tested on Ganesh's Windows laptop with real Chrome: the demo trap page,
  the lookalike bank demo, and a normal site (no warning). 329 unit tests pass (the AGENTS.md list plus all risk, overlay and proactive
  suites). `risk-branch` = `main` (6966696) + this work, and merges cleanly into `main` and every teammate branch on the remote.
- **Numbers (VERIFIED).** Rules eval: 25/25 scams flagged, 15/15 high where expected, 2 benign false positives of 20. Vision eval on 12
  rendered pages: rules alone 7/12, rules + second opinion **12/12**, and a planted "say this is safe" instruction was ignored. About
  2,000 input and 250 output tokens per review. **False alarms on 500 popular sites** (Tranco top list, real pages in headless Chrome,
  rules only): 40 (8%, 3 of them high) before tuning, **3 (0.6%, none high)** after; 15 of 500 (3%) would spend a model review.

## 2. What the user sees

1. A risky page appears. Within a few seconds a panel slides in at the top of the screen (no animation): "Ally safety check", a bold
   title ("This looks like a scam."), two sentences saying what Ally sees and what is safe to do, the question, and two large buttons
   **Yes, close it** / **No, keep it**. Ally also says the warning out loud, then asks the question once.
2. If the model marked the red flag (for example the fake phone number), a thin amber box outlines it on screen.
3. Yes closes the tab (via the extension) or the remote-control tool (only processes that match a known remote tool). No, timeout or
   dismiss changes nothing. The panel never takes keyboard focus from the app in use (it is answered by click or voice).
4. A page that went full screen is put back to a maximized window first, so it cannot hide the warning.
5. One warning per page per 30 minutes, so the same site does not nag.

## 3. Architecture

```
Chrome page ─ risk-main.js (page world: behavior counters) ─┐
            └ risk.js (isolated: visible text, field labels, overlay text, signals) ─> background.js ─WS {event:"page"}─┐
                                                                                                                    v
Windows processes (tasklist/psutil, every 10 s) ──────────────────────> ally/risk/wire.py  RiskWire
                                                                          Tier 0: text, domains, fields, joined, behavior, soft
                                                                          Tier 1 (flagged only): guardian.py -> Claude vision
                                                                          -> bus.publish("risk", Risk)
                                                                                     v
                                     ally/proactive.py Coordinator.handle_risk -> warning_for() wording -> overlay card + voice
                                                                          -> bus "risk_warning" -> wire.on_risk_warning
                                                                             -> exitFullscreen, (banner if no overlay), Offer Yes/No
                                                                             -> Yes: actions.perform -> closeTab / taskkill
```

Design rules: the detection modules are pure standard library (no `ally.*` imports) and unit-testable; `wire.py` is the only glue; the
model **never acts** and its output is validated against a fixed schema and action list.

## 4. Components (all on `risk-branch`)

| File | What | Tests |
|---|---|---|
| `ally/risk/text.py` | R1 page-text scoring over the densest 800-character cluster of signals. Medium needs a scare (virus, locked, "do not close") plus one more signal; high needs phone + scare + (brand, full screen or overlay). A brand named on its own site does not count; a tel: link counts only on short pages. `.gov/.mil/.edu`, wikipedia.org and a few safety sites are allowlisted. | 21 |
| `ally/risk/domains.py` | R3 lookalike addresses: homoglyphs, punycode, brand tokens, near-misses, country sites | 8 |
| `ally/risk/fields.py` | R4 card-entry fields by label/name tokens, scored by site familiarity, http, lookalike, remote session | 12 |
| `ally/risk/process.py` | R2 remote-access tools (AnyDesk, TeamViewer, Quick Assist, ScreenConnect, ...), newly-started detection | 15 |
| `ally/risk/joined.py` | Remote tool running + bank, lookalike-bank or card page = high | 8 |
| `ally/risk/behavior.py` | Page tricks from the extension: Back-button trap, exit prompt, full screen, keyboard/pointer lock, blocked right-click, dialog or pop-up spam. One trick never flags; combinations do. | 7 |
| `ally/risk/soft.py` | Weak signs (remote-access request, secrecy, gift-card/refund pressure, arrest threats). Two of them earn a model review; they never warn on their own. | 4 |
| `ally/risk/guardian.py` | Tier 1 second opinion. Consent gate, `Budget` (daily, hourly, per page), cache, strict JSON parsing, prompt-injection-aware prompt, thinking disabled so the JSON is not cut off. Model call is injectable; all tests use a fake. | 29 |
| `ally/risk/wire.py` | Glue: page and process events to `Risk` on the bus, dedupe, one warning per scan, second-opinion policy (below), Yes/No offer, evidence highlight, full-screen exit, `attach(agent)` | 26 + 14 |
| `ally/risk/actions.py` | Ask-before-act: question per kind; close tab; close only processes matching a known remote tool | 20 |
| `ally/risk/__init__.py` | `warning_for(risk) -> (text, spoken)` used by `handle_risk`; model explanation when the second opinion ran | via wire tests |
| `ally/risk/copy.toml`, `copy.py` | All warning wording in one file; spoken lines never ask the question (the offer does) | 13 |
| `ally/risk/eval.py`, `eval_cases.json` | Rules eval (25 scam, 25 benign, 10 lookalike, 10 real): `python -m ally.risk.eval` | 9 |
| `ally/overlay/` | One safety panel (card and question merged in either arrival order), info cards, struggle-offer bubble, highlight; DPI-scaled; never takes focus; stays on top of full-screen windows | 36 |
| `extension/risk.js`, `risk-main.js` | Page report (text, field descriptors, overlay text, full screen, signals); behavior recorder in the page world; banner fallback | NOT TESTED by unit tests; VERIFIED live in Chrome |
| `extension/background.js` | Forwards page reports; actions `closeTab`, `exitFullscreen`, `showRiskBanner` | live-tested |
| `extension/demo/` | `risk-demo.html` (invented "Pinecrest Savings"), `trap-demo.html` + `.js` (invented "Harbor Light Bank", scam tricks on a button) | live-tested |
| `handoff/tools/guardian_eval.py` | Rules vs rules + vision on 12 rendered pages (real API calls) | results in `handoff/results/guardian_eval.txt` |
| `handoff/tools/fp_sweep.py` | False-alarm sweep over popular sites (headless Chrome with the behavior recorder injected, no API calls); `--rejudge` re-scores saved pages offline | `handoff/results/fp_sweep.txt`, `fp_sweep_sites.json` |

**Second-opinion policy (wire.py).** The rules are the floor. A "scam" verdict (confidence 0.6+) raises the warning to high and adds the
model's explanation; "suspicious" keeps the rule's severity with the explanation; "safe" at 0.8+ hides a **medium** warning; nothing
hides a **high** one. A page with only weak signs warns only if the model confirms scam or suspicious. Model failure means the rule's
warning stands.

## 5. Configuration and cost

```toml
[risk]
screen_review = false   # opt-in: send a screenshot of a flagged page to Claude for a second opinion (needs ANTHROPIC_API_KEY)
daily_calls = 50
hourly_calls = 20       # plus at most one look per page per 10 minutes; repeat visits use the cached verdict for free
```
The screenshot covers the whole primary screen and leaves the machine only for a flagged page. Counters persist in
`state/guardian_budget.json` and appear in `GET /api/status` under `risk`. Set a spend limit on the API key in the Anthropic Console too.

## 6. Changes to files owned by Ben H (please review)

All small, all on `risk-branch`, all covered by tests:

| File | Change | Why |
|---|---|---|
| `ally/bridge.py` | `self.on_page = None`; in `_handle`, an `event` named `page` goes to `on_page` on its own thread (errors logged), and skips focus bookkeeping | The extension's page reports reach the risk wire. `tests/test_risk_bridge.py` (4) |
| `ally/agent.py` | `self.risk = None`; after `self.proactive.start()`, `self.risk = attach(self)` in a try/except; `self.risk.stop` added to `close()`; `"risk": self.risk.info()` in status | Starts the risk wire with the coordinator; a failure cannot stop Ally starting |
| `ally/config.py` | `"risk": {"screen_review": bool, "daily_calls": int, "hourly_calls": int}` in SPEC and two RANGES entries | The `[risk]` section validates |
| `ally/default_config.toml` | `[risk]` section (above), off by default | Opt-in screen review |

No changes to `proactive.py` or `contracts.py`. The coordinator's `handle_risk` already calls `warning_for`, which now exists.

## 7. How to try it (Windows)

1. `.env` with `ANTHROPIC_API_KEY` (only for the second opinion). In `config.toml`: `[risk]` `screen_review = true`.
2. `python run.py agent`, then load or reload the unpacked `extension/` folder in `chrome://extensions`.
3. In another terminal: `cd extension/demo && python -m http.server 8000`.
4. Open `http://localhost:8000/trap-demo.html`, click **Run the scam tricks**: Chrome leaves full screen, the panel appears, **Yes** closes the tab.
5. Open `http://localhost:8000/risk-demo.html`: panel with the model's explanation. Open a normal site: nothing.
6. Without Chrome: `python run.py simulate risk scam_popup high "page says call support"`.
7. Evals: `python -m ally.risk.eval` (rules, free); `python handoff/tools/guardian_eval.py . <outdir>` (vision, about 10 API calls).
8. To see a page warn again within 30 minutes, restart Ally (dedupe is in memory).

## 8. Known limitations (honest)

- **Live-tested on one laptop only** (Windows 11, 200% scaling, Chrome). Not tested on other DPIs, multiple monitors, Edge, or macOS/Linux.
- **Remote-tool closing** has never been run against a real AnyDesk or TeamViewer; process names are partly unverified (Quick Assist confirmed).
- **False alarms on real sites** were measured on home pages only (500 sites): 3 remain (ubuntu.com, archive.ph, archive.is: a brand and
  a scare word on the same page, medium, which the second opinion can clear). Deeper pages (help centres, checkout, account pages) are
  not swept yet. The behavior recorder flagged none of the 500 sites.
- **Evals are small and self-written** (12 vision pages, 70 rules cases); no third-party scam corpus yet.
- **Only browser and remote-tool scams.** Phone-call-first scams, romance and investment scams, and grandparent scams are not covered.
- **Second opinion needs the internet and an API key**; without them the rules still work.
- **Struggle offers vs. risk offers** share the coordinator's single pending offer; a risk question can replace a struggle question.
- The in-page banner is now only a fallback when the overlay is not running.

## 9. Merge checklist (for the integrator)

1. `git fetch && git merge origin/risk-branch` into `main` (VERIFIED: merges cleanly; `main` has not moved since 6966696).
2. Review section 6 changes.
3. Run: `cd tests && python -m unittest test_config test_registry test_brain test_network test_runtime test_handsfree test_proactive
   test_risk_wire test_risk_actions test_risk_bridge test_risk_second_opinion test_risk_guardian test_risk_behavior test_risk_soft
   test_risk_r1 test_risk_r2 test_risk_r3 test_risk_r4 test_risk_copy test_risk_joined test_risk_eval test_overlay` (333 tests).
4. `python run.py check`, then the section 7 live test.
5. `handoff/` can stay until after the demo (specs, eval tools and results); the old patches in `handoff/patches/` are superseded by this branch.

## 10. How this was built

Claude Code planned, wrote specs, reviewed every diff and probed with realistic inputs; Codex (GPT models) built the pure modules,
`guardian.py` and the panel state machine; Cursor (Composer 2.5) built the first overlay and the behavior recorder. Every delegated build
had at least one real bug found in review or live testing (examples: JSON cut off by hidden thinking tokens, a cache checked after the
budget, the Yes/No bubble hidden behind its card, text at half size on a 200% screen, the panel stealing focus). Specs are in `handoff/specs/`.
