# Codex spec: ally/risk/__init__.py (warning_for) and ally/risk/wire.py

You work in the git worktree `wt-risk-all` (branch `risk-branch`). Do not commit, push, merge or switch branches.

## Goal
Real risk warnings must flow onto Ally's event bus with the wording from `ally/risk/copy.toml`.
Today `Coordinator.handle_risk` (ally/proactive.py) does `from .risk import warning_for` inside a try/except. That import fails
(no `ally/risk/__init__.py`), so users get a generic fallback text. Fix that (step 1) and add the adapter that turns page data and
running processes into `Risk` objects on the bus (step 2).

## Allowed files (create only these)
- `ally/risk/__init__.py`
- `ally/risk/wire.py`
- `tests/test_risk_wire.py`
Everything else is read-only for you. Do not edit `ally/contracts.py`, `ally/proactive.py`, `ally/agent.py`, `ally/bridge.py`
or any existing risk module. If you need a change there, stop and say so in your report.

## Read first
`ally/contracts.py` (Risk, RISK_KINDS, EventBus), `ally/proactive.py` (`handle_risk`, NullUI, NullVoice, Coordinator),
`ally/risk/copy.py` + `copy.toml`, `text.py`, `domains.py`, `fields.py`, `process.py`, `joined.py`, and `tests/test_proactive.py`
for the test style. Tests run with `cd tests && python -m unittest test_risk_wire` and start with `from helpers import ROOT  # noqa: F401`.

## Step 1: `ally/risk/__init__.py`
Keep it tiny. Export `warning_for(risk) -> tuple[str, str]` (text, spoken), the exact call in `handle_risk`.
- Choose the copy key with `copy.copy_key(kind, usual_site, joined)`:
  - `joined` is true when `risk.source == "risk.joined"` (and kind is `remote_access`).
  - `usual_site`: for `untrusted_site`, use `domains.check_domain(risk.url).brand` mapped to that brand's first real domain in
    `domains.BRANDS` (empty string when there is no brand, which selects `untrusted_site_generic`).
  - Fields passed to `copy.render`: `tool=risk.app` (the remote tool name), `host=domains.host_of(risk.url)`, `usual_site`.
- `text` = title + " " + body from the WarningCopy; `spoken` = WarningCopy.spoken.
- NEVER raise. Unknown kind or any error returns a short safe sentence like
  `("This page may not be safe. Please be careful.", "This page may not be safe. Please be careful.")`.
- Do not import `ally.contracts` here (keeps `import ally.risk` cheap). Importing the pure rule modules lazily inside the
  function is fine. Do not shadow the stdlib `copy` module: use `from . import copy as warning_copy` or similar.
- The package currently has NO `__init__.py` on purpose. Adding this one is the point of this task. Keep it free of side effects.

## Step 2: `ally/risk/wire.py`
This module MAY import `ally.contracts.Risk` and `EventBus` (allowed, the bus is on main). No other `ally.*` imports except
`ally.risk.*`.

### `scan_page(page: dict) -> list[Risk]`
`page` is what the Chrome extension sends: `url`, `title`, `text` (visible text), optional `overlay_text`, `fullscreen` (bool),
`has_tel_link` (bool), `fields` (list of dicts with keys like name/id/placeholder/label/type/autocomplete; anything else, or None,
means no fields). Be defensive: non-dict page, missing keys, wrong types, huge text (cap `text` at 20000 chars) never raise.
Return a list of `Risk` (sorted most important first: severity high before medium, then kinds in the order scam_popup,
remote_access, untrusted_site, payment_field):
- R1 `text.score_text(text, url, overlay_text, fullscreen, has_tel_link)`: severity None -> nothing; else
  `Risk("scam_popup", severity, evidence=ts.evidence, url=url, source="risk.text")`. Skip entirely when `ts.allowlisted`.
- R3 `domains.check_domain(url)`: severity not None -> `Risk("untrusted_site", severity, evidence, url, source="risk.domain")`.
- R4: `fields.detect_card_fields(fields)`; if present, `fields.score_payment(card, domain_known=<host is a known brand or in
  domains.TOP_SITES-style allow>, insecure=url starts with "http:", lookalike=<R3 flagged this page>, remote_session=<a remote
  tool is running now, from the last process snapshot the wire holds>)`; severity not None -> `Risk("payment_field", ...)`.
  Read `domains.py` to decide what "domain known" means: use the same notion it uses to decide a host is a real brand/top site.
- Every evidence string goes through `text.redact()` and is truncated to 200 chars. Evidence never contains digits runs of 7+ or URLs.
- Severity mapping: rule severities are "medium"/"high" already; pass through. Never emit "low".

### `class Deduper`
`Deduper(window_s=1800.0, clock=time.time)`; `fresh(risk) -> bool`: True the first time a (host, kind) is seen within the window,
False afterwards, True again after the window. Host from `domains.host_of(risk.url)`; for risks with no url (remote_access) key on
(app, kind). Bound memory: drop expired keys on each call.

### `class RiskWire`
`RiskWire(bus, snapshot_fn=process.snapshot_processes, poll_seconds=10.0, clock=time.time, deduper=None)`
- `on_page(msg: dict) -> None`: msg is the bridge event (`{"type":"event","name":"page", url, title, text, ...}`) or the plain page dict.
  Remember `last_page` (url, whether card fields present, and the time of the last scam-looking text, i.e. a scam_popup risk).
  Run `scan_page`, drop the non-fresh ones, publish ONLY the first remaining one with `bus.publish("risk", risk)`
  (one warning per scan; a card plus a spoken line for every signal on a page is too much for the user).
  Also run `joined.joined_risk(running_tools(last snapshot), url, card_present)`; a JoinedRisk becomes
  `Risk("remote_access", "high", evidence=jr.evidence, url=url, app=", ".join(jr.tools), source="risk.joined")` and takes
  priority over the others when present and fresh.
- `poll_once() -> None`: `snap = snapshot_fn()` (catch all exceptions, log at debug, return), store it as the last snapshot, run
  `RemoteWatcher.observe(snap, now, last_scam_text_at, payment_page_open)` where `payment_page_open` is true when the last page had card
  fields or is a bank/lookalike page. For each event: `Risk("remote_access", event.severity, evidence=event.evidence, app=event.tool,
  source="risk.process")`, deduped, published. Also re-run the joined rule against the last page when a tool appeared.
  The first poll only records the baseline (RemoteWatcher already does this).
- `start()` / `stop()`: a daemon thread calling `poll_once` every `poll_seconds` until stopped; `stop()` is idempotent and joins
  briefly. Exceptions in the loop are logged, never raised.
- All state behind a `threading.Lock`; `on_page` is called from bridge threads.

### `attach(agent) -> RiskWire`
`wire = RiskWire(agent.bus)`; if `agent.bridge` has an attribute `on_page`, set `agent.bridge.on_page = wire.on_page` (this does
nothing until the bridge hook lands, and must not error without it); `wire.start()`; return wire.

## Tests: `tests/test_risk_wire.py` (about 20)
- warning_for: every kind in RISK_KINDS returns two non-empty strings with no digit runs of 7+ and no "!"; joined uses the
  joined_remote_payment wording; an untrusted_site lookalike of a brand mentions the real domain; garbage input (None, wrong kind,
  object without attributes) returns the safe sentence and never raises.
- Through the real coordinator: build `Coordinator(bus, call_tool, HabitMemory(tmp_path), context_fn, ui=NullUI())` as in
  `tests/test_proactive.py`, publish a Risk, assert `ui.shown` has a card whose text equals `warning_for(risk)[0]` (NOT the fallback
  "This looks risky").
- scan_page: a tech-support scam page (use fictional 555-01xx numbers, host `*.example`) gives a high scam_popup; `paypa1.com`
  gives untrusted_site; a checkout page with card fields on an unknown host gives payment_field; a normal recipe blog and
  `support.microsoft.com` give []; junk input gives [].
- Deduper window with a fake clock; RiskWire.on_page publishes once per page and again only after the window; one risk per scan;
  poll_once with a fake snapshot fn (baseline poll publishes nothing; AnyDesk appearing publishes remote_access; the same tool does
  not repeat); joined risk when AnyDesk is running and a chase.com page arrives; a snapshot fn that raises does not break poll_once;
  attach on a fake agent object with and without `bridge.on_page`; start/stop are idempotent.
- Purity guard: `ally/risk/__init__.py` has no `ally.contracts` import.

## Constraints
Python 3.11+, standard library only, match surrounding code (type hints, small functions, comments only where the reason is not obvious).
User-facing text is plain and short. Phone numbers in tests are fictional (555-01xx). No real brand pages as scam hosts.
Do not run `run.py`, `e2e_chrome.py` or the whole suite. Run only `cd tests && python -m unittest test_risk_wire test_proactive`.

## Final report
List files changed, test results, and anything you could not do or assumed.
