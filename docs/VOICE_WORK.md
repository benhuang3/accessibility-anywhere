# Ally Voice: `say()` and `ask_yes_no()` on Cartesia, with an OS-voice fallback

**Project:** Ally (Cal Hacks 13), repo `benhuang3/accessibility-anywhere`
**Owner:** Benjamin Wang (Voice)
**Notion task:** "Voice: say() and ask_yes_no() on Cartesia, with an OS-voice fallback" (P0 demo-critical, AI/Agent)
**Work done:** October 24–25, 2026
**Branch / commit:** `benw/voice`, commit `fe5b1df`, on top of `main` at `fef8cf4`
**Status:** Built and tested. **Committed but not pushed.** The session had no write access to the repo (see [Git status](#14-git-status-and-how-to-push)). The same change is in `benw-voice.patch`.

---

## Contents

1. [Summary](#1-summary)
2. [Background: what this task is](#2-background-what-this-task-is)
3. [What was built, at a glance](#3-what-was-built-at-a-glance)
4. [How a spoken question works, step by step](#4-how-a-spoken-question-works-step-by-step)
5. [Module reference](#5-module-reference)
6. [Configuration reference](#6-configuration-reference)
7. [How teammates use it](#7-how-teammates-use-it)
8. [Interfaces: tools, HTTP API, command line](#8-interfaces-tools-http-api-command-line)
9. [Testing: what exists and what it covers](#9-testing-what-exists-and-what-it-covers)
10. [Metrics](#10-metrics)
11. [Bugs found by testing, and how they were fixed](#11-bugs-found-by-testing-and-how-they-were-fixed)
12. [Design decisions and where they differ from the research](#12-design-decisions-and-where-they-differ-from-the-research)
13. [Known limitations and what is not verified](#13-known-limitations-and-what-is-not-verified)
14. [Git status and how to push](#14-git-status-and-how-to-push)
15. [Merging with the rest of the team's work](#15-merging-with-the-rest-of-the-teams-work)
16. [How to test it yourself](#16-how-to-test-it-yourself)
17. [Recommended next steps](#17-recommended-next-steps)
18. [Sources](#18-sources)

---

## 1. Summary

Ally is a proactive accessibility helper for older adults. It notices when someone is struggling, offers help, learns what they need, and eventually applies it automatically. This work gives Ally its **voice**: it can **speak an offer** ("Want me to make this bigger?"), **listen for a yes or no**, and **give calm spoken scam warnings**. It keeps working when the cloud voice service fails.

**Headline results**

| Result | Value |
|---|---|
| Automated tests | **154 pass** (100 existing + 54 new) |
| End-to-end scenarios (real agent, simulated hardware) | **21 / 21 pass** |
| Yes/no understanding on 184 labeled answers | **100%**, **0 false "yes"** (see caveat in §13) |
| Time from the end of the user's speech to the answer | **700 ms** (was 1100 ms before the "eager answers" improvement; research target < 800 ms) |
| Time to start speaking a saved prompt | **1–2 ms** |
| Time to start speaking a new prompt | **~230 ms** (includes 200 ms of simulated Cartesia latency) |
| Cartesia hanging: first sentence / later sentences | **3 s** (the timeout) / **1 ms** (circuit breaker) |
| Code change | 24 files, +3,195 / −64 lines |

**Four real bugs** were found by end-to-end testing and fixed (§11): late answers were ignored, a hanging Cartesia stalled every sentence, two warning questions invited answers that meant the opposite, and early acceptance could misread "Yeah… no".

---

## 2. Background: what this task is

### 2.1 Where voice sits in Ally

Ally's loop is **notice → offer → learn → automate**. The team splits the work by interface:

| Role | Owner | Folder |
|---|---|---|
| Senses (webcam + mouse/keyboard signals) | Hayden | `ally/senses/` |
| Brain & OS (decides what to offer, habits, merges) | Ben H | `ally/` core |
| UI, frontend, risk rules | Ganesh | `ally/overlay/`, `dashboard/`, `extension/`, `ally/risk/` |
| **Voice** | **Ben W** | **`ally/voice_confirm.py` + voice files** |

The **contract** between Brain and Voice (Team Roles & Interfaces page):

```
say(text)
ask_yes_no(prompt) -> yes / no / timeout
Voice falls back to the OS voice if Cartesia fails.
```

### 2.2 What `say()` and `ask_yes_no()` mean

- **`say(text)`** makes Ally speak a sentence and returns immediately, without waiting for a reply. It's used for "Done. Say undo to put it back.", for reading text aloud, and for low-severity warnings.
- **`ask_yes_no(prompt)`** speaks a question, listens for the spoken answer, and returns `"yes"`, `"no"`, or `"timeout"`. It's the core demo moment: Ally notices struggle, asks "Want me to make this bigger?", the user says "yeah", and the text grows.

### 2.3 What Cartesia is

Cartesia is a voice-AI company with a developer API. Ally uses two of its model families:

- **Sonic (text-to-speech):** turns text into realistic speech. Ally uses `sonic-3.6`, falling back to `sonic-3`. It supports per-request **speed**, **volume** and **emotion** (e.g. `calm`, `content`, `sympathetic`), plus inline SSML such as pauses.
- **Ink (speech-to-text):** turns the user's spoken answer into text. Ally uses `ink-whisper`.

Cartesia is also a Cal Hacks sponsor track, which Voice owns.

### 2.4 What "OS-voice fallback" means

Cartesia is a cloud service, so venue Wi-Fi, a missing key or an outage can break it. The fallback is the speech engine **built into the operating system**: Windows SAPI voices (e.g. "Microsoft Zira Desktop"), macOS `say`, or Linux `espeak-ng` / `spd-say`. It sounds robotic, but it works offline with no key, so Ally never goes silent mid-demo.

### 2.5 State of the repo when work started

- `main` had a working reactive Ally, with a `Speaker` class that already did Cartesia speech with a basic fallback to the OS voice.
- **There was no `voice_confirm.py` and no `contracts.py`.** The "foundation patch" described in Notion (with `Offer`, the event bus and a starter voice confirm) had not been merged. So this work was built on `main`, against the *documented* contract. The `Offer` integration is duck-typed so it plugs in when the patch lands.
- Hayden's `hayden/senses` branch exists and is unmerged.

---

## 3. What was built, at a glance

### 3.1 Architecture

```
                         Brain (Ben H)            Risk rules (Ganesh)        Buttons / dashboard / CLI
                              |                          |                            |
              say(text)  ask_yes_no(prompt)        warn(risk)                  answer("yes"|"no")
                              \__________________________|____________________________/
                                                         |
                                        ally/voice_confirm.py  (VoiceConfirm)
                       +---------------------------------+-------------------------------------+
                       |  speak the question             |  listen for the answer              |
                       |  (styles: offer, warning,       |  - mic: borrow the always-on        |
                       |   sorry, done, read)            |    listener, or open its own        |
                       |                                 |  - segment speech (eager pause)     |
                       |                                 |  - transcribe: Cartesia Ink ->      |
                       |                                 |    offline Whisper                  |
                       |                                 |  - classify: YES / NO / UNDO /      |
                       |                                 |    REPEAT / SLOWER / unclear        |
                       +---------------+-----------------+-------------------------------------+
                                       |
                              ally/speech.py  (Speaker)
                 saved prompt? -> Cartesia sonic-3.6 -> sonic-3 -> OS voice
                 (circuit breaker skips Cartesia for 30 s after a network failure)
```

### 3.2 Files changed

| File | Change | What it does |
|---|---|---|
| `ally/voice_confirm.py` | **new**, 1,028 lines | `say`, `ask_yes_no`, the answer classifier, re-ask logic, mic sharing, risk warnings, `Offer` hook, latency log, pre-rendering, voice audition, module-level API and a standalone CLI |
| `ally/speech.py` | +403 lines | Cartesia client with styles (emotion/speed/volume), model fallback, `auto` engine, voice-name lookup, on-disk prompt cache, circuit breaker, SSML stripping for the OS voice, voice list, WAV duration/words-per-minute helpers |
| `ally/listener.py` | +59 lines | Mic "claim" so a question borrows the always-on listener's microphone; eager-pause segmenting; `in_speech` flag |
| `ally/voice.py` | small | Push-to-talk defers to an open question; Cartesia URL read at call time (for testing) |
| `ally/agent.py` | small | Creates `agent.confirm`, installs it module-wide, pre-renders prompts at start and after settings changes, status, shutdown |
| `ally/config.py`, `ally/default_config.toml`, `config.toml` | new keys | `[speech]` additions and a new `[confirm]` table, with validation and ranges |
| `ally/tools/voice_tools.py` | **new** | Tools: `ask_yes_no`, `answer_question`, `warn_risk`, `list_voices` |
| `ally/tools/__init__.py` | 1 line | Registers the voice tools |
| `ally/server.py` | +32 lines | `GET /api/voice`, `POST /api/voice/{say,ask,answer,warn,cancel}` |
| `ally/cli.py` | +82 lines | `run.py say / ask / answer / warn / voices / voice-test` |
| `ally/doctor.py` | rewritten voice section | Live Cartesia check (model + voice), what the spoken questions will use, what's missing |
| `ally/paths.py`, `.gitignore` | small | `cache/` folder for saved prompt audio (git-ignored) |
| `README.md`, `.env.example` | docs | Voice section, testing commands, troubleshooting, privacy note |
| `tests/test_voice_confirm.py` | **new**, 54 tests | Unit tests (§9.1) |
| `tests/e2e_voice.py` | **new** | End-to-end harness with the real agent (§9.2) |
| `tests/fake_cartesia.py` | **new** | Local stand-in for the Cartesia API, with failure modes |
| `tests/fakes/sounddevice.py`, `tests/fakes/bin/aplay`, `tests/fakes/bin/espeak-ng` | **new** | Fake microphone, speaker and OS voice with realistic timing |
| `tests/voice_phrases.py` | **new** | 184 labeled spoken answers and an evaluation script |

---

## 4. How a spoken question works, step by step

What happens on `ask_yes_no("Want me to make this bigger?")`:

1. **Open the question.** A new question replaces any older one, and the older one returns `timeout` (intent `SUPERSEDED`). If `[confirm] enabled = false` or the prompt is empty, it returns `timeout` at once.
2. **Speak.** The prompt is spoken in the `offer` style: calm, speed 0.9× the user's rate.
   - A prompt that was **saved at startup** plays from the cache (1–2 ms).
   - Otherwise it goes to Cartesia, then the fallback model, then the OS voice.
   - If a button answer arrives mid-sentence, Ally **stops talking immediately**.
3. **Wait 250 ms** (`listen_delay_ms`) so the microphone doesn't hear the end of Ally's own voice.
4. **Listen.**
   - If the always-on listener is running, the question **borrows its microphone** (`listener.claim()`); otherwise it opens its own stream.
   - The user has `listen_seconds` (default 8) to **start** answering. An answer that starts in time is **heard out**, up to `max_answer_seconds` more.
   - Audio heard while Ally was talking, or from before the question, is thrown away.
5. **Transcribe**, first with Cartesia Ink, then offline Whisper if Cartesia fails.
6. **Classify** the words (§5.1.3). What happens next:

   | Heard | Result |
   |---|---|
   | Clear yes ("yeah, sure", "no problem", "close it" for "Close it?") | `yes`, by `voice` |
   | Clear no ("no thanks", "not now", "I'm okay") | `no`; Ally says "No problem. I'll leave it as is." |
   | Undo ("put it back", "too big") | `no` (intent `UNDO`) |
   | "What?" / "Say that again" | Replays the question once, reworded and 15% slower |
   | "Slower, please" | Same, and lowers the speech rate **permanently** (saved as a habit) |
   | Unclear ("maybe", "yeah, no", "that's fine") | "Sorry, I missed that. *[reworded question]*" once; then "You can tap Yes or No." and `timeout` |
   | Only filler or noise ("um", "Thank you." from a cough) | Keeps listening; if nothing else comes, `timeout` (intent `NOISE`) |
   | Silence | `timeout` (intent `SILENCE`); **never repeated, never acted on** |

7. **Eager answers.** After a 0.45 s pause, the speech so far is transcribed early. A clear one-to-three-word yes or no is accepted without waiting for the full ~0.85 s pause. This cut reply time from 1100 to 700 ms. Bare openers ("yeah", "sure", "okay", "alright") are **not** accepted early, because they often start a reversal ("Yeah… no").
8. **First answer wins.** A button press, `run.py answer`, or the `Offer` being resolved elsewhere ends the question at any point. A later voice answer is then ignored.
9. **Log.** Timings are written to `logs/voice_latency.jsonl` and kept in memory for the dashboard. The transcript is **not** written to disk.

---

## 5. Module reference

### 5.1 `ally/voice_confirm.py`

#### 5.1.1 Public API

```python
from ally.voice_confirm import say, ask_yes_no            # module-level, use the agent's instance
say("Done. Say undo to put it back.")                      # -> bool (False if speech is off / empty)
ask_yes_no("Want me to make this bigger?")                 # -> "yes" | "no" | "timeout"

vc = agent.confirm                                         # the VoiceConfirm instance
vc.say(text, style="calm", wait=False)                     # styles: calm, offer, question, done, sorry, warning, read, plain, or a dict
vc.ask(prompt, timeout=None, rephrased=None, style="offer", kind="offer", cancel=None)  # -> Answer
vc.ask_yes_no(prompt)                                      # -> str
vc.answer("yes", by="button")                              # answer the open question; True if it was accepted
vc.cancel()                                                # end the open question without an answer
vc.confirm_offer(offer, background=False)                  # ask offer.prompt, then offer.resolve(answer, by="voice")
vc.warn({"kind": "scam_popup", "severity": "high"})        # spoken risk warning -> Answer, or None for low severity
vc.done(); vc.undone(); vc.read_aloud(text)                # common phrases
vc.register_prompts([...])                                 # Brain's offer prompts, so they are pre-rendered too
vc.prerender()                                             # render fixed prompts with Cartesia now
vc.slow_down()                                             # lower the speech rate and keep it
vc.info()                                                  # status for the dashboard
```

**`Answer`** has these fields:
- `value`: `yes` / `no` / `timeout`.
- `by`: `voice` / `button` / `cli` / `dashboard` / `offer` / `none`.
- `intent`: one of `YES`, `NO`, `UNDO`, `AMBIGUOUS`, `SILENCE`, `NOISE`, `NO_MIC`, `NO_STT`, `CANCELLED`, `SUPERSEDED`, `DISABLED`, `BUTTON`, `EMPTY_PROMPT`, `ERROR`.
- `transcript`, `confidence`, `attempts`, `prompt`, `error`.
- `latency`: `prompt_to_audio_ms`, `speech_end_to_answer_ms`, `stt_ms`, `stt_engine`, `tts_engine`, `mic`, `eager`, `total_ms`.

`Answer == "yes"` works, so callers can treat it like the contract's plain string.

#### 5.1.2 What Ally says

All prompts are 10 words or fewer, in plain words, following the Research: Voice prompt rules.

| Key | Text | Style |
|---|---|---|
| `offer` | Want me to make this bigger? | calm, 0.9× speed |
| `done` | Done. Say undo to put it back. | content |
| `no_reply` | No problem. I'll leave it as is. | content |
| `missed` | Sorry, I missed that. *(+ reworded question)* | sympathetic |
| `missed_final` | Sorry, I missed that. You can tap Yes or No. | sympathetic |
| `buttons` | You can tap Yes or No. *(after a re-ask already said sorry)* | sympathetic |
| `read_start` | Sure. Here it is. | content |
| `undone` | Okay, I put it back. | content |

**Risk warnings.** "Yes" always means "take the protective action". Page text is never read aloud, since a scam page's words are exactly what shouldn't be repeated.

| Risk kind | Statement | Question (after a 400 ms pause) |
|---|---|---|
| `scam_popup` | This pop-up may be a scam. | Close it? |
| `untrusted_site` | This website may not be safe. | Close this page? |
| `remote_access` | Someone may be trying to control your computer. | Disconnect them? |
| `payment_field` | This page is asking for card details. | Want me to check it first? |
| *(other)* | Something here may not be safe. | Want me to help? |

- **Low severity** only says the statement; medium and high ask the question.
- **Style:** calm emotion, 0.85× speed. Never "scared" or "alarmed".
- **Wording:** two questions were changed during testing ("Go back?" and "Stop it?"), because the natural echo answers "go back" and "stop it" mean *undo* and *no* (§11).

**Re-asks** use different words. Known prompts have hand-written rewordings ("Want me to make this bigger?" becomes "Should I make the text larger for you?"); others are rewritten by rule ("Want me to X?" becomes "Should I X for you?").

#### 5.1.3 Understanding answers (`classify`)

The rules come from Research: Voice's yes/no table:

1. **Normalize:** lowercase, hyphens to spaces ("uh-huh" becomes "uh huh"), drop punctuation and the wake word ("Ally").
2. **Match phrases longest first**, so "no problem" counts as YES before "no" can match, and "I'm okay" counts as NO before "okay" can. The categories:
   - strong YES, weak YES (please, okay, fine…), NO;
   - UNDO, REPEAT, SLOWER;
   - HEDGE (maybe, I guess, not sure, wait…);
   - SOFT ("that's fine", "it's okay"; these can mean either);
   - backchannel ("uh-huh", "mm-hmm") and negative backchannel ("uh-uh").
   It also covers common speech-to-text spellings: yesh, yeh, ya, noo, naw…
3. **Decide, in this order:**
   1. UNDO wins, unless a strong YES is also present (then unclear).
   2. Any hedge makes it unclear.
   3. YES and NO together ("yeah, no") are unclear.
   4. NO (confidence 0.9, or 0.75 when mixed with a weak yes).
   5. Strong YES (0.9).
   6. "uh-uh" is unclear (speech-to-text confuses it with "uh-huh").
   7. "uh-huh" is a weak YES (0.6).
   8. SLOWER / REPEAT, but only for short replies, so "what about the email…" isn't a repeat request.
   9. SOFT alone is unclear.
   10. **Echoing the question's action** is YES (0.8): "close it" for "Close it?", "yes, disconnect them" for "Disconnect them?".
   11. Weak yes alone, in a short reply, is YES (0.7).
   12. Nothing recognized: unclear, or empty if there were no real words.
4. **Noise transcripts** that speech-to-text invents for coughs and silence ("Thank you.", "You", "Bye.") count as empty.

#### 5.1.4 Sharing the microphone

The always-on listener (for "Ally, …" requests) holds the microphone all the time. Opening a second stream on Windows invites conflicts; the Code Foundation page warns about exactly this. So while a question is open:

- the listener **hands its utterances to the question** instead of handling them as wake-word requests (`claim()` / `release()`);
- a spoken "yes" is never mistaken for a new request;
- the push-to-talk hotkey replies "Listening for yes or no." instead of opening the mic again;
- without the listener running, the question opens its own short-lived stream.

### 5.2 `ally/speech.py`

| Feature | Detail |
|---|---|
| **Styles** | `Speaker.say(text, style={"emotion", "speed", "volume", "cache"})`. Speed multiplies the user's rate and is clamped to Cartesia's 0.6–1.5. The OS voice honours speed only. |
| **Engine `auto`** | Uses Cartesia when `CARTESIA_API_KEY` **and** a voice are set, otherwise the OS voice. This is now the default; without a key it behaves exactly as before. |
| **Model fallback** | `cartesia_model` (sonic-3.6), then `cartesia_fallback_model` (sonic-3), then the OS voice. A rejected model is remembered and skipped from then on. |
| **Emotion/SSML retry** | If Cartesia rejects a request only because of the emotion or SSML extras, it's retried once without them. |
| **Voice names** | `cartesia_voice_id` may be an ID or a name ("Tessa"). Names are looked up once in Cartesia's voice library. |
| **Prompt cache** | Ally's fixed prompts are rendered at startup and saved in memory and in `cache/voice/`. The key covers text, voice, model, speed, emotion and volume, so changing any of them re-renders. **Text read from pages is never cached.** |
| **Circuit breaker** | After a network failure (unreachable or timed out), Cartesia is skipped for `cartesia_retry_seconds` (default 30 s); for a 5xx/429 error it's 15 s at most. Sentences go straight to the OS voice instead of each waiting for the timeout, while saved prompts still play in the Cartesia voice. |
| **SSML** | `pause(ms)` inserts `<break time="…ms"/>`. `strip_ssml()` removes the tags before text goes to the OS voice, which would otherwise read them out. |
| **Helpers** | `cartesia_voices()`, `resolve_voice()`, `wav_seconds()`, `words_per_minute()`, a `CartesiaError` with `.status`, `.offline` and `.bad_request`. |
| **Status** | `Speaker.info()` reports the active engine, last engine (`cartesia` / `cartesia-cache` / `system`), fallback reason, model in use, cached prompt count and Cartesia retry countdown. |
| **Testing hook** | `CARTESIA_BASE_URL` environment variable points Ally at a test server. |

### 5.3 `ally/listener.py`

- `Utterance.eager`: marks a piece of speech yielded at a short pause rather than at the end.
- `Segmenter(eager_silence=…)`: yields the speech so far at the first pause of that length, then the full utterance at the end. This is the same idea as Cartesia ink-2's `turn.eager_end`. It's off by default, so wake-word handling is unchanged.
- `WakeListener.claim()` / `release()` / `claimed` / `in_speech` / `claim_eager`: lend the microphone to a question.

---

## 6. Configuration reference

### `[speech]` (new or changed keys in **bold**)

| Key | Default | Meaning |
|---|---|---|
| **`engine`** | **`"auto"`** | `auto` / `cartesia` / `system` / `off` (was `system`) |
| `rate` | 1.0 | User's speech rate |
| `voice` | "" | OS voice name |
| **`cartesia_voice_id`** | "" | A Cartesia voice ID **or name** |
| `cartesia_model` | "sonic-3.6" | Primary model |
| **`cartesia_fallback_model`** | "sonic-3" | Tried when the primary is rejected |
| **`emotion`** | "calm" | Emotion for ordinary speech |
| **`cache_prompts`** | true | Save Ally's fixed prompts (works offline) |
| **`cartesia_timeout_seconds`** | 4 | Give up on a Cartesia request after this long |
| **`cartesia_retry_seconds`** | 30 | Skip Cartesia this long after a network failure |

### `[confirm]` (new table)

| Key | Default | Meaning |
|---|---|---|
| `enabled` | true | Spoken questions on/off (buttons still work) |
| `listen_seconds` | 8 | Time for an answer to **start** |
| `listen_delay_ms` | 250 | Pause after Ally stops talking before listening |
| `max_answer_seconds` | 5 | Longest answer recorded |
| `eager_answers` | true | Accept a clear yes/no after a short pause |
| `eager_silence_ms` | 450 | That short pause |
| `reask` | true | One reworded re-ask after an unclear answer |
| `say_no_reply` | true | Say "No problem. I'll leave it as is." after a spoken no |
| `speed` | 0.9 | Question speed (× speech rate) |
| `warning_speed` | 0.85 | Risk warning speed |
| `emotion` | "calm" | Question emotion ("sympathetic" is used after a miss) |
| `chime` | false | A tone before each question |
| `prerender` | true | Render fixed prompts with Cartesia at start |
| `log_latency` | true | Write `logs/voice_latency.jsonl` |

All keys are validated, with ranges (e.g. `listen_seconds` 2–30, `eager_silence_ms` 200–1500). As with the rest of Ally's config, changes apply within about a second, without a restart.

`.env` needs `CARTESIA_API_KEY=` for the Cartesia voice and transcription.

---

## 7. How teammates use it

**Brain (Ben H): a proactive offer**

```python
# Simple contract
if agent.confirm.ask_yes_no("Want me to make this bigger?") == "yes":
    apply_fix()
    agent.confirm.done()                  # "Done. Say undo to put it back."

# With the foundation Offer (when merged): first answer wins, buttons keep working
agent.confirm.confirm_offer(offer, background=True)   # resolves offer with by="voice"

# Pre-render your own offer prompts at startup so they play instantly and offline
agent.confirm.register_prompts(["Want me to read this to you?", "Want me to turn on high contrast?"])
```

A `timeout` means **do nothing**. Read `Answer.intent` to tell apart silence, an unclear answer, no microphone, and so on (useful for cooldowns).

**UI / overlay (Ganesh): the Yes / No buttons**

```python
agent.confirm.answer("yes", by="button")      # or POST /api/voice/answer {"answer": "yes", "by": "button"}
agent.confirm.info()["mode"]                  # "idle" | "speaking" | "listening": drive a mic indicator
agent.confirm.info()["question"]              # the open question, for a caption
```

**Risk (Ganesh): spoken warnings**

```python
ans = agent.confirm.warn({"kind": "scam_popup", "severity": "high"})
if ans == "yes":
    close_the_popup()
```

**Gaze (Hayden): answer by looking.** Call `agent.confirm.answer("yes", by="gaze")` after a 2-second look at Yes.

**Claude (the Brain's model)** can call the `ask_yes_no` tool before doing something the user didn't ask for. `warn_risk` is deliberately hidden from Claude, so only the risk rules can raise warnings.

---

## 8. Interfaces: tools, HTTP API, command line

### Tools (registered in the tool registry)

| Tool | Visible to Claude | Purpose |
|---|---|---|
| `ask_yes_no(question)` | yes | Ask out loud, return yes / no / timeout |
| `answer_question(answer)` | no | What the buttons do |
| `warn_risk(kind, severity)` | no | Spoken risk warning |
| `list_voices(search)` | no | Cartesia voice list |

### HTTP API (local only; needs the `X-Ally-Token` header like the rest of `/api/*`)

| Method & path | Body | Returns |
|---|---|---|
| `GET /api/voice` | — | `VoiceConfirm.info()`: mode, open question, transcription order, latency p50s and last 5, speech info |
| `POST /api/voice/say` | `{text, style, wait}` | `{ok, engine}` |
| `POST /api/voice/ask` | `{prompt, rephrased?, timeout?, kind?}` | The `Answer` (waits for it) |
| `POST /api/voice/answer` | `{answer: "yes"\|"no", by}` | `{ok, answered}` |
| `POST /api/voice/warn` | `{kind, severity, ask?}` | The `Answer`, or `{"value": "said"}` |
| `POST /api/voice/cancel` | — | `{ok}` |

`GET /api/status` now also includes `confirm`, and a richer `speech` section.

### Command line (`python run.py …`)

`say`, `ask` and `warn` go through the running agent if there is one; otherwise they run on their own.

| Command | What it does |
|---|---|
| `say "text" [--style warning]` | Speak, and print which engine was used |
| `ask "question"` | Ask out loud; print the full `Answer` |
| `answer yes\|no` | Answer the open question (like a button) |
| `warn scam_popup` | Spoken risk warning |
| `voices [NAME]` | List Cartesia voices |
| `voice-test [VOICE…] [--play]` | Render Ally's prompts with each voice; print request time, duration and words per minute |
| `doctor` | Now includes live voice checks |

Also `python -m ally.voice_confirm say|ask|classify|warn|audition …` for trying voice without the agent at all.

---

## 9. Testing: what exists and what it covers

### 9.1 Unit tests (`tests/test_voice_confirm.py`, 54 tests, ~20 s)

These use small fakes (a recording speaker, a scripted "ears" queue, and the fake Cartesia server), with no agent, no hardware and no key.

| Group | Tests | What they check |
|---|---|---|
| Understanding answers | 4 | All 184 labeled phrases; **zero false yes** and zero yes-heard-as-no; echoing the action is yes; re-asks use other words and stay ≤ 10 words |
| The asking loop | 20 | yes; no + reply; silence is never repeated; noise; unclear → reworded re-ask → yes; unclear twice → buttons with only one "sorry"; re-ask off; "slower" replays slower and saves the rate; undo counts as no; filler then answer; answer under way at the deadline is heard out; eager accepted only when clear (a bare "Yeah" waits, a clear "No." may end early); button wins and stops speech; late button after a voice answer is ignored; bad answer value rejected; a new question supersedes the old; disabled / empty prompt; no microphone waits for buttons; speech-to-text failure; speech off still listens; latency log written **without transcripts** |
| Offers and risks | 8 | `Offer` resolved by voice; resolved elsewhere stops listening within 1 s; silence never resolves an offer; scam warning has a pause tag, calm emotion and slower speed; low severity only says; no warning question invites an opposite-meaning echo; every prompt ≤ 10 words; pre-render set includes registered and risk prompts |
| Cartesia speech layer | 12 | Request shape and voice-name lookup; emotion rejected → plain retry; model fallback remembered; down → OS voice without SSML tags; circuit breaker (first sentence waits ~1 s, second < 0.3 s); saved prompt plays from disk while offline; page text never cached; `auto` engine selection; unknown voice name; voice list; words-per-minute; transcription through the fake |
| Microphone plumbing | 3 | Eager piece, then more speech, then the full utterance; no eager pieces by default; claimed utterances skip wake-word handling, and eager pieces are dropped when unclaimed |
| Config and agent | 7 | New keys validate and bad values are rejected; shipped `config.toml` matches defaults; module functions use the agent's instance; status and the answer endpoint; ask endpoint answered by a "button"; tools registered (and `warn_risk` hidden from Claude) |

The 100 existing tests still pass unchanged.

### 9.2 End-to-end harness (`tests/e2e_voice.py`, 21 scenarios + benchmark, ~4 min)

**Real:** the agent process (`run.py agent`), its HTTP API, config hot-reload, `VoiceConfirm`, `Speaker`, the Cartesia client code, the microphone segmenter, the fallback chain, the prompt cache and the latency log.

**Faked**, so it runs with no key, speakers or microphone:
- **Cartesia** (`tests/fake_cartesia.py`): a local server whose audio lasts as long as real speech (165 wpm × speed). It adds 200 ms (speech) and 150 ms (transcription) latency, and can be switched to `down` (503), `hang`, `reset`, reject a model, or reject emotions.
- **The microphone** (`tests/fakes/sounddevice.py`): plays a scripted "tape" of speech bursts at set times after the stream opens. The fake transcription maps each burst's length to a scripted transcript.
- **Speakers / OS voice** (`tests/fakes/bin/aplay`, `espeak-ng`): wait as long as the sound would last, and log what played.

Each scenario is a scripted performance: "Ally asks, the user says X at this moment, Cartesia behaves like Y". The harness checks the answer and records the timings. It also checks that prompts were pre-rendered at startup, that the voice name was looked up, that the OS voice was really used in the fallback cases, that the model fallback happened, that the latency log has a row per question, and that `/api/status` includes the voice sections.

**Benchmark:** the same "Yes please." exchange 10 times with eager answers off, then 10 times on, switching the setting through the live config file.

### 9.3 Answer-phrase evaluation (`tests/voice_phrases.py`)

184 labeled answers to "Want me to make this bigger?", written the way speech-to-text produces them: clear yes (60+), clear no (40+), undo (8), repeat/slower (17), unclear that must never be acted on (40+, including Whisper's noise words and off-topic sentences), and speech-to-text spellings. It prints accuracy, false-yes count and rate, yes/no recall, and the list of mistakes.

### 9.4 Other checks done

- `pyflakes` on all changed files: clean, apart from two intentional "is this installed?" imports.
- The patch was applied to a fresh clone of `main`, and the voice tests passed there.
- A trial merge against `origin/hayden/senses`: two small conflicts in `ally/agent.py` (§15).
- `run.py doctor` output checked in the sandbox.

---

## 10. Metrics

**Important:** these come from the simulated run. They measure **Ally's own overhead plus the simulated Cartesia latency** (200 ms speech, 150 ms transcription), not Cartesia's real performance. Run `python run.py voice-test` with a real key for real numbers.

### 10.1 End-to-end scenarios (final run, all passed)

Column meanings:
- **1st audio:** ms from starting to speak until sound began.
- **Reply:** ms from the end of the user's speech until the answer was decided.
- **Total:** the whole exchange, including Ally's speech.

| # | Scenario | What it represents | Answer | By | Intent | Tries | 1st audio | Reply | Total | Engine |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | yes (saved prompt) | The core demo moment | yes | voice | YES | 1 | 1 | 700 | 4609 | cartesia-cache |
| 2 | no thanks | Declining is respected | no | voice | NO | 1 | 1 | 700 | 4972 | cartesia-cache |
| 3 | uh-huh | Common older-adult "yes"; low confidence waits for the full pause | yes | voice | YES | 1 | 3 | 1100 | 4965 | cartesia-cache |
| 4 | silence | Silence never counts as yes; not repeated | timeout | none | SILENCE | 1 | 1 | — | 5973 | cartesia-cache |
| 5 | noise only | Cough written as "Thank you." is ignored | timeout | none | NOISE | 1 | 1 | 2300 | 5956 | cartesia-cache |
| 6 | unclear → re-ask → yes | "Hmm, maybe?" then "Yes please." | yes | voice | YES | 2 | 1 | 700 | 15016 | cartesia |
| 7 | unclear twice → buttons | "Yeah, no." twice | timeout | none | AMBIGUOUS | 2 | 1 | 3100 | 22425 | cartesia-cache |
| 8 | "slower" → replay → yes | Pace adapts and is remembered | yes | voice | YES | 2 | 236 | 700 | 13445 | cartesia |
| 9 | new prompt | Cost of a live Cartesia request | yes | voice | YES | 1 | 233 | 700 | 5960 | cartesia |
| 10 | answer starts late (2.6 s of 3) | Slow speakers aren't cut off (**was a bug**) | yes | voice | YES | 1 | 1 | 700 | 7542 | cartesia-cache |
| 11 | pause inside the answer | "Um." … "Yes." is heard out | yes | voice | YES | 1 | 1 | 700 | 5634 | cartesia-cache |
| 12 | button wins mid-prompt | First answer wins; speech stops | yes | cli | BUTTON | 1 | 240 | — | 404 | cartesia |
| 13 | scam warning → "close it" | Echoing the action is yes | yes | voice | YES | 1 | 1 | 701 | 6131 | cartesia-cache |
| 14 | remote access → "no" | "No" respected in a warning | no | voice | NO | 1 | 1 | 701 | 7309 | cartesia-cache |
| 15 | Cartesia down, saved prompt | Saved audio keeps the Cartesia voice offline | yes | voice | YES | 1 | 1 | 700 | 4838 | cartesia-cache |
| 16 | Cartesia down, new prompt | Instant switch to the OS voice | yes | voice | YES | 1 | 10 | 700 | 4982 | system |
| 17 | Cartesia hangs, new prompt | Worst case: waits out the timeout once | yes | voice | YES | 1 | 3010 | 700 | 7979 | system |
| 18 | Cartesia hangs, 2nd new prompt | Circuit breaker: no more waiting (**was a bug**) | yes | voice | YES | 1 | 1 | 701 | 4964 | system |
| 19 | Cartesia back after retry window | Recovers on its own | yes | voice | YES | 1 | 228 | 700 | 5062 | cartesia |
| 20 | sonic-3.6 rejected → sonic-3 | Account without the newest model | yes | voice | YES | 1 | 236 | 700 | 5064 | cartesia |
| 21 | Cartesia transcription down, no Whisper | Asked out loud; buttons decide | timeout | none | NO_STT | 1 | 227 | — | 13265 | cartesia |

Notes on the numbers:
- **Total time** is dominated by Ally's own speech (a six-word question at 0.9× speed takes about 2.5 s) and the simulated pauses on the tape.
- **Reply of 1100 ms** for "uh-huh" is intended: low-confidence answers wait for the full pause.
- **Rows 5 and 7** show "reply" as the time until the listening window closed, since no decisive answer came.
- **Row 8** made 17 speech requests: after "slower", all prompts are re-rendered in the background at the new speed. That costs Cartesia credits; see §13.

### 10.2 Latency benchmark (10 trials each, saved prompt, user says "Yes please.")

| Mode | Reply p50 | Reply p95 | First audio p50 | First audio p95 |
|---|---|---|---|---|
| Full pause (before) | 1100 ms | 1103 ms | 1 ms | 9 ms |
| **Eager answers (after)** | **700 ms** | **701 ms** | 1 ms | 2 ms |

The 700 ms breaks down as about 450 ms of pause, 150–160 ms of (simulated) transcription, and about 90 ms of Ally overhead. Research target: under 800 ms from end of speech to fix.

### 10.3 Answer understanding

| Metric | Value |
|---|---|
| Phrases | 184 |
| Accuracy | 100% |
| False "yes" (anything not yes treated as yes) | **0** (rate 0.0) |
| Yes recall / No recall | 100% / 100% |
| Yes heard as no | 0 |
| Unclear kept unclear | 100% |

Before fixes, the first version scored 99.4% on 162 phrases. Its one mistake was a long off-topic sentence containing "what" being read as "repeat".

---

## 11. Bugs found by testing, and how they were fixed

| # | Bug | How it was found | Impact | Fix |
|---|---|---|---|---|
| 1 | **Answers that ran past the listening window were thrown away.** The window was checked against when an answer *ended*, not when it *started*. | E2E: "slower, please" (1.7 s of speech starting at 0.5 s of a 3 s window) came back as silence. | An older user who starts answering at 7 s and speaks for 2 s would have been ignored. | The window now covers the *start* of speech. An answer already in progress is heard out (up to `max_answer_seconds` + 1.5 s). The mic sources report "someone is talking right now". New scenario: "answer starts late". |
| 2 | **A hanging Cartesia blocked every new sentence for the full timeout** (8 s by default). | E2E: both "hang" scenarios took 3,011 ms to first audio. | Venue Wi-Fi trouble would make every sentence wait 8 s. | **Circuit breaker:** after a network failure, skip Cartesia for 30 s and use the OS voice at once (saved prompts still play in the Cartesia voice). Default timeout lowered from 8 s to 4 s. Second hang now takes 1 ms. |
| 3 | **Two warning questions invited answers meaning the opposite.** "Stop it?" made the answer "stop it" count as **no**, and "Go back?" made "go back" count as **undo**. Echoes like "close it" were read as unclear. | Reviewing the classifier against the risk questions. | A user agreeing to stop a remote-control session would have been ignored. | Reworded to "Disconnect them?" and "Close this page?". Added the echo-the-action rule. Added a test that every warning's echo counts as yes. |
| 4 | **Early acceptance could misread "Yeah… no".** A bare "Yeah" followed by a pause was accepted as yes before "…no" arrived. | Unit test written for exactly that case failed. | Ally could act against the user's intent (reversible with undo, but wrong). | Bare openers (yeah, sure, okay, alright, fine, well) never end an answer early. A clear "no" may, since acting on it too soon only means doing nothing. |
| 5 | **A long off-topic sentence containing "what" was read as "repeat".** | Phrase evaluation. | A stray sentence would replay the question instead of counting as unclear. | Repeat/slower only fire for short replies (≤ 4 other words). |
| 6 | **"Sorry, I missed that" was said twice** after two unclear answers. | Listening to the e2e transcript. | Repetitive and patronizing. | After a re-ask has already apologized, the closing line is just "You can tap Yes or No." |

Smaller fixes: the noise-only case reports intent `NOISE` instead of `SILENCE`, and the fake server no longer prints broken-pipe errors when the client gives up.

---

## 12. Design decisions and where they differ from the research

**Followed from Research: Voice**
- Calm voice; questions slightly slower (0.9×) and warnings slower still (0.85×), with a pause before the question; never "scared" or "alarmed".
- Prompts of 10 words or fewer; reword instead of repeating; one re-ask, then buttons.
- Never act on silence or ambiguity; "no problem" is yes; "yeah, no" is unclear; "uh-huh" is a low-confidence yes.
- Half-duplex: listen only after speaking, and discard audio heard while speaking; one microphone owner.
- No wake word for answers; pre-render the fixed prompts at startup; model IDs in config; OS voice as the last fallback.
- Latency logged to `voice_latency.jsonl` with p50s exposed for a dashboard chip.

**Where this differs, and why**

| Research suggested | Built instead | Why |
|---|---|---|
| Streaming speech over Cartesia's WebSocket | Kept the existing `POST /tts/bytes` (whole audio per sentence) | Already working in the repo and short prompts are pre-rendered, so first audio for offers is ~1 ms anyway. Streaming matters for **long read-aloud**; it's recommended as a next step (§17). |
| `ink-2` streaming transcription with built-in turn detection | Batch `ink-whisper` on locally segmented audio, plus offline Whisper | Already working in the repo, testable offline, and the research says ink-2's status is unclear. The eager-pause segmenter gives the same "act on a clear answer early" behaviour. |
| `Cartesia-Version: 2026-03-01` | Kept the repo's `2026-08-14` | The existing code (written and run by the team) uses it. Untested by me against the real API. |
| Speed target 150–160 wpm | Speed 0.9× for questions; `voice-test` measures real wpm | Cartesia treats speed as guidance; it must be measured with the chosen voice. |
| Rate-limit offers (cooldown after a No) | Not in voice | That's the Brain's job (Brain & Habits research). Voice returns the intent so the Brain can apply cooldowns. |
| Warning questions in the research's longer wording | Short versions ("This pop-up may be a scam. Close it?") | Kept to the 10-word rule. Adding the "why" sentence is recommended (§17). |

**Other choices**
- **"Yes" always means the protective action** in warnings, so a misheard or reflexive "yes" can only make the user safer.
- **Transcripts are not written to disk**; only timings and intents are.
- **`warn_risk` is hidden from Claude**, so warnings come only from the deterministic risk rules.
- **Default engine changed to `auto`.** Without a key it behaves exactly as before; with a key and a voice it uses Cartesia automatically.

---

## 13. Known limitations and what is not verified

**Not verified against the real world**
- **Real Cartesia:** no request was made to the real API (the sandbox's network blocks it). Still unknown: whether `sonic-3.6` accepts `generation_config` emotion and speed, the real latency, and whether a voice *name* resolves as expected. The code retries without emotion and falls back to `sonic-3` if needed, but **run `python run.py doctor` and `voice-test` with a key.**
- **A real microphone:** the segmenter thresholds were tested on synthetic audio only. Noisy venues, soft voices and laptop-speaker echo are untested; use a headset for the demo recording.
- **Real older voices:** the 184 phrases were written by the same person who wrote the rules, so 100% shows the rules are consistent, **not** real-world accuracy.
- **Windows specifics:** the e2e harness's fake speakers only work on Linux. On Windows the run plays through real speakers, and the "OS voice fallback used" check reports a false failure.

**Current behaviour to be aware of**
- Without a Cartesia key **and** without `faster-whisper`, Ally can ask out loud but **cannot hear answers**; only the buttons work. Offline Whisper downloads about 145 MB on first use.
- Answer understanding is **English only**.
- After "slower", all prompts are re-rendered at the new speed (about 16 Cartesia requests, in the background). That costs credits.
- The listening window (8 s) and the UI's offer duration (20 s in the UI research) are different timers. After voice times out, the buttons stay clickable; the team should agree the policy.
- The always-on listener's own end-of-speech detection doesn't use the eager pause, except while a question has borrowed the microphone.

**Depends on other people's work** (not yet merged with voice)
- Offers triggered by real struggle (Brain + Senses), real scam detection (risk rules + extension), and on-screen buttons (overlay). Until then, `run.py ask / warn / answer` stand in for them.

---

## 14. Git status and how to push

- Branch `benw/voice`, commit **`fe5b1df`** ("Voice: say() and ask_yes_no() on Cartesia, with an OS-voice fallback"), on top of `main` at `fef8cf4`.
- **Not pushed.** GitHub refused with "benhuang3/accessibility-anywhere is not in this session's authorized repository set". A request for write access during the session was denied, so no other route was tried.
- The same change is in **`benw-voice.patch`** (24 files). It was verified to apply cleanly to a fresh clone of `main`.

**To push from your own laptop:**

```powershell
git clone https://github.com/benhuang3/accessibility-anywhere.git
cd accessibility-anywhere
git checkout -b benw/voice
git apply --index path\to\benw-voice.patch
git commit -m "Voice: say() and ask_yes_no() on Cartesia, with an OS-voice fallback"
git push -u origin benw/voice
```

Or give the session push access to the repo, and it can push and open a pull request.

---

## 15. Merging with the rest of the team's work

- **`hayden/senses`:** a trial merge gives **two small conflicts in `ally/agent.py`**, both where each branch adds a line in the same spot:
  1. In `__init__`, voice adds `self.confirm = VoiceConfirm(...)` + `install_voice(...)` and senses adds `self.senses = Senses(self)`. **Keep both.**
  2. In `close()`, voice adds `self.confirm.cancel` to the shutdown list and senses adds `self.senses.stop`. **Keep both** in one list.

  Everything else (`README.md`, `config.py`, `default_config.toml`, `doctor.py`, `tools/__init__.py`) merges automatically.
- **Foundation patch** (`contracts.py`, `Offer`, event bus, `simulate`): when merged, wire the Brain's offers to `agent.confirm.confirm_offer(offer, background=True)`. The patch's own starter voice confirm, if any, should be replaced by this one. `simulate answer yes` can call `agent.confirm.answer("yes", by="simulate")`.
- **Notion:** the Current State page's Ownership list still shows Ganesh on voice confirmation; Team Roles (which governs) shows Ben W. The voice task is still "To do", and its due time (stored as 20:00 UTC) is 1:00 PM Pacific on Oct 24. These were left unchanged.

---

## 16. How to test it yourself

**Level 1: automated (no key or hardware; ~1 minute)**
```powershell
python -m unittest discover -s tests -p "test_*.py"     # expect: Ran 154 tests ... OK (skipped=1)
python tests\voice_phrases.py                           # expect: "false_yes": 0
python -m ally.voice_confirm classify "yeah, no"        # check one answer
```

**Level 2: simulated end to end (~4 minutes; cleanest on Linux/WSL)**
```powershell
python tests\e2e_voice.py --json metrics.json
```

**Level 3: real laptop**
1. **No key (tests the fallback):**
   ```powershell
   python run.py doctor
   python run.py say "Want me to make this bigger?"
   python run.py ask "Want me to make this bigger?"
   ```
   Expect `spoke with: system` from `say`, and for `ask`, an answer printed after you speak. Hearing you needs `faster-whisper`.
2. **With a key:**
   1. Put `CARTESIA_API_KEY=` in `.env`.
   2. Run `python run.py voices`, then `python run.py voice-test Tessa Leo Marian Dana --play`. Pick the lowest voice that still sounds warm, near 150–160 wpm.
   3. Set `[speech] cartesia_voice_id`.
   4. Run `doctor` again; "Cartesia voice" should say `works`.
   5. Run `say`, `ask` and `warn scam_popup` again; expect `spoke with: cartesia`.
3. **Inside the agent:**
   - Terminal 1: `python run.py agent`.
   - Terminal 2: `python run.py ask "Want me to make this bigger?"`, without answering out loud.
   - Terminal 3: `python run.py answer yes` (acts like a button). Terminal 2 should print `"by": "cli"`.
   - Afterwards: `python run.py status` and `logs\voice_latency.jsonl`.
4. **Break things on purpose:**

   | Do this | Expect |
   |---|---|
   | Wi-Fi off, ask the standard offer | Still the Cartesia voice (saved prompt) |
   | Wi-Fi off, say something new | Windows voice within ~4 s; next sentence instantly |
   | Wi-Fi on, wait 30 s, say something new | Back to Cartesia |
   | Stay silent | `timeout`, not repeated |
   | Say "maybe" | "Sorry, I missed that…" and a reworded question |
   | Say "slower, please" | Slower replay; later speech stays slower |
   | Block the mic in Windows privacy settings | Asked out loud; waits for a button |

**Level 4: an older person.** Run `ask` and `warn scam_popup` with two or three older adults. Write down their exact words, add misses to `tests/voice_phrases.py`, and ask whether the voice is too fast or too high-pitched.

---

## 17. Recommended next steps

**Quick, high value, in the voice code (tonight)**
1. **Say *why* in warnings:** "This pop-up may be a scam. Real Windows warnings never include a phone number. Close it?" The reason is what protects people next time.
2. **Combined warning for the most dangerous moment**, when a remote-control tool is running *and* the bank site opens: "Someone may be able to see your screen, and you've just opened your bank. Shall I close the remote tool?"
3. **"Stop asking me"** as its own answer type, separate from "not now" and "no", so the Brain can apply the right cooldown.
4. **Spoken memory payoff:** "I made this bigger, like last time. Say undo to change it."
5. **Spoken first-run setup:** webcam consent, speed check and voice choice, all with `ask_yes_no`.
6. **Streaming read-aloud** over Cartesia's WebSocket, with word timestamps for highlighting in the reading card.

**Needs a teammate**
- Captions for every spoken line, and a visible "Listening" light (overlay).
- Latency and trust numbers on the dashboard: reply time, acceptance rate, undo rate.
- Answer by looking at Yes for 2 seconds (gaze calls `answer(..., by="gaze")`).

**Before the demo**
- Real-key smoke test (`doctor`, `voice-test`), choose and set the voice, install `faster-whisper`, record with a headset.
- Ten minutes with real older adults, and add their phrases to the test set.

**For the pitch's "what's next"**
- Trusted-contact alerts and a caregiver summary.
- Other languages.
- Fewer cloud dependencies.

---

## 18. Sources

**Notion (Cal Hacks 13 HQ)**
- [Task: Voice: say() and ask_yes_no() on Cartesia, with an OS-voice fallback](https://app.notion.com/p/40396bb76cac8143978de97e7e15c4af)
- [Research: Voice](https://app.notion.com/p/40396bb76cac81e89fa0ea5be868c799)
- [Team Roles & Interfaces](https://app.notion.com/p/40396bb76cac812a8d14fe9f47ac0b9d)
- [Code Foundation (start here)](https://app.notion.com/p/40396bb76cac8163babfc5569dbf34ab)
- [Current State — Start Here](https://app.notion.com/p/40396bb76cac8147b220f983a4881ec2)
- [Idea](https://app.notion.com/p/40396bb76cac818c9e0fd5c791857d14)
- [Cal Hacks 13 HQ](https://app.notion.com/p/40396bb76cac8168a8cedc24534a468b)
- [Research: Scam & Risk](https://app.notion.com/p/40396bb76cac819ebdb5f31ce593e495)
- [Research: Brain & Habits](https://app.notion.com/p/40396bb76cac81a9a870d85ce10fda1a)
- [Research: UI & Demo](https://app.notion.com/p/40396bb76cac81bd96c6e26a29add38b)
- [Pitch & Submission](https://app.notion.com/p/40396bb76cac8114981ee4cf53a3463e)

**Cartesia documentation**
- [Overview](https://docs.cartesia.ai/get-started/overview.md)
- [Text-to-Speech (Bytes)](https://docs.cartesia.ai/2026-03-01/api-reference/tts/bytes.md)
- [Volume, speed and emotion](https://docs.cartesia.ai/build-with-cartesia/capability-guides/volume-speed-emotion)
- [List voices](https://docs.cartesia.ai/2026-03-01/api-reference/voices/list.md)

**Repository**
- [benhuang3/accessibility-anywhere](https://github.com/benhuang3/accessibility-anywhere): `main` at `fef8cf4`; `hayden/senses` at `18dbceb`
