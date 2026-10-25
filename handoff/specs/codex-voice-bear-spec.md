# Spec: Ally's bear appears whenever the person talks to Ally (voice status on the bus + overlay)

About 40 minutes of build time. Keep it small. No screenshots needed from you; I will test live.

## Why

The overlay bear (`ally/overlay/`, the companion design) only appears for warnings and offers, so Ally feels
like an alarm. It should appear whenever the person talks to Ally, so the bear simply *is* Ally. The team
contract in `docs/integration_plan.md` (search "Contract additions") promised a bus topic `"voice"` that was never
built:

`{"state": "listening" | "thinking" | "speaking" | "idle", "text": caption, "offer_id": int | None, "kind": str, "ts": float}`

(`"thinking"` is new here; add it to that contract line in the doc.)

## Behaviour

1. **Wake word heard** (`Listener.prompt()` in `ally/listener.py`, i.e. "Ally" on its own, the listen hotkey,
   or the `voice.py` listen paths that already log "Listening..."): publish `listening` (text "Listening…",
   kind "request"). The bear appears in the bottom-right corner with its **listening** face and a small card
   "Listening…".
2. **Request heard** (the listener is about to run a request: `Listener.handle` just before starting `_run`,
   with the request text): publish `thinking` with the person's words. The card shows them in quotes, e.g.
   “Make this easier to read”, bear idle/listening face.
3. **Reply**: when `Agent.handle_text(..., source="voice")` has its reply text and starts speaking it, publish
   `speaking` with the reply text. The bear's **happy** face; the card shows the reply (trimmed to ~200 chars).
   Then publish `idle` when the speech has finished: use the speaker's own completion (`ally/speech.py`
   `Speaker.speaking` / `wait`; Hayden's Cartesia voice runs behind it) on a daemon thread, with a fallback
   timer of about 0.4 s per word (min 3 s, max 20 s) if the speaker can't tell. The card fades **4 s after
   idle**. If the reply is empty or the request failed, go idle right away (the card fades after 4 s).
   A wake word heard with nothing after it times out to idle after the listener's own listening window
   (or 8 s if there's no such value).
4. **Risk always wins**: a risk card, a done note or any offer replaces the voice card immediately, and voice
   updates never replace a risk card or an open offer (the existing risk-beats-info rule; give the voice card
   its own card kind, e.g. `"voice"`, treated like info for that rule).
5. **Quiet during a helper call**: while a call is open (`risk_contact` with `call_opened` until
   `risk_call_ended`; `ally/voice_bus.py` already tracks this as `quiet`), the overlay shows no voice cards.
   Simplest: the overlay subscriber ignores voice states while a call is open (track the two topics there).

Only `source == "voice"` requests show the bear; typed requests from the dashboard/popup do not.

## Wiring

- Publish with `agent.bus.publish("voice", {...})` (the EventBus in `ally/contracts.py`; publishing with no
  subscribers is fine). Wrap every publish in try/except so voice never breaks because of the overlay.
- The overlay gets the messages through a subscriber set up where the overlay is attached
  (`Agent.start_proactive` in `ally/agent.py` creates the overlay / coordinator: find it). The subscriber only
  queues work onto the overlay's Tk queue (bus handlers run on the publisher's thread).
- Overlay: a method like `Overlay.voice(state, text)` that renders through the existing card/bubble machinery
  with the new tone: listening → bear "listening", thinking → "idle" (or "listening"), speaking → "happy",
  no badge; idle → schedule the 4 s fade (cancel it if a newer voice state arrives). Pure tone mapping in
  `ally/overlay/layout.py` `card_tone` with tests. No buttons on the voice card.

## Allowed files

`ally/listener.py` and `ally/voice.py` (publish lines only), `ally/agent.py` (publish around the voice reply,
and the overlay subscription in `start_proactive`), `ally/overlay/overlay.py`, `ally/overlay/layout.py`,
`tests/test_overlay.py`, new `tests/test_voice_bear.py`, `docs/integration_plan.md` (the contract line only).
Do not change speech/Cartesia code, wake-word logic, or how requests are answered. Never touch `.env`,
`config.toml`, `state/`, `logs/`. Do not start Ally or use port 8765.

## Acceptance

- `cd tests && python -m unittest test_voice_bear test_overlay test_handsfree test_runtime test_brain test_registry test_config test_network test_risk_wire test_risk_cooldown test_call_mini`
  pass (a Windows speech-worker failure that also fails on the base commit is fine; say so).
- Tests use fakes: no microphone, no speech, no real Tk windows.
- Files are CRLF; preserve endings. Git is not writable from your sandbox: do not commit. End with a short
  report: files changed, where each state is published, test results.
