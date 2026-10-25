# Ally integration plan & per-person next steps

Written after merging all feature branches into `integration`. Source of truth for tasks is the
Notion HQ; this file reconciles that board with what the merge actually produced.

## Integration status (branch `integration`)

Merged, in order, on top of `main`:

1. **Ben H — text enlargement** (`enlarge_text`, `win_uia.py`, extension edits). Committed first.
2. **Hayden — senses** (`ally/senses/`, face + movement signals). Clean merge.
3. **Ben W — voice** (`voice_confirm.py`, `voice_tools.py`, Cartesia say/ask_yes_no). Two trivial
   union conflicts resolved (both `agent.py` wiring and `tools/__init__.py` keep senses + voice).
4. **Ganesh — risk** (`ally/risk/`, eval set, plus the `handoff/` folder with the foundation patch).
   Clean merge.

**226 tests pass**, `config.toml` validates, `run.py doctor` loads every tool. Nothing pushed; this
is a local branch for review before it goes to `main`.

What is present now: real, independently built **senses**, **voice**, **risk**, and **text
enlargement**. What is NOT present: the connective tissue that makes them one product.

## The one open design decision (Ben H to settle)

The foundation patch (`handoff/foundation/0001-proactive-foundation.patch`) is in the tree as a file
but **not applied**. It contains two different kinds of thing:

- **Glue nobody else built** — `ally/contracts.py` (the Struggle/Risk/Offer types + event bus),
  `ally/proactive.py` (fuse → pick fix → offer), `ally/habits.py` (auto-learn), `ally/overlay/`
  (highlight, Yes/No bubble, reading card), and the `simulate` CLI command. **This is wanted.**
- **Skeleton versions of already-built parts** — `senses/behavior.py`, `senses/vision.py`,
  `risk/__init__.py`, `voice_confirm.py`. The team built better, live-tested versions of these
  independently, so applying the whole patch would collide with real code. **This is not wanted.**

**Decision needed:** how to take the glue without the skeletons.

- **Option A (recommended):** cherry-pick only the glue — `contracts.py`, `proactive.py`,
  `habits.py`, `overlay/`, the `simulate` command, and the `agent.py`/`server.py` hooks — then adapt
  the hand-built senses/voice/risk to publish onto `contracts.py`'s bus. Keeps all tested work;
  builds only the missing middle.
- **Option B:** apply the whole patch on a fresh base and re-port everyone's real work onto it. More
  disruptive; discards the clean integration above.

This is deferred to Ben H because it defines the contract shape and how every module connects. No
glue has been added yet.

## Current ownership (after the Oct 24 role swaps)

| Person | Owns | Folders |
|---|---|---|
| **Hayden** | Senses: vision (squint, lean, head pose) + movement (clicks, hesitation, retype) | `ally/senses/` |
| **Ben H** (you) | Brain & OS, **integration owner** (merges), proactive routing, habits, contracts | `ally/` core, `ally/platforms/`, `ally/tools/` |
| **Ganesh** | UI/frontend/demo + risk logic + pitch/submission | `ally/overlay/`, `dashboard/`, `extension/`, `ally/risk/` |
| **Ben W** | Voice (Cartesia say/ask_yes_no, spoken offers/warnings) + admin (credits, domain) | `ally/voice_confirm.py`, voice files |

Build until feature freeze (Sun midnight, owner: Ben H). Demo/script/submission are a shared pool
delegated at freeze.

## Per-person next steps

### Ben H (you) — unblock everyone, then integrate
1. **Settle the design decision above.** Until the bus exists, nothing connects end to end.
2. **Add the glue (Option A):** bring in `contracts.py`, `proactive.py`, `habits.py`, and the
   `simulate` command from the patch; wire `agent.py` to start the proactive loop.
3. **Adapt senses/voice/risk to the bus:** senses publishes `Struggle`, risk publishes `Risk`,
   `proactive` turns them into an `Offer`, voice/overlay present it, `agent.call_tool` applies the
   fix (zoom / `enlarge_text` / magnify).
4. **Wire `enlarge_text` as an accommodation** so an accepted offer can call it (Tier A/C already
   work; the accommodation's `{tool, args}` should target `enlarge_text`).
5. **Demo OS + doctor:** run `python run.py doctor` on the Windows laptop/VM; log the OS decision.
6. **Validate the Windows text path** (`win_uia.py` `read_focused_box`, UIA read/write) on the VM —
   confirm `tagPOINT` resolves and the COM call-thread model holds.
7. **M3 auto-learn:** once `habits.py` is in, confirm two-yes → auto-apply works end to end.
8. **GitHub repo + README** public by submission; **fallback audit**; **feature freeze** owner.

### Hayden — senses on real faces
1. **Live test matrix** (the P0 that's still open): 5 people, glasses, smiling/laughing, dim light;
   set the squint/lean thresholds from real data, not synthetic.
2. **Publish onto the bus** once Ben H lands `contracts.py`: emit the documented `Struggle` shape
   (`kind`, `confidence`, `target{app,site,window,x,y,box}`, `source`, `detail`, `ts`).
3. Confirm the two-signal fusion policy with Ben H (the `[senses] solo` default) before freeze.

### Ganesh — the visible half + risk
1. **Overlay** (`ally/overlay/`): the reading card, highlight box, and Yes/No offer bubble. This
   also unblocks Ben H's **Tier B** (`show_text_overlay` is a stub waiting on it) and the demo's
   "reading card" moment. Build to the UI spec (≥64px buttons, 28–32px question, ≤12-word copy).
2. **Extension risk content script:** scan page text + URL, POST to `/api/event` as a `Risk`,
   show the in-page warning banner.
3. **Risk logic** (`ally/risk/` is merged): wire `check_text`/`check_url`/process check into the
   bus; add the remote-access check.
4. **Dashboard "Proactive" panel:** live signal meters, decision trace, habit ledger with Undo.
5. Privacy/consent screen; demo stage pages (fictional-brand bank + fake pop-up).

### Ben W — voice + admin
1. **Voice confirm is merged** (`voice_confirm.py`): confirm `say()` and `ask_yes_no()` work on
   Cartesia with the OS-voice fallback, and that it shares the listener's mic without contention.
2. **Wire to the bus** once it lands: `proactive` calls `ask_yes_no(prompt)` → `yes/no/timeout`;
   spoken risk warnings on a `Risk`.
3. Claim free credits (Cartesia, Apify, etc.); register the domain (admin tasks).

## Blockers summary

- **The bus is the critical path.** Senses/voice/risk/text all exist but can't talk until `contracts.py`
  + `proactive.py` land. That is Ben H's first move after the design decision.
- **Tier B text replacement** waits on Ganesh's overlay.
- **Windows text + magnify** waits on VM validation.

## Change log (everyone: add an entry here with every push)

Newest first. Each entry: date and time, who, branch and commit, what changed, and what others need
to know or do. Agents: read the newest entries before starting work.

### Oct 24, 22:40 PT — Ben H (via Claude Code) — branch `worktree-voice`, voice in the proactive loop

**Changed:** new `ally/voice_bus.py` (`BusVoice`), `ally/voice_confirm.py`, `ally/voice.py` (one line),
new `tests/test_voice_bus.py`, new `tests/voice_fakes.py` (shared fakes), `tests/test_voice_confirm.py`,
`tests/voice_phrases.py`. Not wired in yet: `agent.py` still passes `NullVoice`.
- `BusVoice` is the Coordinator's `voice=`: it asks the Offer out loud and resolves it, says "Done. Say
  undo to put it back." after an accepted fix (or "Sorry, that didn't work."), then listens 6 s for
  "undo" (runs `reset_all`; anything else heard goes on to the listener). Lines said during a question
  wait for it to end.
- Risk Offers (`context="risk"`, as RiskWire on `risk-branch` makes them): calm, slower voice; the
  question waits up to 1 s for its warning and says both together.
- Labelled choices (RiskWire's "Are you sure?" with "Close it" / "Keep it open"): asked as "Should I close
  it, or keep it open?"; a bare "yes" / "I'm sure" is unclear, never a close.
- `offer.pause_seconds`: "no" isn't accepted before the pause ends. `created + timeout`: no re-ask that
  can't finish. "Stop asking me" is its own no (snoozes struggle offers; a plain no for risk).
- While a helper video call is open (`risk_contact` with `call_opened`) voice asks nothing for 10 min.
**For Ben H (integration):** in `agent.py`, `voice=BusVoice(self.confirm, self.bus, call_tool,
snooze=getattr(self.proactive, "snooze", None))`; do it on `benh/os-layer` (its gaze lines sit there).
Consider `offer_timeout_seconds` ~20 (gaze cards use `offer.timeout` too, so with Hayden).
**For Ganesh:** please publish a "call ended" event (voice goes quiet for a fixed 10 min until then), and
check the spoken wording for labelled choices.

### Oct 24, 22:01 PT — Ben H (via Claude Code) — branch `worktree-voice`, voice fixes

**Changed:** `ally/voice_confirm.py`, `ally/speech.py`, `ally/listener.py`, `tests/test_voice_confirm.py`,
one comment line in `config.toml` / `ally/default_config.toml`.
- `voice_confirm.py` loads on Python 3.11 again (an f-string needed 3.12).
- Re-asks use the last question ("Having trouble clicking? Want me to ...?").
- Risk questions: "stop them" / "get me out of here" mean yes (the protective action); a bare "stop",
  "cancel" or "go back" is unclear and re-asked, never a silent no.
- "Ally, stop" (an explicit `Speaker.stop()`) ends an open question; other speech doesn't.
- A yes/no from `run.py answer`, the dashboard or the API now resolves the Offer too.
- Voice-name lookups use the 4 s speech timeout; unknown names aren't looked up again for 60 s.
**For everyone:** `/api/stop`, `stop_speaking` and `reset_all` now end an open spoken question.
**For Ganesh:** please check the risk-answer rule above.
