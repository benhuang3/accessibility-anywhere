# Ally scam and risk protection: handoff document

Written Saturday Oct 24, 2026 (PDT), during Cal Hacks 13, by Claude Code working with Ganesh. It documents everything built so far for the scam and risk protection feature, why, what is verified, what is not, and what is left.

**Audience, two readers.**
1. Every teammate: read sections 1 to 3 and 10 to check that the goal, decisions and ownership are right, and flag corrections.
2. The one integrator (the person with the max plan): read everything, verify claims against the code, merge, then restructure the team notes (Notion and the repo docs). Section 11 is written for you.

**How to read the labels.** Every claim carries one of these:
- **VERIFIED**: someone ran it or checked it directly during this session; the evidence is stated.
- **REPORTED**: taken from another person's or page's report, not independently checked.
- **NOT TESTED**: code exists but was never run in its real environment.
- **PROPOSED**: a suggestion that nobody with authority has accepted.
- **NOT BUILT**: planned, no code yet.

Trust the code over this document if they disagree. Line numbers refer to a scratch copy of the foundation patch and will shift.

---

## 1. TL;DR

- **Goal:** Ally warns older adults, calmly and with consent, at the moments scammers strike: a fake virus pop-up, a remote-control tool being installed, a lookalike bank site, a card form on an unfamiliar site, and the strongest signal, a remote tool running while a bank page is open.
- **Built (VERIFIED, 81 passing unit tests, all in `ally/risk/`):** five pure detection rules (R1 page text, R2 remote tools, R3 lookalike domains, R4 card fields, a joined rule), the warning wording in one config file, and an evaluation set with a precision/recall runner.
- **Built but not merged or connected (older scratch branches):** an in-page warning banner and content script (NOT TESTED in real Chrome), a bridge hook, an adapter that publishes risks onto the bus, and a dashboard "Proactive help" panel (VERIFIED in headless Chromium, 17 checks).
- **Not built:** ask-before-act (closing a tab or tool only after a yes), the adapter that turns rule results into the foundation's `Risk` objects, extension updates, demo pages, and a design pass.
- **Blocked on others:** Ben H must merge the foundation and a small bridge hook. Real-device checks (Windows overlay, Chrome, end-to-end) need a person at the demo laptop.
- **Deadlines (REPORTED, Notion):** internal feature freeze Sat midnight; Devpost submission and check-in Sun Oct 25 (9:00 AM PDT submission). The Notion page still records a conflicting 2 AM freeze as superseded but unconfirmed by Ben H.
- **One surprise to know about:** the team's GitHub repo `benhuang3/accessibility-anywhere` is already **PUBLIC** (VERIFIED with `gh repo view`), although some notes say it goes public only at submission. Everything pushed there is visible now.
- **Possible rebuild (REPORTED, unconfirmed):** Ganesh has mentioned that parts of Ally, notably zoom, may be rebuilt while the computer-vision features stay. No design for that exists in Notion or the repo, so the rules were written to be portable (section 4.2).

---

## 2. Goal and context

### 2.1 The product
Ally is a Windows accessibility agent for older adults (Notion: a proactive layer that notices body, behavior and screen signals, offers help, learns accommodations and automates them). Scam protection is one of its "risky moments" features. Accepted scope (Notion Decisions): no custom hardware; laptop webcam, mic and speakers only; Notion is the shared team hub.

### 2.2 Why scam protection
From the "Research: Scam & Risk" page (PROPOSED, not authoritative; every number below is REPORTED there with its source):
- People 60+ reported about $7.7B in losses to the FBI in 2025; tech-support fraud about $1.04B of that. They are about 5x as likely as younger adults to report losing money to tech-support scams (FTC, older data).
- The scam is a chain, and software can interrupt each link:

| Step | What happens | Where Ally steps in |
|---|---|---|
| 1 Lure | Full-screen pop-up or locked page: "virus found", a phone number, a countdown | R1 page text, R3 for the site |
| 2 Call | Victim phones the number | (spoken pause before dialing; not built) |
| 3 Remote access | "Technician" has them open AnyDesk, TeamViewer, Quick Assist | R2 process watch |
| 4 Payment | Fake scan, then a fee by gift card, crypto, wire | R1 payment words, R4 card fields |
| 5 Refund follow-up | Victim logs in to their bank while the scammer watches | **Joined rule** (strongest signal) |
| 6 Persistence | A second remote tool installed | R2 |

- Differentiator versus Edge Scareware Blocker and Chrome's AI checks: cross-surface (page plus running process plus payment field), voice-first with ask-before-act, tuned for older adults. The browser-only tools cannot see a remote tool running next to a bank page.

### 2.3 Non-goals and constraints
- Never close a page or kill a process without an explicit yes (voice or click).
- Wording is calm and hedged ("may", "looks like"), no capital-letter shouting, **no phone numbers in Ally's own warnings**.
- No live scam sites in tests or demos; demo pages are localhost only, labeled DEMO, with invented brands.
- No API keys or secrets in the repo.
- Do not claim "nothing leaves the computer" for the product as a whole: screenshots for "describe my screen" and voice audio go to cloud services. Page text for scam checks is designed to stay local (NOT TESTED with a network capture).

---

## 3. Decisions and ownership in force

All **ACCEPTED** unless marked. Source: Notion Decisions table and "Current State — Start Here", read Sat Oct 24 afternoon. Notion pages are marked "unverified" by Notion itself; they record intent, not proof of implementation.

| Decision | Detail | Date (PDT) |
|---|---|---|
| Risk stack: rules R1 to R4, R5 (LLM check) optional | Safe Browsing stays an optional layer. Rules run locally. | Oct 24 about 2:50 PM, Ganesh |
| Extension risk events use the existing `background.js` WebSocket bridge for now | Not a direct `/api/event` POST: the extension has no `X-Ally-Token`, and a second transport is discouraged. Ben H adds the bridge-to-bus hookup. Revisit only if the bridge blocks the demo. | Oct 24 about 2:50 PM, Ganesh |
| Ally never closes a page or kills a process without an explicit yes | Applies to close page, close remote tool, open the usual site | Oct 24 about 2:55 PM, Ganesh |
| Ganesh owns all risk and scam work end to end, including wording and spoken text | Ben W's voice only provides `say()` and `ask_yes_no()` | Oct 24 2:55 PM change log |
| **R1 "high" needs a brand claim, a fullscreen page or an overlay signal as well as a phone number and an alarm word** | Phone plus alarm alone matched ordinary bank and shipping help pages. **Ganesh decided this; he said he would log it as a Decisions row. It is NOT yet logged.** | Oct 24 evening, Ganesh |
| Ally's AGENTS.md is the team's; we keep only an ownership line and the dashboard CSP rule | | Oct 24 evening, Ganesh |

**Ownership (Notion Current State change log, role swap at 10:20 AM, then 2:55 PM):** Ganesh owns UI, frontend, demo, overlay, dashboard Proactive panel, extension risk UI, consent screen, the Python risk logic, pitch and submission. Ben W owns voice (voice confirm, Cartesia, spoken offers and warnings) and admin. Ben H owns core brain, habits, contracts, integration and merges. Hayden owns `ally/senses/`.

**Open decisions that are not mine to make** (section 10): OS for the demo (Windows REPORTED, unconfirmed by Ben H), internal freeze time, the bridge message name, sponsor tracks.

---

## 4. Architecture

### 4.1 Data flow (target design)

```
extension/risk.js (content script, in the page)
  -> chrome.runtime.sendMessage -> background.js
  -> existing WebSocket bridge: {"type":"event","name":"page", url, title, text, fields}   [name is PROPOSED]
  -> ally/bridge.py _handle -> on_page hook (Ben H)                                        [hook exists only on a scratch branch]
  -> ally/risk adapter: R1 text, R3 domain, R4 fields -> dedupe, one warning per page      [adapter: older version on scratch branch; new one NOT BUILT]
  -> bus.publish("risk", Risk)  (ally/contracts.py, foundation)
  -> ally/proactive.py handle_risk: card + spoken warning, publishes "risk_warning"
  -> adapter subscribes "risk_warning" -> bridge.broadcast("showRiskBanner") -> in-page banner

Separately, every ~2 seconds: R2 watcher over a process snapshot -> RemoteEvent
  -> joined rule (remote tool running AND bank/lookalike/card page open) -> high
  -> ask-before-act: Coordinator.ask(Offer) yes/no -> only after "yes": close tab (extension) or end process   [NOT BUILT]
```

### 4.2 Design principle: pure rules, thin glue
Every rule module in `ally/risk/` is standard-library only and **imports nothing from `ally`** (no `contracts`, bridge or bus). They take plain input and return a small frozen dataclass. Reasons:
- The foundation patch is not on `main`, so the rules had to be buildable and testable without it.
- If the core is rebuilt, only the adapter changes.
- They are easy to test with realistic inputs.
There is deliberately **no `ally/risk/__init__.py`** on this branch (a namespace package), because the foundation patch ships its own starter `__init__.py`; see section 11 for what that means at merge.

### 4.3 Foundation interfaces the glue depends on (VERIFIED in a scratch copy with the patch applied)
- `ally/contracts.py`: `RISK_KINDS = ("scam_popup","untrusted_site","remote_access","payment_field")` (:25), `SEVERITIES = ("low","medium","high")` (:26), `Risk(kind, severity, evidence, url, app, source, ts)` (:90), `Offer(prompt, options, accommodation, context, reason, timeout, highlight)` with first-answer-wins `resolve()` (:131), `EventBus.subscribe/publish` (:184/:194), `parse_event` (:207).
- `ally/proactive.py`: `handle_risk` (:292) shows `ui.show_card(text, actions=("OK",))` and `voice.say(spoken)`; `Coordinator.ask(offer) -> choice` (:256) is the existing yes/no path; it imports `warning_for` from `ally.risk` (so the adapter `__init__.py` must provide it).
- Not in the codebase: any "close this tab" action (`extension/background.js` `ACTIONS` has `navigate` and `openUrl` only), any process-kill tool, and `psutil` in `requirements-optional.txt`.

---

## 5. Repository and branch map

The integration branch is **`ganesh/risk-handoff`** on the team repo `benhuang3/accessibility-anywhere` (the branch this document is on). **Team `main` and every other existing branch were not changed.** All the branches listed below also exist in a private backup repo, `GaneshKar1/ally-risk-work` (PRIVATE, VERIFIED; ask Ganesh for access). The code on the scratch branches is also included here as patch files under `handoff/patches/`, so nothing depends on that backup.

| Branch | Tip | What it is | Status |
|---|---|---|---|
| **`ganesh/risk-handoff` (this branch: `ganesh/risk-all` plus the handoff files)** | see `git log` | **Integration branch.** Team `main` (4702a36) + R1 to R4 + warning copy + joined rule + eval + AGENTS.md. | 81 risk tests pass (VERIFIED). **Start here.** |
| `ganesh/risk-r1` | 10a96b1 | R1 page-text scoring (`text.py`), plus the R1 "high" guard | merged into risk-all |
| `ganesh/risk-r2` | 7a45624 | R2 remote-tool watcher (`process.py`) | merged |
| `ganesh/risk-r3` | 47bc34e | R3 lookalike domains (`domains.py`) | merged |
| `ganesh/risk-r4` | a74ccb3 | R4 card fields (`fields.py`) | merged |
| `ganesh/risk-copy` | c27fa67 | Warning wording (`copy.toml`, `copy.py`) | merged |
| `eval` | 068aa26 | Eval set and runner | merged |
| `ganesh/ui` | 046c46c | Plan doc, team AGENTS.md plus our two sections, no-zoom test fix | merged |
| `scratch/foundation` | fc78910 | Foundation patch applied on `fef8cf4`, three conflicts resolved by hand | **scratch, not Ben H's integration** |
| `ganesh/risk` | ba374ed | First (Codex v1) rule set with `check_text/check_url/...` API, plus tests | superseded by r1 to r4 |
| `ganesh/ext-risk` | 700d368 | Extension risk script, banner, demo page, `pageRisk` event | scratch, NOT TESTED in Chrome |
| `ganesh/risk-int` | 8351e64 | v1 rules plus adapter `wire.py`, bridge hook, extension | scratch, partly superseded |
| `ganesh/dash` | de0352f | Everything on the foundation plus the dashboard Proactive panel | scratch; the dashboard panel is the part worth keeping |

`ganesh/dash`, `ganesh/risk-int`, `ganesh/ext-risk` and `scratch/foundation` form one chain on top of the foundation patch. They exist so the glue code (extension, bridge hook, adapter, dashboard) can be tested; their `ally/risk` rules are the old v1 API.

Other local-only items I did not push: the original `main` (fef8cf4), a stray harness branch equal to team `main`, and `CLAUDE.md` (personal Notion rules, deliberately excluded because the repo is public).

**Dry-run merges (VERIFIED with `git merge-tree`):** team `main` merged into `risk-all` with one conflict (`AGENTS.md`, add/add), now resolved on `main`; Hayden's PR #1 (`hayden/senses`, 18dbceb) merges into `risk-all` with no conflicts, but its senses layout differs from the foundation patch's (see section 11).

---

## 6. What is implemented

All paths are on `ganesh/risk-handoff`. Tests are run from the `tests` folder: `python -m unittest test_risk_r1 test_risk_r2 test_risk_r3 test_risk_r4 test_risk_copy test_risk_joined test_risk_eval` (81 tests, VERIFIED passing, no screen access).

### 6.1 R1: page text (`ally/risk/text.py`, 117 lines, 17 tests)
`score_text(text, url="", overlay_text="", fullscreen=False, has_tel_link=False, extra_allow=()) -> TextScore(score, severity, signals, evidence, allowlisted)`.
Implements the accepted spec: phone +3 (+1 for toll-free area codes), brand +2, alarm +2, call-to-action +2, payment +1, fullscreen +2, +1 per category also seen in overlay text. Severity: "high" needs score >= 7 **and** phone **and** alarm **and** (brand or fullscreen or overlay signal); otherwise score >= 4 is "medium"; below 4 is None. Allowlisted hosts (microsoft.com family, ftc.gov, fbi.gov, ic3.gov, cisa.gov, aarp.org, plus the user's bank domains) are never flagged. Evidence is fixed plain phrases: no page text, no digits. `redact()` and `normalize()` are exported.

### 6.2 R2: remote-access tools (`ally/risk/process.py`, 130 lines, 15 tests)
`match_remote_tool(name, original_filename="")`, `RemoteWatcher.observe(processes, now, last_scam_text_at, payment_page_open) -> [RemoteEvent]`, `snapshot_processes()`.
Exact normalized-name matching for AnyDesk, TeamViewer, UltraViewer, Quick Assist, ScreenConnect, ConnectWise Control, NetSupport, RustDesk, Supremo, Splashtop, LogMeIn, AeroAdmin, Ammyy, Zoho Assist, GetScreen. First call is a baseline; later calls report newly started tools. Severity "medium", or "high" when started within 10 minutes of a scam-text hit or while a payment/bank page is open. One alert per tool per snapshot. The collector uses `psutil` if installed, else Windows `tasklist`, else returns an empty list.
**Names are UNVERIFIED except one:** `QuickAssist.exe` exists in the installed Quick Assist package on Ganesh's laptop (VERIFIED, package `MicrosoftCorporationII.QuickAssist` v2.0.61); the *running* process name was never observed.

### 6.3 R3: lookalike domains (`ally/risk/domains.py`, 223 lines, 8 tests)
`check_domain(url, extra_brands=None, allow=()) -> DomainCheck(severity, reasons, evidence, host, registrable, brand)`.
28 brands (banks, Microsoft, Google, Amazon, PayPal, USPS and so on) with their real domains, about 40 allowlisted popular sites, an embedded subset of multi-part public suffixes. Reasons: homoglyph and mixed-script and punycode lookalikes (high), brand as a subdomain of an unrelated domain with a trust keyword (high), brand token, brand plus keyword, near-miss by edit distance for brand names of 6+ letters (medium), raw IP (medium). Brands' own country sites (google.co.uk, paypal.de, amazon.nl) are trusted for an explicit list of country suffixes only; free TLDs such as .tk, .cc, .ml are not.

### 6.4 R4: card fields (`ally/risk/fields.py`, 103 lines, 12 tests)
`detect_card_fields(fields) -> CardFields`, `score_payment(card, domain_known, insecure, lookalike, remote_session) -> PaymentRisk`.
Takes a list of field descriptors (`name`, `id`, `placeholder`, `label`, `autocomplete`, `type`, `maxlength`, `inputmode`; never values). Names match as token phrases ("credit_card_number", "Enter your card number", "Security code (CVV)"), not equality; "discard", "scorecard", "cardnotes" do not match. Severity: "high" with a lookalike or an active remote session; "medium" on an unfamiliar or insecure site; None on a known https site.

### 6.5 Joined rule (`ally/risk/joined.py`, 67 lines, 8 tests)
`joined_risk(remote_tools, url, card_present=False, extra_banks=None) -> JoinedRisk | None` and `running_tools(processes)`. Always "high" when a remote tool is running **and** the open page is a bank (14 bank brands plus the user's), a lookalike of one, or has card fields.

### 6.6 Warning wording (`ally/risk/copy.toml`, `copy.py`, 12 tests)
Six entries (scam pop-up, remote tool, lookalike with and without a known usual site, card details, and the joined "Let's pause") with title, body, spoken line and two actions; the first action is always the protective default. `render(key, **fields)` fills `{tool}`, `{host}`, `{usual_site}` and passes every filled value through `redact()` so a phone number cannot slip in. Tests enforce: hedged wording, no exclamation marks or shouting, no phone numbers, short spoken text, safe action first. Action ids (`close_page`, `close_tool`, `open_usual`, `go_back`, `explain`, `stay`) are what ask-before-act will run, only after a yes.

### 6.7 Eval set and runner (`ally/risk/eval.py`, `eval_cases.json`, 9 tests)
`python -m ally.risk.eval [--json] [--strict]` from the repo root. 25 scam texts, 25 benign pages, 10 lookalike and 10 real domains. See section 7.2 for results and the honest caveat. **Authorship note:** a Claude subagent drafted the data and runner and then stalled before writing tests; Ganesh's session finished the tests and fixes.

### 6.8 Older scratch work (not on `main`)

**Extension risk script and banner** (`ganesh/ext-risk`, `ganesh/dash`): `extension/risk.js` extracts page text (capped at 20,000 characters) and a field summary, sends it once on load and again on page changes (debounced, at most three resends) over the bridge; shows a calm banner in a closed Shadow DOM (large type, amber, no flashing, "Take me back to safety" and "I understand" buttons wired with `addEventListener`, text inserted with `textContent` only). `background.js` forwards a `pageRisk` message as `{type:"event", name:"page", ...}` and has a `showRiskBanner` action. `extension/demo/risk-demo.html` is a DEMO page with an invented brand. **NOT TESTED in real Chrome.** Known mismatch with the new R4: the old script sends a boolean summary `{payment, password, http}`, not field descriptors.

**Bridge hook and adapter** (`ganesh/risk-int`): a 15-line change to `ally/bridge.py` and `ally/agent.py` (commit 9a27c2c) adds an `on_page` callback and attaches `ally/risk/wire.py`, which scans pages, dedupes (once per site and kind per 30 minutes), publishes only the single most important risk per page scan, and rebroadcasts warnings to the in-page banner. It targets the v1 rule API and needs porting to the pure modules. VERIFIED end to end in Python with a fake agent (a demo page produced one scam warning, one banner call, no repeat on reload).

**Dashboard "Proactive help" panel** (`ganesh/dash`, commit de0352f; files `dashboard/index.html`, `app.js`, `style.css`): a live offer with large Yes/No buttons and countdown, signal meters with the fused score and the threshold line, a plain-language decision trace, learned habits with "Forget this site" and "Forget everything", and a "try it without a camera" section that injects demo events. No inline scripts or styles (CSP `script-src 'self'; style-src 'self'`). **VERIFIED:** `handoff/tools/dash_e2e.py` runs a real Agent and HTTP server and real Chromium: 17 of 17 checks (no CSP violations, no failed requests, narrow-screen layout, dark mode) and I read the screenshots. Two real bugs found by it were fixed (a `[hidden]` rule overridden by CSS; demo buttons wrongly picked up by the quick-actions handler). **Limits:** the signal meters parse the coordinator's log text ("lean 0.40 (fused 0.67 ...)") because structured fields are not logged; undo is per site, not per adjustment; the overlay on real Windows was never run.

---

## 7. Measured results

### 7.1 Tests
81 risk unit tests pass on `main`: R1 17, R2 15, R3 8, R4 12, copy 12, joined 8, eval 9 (VERIFIED). Plus, on the scratch chain at the time: 135 tests including the foundation's 96 (VERIFIED then), and the dashboard's 17 browser checks.
Do **not** run the repo's full older suites on a branch that lacks the no-zoom fix (commit `7974a9e`): tests that build a real Agent drive the real Windows magnifier, color filter and text size. This happened once in this session and zoomed the user's screen; `main` here has the fix.

### 7.2 Eval results (VERIFIED, `python -m ally.risk.eval`)
```
Text rules (R1)
  Recall on scam_text (medium or high): 100.0% (25/25)
  Recall of high where high expected:   100.0% (15/15)
  Precision (strict benign false positives): 89.3% (3 false positives of 20 strict benign)
  Benign cases wrongly high: 0
Domain rules (R3), lookalike = positive:  precision 100.0%  recall 100.0%  (TP=10 FN=0 FP=0 TN=10)
Failures: b16 small business with phone; b20 conference news with brands and alarm words; b22 safety advice on unknown host
          (each scored medium, 4 points, expected none)
```
**Read these numbers skeptically.** The cases were written by a model that had read the rules, so near-perfect recall is expected and says little about real scams. This is a smoke test. A useful next step is a case set written by someone who has not seen the rules.

### 7.3 Real defects found in Codex-written first drafts (and fixed)
Each Codex build passed its own tests and still had a real bug that only probing with realistic inputs exposed. Lesson: always probe with realistic cases.

| Module | Defect in the first draft | Fix |
|---|---|---|
| R1 | Any bare 10-digit run (order or tracking number) counted as a phone number, so "package blocked, contact support" scored high | Bare 10-digit runs count only if the area code is toll-free |
| R2 | Two processes of one tool (TeamViewer.exe plus TeamViewer_Service.exe) gave two alerts; Zoho Meeting (a meeting app) was on the watchlist | One alert per tool; removed |
| R3 | google.co.uk, google.de, paypal.de, apple.co.uk, amazon.nl, walmart.ca flagged "medium" | Explicit country-suffix allowance; free TLDs excluded |
| R4 | Field names matched only by exact equality: `credit_card_number`, "Enter your card number", "Security code (CVV)", `card_cvc` were all missed; non-list input crashed | Token-phrase matching; robust input handling |
| v1 rules (`ganesh/risk`) | usa.gov, apply.com, case.edu, chases.com flagged by edit distance to short brand names; any gift-card page with a number scored high; `amaz0n-help.com` missed | Edit distance only for brands of 6+ letters; scoring changed; digit tokens folded |

---

## 8. What is not implemented, and the plan

Estimates are for a person or agent working from the existing code. Dependencies are stated.

| # | Item | Est. | Depends on | Notes |
|---|---|---|---|---|
| 1 | **Adapter** `ally/risk/__init__.py`: build foundation `Risk` objects from `TextScore`, `DomainCheck`, `PaymentRisk`, `RemoteEvent`, `JoinedRisk`; `warning_for(Risk)` via `copy.render`; dedupe (port the old `Deduper`); one warning per page, highest severity first | 45 min | foundation merge | The foundation's `proactive.py` imports `warning_for` from `ally.risk`; the foundation's `tests/test_proactive.py` has risk tests against the old behavior and must be re-run |
| 2 | **Wiring**: process poll thread (R2 every ~2 s), shared state so the joined rule sees the current page and running tools, page events from the bridge | 45 min | #1, Ben H bridge hook | Port the v1 `wire.py` from `ganesh/risk-int` |
| 3 | **Ask-before-act**: `closeTab` action in `extension/background.js`; a risk Offer through `Coordinator.ask`; `taskkill` for a remote tool only after "yes"; log every action | 60 min | #1, #2, Ben H `on_risk` hook | Action ids are already in `copy.toml`. No existing close-tab or kill tool |
| 4 | **Extension update**: send field descriptors (never values) instead of the boolean summary; send overlay/modal text and a fullscreen flag; banner buttons trigger ask-before-act | 60 min | #3 | The current script is from the old design |
| 5 | **Demo pages** (invented brands only, labeled DEMO, localhost) and the 60-second run-of-show | 45 min | design pass | Example brands used so far: "Pinecrest Savings", "Larkspur PC Guard" |
| 6 | **Design pass** (Cursor Opus 5.5, read-only) for banner, ask-before-act confirmation, consent screen, demo pages | 30 min | user answering Cursor's workspace-trust prompt | Brief is in `handoff/specs/cursor-design-brief.md`; the run never started |
| 7 | **Consent and privacy screen** (dashboard): camera toggle, pause proactive help, "what stays here vs the cloud" | 45 min | design pass | Wording must match the real data path (section 2.3) |
| 8 | **Independent eval cases**, plus cases for R2, R4 and the joined rule; add pass thresholds | 45 min | none | See 7.2 |
| 9 | **Real-device verification**: Chrome extension end to end, Windows overlay, Quick Assist running process name, DPI scaling, mic contention with voice confirm | 60 min | demo laptop | Everything the Notion pages call OPEN |
| 10 | Optional: R5 LLM check on borderline pages; Google Safe Browsing layer with required attribution | 60+ min | none | Cut first |

**Cut order if late:** shrink the eval set; keep strings in one dict instead of a config file; drop the R4 maxlength rule; drop the joined rule; replace the live R2 watcher with `python run.py simulate risk remote_access high "..."`. Never cut: R1 with allowlist, R3, the in-page banner, ask-before-act.

---

## 9. Known limitations and risks (honest list)

- **False positives erode trust.** R1 still flags three plausible benign pages at medium (b16, b20, b22). "Medium" is a nudge, never a block. The unknown-bank help page ("if your account is locked, contact support at ...") is medium under the new guard; a bank not on the allowlist can still get a nudge.
- **R1 is English and US-only** (US phone formats; no homoglyph folding inside page text). `redact()` also rewrites 8-digit ISO dates as "a phone number" (cosmetic, only affects evidence strings). `score_text(None)` raises; the adapter must pass strings.
- **R2:** process names beyond Quick Assist unverified; `original_filename` is never collected (psutil cannot read it), so a renamed portable binary is missed; an installed TeamViewer service would trigger an "already running" notice at every agent start; the module does not poll by itself (the caller must call `observe` about every 2 seconds).
- **R3:** brand list of 28 is hand-curated; the public-suffix handling is an embedded subset; the user's own banks must be passed in (`extra_brands`/`allow`), ideally from `habits.py`.
- **The extension banner and bridge path have never run in real Chrome.** All bridge tests used a fake agent.
- **The overlay has never been run on Windows** (Notion: rendered on Linux/Xvfb). The dashboard panel's data fidelity depends on the coordinator's log text.
- **Privacy claims** need a network-capture check before any public wording about what stays local.
- **Repo visibility:** it is public now. Do not push `.env`, keys, or personal notes. Nothing in this branch set contains secrets (VERIFIED by a pattern scan across all pushed branches and a check for tracked env, key and credential files).

---

## 10. Open questions for the group (need an owner)

1. **Is scam protection part of any rebuild?** Ganesh mentioned that parts of Ally (notably zoom) may be rebuilt with the vision features kept. Where is that design, and who owns it? Until then, the rules stay pure and portable.
2. **Bridge message name:** this work uses `{type:"event", name:"page"}`. Notion's Build page mentions `riskAlert` but marks it SUPERSEDED. Ben H: confirm.
3. **Ben H's five hook requests** (also in `docs/RISK_PLAN.md`): bridge `on_page` hook; `attach(...)` in `agent.py`; an `on_risk` call in `handle_risk`; confirm `Coordinator.ask` is safe from another thread while no struggle offer is pending; `psutil` in `requirements-optional.txt` (or accept the `tasklist` fallback).
4. **Foundation merge:** the patch conflicts with `main` in `agent.py`, `config.py`, `requirements-optional.txt`; Hayden's PR #1 uses a different senses layout (`detectors.py`, `fusion.py`, `manager.py`) than the patch (`behavior.py`, `vision.py`). Ben H must reconcile.
5. **Demo OS:** Windows is assumed everywhere; Ben H has not recorded a confirmed demo laptop.
6. **Internal freeze:** midnight Saturday vs 2 AM Sunday; needs one agreed cutoff.
7. **Log the R1 guard decision** in the Notion Decisions table (Ganesh said he would).
8. **Make the repo public deliberately.** It is already public; confirm that is acceptable, or restrict it.

---

## 11. Integration guide (for the integrator)

### 11.1 Suggested order
1. Check out `ganesh/risk-handoff` (it already contains team `main` 4702a36, our rules and the resolved `AGENTS.md`). Run the 81 risk tests.
2. Land the foundation (Ben H or you). Expected conflicts: `ally/agent.py`, `ally/config.py`, `requirements-optional.txt` (additive; `scratch/foundation` shows one resolution, commit fc78910). Also reconcile with PR #1 (section 10.4).
3. **Create `ally/risk/__init__.py`.** The foundation adds one with starter functions (`check_text`, `check_url`, `check_process_names`, `warning_for`, `WARNINGS`). Replace its body with the adapter (section 8, item 1) so `proactive.py`'s `from .risk import warning_for` keeps working and `tests/test_proactive.py` risk tests pass or are updated deliberately.
4. Apply the bridge hook (`ganesh/risk-int`, commit 9a27c2c: `ally/bridge.py` `on_page`, `ally/agent.py` attach and stop).
5. Port `wire.py` to the new rules, then the extension changes (item 4), then ask-before-act (item 3).
6. Cherry-pick the dashboard panel from `ganesh/dash` (commit de0352f touches only `dashboard/`).
7. Re-run everything on the Windows demo laptop (item 9).

### 11.2 Mapping from the old v1 API to the pure modules
| Old (`ganesh/risk`, foundation starter) | New |
|---|---|
| `check_text(text, url)` -> list of `Risk` | `score_text(...)` -> `TextScore`; map non-None severity to `Risk("scam_popup", severity, evidence, url=url, source="text")` |
| `check_url(url)` -> list of `Risk` | `check_domain(url)` -> `DomainCheck`; `untrusted_site`, plus `remote_access` if the host is a remote-tool site (not in R3; keep the v1 host check or add a rule) |
| `check_process_names(names)` | `RemoteWatcher.observe(...)` -> `RemoteEvent`; map to `Risk("remote_access", severity, evidence, app=tool, source="process")` |
| payment field | `detect_card_fields` + `score_payment` -> `Risk("payment_field", ...)` |
| (none) | `joined_risk(...)` -> `Risk("remote_access"/"payment_field"...)` high, copy key `joined_remote_payment` |
| `warning_for(Risk)` | `copy.render(copy.copy_key(kind, usual_site, joined), tool=..., host=..., usual_site=...)` |

### 11.3 Restructuring the notes (suggestions, PROPOSED)
- **Decisions table:** add the R1 guard decision and the "AGENTS.md is the team's" decision; add "repo is public" as a fact, not a decision.
- **Tasks:** update Ganesh's risk rows with evidence from section 6 and 7 (commit hashes, 81 tests); keep REPORTED vs VERIFIED labels; do not promote a report to VERIFIED without the evidence listed here.
- **Current State page:** replace the "starter heuristics" and "no extension content script" statements with section 6 and 8 of this document; keep the open-validation items (section 9) open.
- **Build checklist (Research: Scam & Risk, section 10):** it still says the content script POSTs `/api/event`; that was superseded by the bridge decision.
- Remove or archive: the v1 scratch branches once the adapter lands.

### 11.4 How to verify after integrating
```
cd tests
python -m unittest test_risk_r1 test_risk_r2 test_risk_r3 test_risk_r4 test_risk_copy test_risk_joined test_risk_eval   # 81
cd ..                      # repo root
python -m ally.risk.eval   # precision and recall report
```
For the dashboard panel on the scratch chain: `python handoff/tools/dash_e2e.py <repo-root> <output-folder>` (needs `pip install playwright` and `python -m playwright install chromium`; it uses port 8799 and stubs tool calls, so it never touches the screen).
Do not start `python run.py` or `e2e_chrome.py` casually: they use port 8765 and the team's AGENTS.md says not to.

---

## 12. Try it yourself (no setup beyond Python 3.11)

From the repo root, in a Python shell:
```python
from ally.risk.text import score_text
from ally.risk.domains import check_domain
from ally.risk.fields import detect_card_fields, score_payment
from ally.risk.process import RemoteWatcher
from ally.risk.joined import joined_risk
from ally.risk.copy import render, copy_key

score_text("Microsoft Windows Security Alert! Your computer is infected. Do not close this window. Call support now at 1-800-555-0142")
check_domain("https://paypa1.com/login")
card = detect_card_fields([{"name": "credit_card_number"}, {"label": "Security code (CVV)"}]); score_payment(card, domain_known=False)
w = RemoteWatcher(); w.observe([{"name": "chrome.exe"}], now=0); w.observe([{"name": "AnyDesk.exe"}], now=5, payment_page_open=True)
joined_risk(("AnyDesk",), "https://www.chase.com")
render(copy_key("remote_access", joined=True), tool="AnyDesk")
```
Edit `ally/risk/copy.toml` to change wording; the copy tests check tone and safety rules.

---

## 13. How this was built (process notes)

- **Division of labor:** Claude Code planned, specified, reviewed and committed; Codex wrote first drafts of R1 to R4 in separate git worktrees from written specs (`handoff/specs/`), run headless in `workspace-write`; Claude read every diff, probed with realistic inputs and fixed real defects (section 7.3). A Claude subagent drafted the eval set.
- **Reviewer rule (Ganesh):** the reviewer must be a different model from the author, read-only, and no tool is ever run with approve-everything flags.
- **Tools, honestly:**
  - Codex runs through a ChatGPT plan login; about 3% of the allowance was used by two early runs.
  - **Cursor CLI** works (`cursor-agent.ps1`; Opus 5.5 is `claude-opus-5-5-high`; `--mode plan` and `--mode ask` are read-only; plain `-p` is NOT sandboxed). The design run was blocked at a workspace-trust prompt that only the user answers.
  - **`agy` (Antigravity CLI)** is installed and signed in, and its settings already allow `command(git diff)` and `command(git log)`, but headless review prompts are still denied for a tool it will not name. Unresolved.
  - **Gemini CLI** sign-in is rejected by Google ("client no longer supported").
  - An attempt to run work in the cloud with a remote agent **fell back to a local git worktree** and stalled after 10 minutes; partial output was salvaged. Do not rely on it to keep working with a closed laptop.
- **Mistakes worth knowing about:** unit suites run on branches without the no-zoom fix zoomed the user's real screen once (since fixed on all pushed branches); a shell quoting problem corrupted two edits during the session and was caught by the tests.

---

## 14. Appendix

### 14.1 Files in the `handoff/` folder
- `handoff/specs/`: the exact written briefs given to Codex for R1 to R4 (`codex-r1-spec.md` to `codex-r4-spec.md`), the earlier combined risk spec (`codex-risk-spec.md`), and the Cursor design brief (`cursor-design-brief.md`).
- `handoff/tools/dash_e2e.py`: the dashboard browser test. `handoff/tools/gen_eval_cases.py`: the script the eval subagent used to generate `eval_cases.json`.
- `handoff/patches/`: the code from the scratch branches, as patches in the order they apply on top of the foundation (see `handoff/patches/README.md`): `01-foundation-resolved` (foundation applied on `fef8cf4` with three conflicts resolved by hand), `02-v1-risk-rules`, `03-extension-bridge-hook-adapter`, `04-dashboard-panel`, and `05-extension-only` (the extension part alone).
- `handoff/foundation/0001-proactive-foundation.patch` and `FOUNDATION.md`: the foundation patch and team guide exactly as they were on Ganesh's machine. Do not apply the patch blindly (Notion says so too); it conflicts with current `main`.

### 14.2 Glossary
- **Risk:** the foundation's event for a risky moment (`kind`, `severity`, `evidence`, `url`, `app`).
- **Offer:** a yes/no question shown on screen and asked by voice; the first answer wins.
- **Joined rule:** a remote tool running while a bank or payment page is open.
- **Ask-before-act:** Ally takes no closing action without an explicit yes.
- **R1 to R4 / R5:** detection rules from the research page; R5 is the optional LLM check (not built).
- **Scratch chain:** the older branches built on the foundation patch (`scratch/foundation`, `ganesh/risk-int`, `ganesh/ext-risk`, `ganesh/dash`).

### 14.3 Other documents in the repo
- `docs/RISK_PLAN.md`: the original build plan written earlier today. This handoff supersedes it where they differ (notably: it predates the R1 guard decision, and lists items now built).
- `AGENTS.md`: the team's rules plus two additions (risk and UI ownership, dashboard CSP).
