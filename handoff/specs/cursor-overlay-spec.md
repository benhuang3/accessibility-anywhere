# Cursor spec: ally/overlay/ (on-screen warning card, highlight, Yes/No bubble)

Worktree `wt-overlay`, branch `ganesh/overlay`. Do not commit, push, merge or switch branches. Python 3.11+, standard library only
(tkinter). Windows is the target; the code must also import cleanly on a machine with no display.

## Goal
Ally has a proactive coordinator (`ally/proactive.py`) that shows things to an older adult through a UI object. Today that is
`NullUI`. Build the real one: `ally.overlay.Overlay`. `Agent.start_proactive` (ally/agent.py) already does:

    from .overlay import Overlay
    ov = Overlay()
    if ov.start(): self.overlay = self.proactive.ui = ov      # else: self.events.add("error", ov.error)
    ...  agent.close() calls ov.stop()

## Allowed files (create only these)
- `ally/overlay/__init__.py` (exports `Overlay`)
- `ally/overlay/overlay.py`, plus at most one more module, e.g. `ally/overlay/layout.py` for pure layout/queue logic
- `tests/test_overlay.py`
Everything else is read-only. Do not edit proactive.py, contracts.py, agent.py, config files or risk modules.

## Interface (must match `NullUI` in ally/proactive.py exactly, plus start/stop/error)
    class Overlay:
        error: str                                  # why start() failed ("" when fine)
        def start(self) -> bool                     # create the Tk thread; False + .error when no display/tkinter
        def stop(self) -> None                      # idempotent, never raises, closes any window
        def highlight(self, box)                    # box = (left, top, width, height) in physical screen pixels, or None
        def show_card(self, text, actions=("OK",), kind="info")   # kind: "info" | "risk"
        def offer(self, offer)                      # offer is ally.contracts.Offer; shows prompt + Yes/No
        def clear(self)                             # remove the offer bubble and highlight

Rules:
- All methods are called from the coordinator thread, NOT the Tk thread. Marshal through a `queue.Queue` that the Tk thread drains
  with `root.after(...)`. Never touch Tk widgets from another thread. Methods return immediately.
- Answers: when the user clicks Yes or No (or presses Enter = Yes, Esc = No) call `offer.resolve("yes"|"no", "overlay")`. If the
  offer is already resolved elsewhere (voice answered first, timeout), `clear()` removes the bubble. Never block the Tk thread.
- `show_card(text, actions, kind)`: a card with the text and one big button per action label. A click closes the card
  (and, if the optional `on_action` callback was passed to `Overlay(on_action=None)`, calls `on_action(label)`). `kind="risk"`
  uses a calm amber style (background #FFF4D6, dark text #1A1A1A, 4px #B26A00 border), NOT red and NOT flashing. `kind="info"`
  uses a white card. A new risk card replaces an older info card; a risk card is never replaced by an info card.
- Accessibility (older adults, low vision): body text at least 28 px (use pt sizes that give >= 28 px), titles 32 px, buttons at
  least 64 px tall and 220 px wide with 24 px type, contrast ratio >= 7:1, no animation, no sound, keyboard focus on the first
  button when the card appears. Windows are `-topmost`, placed top-center of the primary monitor, 40 px from the top, width ~
  min(900, 80% of screen width). Never steal focus permanently: take focus once on show.
- The highlight is a thin (6 px) amber rectangle outline around `box`, made of four small borderless topmost windows (or a
  transparent-color overlay), click-through is not required. It clears with `clear()`.
- DPI: call `ctypes.windll.shcore.SetProcessDpiAwareness(2)` inside a try/except on Windows before creating Tk, so pixel
  coordinates match the physical screen. Guard everything Windows-specific with try/except and `sys.platform`.
- `start()` returns False with `.error = "no display"` (or the real exception text) when tkinter cannot open a display; it must
  not raise and must not hang (use a threading.Event with a 3 s timeout for the Tk thread to report ready).

## Pure logic to unit test (no window needed)
Put it in a small module that imports nothing from tkinter at module level:
- `fit_card(screen_w, screen_h) -> (x, y, w)`: top-center placement and width as above.
- `wrap_lines`-style helper or character budget so long text is wrapped rather than cut; text is always set as plain text
  (never evaluated), control characters stripped, max 400 characters.
- Command queue: `Overlay` converts method calls into plain command tuples ("card", text, actions, kind), ("offer", id, prompt),
  ("highlight", box), ("clear",) before they reach Tk. Test this conversion and the replace rules (risk beats info, newest offer
  wins) with a fake Tk-less runner.
- `start()` on a machine without a display returns False (simulate by making the Tk factory raise).
- Offer resolution: a fake offer object records `resolve(choice, by)`; feeding the pure "button pressed" handler with "yes" /
  "no" / Enter / Esc produces the right call, and only the first answer counts.

## Tests: `tests/test_overlay.py`
Start with `from helpers import ROOT  # noqa: F401` like the other tests. Run only `cd tests && python -m unittest test_overlay`.
Tests must never open a real window and must not touch the screen/magnifier. Do not run `run.py`.

## Style
Match the surrounding code: small functions, type hints, comments only where the reason isn't obvious. User-facing text is plain.
Final report: files changed, test results, anything not done or assumed.

## Out of scope
Wiring warnings to the bus, the rules, the dashboard, the Chrome extension, voice.
