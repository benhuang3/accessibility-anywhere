# How Ally uses Cartesia

For Ally's users (older adults, many with declining eyesight), **voice is the interface**. Ally speaks first ("Having
trouble reading? Want me to make it bigger?"), listens for the answer, reads pages and pictures aloud, and warns
calmly about scams. Every word Ally says goes through **Cartesia Sonic**, and spoken yes/no answers are heard with
**Cartesia Ink**. Ally is built so that nothing the user hears depends on the network: Cartesia first, then
cached Cartesia audio, then the computer's own voice.

| | |
|---|---|
| Speaking | Sonic `sonic-3.6`, falling back to `sonic-3` (`POST /tts/bytes`), per-utterance emotion, speed, volume and language |
| Hearing | Ink `ink-whisper` for spoken answers (`POST /stt`); streaming Ink `ink-2` turn detection over WebSocket (`/stt/turns/websocket`) built and tested, not wired in yet |
| Voices | Library search and lookup by name (`GET /voices`); the demo uses "Michelle" (`[speech] cartesia_voice_id`) |
| Code | `ally/speech.py` (Sonic, styles, cache, circuit breaker), `ally/voice_confirm.py` (spoken questions), `ally/voice.py` (Ink), `ally/stt_stream.py` (streaming Ink), `ally/tools/browser_tools.py` (reading pages, translation) |
| Tests | 119 unit tests on these paths, a 21-scenario end-to-end run and a 184-answer phrase set, all against a fake Cartesia server (see Testing) |

---

## 1. Where the user hears Cartesia

| Moment | What Ally does with Sonic |
|---|---|
| **Offers** ("Having trouble reading? Want me to make it bigger?") | `offer` style: calm emotion, a little slower (speed 0.9), cached on disk |
| **Questions and re-asks** | `question` style, then one re-ask in other words; "What?" and "slower" replay it more slowly |
| **Scam warnings** ("This pop-up may be a scam. Close it?") | `warning` style: calm (never alarmed), slower still (0.85), a short pause before the question |
| **Apologies** ("Sorry, I didn't catch that") | `sympathetic` emotion |
| **Confirmations** ("Done. Say undo to put it back.") | cached, so instant |
| **Reading pages aloud** | Long pages a part at a time ("Say keep reading for more"); page text is never cached |
| **Reading in another language** ("read this page in Spanish") | Claude translates the part being read; Sonic speaks it with `language: "es"` (21 languages by name) |
| **Pictures and the screen** ("describe the picture") | Claude's description, spoken by Sonic |
| **Replies to every request** | "Simple mode is on. I hid a cookie banner, 4 ads…", "Switched to Facebook." |

Style table: `ally/voice_confirm.py` (`VoiceConfirm.style`); generation settings: `ally/speech.py` (`tts_body`): `speed`
(0.6-1.5), `volume`, `emotion`, `language`, and inline tags for pauses, speed and spelling.

## 2. Speaking: how a sentence becomes sound

```
 text ─► pronounce() ─► chunks (≈400 chars, sentence breaks) ─► for each chunk:
            prompt cache hit? ──yes──► play cached Cartesia WAV (1-2 ms)
                   │ no
            circuit breaker open? ──yes──► built-in voice (rest of the text, same speed)
                   │ no
            resolve voice name → id (cached)  ─► POST /tts/bytes {model_id, voice, language,
                                                  generation_config: {speed, emotion, volume}}
                   │ model rejected? try the fallback model;  styled request rejected? retry plain
                   └► play WAV, keep going; a later chunk failing finishes the text with the built-in voice
```

- **Prompt cache** (`PromptCache`): Ally's fixed prompts are rendered by Sonic at start and stored on disk, keyed
  by text, voice, model, speed, emotion, volume and language, so offers and warnings keep the Cartesia voice even
  offline. Page text and anything personal is never cached.
- **Circuit breaker**: after a network failure, 5xx or 429, Ally uses the built-in voice for 15-30 s instead of
  making every sentence wait for the timeout (`cartesia_timeout_seconds`, default 4).
- **Model fallback**: a model the account can't use (`sonic-3.6`) is skipped from then on in favour of `sonic-3`.
- **Interruptible**: "Ally, stop" cuts the current sentence at once; queued sentences never talk over each other.
- **Measured** (against a fake Cartesia with 200 ms of simulated latency, `docs/VOICE_WORK.md`): a cached prompt
  starts in 1-2 ms, a new one in about 230 ms; with Cartesia hanging, the first sentence waits 3 s and later ones
  1 ms thanks to the circuit breaker.

## 3. Hearing: spoken answers with Ink

After Ally finishes speaking (so it never hears itself), it borrows the always-on listener's microphone, records the
answer and transcribes it with Ink (`ink-whisper`, `POST /stt`), or offline Whisper without a key.

- **Safety first**: silence or an unclear answer ("yeah, no", "maybe", "that's fine") never counts as yes. A clear
  "yes"/"no" is accepted after a 0.45 s pause instead of the full one (about 700 ms from the end of speech to the
  answer, down from 1100 ms).
- **On-screen Yes / No buttons always work too**; the first answer wins.
- **Streaming Ink** (`ally/stt_stream.py`): a complete client for Ink-2's turn-detection WebSocket (turn start,
  eager end, end, resume), with 28 tests against a fake server. It is not wired into listening yet; the batch call
  is what runs today.

## 4. Configuration

`[speech]`: `engine` (`auto`/`cartesia`/`system`/`off`), `cartesia_voice_id`, `cartesia_model`,
`cartesia_fallback_model`, `cartesia_timeout_seconds`, `cartesia_retry_seconds`, `emotion`, `language`, `rate`,
`max_chars` (one part of a page read aloud). `[voice] engine` for hearing. `[confirm]` for listening time, re-asks
and question speeds. Key: `CARTESIA_API_KEY` in `.env`.

Command line: `python run.py voices` (search the library), `python run.py voice-test Michelle Tessa --play`
(renders Ally's prompts in each voice and prints time to audio and words per minute; aim for 150-160),
`python run.py say "..."`, `python run.py ask "..."`, `python run.py warn scam_popup`. `GET /api/voice` reports the
engine in use and why Ally fell back, if it did.

## 5. Testing

| Suite | Tests | Covers |
|---|---|---|
| `tests/test_voice_confirm.py` | 79 | Speaking with styles, the prompt cache, fallbacks and the circuit breaker, spoken questions, re-asks, eager answers, warnings, voice listing, Ink transcription |
| `tests/test_stt_stream.py` | 28 | The streaming Ink-2 client: protocol, turns, errors, timeouts |
| `tests/test_cartesia_reading.py` | 12 | Reading in parts and "keep reading", reading in other languages (the `language` field reaching `/tts/bytes`), a rejected language not disabling the model, cache keys |
| `tests/e2e_voice.py` | 21 scenarios | The real agent asks spoken questions against a fake Cartesia, microphone and speakers |
| `tests/voice_phrases.py` | 184 answers | Yes/no understanding: 100%, 0 false "yes" |

`tests/fake_cartesia.py` and `tests/fake_cartesia_ws.py` implement the endpoints (`/tts/bytes`, `/stt`, `/voices`,
`/stt/turns/websocket`) and record every request, so tests check exactly what Ally sends.

## 6. Limits (honest)

- The automated tests run against fake Cartesia servers; real-API latency and behaviour are checked by hand on the
  team's machine (the demo uses the "Michelle" voice).
- The 184 answers were written by the person who wrote the rules: 100% shows the rules are consistent, not
  real-world accuracy with older voices. Understanding answers is English only.
- Reading in another language uses the configured voice speaking that language; a voice chosen per language would
  sound more native. Translation needs Claude. If Cartesia rejects a language, that part is read by the computer's
  own voice and the Sonic models stay in use for everything else.
- Streaming Ink-2 is built and tested but not yet the default listening path.

More: `docs/VOICE_WORK.md` (Ben W's full voice write-up: design, metrics, bugs found, decisions).
