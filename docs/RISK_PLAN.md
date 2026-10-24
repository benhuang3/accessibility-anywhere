# Risk and scam protection: build plan (Ganesh)

Owner: Ganesh, end to end (Notion Current State, change log 2:55 PM Oct 24). Scope: `ally/risk/`, `extension/` risk script and banner, warning copy, demo pages. Ben H wires the bridge onto the bus.

## Decisions this plan follows (Notion, Decisions table)

- **Accepted:** risk stack is rules R1-R4, R5 (LLM check) optional; Safe Browsing stays an optional layer. (Ganesh, Oct 24 about 2:50 PM)
- **Accepted:** extension risk events travel over the existing `background.js` WebSocket bridge for now, not a direct `/api/event` POST. (Current State change log, 2:55 PM; I read this one from the log line, not its own row)
- **Accepted:** Ally never closes a page or kills a process without an explicit yes, by voice or click. (Ganesh, about 2:55 PM)
- **Status:** Ben H still has to wire bridge messages onto the bus; not reported done. Foundation patch is not on `main`.

Note: "Research: Scam & Risk" (PROPOSED, not authoritative) says the content script should POST `/api/event`. That conflicts with the accepted bridge decision and the repo rule that content scripts never call the HTTP API. This plan follows the bridge.

## Interfaces I build against

`ally/contracts.py` is **not in the `ganesh/ui` checkout or on `origin/main`**. Everything below was verified in my scratch copy (`../wt-risk`, branch `ganesh/dash`, foundation patch applied, `contracts.py` and `proactive.py` unmodified). Line numbers will shift when Ben H merges. Lines marked (mine) are my additions, not the foundation's.

| What | Where | Detail |
|---|---|---|
| Risk kinds | `ally/contracts.py:25` | `scam_popup`, `untrusted_site`, `remote_access`, `payment_field` |
| Severities | `ally/contracts.py:26` | `low`, `medium`, `high` |
| Risk dataclass | `ally/contracts.py:90-113` | fields `kind, severity, evidence, url, app, source, ts`; `from_dict` :106, `to_dict` :112; invalid kind or severity raises `ContractError` |
| Offer (yes/no) | `ally/contracts.py:131` | `Offer(prompt, options=("yes","no"), accommodation, context, reason, timeout, highlight)`; `resolve(choice, by)` :150 (first answer wins); `wait()` :160 |
| Bus | `ally/contracts.py:176-205` | `subscribe(topic, fn) -> unsubscribe` :184; `publish(topic, msg) -> n` :194 |
| Event parsing | `ally/contracts.py:207` | `parse_event`: `{"type":"risk", ...}` becomes `Risk.from_dict` |
| Extension to agent | `extension/background.js:67` `send(msg)`; `:83` `onMessage` | WebSocket; messages `{type:"event", name:...}` |
| Agent to extension | `ally/bridge.py:162` `call(action,args)`; `:189` `broadcast(action,args)` | runs `ACTIONS[action]` in `background.js:320` |
| Bridge inbound events | `ally/bridge.py:103` `_handle`, event branch `:117` | today only focus/tab events; page events fall through (that is the gap) |
| Page event hook | `ally/bridge.py:44`, `:117-121`, `:141-145` (mine) | `on_page` callback, off the reader thread |
| Agent wiring | `ally/agent.py:70` bridge, `:82` bus, `:85` Coordinator, `:141` `call_tool`, `:338` `publish_event` | |
| Attach risk wiring | `ally/agent.py:90`, `:335`, `:401` (mine) | `attach(self)` after `proactive.start()`; stopped in `close()` |
| What happens to a Risk | `ally/proactive.py:292-300` `handle_risk` | logs, publishes `risk_warning`, then `ui.show_card(text, actions=("OK",), kind="risk")` :299 and `voice.say(spoken)` :300 |
| Ask yes/no | `ally/proactive.py:256` `Coordinator.ask(offer) -> choice` | publishes `offer`, `ui.offer`, voice ask in a thread (:278); blocks until answered; sets `self.pending` |
| Overlay | `ally/overlay/window.py:55` `show_card`, `:58` `offer`, `:148` `_do_offer` | the Yes/No bubble resolves the Offer; `show_card` has no answer path |
| Voice | `ally/voice_confirm.py:36` `say`, `:42` `ask(offer)` | |
| Extension banner path | `extension/risk.js`, `background.js:325` `showRiskBanner`, `:529` `pageRisk` (mine) | page text out, banner in |

**Not in the codebase today:** any "close this tab" action (`ACTIONS` has `navigate` :390 and `openUrl` :397 only), any process-kill tool (nothing in `ally/tools/`, `plugins/`), and `psutil` in `requirements-optional.txt` (it is installed on this laptop, v7.2.2).

### How a risk message flows once wired

```
extension/risk.js -> chrome.runtime.sendMessage -> background.js pageRisk -> send({type:"event",name:"page",...})
  -> bridge._handle (event branch) -> on_page(msg) [Ben H hook] -> ally/risk scan_page -> bus.publish("risk", Risk)
  -> Coordinator.handle_risk (card + spoken warning) -> bus "risk_warning"
  -> ally/risk/wire subscribes "risk_warning" -> bridge.broadcast("showRiskBanner") -> in-page banner
```

## What exists in ally/risk/ today vs R1-R4

State of the code on `ganesh/dash` (Codex wrote the first version, I reviewed it and fixed false positives; 39 risk tests, 135 total passing). **Not on `main`, not pushed.**

| Rule | Notion spec | In code today | Missing |
|---|---|---|---|
| **R1** scam text | score: phone +3, brand +2, alarm +2, action +2, payment +1, fullscreen +2; high if score >= 7 and phone and alarm; medium 4-6 is a nudge; weight overlay text; allowlist reference hosts | category-based scoring in `text.py`; phone/payment/gov categories; one `scam_popup` risk | **allowlist hosts** (FTC, Microsoft support pages, AARP...: today they would false-positive); overlay-text and fullscreen signals (content script does not send them); phone as a weighted signal per spec; benign-page tests |
| **R2** remote tools | psutil every 2 s; medium on start, high within 10 min of an R1 hit or with a bank page open; match exe name and original filename | `process.py` tool-name substrings; `wire.py` polls `tasklist` every 10 s; always high; reports tools already running at agent start | psutil; "started just now" vs "already running"; `client32`, `ConnectWiseControl.Client`; real process names verified here (Quick Assist); severity ladder |
| **R3** lookalike | registrable domain via public-suffix list; 20-30 brands incl. the user's banks; edit distance 1 (2 for 10+ letters); homoglyph skeleton; punycode; brand + login/secure under another domain; allowlist | `domains.py`: 10 brands, skeleton, token match, distance (brands of 6+ letters only), real-domain exemptions incl. amazon regional | public-suffix list (today a short heuristic); bigger brand list; user's banks from `habits.py`; allowlist of popular real sites |
| **R4** card fields | `autocomplete=cc-*`, name/id patterns, numeric maxlength 13-19; medium off-allowlist, high with R3 or R2 | `risk.js` detects fields; `fields.py` flags only on http pages or alongside another risk | maxlength rule; "unfamiliar site" test; R2 combination |
| Joined rule | remote tool running + bank/payment page = high | none (page scan and process poll do not share state) | shared state object |
| Ask-before-act | yes/no before closing | none: `handle_risk` shows an "OK" card | everything in the open gaps below |
| Warning copy | hedged, calm, one action, safe default | strings hardcoded in `ally/risk/__init__.py` | one config file; Notion section 5 wording |
| Eval | about 25 scam, 25 benign, 10 lookalike, 10 real; precision and recall | none | all |

## Open gaps / what I need from Ben H

I will not edit his files. Paste-ready request:

```
Ben H, 5 small changes needed for scam protection (all optional to review; my diff for 1-2 is commit 9a27c2c on ganesh/risk-int):
1. bridge.py _handle, event branch (~line 117): if msg["name"] == "page", call self.on_page(msg) on a thread (new Bridge attribute, default None).
2. agent.py: after self.proactive.start() add `from .risk.wire import attach; self.risk = attach(self)`; add self.risk.stop to the close() list.
3. proactive.py handle_risk (~line 292): after the card and say(), call self.on_risk(r) if it is set (default None), so ally/risk can ask before acting.
4. Confirm Coordinator.ask(offer) (proactive.py:256) is safe to call from a non-coordinator thread while no struggle offer is pending, or expose ask_yes_no(prompt, reason).
5. requirements-optional.txt: add psutil>=5.9 (R2), or say you prefer my tasklist fallback. Also OK to add a `demo_brands` key under [proactive]?
```

Items I own and need no one for: `closeTab` action in `extension/background.js`, the `risk.js` banner buttons, `taskkill` call inside `ally/risk/` (only after a yes), all rules and copy.

## Build order and estimates

Now is about 3 PM Sat. Feature freeze is midnight. About 6.5 hours of work leaves about 2 hours of buffer. Each step ends with tests run by me and a commit.

| # | Step | Est. | Who builds | Status |
|---|---|---|---|---|
| 1 | R1 scam-text scoring as a pure function: spec weights, allowlist, benign cases (FTC and Microsoft advice pages, news), tests | 45 min | Codex | partly built |
| 2 | R3 lookalike domains: public-suffix list, bigger brand list, allowlist, tests | 40 min | Codex | partly built |
| 3 | R4 card-field detection (maxlength rule, off-allowlist logic) | 30 min | Codex (rule), me (script part) | partly built |
| 4 | Extension script and banner through the bridge: overlay text, fullscreen flag, banner buttons | 60 min | me | mostly built, not tested in real Chrome |
| 5 | R2 process watcher: psutil, verify process names on this laptop including Quick Assist, severity ladder | 45 min | Codex (matching), me (names) | partly built |
| 6 | Joined rule: remote tool running + bank/payment page = high | 30 min | me (touches shared state) | not started |
| 7 | Ask-before-act: `closeTab` action; yes/no through `Coordinator.ask`; `taskkill` only after yes; log every action | 60 min | me | not started |
| 8 | Warning copy in one config file (`ally/risk/copy.toml`): hedged, calm, no phone numbers in our own warnings | 30 min | me | not started |
| 9 | Local demo pages (invented brands only) and the eval set with precision and recall printed | 75 min | Codex (eval data and runner), me (pages) | one page exists |

Total about 6 h 55 min. Steps 1, 2, 3 and 5 can run in parallel worktrees.

## Delegation and review

- **Codex builds** pure-function rules and tests: steps 1, 2, 3 (rule logic), 5 (matching) and the eval runner in 9. Each gets its own worktree on its own branch (for example `../wt-risk-r1`), restricted to `ally/risk/` and `tests/test_risk.py`, with: goal, allowed files, contract (`Risk` at `contracts.py:90`), acceptance tests, out of scope. Run: `codex exec -s workspace-write -C <worktree> "<spec>"` (worked earlier on this laptop, including running tests). The team's `scripts/dispatch.ps1` on `main` uses `agent/<name>` branches in `../ally-codex`; either works.
- **Claude (me)** writes steps 4, 6, 7 and 8, runs the tests, reads each diff, and is the only one who commits.
- **Reviewers (different model from the author, read-only):**
  - Code Codex wrote (`ally/risk/` rules): `agy` with the diff piped in, or me. `agy` is **currently blocked**: headless mode wants a "command" permission it cannot prompt for. Its only documented hint is `permissions.allow` with `command(<target>)`; I could not find the file location or full syntax, so I will not write the rule. If you want `agy`, tell me where its `settings.json` lives and I will propose the narrowest rule (`git diff` and `git log` only) and ask before saving. Until then I review Codex's code myself.
  - Code I write (extension script, wiring, ask-before-act): Codex read-only, with the diff piped in: `git diff main...HEAD | codex exec -s read-only -C <worktree> "Review against AGENTS.md and docs/RISK_PLAN.md. Concrete bugs and contract or CSP violations only, file and line. Do not edit files."`
  - Anything touching shared contracts or bridge messages: me plus one external reviewer (Codex read-only).
- Reviews run only at the 6 PM and 10 PM checkpoints, plus once on the extension content script. I verify each finding against the code and discard any without a real file and line, and I tell you what I discarded.
- No `--dangerously-skip-permissions` or equivalent, for any tool.

## Rules for this work

- No API keys in the repo. No live scam sites. Demo pages are served from localhost only, labeled DEMO, with invented brands (not Microsoft, not a real bank).
- Ally's own warnings never contain phone numbers (demo pages may show a fictional one).
- Hedged, calm wording; the safe choice is the default button.
- Rules stay portable: pure functions in `ally/risk/` that return `Risk`; bus and bridge code is confined to `ally/risk/wire.py`, so a rebuilt core only needs a new adapter.

## Cut list if I run late (in this order)

1. Shrink the eval set to about 10 / 10 / 5 / 5 (keep precision and recall printed).
2. Copy config file: keep strings in one Python dict.
3. R4 maxlength rule and the "unfamiliar site" refinement.
4. Joined rule (step 6).
5. R2 live process watcher: fall back to `python run.py simulate risk remote_access high "..."`.
6. Never cut: R1 with allowlist, R3, the in-page banner, ask-before-act.
