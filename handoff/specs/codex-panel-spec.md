# Codex spec: one calm safety panel instead of three pop-ups (ally/overlay/)

Worktree `wt-risk-all`, branch `risk-branch`. Do not commit, push, merge or switch branches. Never read `.env`.

## Problem (from a live test on Windows)
When Ally spots a risky page, three things appear at the same moment: the warning card (`show_card(text, ("OK",), "risk")` from
`Coordinator.handle_risk` in `ally/proactive.py`), a separate Yes/No bubble below it (`offer(offer)` from `Coordinator.ask`, called by
`ally/risk/wire.py` with `Offer(prompt, context="risk", ...)`), plus a highlight box. The user called the result "terrible". The user is
an older adult; it must look like ONE calm, clear, trustworthy message.

The two calls arrive in either order, a few milliseconds apart, from the coordinator thread. Read `ally/overlay/overlay.py`,
`ally/overlay/layout.py`, `tests/test_overlay.py`, `ally/proactive.py` (`NullUI`, `Coordinator.ask`, `handle_risk`) and
`ally/contracts.py` (`Offer`) first.

## Allowed files
`ally/overlay/overlay.py`, `ally/overlay/layout.py`, `tests/test_overlay.py`. Nothing else. The public interface must not change:
`Overlay(on_action=None)`, `start() -> bool`, `.error`, `stop()`, `highlight(box)`, `show_card(text, actions=("OK",), kind="info")`,
`offer(offer)`, `clear()`. Keep everything that already works: Tk on its own thread with a command queue, DPI awareness, `_place()`
(set geometry, settle, set position again: Tk drops positions otherwise), `_keep_on_top()` every ~0.5 s, risk card never replaced by
an info card, plain text only, Enter = Yes and Esc = No.

## What to build
### 1. Merge a risk card and a risk offer into ONE panel
- If a risk card is showing (or arrives within 1.5 s) and an offer with `offer.context == "risk"` arrives (or arrived up to 1.5 s
  earlier), render a single panel window. Never two windows for one risk.
- Panel content, top to bottom:
  1. a small label "Ally safety check" (18 px equivalent, muted #6B4E16), with a 10 px amber accent bar on the left edge of the panel
  2. the title: the first sentence of the card text (split at the first ". " / "? "), bold, 32 px equivalent, #1A1A1A
  3. the rest of the card text, regular, 26 px equivalent, #2B2B2B, wrapped to the panel width (use Label `wraplength` in pixels)
  4. the question (`offer.prompt`), semibold, 28 px equivalent, with 16 px space above
  5. one row of two buttons, at least 64 px tall and 260 px wide, 24 px text, 24 px gap:
     primary = Yes (dark amber #7A4100, white text), secondary = No (white, #1A1A1A text, 2 px #1A1A1A border).
     Labels: if the prompt starts with "Want me to close", use "Yes, close it" / "No, keep it"; otherwise "Yes" / "No".
- Panel style: background #FFF8E6, 2 px #B26A00 outer border, 32 px inner padding, top-center like today (`fit_card`).
  No flashing, no animation, no sound. Keyboard focus on the primary button once.
- A Yes/No click (or Enter/Esc) calls `offer.resolve("yes"|"no", "overlay")` and closes the WHOLE panel (card included): the warning
  was about this decision.
- If the offer ends elsewhere (voice answered, timeout: the coordinator then calls `clear()`), the panel goes back to the card-only
  form described in 2, without moving or flickering more than necessary (rebuilding the window in place is fine).

### 2. Card only (no offer, or after the offer ended)
Same panel look, with title + body and a single "OK" button (primary style). OK closes it. A risk card with no offer stays until OK.

### 3. Offers that are not about risk (context != "risk")
Keep a separate bubble, restyled to match the panel family (white background, 2 px #1A1A1A border, same fonts and button styles),
placed below a showing card exactly as today. These come from the struggle path ("Want me to make this page bigger?").

### 4. Highlight
Unchanged: thin amber outline windows; cleared by `clear()`.

### 5. Pure, testable logic in layout.py (no tkinter import at module level)
- `split_title(text) -> tuple[str, str]`
- `button_labels(prompt) -> tuple[str, str]`
- A small state machine, e.g. `PanelState`, that receives events (`card(text, actions, kind, t)`, `offer(id, context, prompt, t)`,
  `offer_done(id)`, `clear()`, `ok()`), and returns what should be on screen: `("none" | "card" | "panel" | "bubble" | "card+bubble", ...)`.
  The Tk code renders whatever the state says. All merge timing (the 1.5 s window) lives here with an injectable clock.

## Tests (`tests/test_overlay.py`, keep the existing ones passing; add about 15)
No real window, no screen access. Cover: card then risk offer -> one panel; risk offer then card -> one panel; offer 3 s before
the card -> no merge (separate); non-risk offer -> bubble, never merged; Yes/No on the panel resolves once with by="overlay" and
closes everything; offer ended elsewhere then clear() -> card-only; OK on card-only closes it; info card never replaces risk card;
`split_title` on one sentence, two sentences, a question, empty; `button_labels`; long text is sanitized and capped.
Run only: `cd tests && python -m unittest test_overlay test_proactive`.

## Report
Files changed, test results, and anything you could not verify (you cannot see the screen; I will screenshot it).
