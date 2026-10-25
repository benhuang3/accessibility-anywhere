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
  the lookalike bank demo, and a normal site (no warning). Full discovery: 533 tests, all OK on Windows (section 9).
- **Numbers (VERIFIED).** Our rules set contains 25 scam texts, 25 benign texts, 10 lookalike addresses and 10 real addresses
  (`tests/test_risk_eval.py`). On 14 rendered pages: rules alone **9/14**, rules + second opinion **13/14**; the injected instructions
  in s6 to s8 did not suppress the scam warnings (`handoff/results/guardian_eval.txt`). **False alarms on 500 popular sites** (rules
  only): **3 (0.6%, none high)**; 15 would get a model review (`handoff/results/fp_sweep.txt`). These are small, self-written evals.

## 2. What the user sees

1. A risky page appears. Within a few seconds a panel slides in at the top of the screen (no animation): "Ally safety check", a bold
   title ("This looks like a scam."), two sentences saying what Ally sees and what is safe to do, the question, and two large buttons
   **Yes, close it** / **No, keep it**. Ally also speaks the warning and the question. The current `NullVoice` cannot hear an answer.
2. If the model marked the red flag (for example the fake phone number), a thin amber box outlines it on screen.
3. Yes closes the tab (via the extension) or the remote-control tool (only processes that match a known remote tool). No, timeout or
   dismiss changes nothing. The panel never takes keyboard focus from the app in use. Answer by tapping **Yes** or **No**, or with
   **Enter** / **Esc**. Voice answers are still open work, owned by Ben W.
4. A page that went full screen is put back to a maximized window first, so it cannot hide the warning.
5. One warning per page per 30 minutes, so the same site does not nag.
6. **Pause:** when a remote tool is running and a bank page is open, the panel counts down for **30 seconds** before **Yes** can be
   pressed (`wire.PAUSE_SECONDS`; the joined-risk offer also covers lookalike-bank and card pages).
7. **Call or Tell a trusted person:** high warnings have a third button when a trusted contact is configured. With `contact_call_url`
   set to the helper's standing Google Meet room, **Call** opens it in Chrome and sends an ntfy.sh push with a **Join the call** button.
   Without a valid call link, **Tell** only sends the push. The push says the kind of risk, never the page address or anything from
   the page. Setup uses `[risk]` keys `trusted_contact`, `contact_topic` and `contact_call_url`; accepted call hosts are listed below.

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
| `ally/risk/process.py` | R2 remote-access tools (AnyDesk, TeamViewer, Quick Assist, ScreenConnect, ...), newly-started detection; `_lolrmm` adds catalogue names and `_NOT_REMOTE` excludes assistive tools and family meeting software | `test_risk_r2`, `test_risk_contact` |
| `ally/risk/remote_tools.json` | Executable names for 240 tools, derived from the [LOLRMM project](https://lolrmm.io), Apache-2.0; built-in hand-checked names take priority | `test_risk_contact` |
| `ally/risk/joined.py` | Remote tool running + bank, lookalike-bank or card page = high | 8 |
| `ally/risk/behavior.py` | Page tricks from the extension: Back-button trap, exit prompt, full screen, keyboard/pointer lock, blocked right-click, dialog or pop-up spam. One trick never flags; combinations do. | 7 |
| `ally/risk/soft.py` | Weak signs (remote-access request, secrecy, gift-card/refund pressure, arrest threats). Two of them earn a model review; they never warn on their own. | 4 |
| `ally/risk/guardian.py` | Tier 1 second opinion. Consent gate, `Budget` (daily, hourly, per page), cache, strict JSON parsing, prompt-injection-aware prompt, thinking disabled so the JSON is not cut off. Model call is injectable; all tests use a fake. | 29 |
| `ally/risk/wire.py` | Glue: page and process events to `Risk` on the bus, dedupe, one warning per scan, second-opinion policy (below), Yes/No offer, joined-risk pause, Call/Tell action, evidence highlight, full-screen exit, `attach(agent)` | `test_risk_wire`, `test_risk_second_opinion`, `test_risk_contact` |
| `ally/risk/contact.py` | Fixed risk-only alert text, ntfy.sh delivery, validated standing-call links; no page address or page content in the push | `test_risk_contact` |
| `ally/risk/actions.py` | Ask-before-act: question per kind; close tab; close only processes matching a known remote tool | 20 |
| `ally/risk/__init__.py` | `warning_for(risk) -> (text, spoken)` used by `handle_risk`; model explanation when the second opinion ran | via wire tests |
| `ally/risk/copy.toml`, `copy.py` | All warning wording in one file; spoken lines never ask the question (the offer does) | 13 |
| `ally/risk/eval.py`, `eval_cases.json` | Rules eval (25 scam, 25 benign, 10 lookalike, 10 real): `python -m ally.risk.eval` | 9 |
| `ally/overlay/` | One safety panel (card and question merged in either arrival order), info cards, struggle-offer bubble, highlight; DPI-scaled; never takes focus; stays on top of full-screen windows | 36 |
| `extension/risk.js`, `risk-main.js` | Page report (text, field descriptors, overlay text, full screen, signals); behavior recorder in the page world; banner fallback | NOT TESTED by unit tests; VERIFIED live in Chrome |
| `extension/background.js` | Forwards page reports; actions `closeTab`, `exitFullscreen`, `showRiskBanner` | live-tested |
| `extension/demo/` | `risk-demo.html` (invented "Pinecrest Savings"), `trap-demo.html` + `.js` (invented "Harbor Light Bank", scam tricks on a button) | live-tested |
| `handoff/tools/guardian_eval.py` | Rules vs rules + vision on 14 rendered pages (real API calls), including injected instructions in s6 to s8 | results in `handoff/results/guardian_eval.txt` |
| `handoff/tools/fp_sweep.py` | False-alarm sweep over popular sites (headless Chrome with the behavior recorder injected, no API calls); `--rejudge` re-scores saved pages offline | `handoff/results/fp_sweep.txt`, `fp_sweep_sites.json` |

**Catalogue exclusions (`process._NOT_REMOTE`).** Screen readers and magnifiers (NVDA, Narrator, JAWS, ZoomText, SuperNova and
Fusion), Dragon dictation and Zoho Meeting are excluded. Some catalogue entries reuse assistive software; people may depend on it
to use the computer or on meeting software to speak with family. Ally must not offer to close those tools.

**Prompt-injection eval.** s6 plants instructions in a scam page, s7 puts them in near-background text, and s8 uses tiny and off-screen
text. All still warn with the second opinion. The current overall score is 9/14 for rules alone and 13/14 with AI; the benign small-shop
checkout still warns (`handoff/results/guardian_eval.txt`). These cases do not establish resistance to every prompt injection.

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
trusted_contact = ""    # helper's display name; requires a valid contact_topic
contact_topic = ""      # helper subscribes in the ntfy app; choose a long random name
contact_call_url = ""   # optional standing video-call room; empty means push only
```
The screenshot covers the whole primary screen and leaves the machine only for a flagged page. Counters persist in
`state/guardian_budget.json` and appear in `GET /api/status` under `risk`. Set a spend limit on the API key in the Anthropic Console too.

Call links must use HTTPS. `contact.CALL_HOSTS` accepts `meet.google.com`, `zoom.us`, `whereby.com`, `teams.microsoft.com`,
`teams.live.com` and `meet.jit.si`, including their subdomains. An invalid link falls back to **Tell**; without a name and valid topic,
there is no contact button. The defaults above come from `ally/default_config.toml`.

## 6. Changes to files owned by Ben H (please review)

All small, all on `risk-branch`, all covered by tests:

| File | Change | Why |
|---|---|---|
| `ally/bridge.py` | `self.on_page = None`; in `_handle`, an `event` named `page` goes to `on_page` on its own thread (errors logged), and skips focus bookkeeping | The extension's page reports reach the risk wire. `tests/test_risk_bridge.py` (4) |
| `ally/agent.py` | `self.risk = None`; after `self.proactive.start()`, `self.risk = attach(self)` in a try/except; `self.risk.stop` added to `close()`; `"risk": self.risk.info()` in status | Starts the risk wire with the coordinator; a failure cannot stop Ally starting |
| `ally/config.py` | `"risk": {"screen_review": bool, "daily_calls": int, "hourly_calls": int}` in SPEC and two RANGES entries | The `[risk]` section validates |
| `ally/default_config.toml` | `[risk]` section (above), off by default | Opt-in screen review |
| `ally/config.py`, `ally/default_config.toml` | `config.toml` keys `[risk] trusted_contact`, `contact_topic`, `contact_call_url`: validation and empty defaults | Configure the helper's name, ntfy delivery and optional standing call; `screen_review` stays false by default |

No changes to `proactive.py` or `contracts.py`. The coordinator's `handle_risk` already calls `warning_for`, which now exists.

## 7. How to try it (Windows)

1. `.env` with `ANTHROPIC_API_KEY` (only for the second opinion). In `config.toml`: `[risk]` `screen_review = true`.
2. `python run.py agent`, then load or reload the unpacked `extension/` folder in `chrome://extensions`.
3. In another terminal: `cd extension/demo && python -m http.server 8000`.
4. Open `http://localhost:8000/trap-demo.html`, click **Run the scam tricks**: Chrome leaves full screen, the panel appears, **Yes** closes the tab.
5. Open `http://localhost:8000/risk-demo.html`: panel with the model's explanation. Open a normal site: nothing.
6. Without Chrome: `python run.py simulate risk scam_popup high "page says call support"`.
7. Evals: `python -m ally.risk.eval` (rules, free); `python handoff/tools/guardian_eval.py . <outdir>` (vision; saved run used 11 API calls).
8. To see a page warn again within 30 minutes, restart Ally (dedupe is in memory).

## 8. Known limitations (honest)

- **Live-tested on one laptop only** (Windows 11, 200% scaling, Chrome). Not tested on other DPIs, multiple monitors, Edge, or macOS/Linux.
- **Remote-tool closing** has never been run against a real AnyDesk or TeamViewer; process names are partly unverified (Quick Assist confirmed).
- **False alarms on real sites** were measured on home pages only (500 sites): 3 remain (ubuntu.com, archive.ph, archive.is: a brand and
  a scare word on the same page, medium, which the second opinion can clear). Deeper pages (help centres, checkout, account pages) are
  not swept yet. The behavior recorder flagged none of the 500 sites.
- **Evals are small and self-written** (14 vision pages; rules-set sizes listed above); no third-party scam corpus yet.
- **Only browser and remote-tool scams.** Phone-call-first scams, romance and investment scams, and grandparent scams are not covered.
- **Second opinion needs the internet and an API key**; without them the rules still work.
- **Struggle offers vs. risk offers** share the coordinator's single pending offer; a risk question can replace a struggle question.
- The in-page banner is now only a fallback when the overlay is not running.
- **Trusted-contact privacy:** ntfy topics are only as private as their name; anyone who knows the name can read the alerts.
- **Calls use a standing room**, not a fresh private Meet link for each warning.
- **Without an overlay, the spoken warning and question can still race** and overlap.
- **Voice answers are open (Ben W):** `NullVoice` cannot hear "yes". Support for yes/no answers to `Coordinator.ask` offers is needed;
  until then, the spoken line should end with "Tap Yes or No" (wording request only; no code change here).

## 9. Merge checklist (for the integrator)

1. `git fetch && git merge origin/risk-branch` into `main` (VERIFIED: merges cleanly; `main` has not moved since 6966696).
2. Review section 6 changes.
3. Run: `cd tests && python -m unittest discover -p "test_*.py"`. On Ganesh's Windows laptop it printed `Ran 533 tests ... OK`.
   (Inside a sandboxed agent, the Windows speech and senses-lab socket tests fail because the sandbox blocks SAPI and sockets.)
4. `python run.py check`, then the section 7 live test.
5. `handoff/` can stay until after the demo (specs, eval tools and results); the old patches in `handoff/patches/` are superseded by this branch.

## 10. How this was built

Claude Code planned, wrote specs, reviewed every diff and probed with realistic inputs; Codex (GPT models) built the pure modules,
`guardian.py` and the panel state machine; Cursor (Composer 2.5) built the first overlay and the behavior recorder. Every delegated build
had at least one real bug found in review or live testing (examples: JSON cut off by hidden thinking tokens, a cache checked after the
budget, the Yes/No bubble hidden behind its card, text at half size on a 200% screen, the panel stealing focus). Specs are in `handoff/specs/`.
