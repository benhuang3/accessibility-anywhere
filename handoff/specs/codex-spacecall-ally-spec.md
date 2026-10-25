# Codex spec B: hook "Call Priya" up to Ally Call (SpacetimeDB), keeping Meet as the fallback

Worktree `wt-risk-all`, branch `risk-branch`.
- Do not commit, push or switch branches.
- Never read `.env` or `config.toml`.
- Do not start `run.py`.

Another Codex job is building the call app in `spacecall/`; don't touch it.

## Allowed files
- `ally/risk/wire.py`, `ally/risk/contact.py`
- `extension/background.js` (openCall only, plus the call-window close event)
- `ally/config.py` and `ally/default_config.toml` (new `[risk]` keys only)
- `tests/test_risk_contact.py`, plus a new `tests/test_risk_spacecall.py`
- `docs/RISK_HANDOFF.md` (a short subsection plus the config row)

## Contract with the call app (fixed; the other job builds the page)
- `spacecall_url`: an https base URL, for example `https://ganeshkar1.github.io/ally-call/`.
- **Room id:** `secrets.token_urlsafe(16)`, which gives 22 URL-safe characters. A fresh one per incident (per tap of the Call button,
  or per auto-alert).
- **Elder (PC) URL:** `<spacecall_url>#room=<id>&role=elder&name=<urlencoded helper name>&kind=<risk.kind>`.
- **Helper (phone) URL:** the same, with `role=helper`.
- The page itself creates the incident in SpacetimeDB and shows the status, so Ally makes **no** SpacetimeDB calls.

## Changes
1. **Config:**
   - `[risk] call_service`: `"meet"` (default) or `"spacecall"`.
   - `[risk] spacecall_url`: string, default `""`.
   - Validation: `spacecall_url` must be https and must not contain user info, a port, whitespace or a `#`.
   - `call_service = "spacecall"` with no valid `spacecall_url` falls back to Meet behavior. If there is no Meet link either, it falls
     back to "Tell".
2. **`contact.py`:**
   - `valid_call_url` also accepts URLs whose origin and path prefix match the configured `spacecall_url`. Pass it in; don't use
     globals.
   - The ntfy "Join the call" action and `Click` use the **helper URL**.
3. **`wire._tell` and auto-alert:**
   - With spacecall active:
     - make the room id;
     - open the elder URL with `self.call("openCall", {"url": elder_url, "name": name, "service": "spacecall"})`, falling back to
       `openUrl` as today;
     - push the helper URL.
   - Auto-alert and a later Call tap must use the **same room**. Keep it on the alert dict or offer.
   - The panel note stays as today.
   - Never put a room id in logs or on the bus. The bus `risk_contact` event gets `"service": "spacecall"` only.
4. **`background.js` `openCall`:**
   - Accept the spacecall origin as well as meet.google.com. The agent passes `service`; check the URL against an allow-list built
     from a `spacecallOrigin` the agent sends. Never accept arbitrary hosts.
   - Open the same borderless popup.
   - Don't inject `call.css` or the Meet skin for spacecall, because the page is already ours. Keep `call.js` matched to Meet only.
5. **Call-ended event** (requested by Ben H and Ben W: voice goes quiet while a call is open and needs to know when it ends):
   - When a remembered call window closes, send the agent a bridge event.
   - Check how `background.js` already sends events (`send({type: "event", name: ...})`) and how `ally/bridge.py` routes them. Read
     it, don't edit it.
   - If an existing event path reaches the bus, publish `risk_call_ended` with `{"service": ...}`. If it needs a `bridge.py` change,
     stop and describe the exact 3-line change in the report instead.

## Tests (about 10)
- Room id format, and a new id for each incident.
- Elder and helper URLs built correctly and URL-encoded.
- The push uses the helper URL.
- Auto-alert plus Call share a room.
- Fallback to Meet when `spacecall_url` is empty or invalid.
- Invalid `spacecall_url` values rejected (http, port, user info, `#`).
- `valid_call_url` accepts spacecall URLs only under the configured prefix.
- No room id on the bus.
- `node --check extension/background.js`.
- Run:
  - `cd tests && python -m unittest test_risk_contact test_risk_spacecall test_risk_wire test_risk_cooldown test_risk_actions test_config test_proactive`
  - then full discovery. Sandbox-only failures in speech and socket tests are known.

## Report
Files, test output, and whether the call-ended event needed a `bridge.py` change.
