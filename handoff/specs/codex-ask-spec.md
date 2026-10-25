# Codex spec: ask-before-act for risk warnings (ally/risk/actions.py + wire.py changes)

Worktree `wt-risk-all`, branch `risk-branch`. Do not commit, push, merge or switch branches.

## Goal
When a risk warning is shown, Ally asks "Shall I close it?" with a Yes/No offer and acts ONLY after a yes. Nothing is ever
closed without an explicit yes. Today `Coordinator.handle_risk` (ally/proactive.py) shows a card with OK and speaks `warning_for`;
`RiskWire` (ally/risk/wire.py) publishes `Risk` onto the bus. Read both, plus `ally/contracts.py` (Offer, Accommodation),
`ally/risk/copy.toml` (action ids: close_page close_tool open_usual go_back explain stay), `ally/risk/process.py`
(REMOTE_TOOLS, match_remote_tool, snapshot_processes).

## Allowed files
- `ally/risk/actions.py` (new)
- `ally/risk/wire.py` (edit)
- `tests/test_risk_actions.py` (new), `tests/test_risk_wire.py` (edit only if an existing test must change)
Do not edit proactive.py, contracts.py, agent.py, bridge.py or the extension. Stop and report if you need to.

## `ally/risk/actions.py`
Pure/side-effect-isolated, standard library only, all side effects through injected callables so tests never touch real tabs or processes.
- `ask_for(risk) -> tuple[str, str] | None`: the (prompt, action_id) to offer, or None when no action applies:
  - kind `scam_popup`, `untrusted_site`, `payment_field` -> ("Want me to close this page?", "close_page")
  - kind `remote_access` -> (f"Want me to close {risk.app or 'the remote-control tool'}?", "close_tool"); `risk.app` goes through `text.redact` and is
    capped at 40 chars.
  - anything else -> None.
- `close_page(call, url) -> bool`: `call("closeTab", {"url": url})`; True when the reply shows it closed at least one tab
  (`reply.get("closed", 0) > 0`); never raises (False on any error). `call` is `bridge.call`-shaped: `call(action, args)`.
- `close_tool(tool_label, snapshot=snapshot_processes, kill=<default>) -> int`: close ONLY processes that
  `process.match_remote_tool(name)` maps to `tool_label` (so a hostile label cannot target other processes). Get names from `snapshot()`;
  default `kill` runs `subprocess.run(["taskkill", "/IM", name, "/F"], capture_output=True, timeout=10)` on Windows only (no shell=True; returns
  without doing anything on other OSes); returns how many were killed; never raises. Unknown label or no match -> 0.
- `perform(action_id, risk, call, snapshot=..., kill=...) -> bool`: dispatch `close_page`/`close_tool`; any other id -> False.

## `ally/risk/wire.py` changes
- `RiskWire` gets an optional `ask` collaborator: `RiskWire(bus, ..., ask=None)` where `ask` is a callable `ask(offer) -> choice` (the
  coordinator's `Coordinator.ask`) and `call` (bridge.call-shaped, default None).
- New method `on_risk_warning(msg)`: subscribed to bus topic `"risk_warning"` in `start()` (and unsubscribed in `stop()`).
  `msg` = `{"risk": risk.to_dict(), "text": ...}` (see `handle_risk`). It must:
  1. broadcast the in-page banner when `call`/a `broadcast` callable exists: `broadcast("showRiskBanner", {"url": risk.url, "kind": risk.kind, "text": msg["text"]})`
     (inject `broadcast` too; default None = skip; never raises);
  2. build `(prompt, action_id) = ask_for(risk)`; if None, stop; else run in a daemon thread (the coordinator worker must not block, and
     `ask` blocks until answered): `offer = Offer(prompt, context="risk", reason=risk, timeout=20.0)`; `choice = ask(offer)`;
     if `choice == "yes"` call `actions.perform(action_id, risk, call, ...)` and publish `bus.publish("risk_action", {"action": action_id, "ok": ok, "kind": risk.kind})`.
     For "no", "timeout", "dismissed": do nothing, publish nothing.
  3. Never ask twice for the same (host, kind) while an ask is pending (a simple in-flight set under the lock), and skip asking when `ask` is None.
- `attach(agent)` passes `ask=agent.proactive.ask`, `call=agent.bridge.call`, `broadcast=agent.bridge.broadcast`, each only when the agent has them
  (use getattr; a fake agent without them must still work).
- `Risk` from dict: use `Risk.from_dict`.

## Tests `tests/test_risk_actions.py` (about 18)
- `ask_for` for each kind; remote_access prompt names the tool; digits/long names are redacted/capped; unknown kind -> None.
- `close_page` True/False/exception; `close_tool` kills only matching processes (fake snapshot with chrome.exe, AnyDesk.exe, TeamViewer.exe and a
  fake `kill` recording names; label "AnyDesk" kills only AnyDesk.exe; label "chrome" or "; calc" kills nothing); non-Windows does nothing
  (patch `sys.platform`).
- Through a real `EventBus` + fake `ask`: yes -> `perform` called and `risk_action` published; no/timeout/dismissed -> nothing closed and nothing published;
  `ask=None` -> nothing; two warnings for the same page while the first ask is pending -> one ask; banner broadcast called with the right args; broadcast raising
  does not break the ask flow; `start()`/`stop()` subscribe and unsubscribe (publishing "risk_warning" after stop does nothing).
- Safety: `perform("close_page", ...)` with an unknown action id returns False and calls nothing.

## Constraints
Python 3.11+, stdlib only, match surrounding style, plain short user text. Fictional 555-01xx numbers and `.example` hosts in tests.
Run only: `cd tests && python -m unittest test_risk_actions test_risk_wire test_proactive`. Do not run run.py or e2e scripts.
Final report: files changed, test results, anything not done.
