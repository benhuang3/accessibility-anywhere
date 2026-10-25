# Spec: Ally accepts more spoken requests and fills them in to the closest known command

About 40 minutes of build time. No screenshots. Keep changes small and well tested.

## Problem

Older adults speak loosely, and Whisper mis-hears. Two places lose requests today:

1. **Wake word.** `ally/listener.py` `split_wake` / `wake_pattern` only accept the exact wake words (default
   `["ally"]`, plus "hey/okay" prefixes). Whisper often writes "Ally" as Allie, Alley, Ali, Aly, Ollie, Olly,
   Al E, Alli, Ally's, Hello Ally, Hi Ally, "Ally," with odd punctuation, or puts it second ("Okay so Ally make it
   bigger"). Those utterances are dropped as "not for Ally".
2. **Strict rules.** `ally/brain.py` `handle()` runs macros, exact tool names, then `rules.match(text, strict=True)`
   (whole-sentence regexes in `ally/rules.py`), and only then Claude. Anything phrased differently waits on a
   Claude round trip, which is slower and sometimes asks a question back instead of acting.

## Changes

### A. Wake word tolerance (`ally/listener.py`)
- Accept common mis-hearings of each configured wake word. For "ally": allie, alley, ali, aly, alli, ollie, olly,
  ally's, "al e", "a lee" (case-insensitive, word-bounded). Generate the variants from the configured word with a
  small table for "ally" plus a generic fallback (for other wake words: just the word and its "-s"/"'s" forms).
- Accept the wake word within the first three words (after filler like "okay so", "um", "hey", "hello", "hi"),
  not only at the very start or end.
- Must still reject everyday speech that merely contains a similar word mid-sentence (e.g. "my friend Ali said
  the alley was dark" must NOT wake). Write tests for both sides.
- `is_stop` keeps working with the variants ("Allie stop").

### B. Auto-fill to the closest command (`ally/rules.py`, `ally/brain.py`)
- Give each rule in `RULES` (and the picture/click rules where it makes sense) a short list of natural example
  phrasings older adults actually say (5–15 each; see the corpus below for the style). Put them in a dict next to
  `RULES`, e.g. `EXAMPLES = {"text_bigger": ["make the words bigger", "i can't read this", ...], ...}`.
- New `rules.closest(text) -> Match | None`: normalize, then score against every example with a blend of
  `difflib.SequenceMatcher` ratio and content-word overlap (ignore filler/stop words). Return the best rule's
  `Match` only if the score clears a threshold and beats the runner-up by a margin (both tuned on the corpus).
  Never auto-fill `click`/number rules, `reset`, `calibrate`/`face_setup`, or anything that sends data or
  money; those stay strict or go to Claude.
- In `Brain.handle`, after strict rules miss and before Claude: try `rules.closest`; if it hits, run it with
  mode "fast" (same as a strict hit). Keep the existing loose/offline path as is.
- The `follow` chatter filter in `Listener.handle` (`len(rest.split()) > 5 and not rules.match(...)`) should also
  accept a `rules.closest` hit.

### C. Corpus test (`tests/test_voice_accept.py`)
A table of at least 60 realistic utterances (as Whisper would write them, including mis-heard wake words and
filler) → expected outcome: the rule name, or `None` (should go to Claude / not wake). Include the demo script:
- "Ally, make it easier to read" / "Allie can you make this easier to read" → the reading/clutter rule that best
  fits (check what `reader`, `clutter`, `look_magnify` and `text_bigger` do and pick the most sensible).
- "zoom in", "can you zoom in a bit", "make it bigger please", "I can't see this" → zoom/text bigger.
- "scroll down", "go down", "keep going", "next part" → scroll_down; "go up a bit" → scroll_up.
- "go back", "take me back", "back to the last page" → back.
- "show me the picture", "open the image", "let me see the photo" → the picture rule.
- "read this to me", "read it out loud" → read_page / read_selection.
- "stop", "Ally stop", "be quiet" → stop.
- Plus negatives: chit-chat, questions for Claude ("what's the weather"), and "call Priya" (must not auto-fill).
Report the hit rate before (strict rules only) and after; the after rate on positives should be ≥ 90% with zero
wrong fills on the negatives.

## Allowed files

`ally/listener.py` (wake-word matching and the follow filter only), `ally/rules.py`, `ally/brain.py`
(`handle` only), new `tests/test_voice_accept.py`, and existing tests that need updating
(`tests/test_handsfree.py`, `tests/test_brain.py`, any `test_*listener*`). Don't change speech, Cartesia,
Claude prompts, tools, or the overlay. Never touch `.env`, `config.toml`, `state/`, `logs/`. Don't start Ally.

## Acceptance

- `cd tests && python -m unittest test_voice_accept test_brain test_handsfree test_runtime test_registry test_config test_network test_voice_bear`
  pass (a Windows speech-worker error that also fails on the base commit is fine; say so).
- Files are CRLF; preserve endings. Don't commit. End with a short report: files changed, before/after hit
  rates, thresholds chosen, anything you deliberately left to Claude.
