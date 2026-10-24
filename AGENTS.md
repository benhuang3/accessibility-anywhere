# AGENTS.md: Ally (Cal Hacks 13.0)

Windows accessibility agent for older adults. Python 3.11+. Read FOUNDATION.md for message formats, the simulate CLI and ownership; don't re-derive it.

## Ownership (touch only your own folders unless told otherwise)

| Owner | Area |
|---|---|
| Ben H | `ally/` core, `proactive.py`, `habits.py`, `contracts.py`, `agent.py`, merges |
| Hayden | `ally/senses/` |
| Ben W | `ally/voice_confirm.py`, voice |
| Ganesh | `ally/overlay/`, `dashboard/`, `extension/` (risk UI), `ally/risk/` |

## Contracts

- `ally/contracts.py` is canonical (Struggle, Risk, Offer, bus topics). Never invent a parallel format.
- The extension talks to the agent ONLY through the existing `extension/background.js` WebSocket bridge. Do not add a new extension<->agent transport.
- Risk events enter the bus via the agent (coordinate with Ben H).
- Content scripts must not call the HTTP API directly.

## Dashboard / extension rules

- CSP is `script-src 'self'`: NO inline `<script>` blocks, NO `onclick=` or other inline `on*=` handlers. All JS lives in separate `.js` files.
- API calls need the `X-Ally-Token` header.

## Overlay rules

- All Tk calls happen on the single overlay thread, via its queue.
- Keep `SetProcessDpiAwareness` before any window is created.

## Secrets

- No API keys in the repo or the extension. `.env` only (it is gitignored). The repo goes public at submission.

## Copy and privacy wording

- Warnings are calm and hedged; never include phone numbers.
- Camera frames stay local. Screenshots for "describe screen" and voice audio go to cloud APIs. Never claim "nothing leaves your computer".

## Workflow

- Small commits; tests must pass before each commit:
  `cd tests && python -m unittest test_config test_registry test_brain test_network test_runtime test_proactive`
- Run `/ship-check` before each merge checkpoint.
- Deadlines: feature freeze Sat midnight, submission Sun Oct 25, 9:00 AM PDT.
