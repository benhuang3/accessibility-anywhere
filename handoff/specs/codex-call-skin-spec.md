# Codex spec: Ally-framed video call (Meet re-skin) + auto-alert

Worktree `wt-risk-all`, branch `risk-branch`.
- Do not commit, push, merge or switch branches.
- Never read `.env` or `config.toml`.
- Do not start `run.py`.

Read first:
- `ally/risk/wire.py` (`on_risk_warning`, `_tell`, `_call_url`, `_make_contact`)
- `ally/risk/contact.py`
- `extension/background.js` (the `ACTIONS` table: `openUrl`, `closeTab`, `exitFullscreen`)
- `extension/manifest.json`
- `ally/config.py` (`[risk]` schema and RANGES) and `ally/default_config.toml` `[risk]`
- `tests/test_risk_contact.py`

## Goal
1. **The video call should look like part of Ally, not like a browser tab.**
   - "Call Priya" opens the standing Meet room in a borderless Chrome popup window.
   - In that window only, Ally joins by itself, hides Meet's control bar, and shows its own large frame:
     - a top bar: "Talking with Priya", or "Connecting to Priya…" before joining;
     - a big red **End call** button.
   - Normal Meet use in other tabs must be completely unaffected.
2. **Auto-alert.** With `[risk] auto_alert = true`, a high warning pushes the trusted contact straight away. No tap is needed.

## Allowed files
- `extension/call.js` (new), `extension/call.css` (new)
- `extension/background.js`, `extension/manifest.json`
- `ally/risk/wire.py`
- `ally/config.py` and `ally/default_config.toml` (one new key only)
- `tests/test_risk_contact.py`
- `docs/RISK_HANDOFF.md` (a short subsection plus the Ben H config row)

## Extension
- **`background.js`:**
  - New `ACTIONS.openCall({ url, name })`:
    - Accept only https URLs on `meet.google.com` (re-check here; never trust the caller).
    - `chrome.windows.create({ url, type: "popup", state: "maximized", focused: true })`.
    - Remember `{ tabId, windowId, name }` in `chrome.storage.session`, because the service worker can restart.
    - Return `{ opened: true, windowId }`.
  - When the tab finishes loading, `chrome.scripting.insertCSS({ target: { tabId }, files: ["call.css"] })`, for remembered call tabs only.
    Add the `scripting` permission only if it's missing.
  - Message handler `allyCallInfo`: reply `{ isCall: true, name }` when `sender.tab.id` is a remembered call tab, and `{ isCall: false }`
    otherwise.
  - Message `allyCallEnd`: close that window and forget it.
  - Forget the tab when its window closes (`chrome.windows.onRemoved`).
- **`manifest.json`:**
  - Add a content script `call.js` for `https://meet.google.com/*`, with `run_at: "document_idle"` and `all_frames: false`.
  - Add a host permission for meet.google.com only if the existing ones don't cover it.
  - Change nothing else.
- **`call.js`:**
  - Ask the background `allyCallInfo` first. If it isn't a call tab, do nothing at all.
  - Then:
    - Add class `ally-call` to `<html>` and build the frame: a top bar of at least 32px font and a red End call button of at least 72px
      height at the bottom centre. Plain DOM, no innerHTML from variables (`textContent` only), no inline handlers.
    - **Auto-join:** poll every 500 ms for up to 30 s for a `button`, or an element with `role="button"`, whose visible text is exactly
      "Join now", "Ask to join" or "Join". Click it once. Until it's gone, the bar says "Connecting to <name>…"; after that,
      "Talking with <name>".
    - **Hide Meet's bottom bar:**
      - Find the element whose `aria-label` starts with "Leave call".
      - Walk up to the first ancestor at least 60% of the viewport wide and under 200px tall, and give it class `ally-hidden`
        (`visibility: hidden`; it stays clickable from JS).
      - Retry with a MutationObserver, throttled, until it's found.
    - **End call:** click Meet's "Leave call" element if present, then send `allyCallEnd`.
    - **Fail-safe:** wrap everything in try/catch. On any error, or if neither the join button nor the leave button is found within 30 s,
      remove `ally-call` and `ally-hidden` so plain Meet shows, and keep the End call button.
- **`call.css`:** scoped under `html.ally-call`. A calm Ally look matching the overlay (amber/neutral, not red sirens, except the End button),
  large type, high contrast, and `.ally-hidden { visibility: hidden !important; }`.
- Run `node --check` on `call.js` and `background.js`.

## Python
- **`wire._tell`:**
  - Open the call with `self.call("openCall", {"url": call_url, "name": name})`.
  - If that returns an error, a falsy value or a dict without `opened`, fall back to the existing `openUrl` call.
- **`auto_alert`** (bool, default `false`):
  - Read it in `_make_contact`'s config path; keep `self.contact` backwards compatible.
  - In `on_risk_warning`, for **high** risks when a contact is configured and `auto_alert` is on:
    - send the push in a daemon thread, so the warning is never delayed;
    - use the same text and join link as `_tell` (share one helper);
    - publish `risk_contact` with `"auto": True`.
  - Rate limit: at most one auto alert per 5 minutes in total, using the wire's injectable clock.
  - If an auto alert succeeded for this warning, the Call button only opens the call and does not send again. Its note says:
    - for `remote_access`: "Opening a call with <name>. Hang up on the other caller first."
    - otherwise: "Opening a call with <name>. They already got the alert."
  - With auto-alert sent and no call link, offer no extra button (they were already told).
- **Config:** add `auto_alert: bool` to the `[risk]` schema, and `auto_alert = false` with a one-line comment in `default_config.toml`.

## Tests (add about 8 to `test_risk_contact.py`; keep all passing)
- Auto-alert:
  - off by default;
  - sends once for high and never for medium;
  - is rate-limited within 5 min and allowed again after;
  - doesn't block (a fake send that waits on an Event doesn't delay `ask`);
  - publishes `auto: True`.
- Call after auto-alert doesn't re-send.
- `openCall` is preferred; an error result falls back to `openUrl`.
- `run.py check` accepts `auto_alert`.
- Run:
  - `cd tests && python -m unittest test_risk_contact test_risk_wire test_overlay test_proactive test_config`
  - then full discovery. Sandbox-only failures in Windows speech and senses-lab socket are known; report the rest.

## Report
Files changed, test output, and every Meet DOM assumption you made (button texts, aria-labels), so they can be checked live.
